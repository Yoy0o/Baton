"""接入清单（manifest.yaml）：一个系统一份，声明暴露哪些能力、风险等级、参数如何接收结果引用。

字段说明见 docs/INTEGRATION_RULES.md 第 10 节。
"""
from __future__ import annotations

import glob
import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .contracts import LOCAL_TOOL_RE, SYSTEM_RE, TOOL_NAME_RE

_ENV_RE = re.compile(r"\$\{([A-Za-z0-9_]+)(?::-([^}]*))?\}")


def expand_env(text: str) -> str:
    """把 ${VAR:-默认值} 替换为环境变量值。"""
    return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), text)


def full_name(system: str, tool: str) -> str:
    return f"{system}__{tool}"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ResultRef(_Strict):
    """参数可接收上一步结果的 result_id，由网关取出并转换。"""

    as_: Literal["ids", "rows", "geojson", "group_plan"] = Field(alias="as")
    field: str | None = None  # as=ids 时取哪个字段


class ParamOverride(_Strict):
    description: str | None = None
    accepts_result: ResultRef | None = None
    default: Any = None
    fixed: Any = None  # 固定值：不暴露给模型，调用时由网关注入


class ResultSpec(_Strict):
    type: Literal["auto", "table", "feature_collection", "object"] = "auto"
    list_path: str | None = None  # 列表在返回体中的位置，如 items 或 data.list
    summary: str | None = None  # 摘要模板，可用 {total}


