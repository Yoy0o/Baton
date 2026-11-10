"""意图识别与场景剧本的定义：把一句话归到某个场景（意图），并取出参数。

场景剧本由各系统随接入清单提供（清单字段 playbooks，指向同目录下的 playbooks.yaml），每个意图包含：
- 触发条件 match（any / all / none 词表，以 re: 开头的为正则）与优先级 priority；
- 槽位 slots：要从原话里取出的参数，类型见 slots.py；
- 前置条件 requires：需要某个工具可用、已有某类结果、或用户给出某个槽位；
- 步骤 steps（call 调工具 / show 展示结果 / write 预览 → 确认 → 提交）、答复模板 answer、追问建议 suggestions。

同一份剧本有三种用途（ADR-0011）：
1. 规则模式按剧本执行（playbook.py），不再为每个场景手写 Python；
2. 模型模式把识别结果、已取出的参数和建议步骤写进系统提示词，模型仍可自行决定；
3. 工具多时，网关按识别出的意图补充路由，只加载相关系统的工具。
字段与校验规则见 INTEGRATION_RULES 第 11 节；校验提示里的规则编号（R11.x）与文档一一对应。
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .contracts import BUILTIN_SYSTEMS, TOOL_NAME_RE
from .slots import check_slot, extract

if TYPE_CHECKING:  # 只用于类型标注：manifest.py 依赖本模块，运行时不能反向导入
    from .config import Settings
    from .manifest import Manifest
    from .transcript import Transcript

INTENT_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
# 模板占位符 {名字.路径|备选|字面量}，渲染见 playbook.render
PLACEHOLDER_RE = re.compile(r"\{([^{}]+)\}")
PATH_RE = re.compile(r"^[a-z_][a-z0-9_]*(?:\.[a-z0-9_]+)*$")
_FROM_CALL_RE = re.compile(r"^([a-z][a-z0-9]{1,19}__[a-z][a-z0-9_]{1,40})\.([A-Za-z_][A-Za-z0-9_]*)$")
# 模板里总能用的名字：examples.<意图 id>（该意图的第一个示例）、help（帮助文本）、today（今天）、intent（当前意图）
GLOBAL_NAMES = frozenset({"examples", "help", "today", "intent"})
# write 步骤的状态说明里额外可用的名字
WRITE_LINE_NAMES = frozenset({"target", "summary", "error", "state"})
WriteState = Literal["skipped", "preview_failed", "not_applied", "applied", "rejected", "failed"]
ResultType = Literal["table", "feature_collection", "group_plan", "object"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def _hit(term: str, text: str) -> bool:
    return bool(re.search(term[3:], text)) if term.startswith("re:") else term in text


def placeholders(value: Any) -> list[str]:
    """模板里引用的名字（每个占位符的每个备选项，字面量除外）。"""
    out: list[str] = []
    if isinstance(value, str):
        for body in PLACEHOLDER_RE.findall(value):
            out += [alt.strip() for alt in body.split("|") if PATH_RE.match(alt.strip())]
    elif isinstance(value, dict):
        for v in value.values():
            out += placeholders(v)
    elif isinstance(value, list):
        for v in value:
            out += placeholders(v)
    return out


# ---------------------------------------------------------------- 剧本文件的结构


class MatchSpec(_Strict):
    """触发条件。none 命中任何一个即排除；all 须全部命中；any 至少命中一个；slots 中的槽位须从原话里取到值。

    打分：all、slots 每项 1 分，any 每命中一个 1 分。
    """

    any: list[str] = Field(default_factory=list)
    all: list[str] = Field(default_factory=list)
    none: list[str] = Field(default_factory=list)
    slots: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> MatchSpec:
        if not self.any and not self.all and not self.slots:
            raise ValueError("match 至少要声明 any、all、slots 之一（R11.2）")
        for term in self.any + self.all + self.none:
            if not term.strip():
                raise ValueError("match 中不能有空词（R11.2）")
            if term.startswith("re:"):
                try:
                    re.compile(term[3:])
                except re.error as e:
                    raise ValueError(f"match 正则 {term!r} 不合法：{e}（R11.2）") from None
        return self

    def test(self, text: str, slots: dict[str, Any] | None = None) -> list[str] | None:
        """命中时返回命中项（词、或“槽位:名字”，用于打分和解释），不命中返回 None。"""
        if any(_hit(t, text) for t in self.none):
            return None
        if not all(_hit(t, text) for t in self.all):
            return None
        hits = [t for t in self.any if _hit(t, text)]
        if self.any and not hits:
            return None
        values = slots or {}
        if any(values.get(name) in (None, "", [], False) for name in self.slots):
            return None
        return [t.removeprefix("re:") for t in self.all + hits] + [f"槽位:{name}" for name in self.slots]


class RequireSpec(_Strict):
    """前置条件，三选一：tool（某个工具当前可用，常用于界面工具）、result（本对话已有某类结果）、slot（用户给出了某个参数）。"""

    tool: str | None = None
    result: str | None = None  # 给找到的结果起个名字，模板里用 {名字.result_id}、{名字.data.xxx} 引用
    type: ResultType | None = None
    scope: Literal["previous", "any"] = "any"  # previous：只认之前轮次产生的结果
    slot: str | None = None
    message: str | None = None  # 不满足时的答复（规则模式），可用模板
    suggestions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> RequireSpec:
        kinds = [k for k in ("tool", "result", "slot") if getattr(self, k)]
        if len(kinds) != 1:
            raise ValueError("requires 的每一项须且只能声明 tool、result、slot 之一（R11.3）")
        if self.tool and not TOOL_NAME_RE.match(self.tool):
            raise ValueError(f"requires.tool {self.tool!r} 不是合法的工具全名（R11.3）")
        if self.result and not self.type:
            raise ValueError(f"requires.result {self.result} 须声明 type（结果类型）（R11.3）")
        return self


class ShowSpec(_Strict):
    """展示一个结果：ui 中第一个可用的界面工具（传 result_id、color_by）；canvas 决定是否同时 / 兜底用工作台画布。"""

    result: str  # 步骤 id 或 requires.result 的名字
    color_by: str | None = None
    title: str = ""
    ui: list[str] = Field(default_factory=list)
    canvas: Literal["always", "fallback", "never"] = "fallback"

    @field_validator("ui")
    @classmethod
    def _ui(cls, v: list[str]) -> list[str]:
        for name in v:
            if not TOOL_NAME_RE.match(name):
                raise ValueError(f"show.ui 中的 {name!r} 不是合法的工具全名（R11.3）")
        return v


class WriteSpec(_Strict):
    """写操作：调用 preview，拿到 confirm_token 后调用 apply；用户确认由运行时强制（ADR-0003）。"""

    preview: str
    apply: str
    args: dict[str, Any] = Field(default_factory=dict)  # 只传给 preview；apply 只收 confirm_token
    target: str | None = None  # 答复里称呼被写入的系统，默认取 apply 所属系统的标题
    lines: dict[WriteState, str] = Field(default_factory=dict)  # 覆盖各状态的默认说明

    @model_validator(mode="after")
    def _check(self) -> WriteSpec:
        for name in (self.preview, self.apply):
            if not TOOL_NAME_RE.match(name):
                raise ValueError(f"write 中的 {name!r} 不是合法的工具全名（R11.3）")
        if self.preview.partition("__")[0] != self.apply.partition("__")[0]:
            raise ValueError(f"write.preview 与 write.apply 须属于同一系统：{self.preview} / {self.apply}（R11.6）")
        if "confirm_token" in self.args:
            raise ValueError("write.args 不能传 confirm_token：令牌只能由网关在预览后签发（R11.6）")
        return self


class StepSpec(_Strict):
    id: str
    when: str | None = None  # 槽位或步骤 id，取值为真时才执行；前面加 not 取反
    call: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    optional: bool = False  # call 失败时继续后面的步骤（默认失败即答复 on_error）
    on_error: str | None = None
    show: ShowSpec | None = None
    write: WriteSpec | None = None

    @model_validator(mode="after")
    def _check(self) -> StepSpec:
        if not INTENT_ID_RE.match(self.id):
            raise ValueError(f"步骤 id {self.id!r} 须为小写 snake_case（R11.3）")
        kinds = [k for k in ("call", "show", "write") if getattr(self, k)]
        if len(kinds) != 1:
            raise ValueError(f"步骤 {self.id} 须且只能声明 call、show、write 之一（R11.3）")
        if self.call is None and (self.args or self.optional or self.on_error):
            raise ValueError(f"步骤 {self.id}：args、optional、on_error 只用于 call 步骤（R11.3）")
        if self.call and not TOOL_NAME_RE.match(self.call):
            raise ValueError(f"步骤 {self.id} 的工具 {self.call!r} 不是合法的工具全名（R11.3）")
        if self.call and "confirm_token" in self.args:
            raise ValueError(f"步骤 {self.id} 不能传 confirm_token：写操作用 write 步骤（R11.6）")
        return self

    def tools(self) -> list[str]:
        if self.call:
            return [self.call]
        if self.show:
            return list(self.show.ui) + (["canvas__show"] if self.show.canvas != "never" else [])
        assert self.write
        return [self.write.preview, self.write.apply]


class SuggestionCases(_Strict):
    """按某个 write 步骤的状态给不同的追问建议。"""

    by: str
    cases: dict[WriteState, list[str]] = Field(default_factory=dict)
    default: list[str] = Field(default_factory=list)


class IntentSpec(_Strict):
    id: str
    title: str = Field(min_length=1, max_length=40)
    description: str = ""
    icon: str | None = None  # 工作台常用任务的图标名（可选，只影响展示）
    examples: list[str] = Field(min_length=1)
    priority: int = Field(default=50, ge=0, le=1000)  # 越大越优先；同优先级按命中词数，再按声明顺序
    match: MatchSpec
    slots: dict[str, dict[str, Any]] = Field(default_factory=dict)
    requires: list[RequireSpec] = Field(default_factory=list)
    steps: list[StepSpec] = Field(default_factory=list)
    answer: str = ""
    suggestions: list[str] | SuggestionCases = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not INTENT_ID_RE.match(v):
            raise ValueError(f"意图 id {v!r} 须为小写字母开头的 snake_case（R11.1）")
        return v

    @model_validator(mode="after")
    def _rules(self) -> IntentSpec:
        problems: list[str] = []
        for name, spec in self.slots.items():
            problems += check_slot(name, spec)
            src = spec.get("from_call") if isinstance(spec, dict) else None
            if src is not None and not (isinstance(src, str) and _FROM_CALL_RE.match(src)):
                problems.append(f"槽位 {name} 的 from_call 须形如 <工具全名>.<参数名>（R11.5）")
        results = [r.result for r in self.requires if r.result]
        step_ids = [s.id for s in self.steps]
        names = list(self.slots) + results + step_ids
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            problems.append(f"槽位、requires.result、步骤 id 不能重名：{', '.join(dup)}（R11.3）")
        known = set(names) | GLOBAL_NAMES
        writes = {s.id for s in self.steps if s.write}

        def refs(where: str, value: Any, extra: frozenset[str] = frozenset()) -> None:
            for ref in placeholders(value):
                if ref.split(".")[0] not in known | extra:
                    problems.append(f"{where} 引用了未定义的 {{{ref}}}（R11.4）")

        for name in self.match.slots:
            if name not in self.slots:
                problems.append(f"match.slots 中的 {name} 不是已声明的槽位（R11.4）")
            elif "default" in self.slots[name]:
                problems.append(f"match.slots 中的槽位 {name} 不能有 default，否则总会命中（R11.4）")
        for r in self.requires:
            if r.slot and r.slot not in self.slots:
                problems.append(f"requires.slot {r.slot} 不是已声明的槽位（R11.4）")
            refs("requires.message", [r.message, r.suggestions])
        for s in self.steps:
            if s.when:
                ref = s.when.removeprefix("not ").strip()
                if ref.split(".")[0] not in known:
                    problems.append(f"步骤 {s.id} 的 when 引用了未定义的 {ref}（R11.4）")
            refs(f"步骤 {s.id}", s.args)
            refs(f"步骤 {s.id} 的 on_error", s.on_error, frozenset({"error"}))
            if s.show:
                if s.show.result.split(".")[0] not in known and not placeholders(s.show.result):
                    problems.append(f"步骤 {s.id} 的 show.result 引用了未定义的 {s.show.result}（R11.4）")
                refs(f"步骤 {s.id}", [s.show.result, s.show.title])
            if s.write:
                refs(f"步骤 {s.id}", s.write.args)
                refs(f"步骤 {s.id} 的 lines", list(s.write.lines.values()), WRITE_LINE_NAMES)
        refs("answer", self.answer)
        if isinstance(self.suggestions, SuggestionCases):
            if self.suggestions.by not in writes:
                problems.append(f"suggestions.by 须为 write 步骤的 id，{self.suggestions.by} 不是（R11.4）")
            refs("suggestions", [list(self.suggestions.cases.values()), self.suggestions.default])
        else:
            refs("suggestions", self.suggestions)
        if problems:
            raise ValueError("；".join(problems))
        return self


class PlaybookFile(_Strict):
    version: int = 1
    intents: list[IntentSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique(self) -> PlaybookFile:
        ids = [i.id for i in self.intents]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            raise ValueError(f"意图 id 重复：{', '.join(dup)}（R11.1）")
        return self


# ---------------------------------------------------------------- 运行时对象


@dataclass
class Intent:
    spec: IntentSpec
    system: str  # 声明该意图的系统
    unavailable: str = ""  # 引用的系统未接入时的原因；不可用的意图不参与识别

    @property
    def id(self) -> str:
        return self.spec.id

    @property
    def title(self) -> str:
        return self.spec.title

    def tools(self) -> list[str]:
        """剧本引用的全部工具，按首次出现排序。"""
        out: list[str] = [r.tool for r in self.spec.requires if r.tool]
        for s in self.spec.steps:
            out += s.tools()
        return list(dict.fromkeys(out))

    def systems(self) -> list[str]:
        return list(dict.fromkeys(t.partition("__")[0] for t in self.tools()))

    @property
    def standalone(self) -> bool:
        """不依赖已有结果、可以单独发起的意图，工作台把它们列为常用任务。"""
        return not any(r.result for r in self.spec.requires)


@dataclass
class IntentMatch:
    intent: Intent
    score: int
    hits: list[str]
    slots: dict[str, Any] = field(default_factory=dict)

    def brief(self) -> dict[str, Any]:
        return {"id": self.intent.id, "title": self.intent.title, "system": self.intent.system, "score": self.score}


class IntentLibrary:
    """全部已接入系统的意图：识别、帮助文本、常用任务。"""

    def __init__(self, manifests: Iterable[Manifest]):
        manifests = list(manifests)
        self.titles = {m.system: m.title for m in manifests} | dict(BUILTIN_SYSTEMS)
        self.ui_titles = {m.system: m.ui_bridge.title or m.title for m in manifests}
        self.intents: list[Intent] = []
        for m in manifests:
            for spec in m.intents:
                it = Intent(spec, m.system)
                missing = [s for s in it.systems() if s not in self.titles]
                if missing:
                    it.unavailable = f"引用的系统未接入：{', '.join(missing)}"
                self.intents.append(it)

    @classmethod
    def load(cls, settings: Settings) -> IntentLibrary:
        from .manifest import load_manifests

        return cls(load_manifests(settings.manifests_glob))

    # ------------------------------------------------------------------ 查询
    def active(self) -> list[Intent]:
        return [i for i in self.intents if not i.unavailable]

    def get(self, intent_id: str) -> Intent | None:
        return next((i for i in self.intents if i.id == intent_id), None)

    def examples(self) -> dict[str, str]:
        return {i.id: i.spec.examples[0] for i in self.active()}

    def presets(self) -> list[dict[str, Any]]:
        """工作台“常用任务”：可单独发起的意图，按清单加载顺序。"""
        out = []
        for i in self.active():
            if not i.standalone:
                continue
            # 画布只是兜底展示时不算经过的系统
            canvas_always = any(st.show and st.show.canvas == "always" for st in i.spec.steps)
            systems = [s for s in i.systems() if s != "canvas" or canvas_always]
            out.append(
                {
                    "id": i.id,
                    "title": i.title,
                    "description": i.spec.description,
                    "prompt": i.spec.examples[0],
                    "icon": i.spec.icon,
                    "systems": systems,
                }
            )
        return out

    def help_text(self) -> str:
        lines = [
            f"- {i.spec.examples[0]}" if i.spec.examples[0] == i.title else f"- {i.title}：{i.spec.examples[0]}"
            for i in self.active()
        ]
        body = "\n".join(lines) or "- （当前没有接入任何场景剧本）"
        return f"当前是**规则模式**（未配置模型），只识别以下几类指令。接入模型后可以自由提问。\n\n{body}"

    def warnings(self) -> list[str]:
        """不阻止加载、但接入方应该知道的问题：意图不可用、引用了不存在的意图示例（渲染为空）。"""
        out = [f"{i.system} 的意图 {i.id} 不可用：{i.unavailable}" for i in self.intents if i.unavailable]
        ids = {i.id for i in self.active()}
        for i in self.intents:
            for ref in placeholders(i.spec.model_dump(mode="json")):
                parts = ref.split(".")
                if parts[0] == "examples" and len(parts) > 1 and parts[1] not in ids:
                    out.append(f"{i.system} 的意图 {i.id} 引用了不存在或不可用的意图示例 {{{ref}}}，将显示为空")
        return out

    # ------------------------------------------------------------------ 识别
    def classify(
        self, text: str, *, today: date, transcript: Transcript | None = None, limit: int = 3
    ) -> list[IntentMatch]:
        """返回命中的意图，按 (优先级, 命中词数) 从高到低；同分按声明顺序。只做规则匹配，不调用模型。"""
        found = []
        for it in self.active():
            slots = self.resolve_slots(it, text, today, transcript)
            hits = it.spec.match.test(text, slots)
            if hits is not None:
                found.append(IntentMatch(it, score=len(hits), hits=hits, slots=slots))
        found.sort(key=lambda m: (-m.intent.spec.priority, -m.score))
        return found[:limit]

    @staticmethod
    def resolve_slots(intent: Intent, text: str, today: date, transcript: Transcript | None = None) -> dict[str, Any]:
        """取槽位：声明了 from_call 时先沿用本对话里该工具最近一次调用的参数，否则从原话提取，再否则用 default。"""
        out: dict[str, Any] = {}
        for name, spec in intent.spec.slots.items():
            value = None
            src = spec.get("from_call")
            if src and transcript is not None:
                tool, _, arg = src.partition(".")
                value = transcript.latest_args(tool).get(arg)
            out[name] = extract(spec, text, today) if value is None else value
        return out


# ---------------------------------------------------------------- 跨系统校验


def validate_library(manifests: Iterable[Manifest]) -> list[str]:
    """需要看到其他清单才能检查的规则。引用的系统未接入不算错误（该意图只是不可用，check 会提醒）。"""
    from .providers.builtin_provider import BUILTIN_TOOLS

    manifests = list(manifests)
    by_system = {m.system: m for m in manifests}
    problems: list[str] = []
    seen: dict[str, str] = {}

    def tool_problem(where: str, name: str, allow_write: bool = False) -> str | None:
        system, _, local = name.partition("__")
        if system in BUILTIN_SYSTEMS:
            return None if name in BUILTIN_TOOLS else f"{where} 引用了不存在的内置工具 {name}（R11.3）"
        m = by_system.get(system)
        if m is None:
            return None
        api = m.api_tool(local)
        if api is not None:
            if api.risk in ("write", "critical") and not allow_write:
                return f"{where} 直接调用了写操作 {name}，写操作只能用 write 步骤（R11.6）"
            return None
        if m.ui_tool(local) is not None:
            return None
        return f"{where} 引用了 {m.system} 清单中未声明的工具 {name}（R11.3）"

    for m in manifests:
        for spec in m.intents:
            where = f"{m.system} 的意图 {spec.id}"
            if spec.id in seen:
                problems.append(f"意图 id {spec.id} 与 {seen[spec.id]} 重复（R11.1）")
            seen[spec.id] = m.system
            for r in spec.requires:
                if r.tool:
                    problems += [p for p in [tool_problem(where + " 的 requires", r.tool)] if p]
            for s in spec.steps:
                for name in ([s.call] if s.call else []) + (s.show.ui if s.show else []):
                    problems += [p for p in [tool_problem(f"{where} 的步骤 {s.id}", name)] if p]
                if s.write:
                    w = s.write
                    for name in (w.preview, w.apply):
                        problems += [p for p in [tool_problem(f"{where} 的步骤 {s.id}", name, allow_write=True)] if p]
                    owner = by_system.get(w.preview.partition("__")[0])
                    if owner is not None:
                        pv = owner.api_tool(w.preview.partition("__")[2])
                        if pv is None or pv.pairs_with != w.apply.partition("__")[2]:
                            problems.append(f"{where} 的步骤 {s.id}：{w.preview} 没有在清单中声明 pairs_with: {w.apply.partition('__')[2]}（R11.6）")
    return problems
