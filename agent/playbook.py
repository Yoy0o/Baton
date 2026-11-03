"""场景剧本执行器：规则模式下按剧本一步步产出工具调用，最后按模板给出答复。

它和真实模型一样只看 messages + tools：每次被调用时从消息历史重新推导“本轮做到哪一步了”
（见 transcript.py），产出下一条 assistant 消息——要么是工具调用，要么是最终答复。
所以它能直接替代模型，也能藏在一个假的 OpenAI 兼容接口后面驱动测试（ADR-0009）。

另外提供 describe()：把识别结果和剧本步骤写成一段文字，模型模式放进系统提示词作参考。
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import date
from typing import Any

from .intents import (
    PATH_RE,
    PLACEHOLDER_RE,
    IntentLibrary,
    IntentMatch,
    ShowSpec,
    StepSpec,
    SuggestionCases,
    WriteSpec,
)
from .transcript import REJECTED_CODES, Call, Transcript

CANVAS_TOOL = "canvas__show"
CANVAS_PLACE = "工作台结果面板"
# write 步骤各状态的默认说明；{target} 是被写入系统的标题
DEFAULT_LINES = {
    "skipped": "{target}：未写回。确认方案后可以说“同步到{target}”。",
    "preview_failed": "{target}：预览失败，{error}",
    "not_applied": "{target}：未提交。",
    "applied": "{target}：已提交。{summary}",
    "rejected": "{target}：你没有确认这次提交，{target}未做任何修改。",
    "failed": "{target}：提交失败，{error}",
}

_MISSING = object()


# ---------------------------------------------------------------- 模板


def lookup(facts: dict[str, Any], path: str) -> Any:
    cur: Any = facts
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return _MISSING
    return cur


def _resolve(body: str, facts: dict[str, Any]) -> Any:
    """一个占位符：备选项用 | 分隔，取第一个有值的；不是名字的备选项按字面量（数字转成数字）。"""
    for alt in (a.strip() for a in body.split("|")):
        if PATH_RE.match(alt):
            value = lookup(facts, alt)
            if value is not _MISSING and value is not None and value != "":
                return value
            continue
        try:
            return json.loads(alt)
        except ValueError:
            return alt
    return None


def render(template: str, facts: dict[str, Any]) -> str:
    def one(m: re.Match[str]) -> str:
        value = _resolve(m.group(1), facts)
        if value is None:
            return ""
        if isinstance(value, bool):
            return "是" if value else "否"
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    return PLACEHOLDER_RE.sub(one, template)


def render_value(value: Any, facts: dict[str, Any]) -> Any:
    """渲染工具参数：整段就是一个占位符时保留原类型（数字、列表、None），否则按文本渲染。"""
    if isinstance(value, str):
        m = PLACEHOLDER_RE.fullmatch(value.strip())
        return _resolve(m.group(1), facts) if m else render(value, facts)
    if isinstance(value, dict):
        return render_args(value, facts)
    if isinstance(value, list):
        return [render_value(v, facts) for v in value]
    return value


def render_args(args: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any]:
    """值为空（None）的参数不传，交给工具自己的默认值。"""
    out = {}
    for k, v in args.items():
        value = render_value(v, facts)
        if value is not None:
            out[k] = value
    return out


def truthy(expr: str, facts: dict[str, Any]) -> bool:
    negate = expr.startswith("not ")
    value = lookup(facts, expr.removeprefix("not ").strip())
    ok = value is not _MISSING and value not in (None, "", [], {}, False, 0)
    return not ok if negate else ok


# ---------------------------------------------------------------- 消息


def tool_calls(*pairs: tuple[str, dict[str, Any]], note: str = "") -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": note,
        "tool_calls": [
            {
                "id": "call_" + uuid.uuid4().hex[:10],
                "type": "function",
                "function": {"name": n, "arguments": json.dumps(a, ensure_ascii=False)},
            }
            for n, a in pairs
        ],
    }


def final(text: str, suggestions: list[str] | None = None) -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "assistant", "content": text}
    if suggestions:
        msg["suggestions"] = [s for s in suggestions if s][:3]
    return msg


# ---------------------------------------------------------------- 执行


class _Done(Exception):
    """剧本在这一步产出了消息（工具调用或答复）。"""

    def __init__(self, message: dict[str, Any]):
        self.message = message


class PlaybookRunner:
    """在一次 complete() 调用里推进一个意图的剧本。"""

    def __init__(self, library: IntentLibrary, match: IntentMatch, transcript: Transcript, tools: set[str], today: date):
        self.lib = library
        self.match = match
        self.spec = match.intent.spec
        self.t = transcript
        self.tools = tools
        self.facts: dict[str, Any] = {
            **match.slots,
            "examples": library.examples(),
            "help": library.help_text(),
            "today": today.isoformat(),
            "intent": {"id": self.spec.id, "title": self.spec.title},
        }

    def next_message(self) -> dict[str, Any]:
        try:
            self._requirements()
            for step in self.spec.steps:
                self._step(step)
            return final(render(self.spec.answer, self.facts) or "已完成。", self._suggestions())
        except _Done as done:
            return done.message

    # ------------------------------------------------------------------ 前置条件
    def _requirements(self) -> None:
        for r in self.spec.requires:
            if r.tool and r.tool not in self.tools:
                ui = self.lib.ui_titles.get(r.tool.partition("__")[0], r.tool)
                default = f"{ui}未打开，无法完成“{self.spec.title}”。请先在右侧工作区打开“{ui}”。"
                raise _Done(final(render(r.message or default, self.facts), self._render_all(r.suggestions)))
            if r.slot and truthy(f"not {r.slot}", self.facts):
                default = f"缺少参数 {r.slot}，请补充后再试，例如：{{examples.{self.spec.id}}}"
                raise _Done(final(render(r.message or default, self.facts), self._render_all(r.suggestions)))
            if r.result:
                found = self.t.latest_result(str(r.type), r.scope)
                if found is None:
                    default = f"还没有可用的 {r.type} 结果，请先完成前面的步骤。\n\n{{help}}"
                    raise _Done(final(render(r.message or default, self.facts), self._render_all(r.suggestions)))
                self.facts[r.result] = found.facts()

    # ------------------------------------------------------------------ 步骤
    def _step(self, step: StepSpec) -> None:
        if step.when and not truthy(step.when, self.facts):
            if step.write:
                self.facts[step.id] = self._write_facts(step.write, "skipped", None)
            elif step.show:
                self.facts[step.id] = {"where": "", "skipped": True}
            return
        if step.call:
            self._call(step)
        elif step.show:
            self._show(step.id, step.show)
        elif step.write:
            self._write(step.id, step.write)

    def _call(self, step: StepSpec) -> None:
        assert step.call
        c = self.t.take(step.call)
        if c is None:
            raise _Done(tool_calls((step.call, render_args(step.args, self.facts))))
        self.facts[step.id] = c.facts()
        if not c.ok and not step.optional:
            template = step.on_error or f"第 {self.spec.steps.index(step) + 1} 步（{step.call}）失败：{{error}}"
            raise _Done(final(render(template, {**self.facts, "error": c.why()})))

    def _show(self, step_id: str, show: ShowSpec) -> None:
        candidates = list(show.ui) + ([CANVAS_TOOL] if show.canvas != "never" else [])
        taken = [c for c in (self.t.take(n) for n in candidates) if c is not None]
        if not taken:
            result_id = render_value(show.result if "{" in show.result else "{" + show.result + ".result_id}", self.facts)
            if not result_id:
                self.facts[step_id] = {"where": "（没有可展示的结果）"}
                return
            pairs: list[tuple[str, dict[str, Any]]] = []
            ui = next((n for n in show.ui if n in self.tools), None)
            if ui:
                pairs.append((ui, render_args({"result_id": result_id, "color_by": show.color_by}, self.facts)))
            if show.canvas == "always" or (show.canvas == "fallback" and not ui):
                title = render(show.title, self.facts) or None
                pairs.append((CANVAS_TOOL, render_args({"result_id": result_id, "color_by": show.color_by, "title": title}, self.facts)))
            if not pairs:
                self.facts[step_id] = {"where": "（界面未打开，未展示）"}
                return
            raise _Done(tool_calls(*pairs))
        places = [self._place(c) for c in taken if c.ok]
        self.facts[step_id] = {"where": "和".join(places) or "（展示失败）", "calls": [c.facts() for c in taken]}

    def _place(self, c: Call) -> str:
        return CANVAS_PLACE if c.name == CANVAS_TOOL else self.lib.ui_titles.get(c.system, c.system)

    def _write(self, step_id: str, w: WriteSpec) -> None:
        p = self.t.take(w.preview)
        if p is None:
            raise _Done(tool_calls((w.preview, render_args(w.args, self.facts))))
        if not p.ok:
            self.facts[step_id] = self._write_facts(w, "preview_failed", p)
            return
        a = self.t.take(w.apply)
        if a is None:
            token = p.env.get("confirm_token")
            if not token:
                self.facts[step_id] = self._write_facts(w, "not_applied", p)
                return
            raise _Done(tool_calls((w.apply, {"confirm_token": token})))
        if a.ok:
            state = "applied"
        else:
            state = "rejected" if a.env.get("error_code") in REJECTED_CODES else "failed"
        self.facts[step_id] = self._write_facts(w, state, a)

    def _write_facts(self, w: WriteSpec, state: str, c: Call | None) -> dict[str, Any]:
        target = w.target or self.lib.titles.get(w.apply.partition("__")[0], w.apply)
        local = {
            "target": target,
            "state": state,
            "summary": (c.env.get("summary") or "") if c else "",
            "error": c.why() if c and not c.ok else "",
        }
        template = w.lines.get(state) or DEFAULT_LINES[state]  # type: ignore[call-overload]
        return {**local, "line": render(template, {**self.facts, **local}), "result": c.facts() if c else {}}

    # ------------------------------------------------------------------ 追问建议
    def _render_all(self, items: list[str]) -> list[str]:
        return [render(s, self.facts) for s in items]

    def _suggestions(self) -> list[str]:
        sg = self.spec.suggestions
        if isinstance(sg, SuggestionCases):
            state = str((self.facts.get(sg.by) or {}).get("state", ""))
            items = sg.cases.get(state) or sg.default  # type: ignore[call-overload]
        else:
            items = sg
        return self._render_all(items)


# ---------------------------------------------------------------- 给模型的提示


def describe(library: IntentLibrary, matches: list[IntentMatch], tools: set[str]) -> str:
    """把识别结果写成系统提示词的一节：意图、已取出的参数、建议步骤、未满足的前置条件。"""
    if not matches:
        return ""
    top = matches[0]
    spec = top.intent.spec
    slots = {k: v for k, v in top.slots.items() if v not in (None, "", [], False)}
    lines = [
        "## 本轮任务识别（按场景剧本规则匹配，仅供参考）",
        f"用户的请求很可能是“{spec.title}”" + (f"：{spec.description}" if spec.description else "") + "。",
    ]
    if slots:
        lines.append("已从原话取出的参数：" + "，".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in slots.items()))
    notes = []
    for r in spec.requires:
        if r.tool and r.tool not in tools:
            notes.append(f"{r.tool} 当前不可用（{library.ui_titles.get(r.tool.partition('__')[0], '对应界面')}未打开）")
        if r.slot and not slots.get(r.slot):
            notes.append(f"用户没有给出 {r.slot}，需要先问清楚")
    if notes:
        lines.append("注意：" + "；".join(notes) + "。")
    if spec.steps:
        lines.append("建议步骤：")
        for i, step in enumerate(spec.steps, 1):
            lines.append(f"{i}. {_describe_step(step, top.slots)}")
    others = [m.intent.title for m in matches[1:]]
    if others:
        lines.append(f"其他可能：{'、'.join(others)}。")
    lines.append("如果用户的意思与此不同，以用户原话为准；写操作仍须先预览、由运行时请用户确认后提交。")
    return "\n".join(lines)


def _describe_step(step: StepSpec, slots: dict[str, Any]) -> str:
    cond = ""
    if step.when:
        neg = step.when.startswith("not ")
        cond = f"（仅当 {step.when.removeprefix('not ').strip()} 为{'假' if neg else '真'}时）"
    if step.call:
        args = ", ".join(f"{k}={_describe_arg(v, slots)}" for k, v in step.args.items())
        tail = "；失败不影响后续" if step.optional else ""
        return f"{step.call}({args}) → 记为 {step.id}{tail}{cond}"
    if step.show:
        where = " 或 ".join(step.show.ui) if step.show.ui else ""
        canvas = {"always": "，同时用 canvas__show", "fallback": "；界面未打开时用 canvas__show", "never": ""}[step.show.canvas]
        color = f"，color_by={step.show.color_by}" if step.show.color_by else ""
        return f"展示 {step.show.result}：{where or 'canvas__show'}{color}{canvas if where else ''}{cond}"
    assert step.write
    args = ", ".join(f"{k}={_describe_arg(v, slots)}" for k, v in step.write.args.items())
    return f"写回：{step.write.preview}({args}) 预览 → 用户确认 → {step.write.apply}(confirm_token){cond}"


def _describe_arg(value: Any, slots: dict[str, Any]) -> str:
    if isinstance(value, str):
        m = PLACEHOLDER_RE.fullmatch(value.strip())
        if m:
            first = m.group(1).split("|")[0].strip()
            root = first.split(".")[0]
            if root in slots:
                v = lookup(slots, first)
                return json.dumps(v, ensure_ascii=False) if v is not _MISSING and v is not None else f"<{root}>"
            return f"<{first}>"
        return json.dumps(value, ensure_ascii=False) if "{" not in value else value
    return json.dumps(value, ensure_ascii=False)