class ApiTool(_Strict):
    operation: str  # OpenAPI operationId
    tool: str  # 工具名（不含系统前缀）
    description: str  # 写给模型看：做什么、何时用、何时不用、返回什么
    risk: Literal["read", "write", "critical"] = "read"
    title: str = ""
    pairs_with: str | None = None  # 预览工具 → 成对的 apply 工具名（不含前缀）
    params: dict[str, ParamOverride] = Field(default_factory=dict)
    result: ResultSpec = Field(default_factory=ResultSpec)
    hooks: dict[Literal["request", "response"], str] = Field(default_factory=dict)
    examples: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("tool")
    @classmethod
    def _tool_name(cls, v: str) -> str:
        if not LOCAL_TOOL_RE.match(v):
            raise ValueError(f"工具名 {v!r} 须为小写 snake_case（R1.2）")
        return v

    @field_validator("description")
    @classmethod
    def _desc(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 8:
            raise ValueError("description 必填且需写清用途（R6.1）")
        if len(v) > 400:
            raise ValueError("description 不超过 400 字（R6.1）")
        return v


class ApiSection(_Strict):
    openapi: str  # OpenAPI 文档地址（URL 或相对清单的文件路径）
    base_url: str | None = None  # 接口根地址，默认同系统 base_url
    expose: list[ApiTool]


class UiTool(_Strict):
    tool: str
    risk: Literal["view", "read"] = "view"  # 界面工具不得修改业务数据（R5.2）
    title: str = ""  # 工作台中显示的名称，如“叠加结果图层”


class UiBridgeSection(_Strict):
    enabled: bool = False
    title: str | None = None
    embed_url: str | None = None  # 工作台内嵌该系统页面的地址
    tools: list[UiTool] = Field(default_factory=list)  # 允许页面注册的界面工具白名单


class AuthSection(_Strict):
    mode: Literal["none", "static_bearer", "token_exchange"] = "none"
    token_env: str | None = None
    audience: str | None = None


class Manifest(_Strict):
    manifest_version: int = 1
    system: str
    title: str
    description: str = ""
    owner: str | None = None
    base_url: str
    crs: str = "EPSG:4326"
    color: str | None = None  # 系统标识色（#RRGGBB），工作台中该系统的工具、页签都用它区分
    keywords: list[str] = Field(default_factory=list)  # 路由关键词：工具多时按它选择相关系统
    auth: AuthSection = Field(default_factory=AuthSection)
    api: ApiSection | None = None
    ui_bridge: UiBridgeSection = Field(default_factory=UiBridgeSection)
    skill: str | None = None  # 知识包 SKILL.md 路径（相对清单）

    path: Path | None = Field(default=None, exclude=True)
    skill_text: str = Field(default="", exclude=True)

    @field_validator("system")
    @classmethod
    def _system(cls, v: str) -> str:
        if not SYSTEM_RE.match(v):
            raise ValueError(f"system {v!r} 须为小写字母开头的 2–20 位小写字母或数字（R1.1）")
        return v

    @field_validator("color")
    @classmethod
    def _color(cls, v: str | None) -> str | None:
        if v is not None and not re.match(r"^#[0-9A-Fa-f]{6}$", v):
            raise ValueError(f"color {v!r} 须为 #RRGGBB 格式")
        return v

    @field_validator("crs")
    @classmethod
    def _crs(cls, v: str) -> str:
        if v != "EPSG:4326":
            raise ValueError("v1 工具输入输出统一 EPSG:4326（WGS84），其他坐标系请在系统侧或 hooks 中转换（R7.1）")
        return v

    @model_validator(mode="after")
    def _rules(self) -> Manifest:
        problems: list[str] = []
        names: list[str] = []
        api_tools = {t.tool: t for t in (self.api.expose if self.api else [])}
        names += list(api_tools)
        names += [t.tool for t in self.ui_bridge.tools]
        dup = {n for n in names if names.count(n) > 1}
        if dup:
            problems.append(f"工具名重复：{', '.join(sorted(dup))}（R1.4）")
        for n in names:
            if not TOOL_NAME_RE.match(full_name(self.system, n)):
                problems.append(f"工具全名 {full_name(self.system, n)} 不符合命名规则（R1.2）")

        paired_targets: dict[str, str] = {}
        for t in api_tools.values():
            if t.pairs_with:
                target = api_tools.get(t.pairs_with)
                if target is None:
                    problems.append(f"{t.tool}.pairs_with 指向不存在的工具 {t.pairs_with}（R3.1）")
                elif target.risk not in ("write", "critical"):
                    problems.append(f"{t.pairs_with} 是 apply 工具，risk 须为 write 或 critical（R3.5）")
                if t.risk != "read":
                    problems.append(f"预览工具 {t.tool} 不得修改数据，risk 须为 read（R3.1）")
                paired_targets[t.pairs_with] = t.tool
        for t in api_tools.values():
            if t.risk in ("write", "critical") and t.tool not in paired_targets:
                problems.append(f"写操作 {t.tool} 缺少成对的预览工具（在预览工具上声明 pairs_with: {t.tool}）（R3.1）")
            if t.risk in ("write", "critical") and t.params:
                problems.append(f"写操作 {t.tool} 的参数由预览回放，不要在其 params 中声明覆盖（R3.2）")

        if self.auth.mode == "token_exchange":
            problems.append("v1 暂未实现 token_exchange，请用 none 或 static_bearer（R8.1）")
        if self.auth.mode == "static_bearer" and not self.auth.token_env:
            problems.append("auth.mode=static_bearer 时须指定 token_env（R8.1）")
        if self.ui_bridge.enabled and not self.ui_bridge.tools:
            problems.append("ui_bridge.enabled=true 时须在 ui_bridge.tools 中声明界面工具白名单（R5.2）")
        if problems:
            raise ValueError("；".join(problems))
        return self

    # -------------------------------------------------------------- 便捷方法
    def api_tool(self, local: str) -> ApiTool | None:
        if not self.api:
            return None
        return next((t for t in self.api.expose if t.tool == local), None)

    def ui_tool(self, local: str) -> UiTool | None:
        return next((t for t in self.ui_bridge.tools if t.tool == local), None)

    @property
    def api_base_url(self) -> str:
        return (self.api.base_url if self.api and self.api.base_url else self.base_url).rstrip("/")


class ManifestError(Exception):
    pass


def load_manifest(path: str | Path) -> Manifest:
    path = Path(path)
    raw = yaml.safe_load(expand_env(path.read_text(encoding="utf-8"))) or {}
    try:
        m = Manifest.model_validate(raw)
    except ValidationError as e:
        msgs = []
        for err in e.errors():
            loc = ".".join(str(x) for x in err["loc"])
            msgs.append(f"{loc}: {err['msg']}" if loc else err["msg"])
        raise ManifestError(f"{path}: " + "；".join(msgs)) from None
    m.path = path
    if m.skill:
        skill_path = (path.parent / m.skill).resolve()
        if skill_path.exists():
            m.skill_text = skill_path.read_text(encoding="utf-8")
    return m


def load_manifests(pattern: str) -> list[Manifest]:
    paths = sorted(glob.glob(pattern))
    manifests, errors = [], []
    for p in paths:
        try:
            manifests.append(load_manifest(p))
        except ManifestError as e:
            errors.append(str(e))
    systems = [m.system for m in manifests]
    dup = {s for s in systems if systems.count(s) > 1}
    if dup:
        errors.append(f"system 重复：{', '.join(sorted(dup))}")
    if errors:
        raise ManifestError("\n".join(errors))
    return manifests
