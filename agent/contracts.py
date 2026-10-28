"""工具契约：所有能力来源（API / 界面 / 内置）统一成同一种工具描述和返回信封。

字段与 MCP 工具对齐（name / description / inputSchema / annotations / structuredContent），
因此同一份工具目录既能给 OpenAI 兼容的 function calling 用，也能经 /mcp 端点给 MCP 客户端用。
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

Risk = Literal["read", "view", "write", "critical"]
Kind = Literal["api", "ui", "builtin"]

# 工具全名：<system>__<tool>，只用小写字母、数字、下划线，兼容主流模型接口对函数名的限制
TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9]{1,19}__[a-z][a-z0-9_]{1,40}$")
SYSTEM_RE = re.compile(r"^[a-z][a-z0-9]{1,19}$")
LOCAL_TOOL_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")

PREVIEW_ROWS = 5  # 返回给模型的预览行数上限
RISK_LABELS = {"read": "只读", "view": "只改界面", "write": "写入，需确认", "critical": "高风险，需确认"}


class ToolSpec(BaseModel):
    """网关内部的工具描述。"""

    name: str
    system: str
    kind: Kind
    description: str
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    risk: Risk = "read"
    title: str = ""
    # 预览工具：成功后由网关签发 confirm_token，供成对的 apply 工具使用
    pairs_with: str | None = None
    examples: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def needs_confirmation(self) -> bool:
        return self.risk in ("write", "critical")

    def model_description(self) -> str:
        text = self.description.strip()
        if self.examples:
            import json

            samples = "；".join(json.dumps(e, ensure_ascii=False) for e in self.examples[:3])
            text += f"\n调用示例：{samples}"
        return text

    def to_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.model_description(),
                "parameters": self.input_schema,
            },
        }

    def to_mcp(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title or self.name,
            "description": self.model_description(),
            "inputSchema": self.input_schema,
            "annotations": {
                "readOnlyHint": self.risk == "read",
                "destructiveHint": self.risk == "critical",
            },
            "_meta": {"io.baton/risk": self.risk, "io.baton/kind": self.kind},
        }

    def to_catalog(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "system": self.system,
            "kind": self.kind,
            "risk": self.risk,
            "risk_label": RISK_LABELS[self.risk],
            "title": self.title,
            "description": self.description,
            "input_schema": self.input_schema,
            "pairs_with": self.pairs_with,
        }


class ToolError(Exception):
    """可被模型纠正的错误：code + message + fix_hint。"""

    def __init__(self, code: str, message: str, fix_hint: str | None = None, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.fix_hint = fix_hint
        self.retryable = retryable

    def envelope(self) -> dict[str, Any]:
        return error_envelope(self.code, self.message, self.fix_hint, self.retryable)


def ok_envelope(
    summary: str,
    *,
    result_id: str | None = None,
    result_type: str | None = None,
    total: int | None = None,
    preview: list[Any] | None = None,
    data: Any = None,
    next_hints: list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    env: dict[str, Any] = {"ok": True, "summary": summary}
    if result_id:
        env["result_id"] = result_id
    if result_type:
        env["result_type"] = result_type
    if total is not None:
        env["total"] = total
    if preview is not None:
        env["preview"] = preview[:PREVIEW_ROWS]
    if data is not None:
        env["data"] = data
    if next_hints:
        env["next_hints"] = next_hints
    env.update({k: v for k, v in extra.items() if v is not None})
    return env


def error_envelope(code: str, message: str, fix_hint: str | None = None, retryable: bool = False) -> dict[str, Any]:
    env: dict[str, Any] = {"ok": False, "error_code": code, "message": message, "retryable": retryable}
    if fix_hint:
        env["fix_hint"] = fix_hint
    return env


# 只给界面看的字段，不进模型上下文
_UI_ONLY_KEYS = {"changes", "columns"}


def for_model(envelope: dict[str, Any]) -> dict[str, Any]:
    """返回给模型的精简信封：去掉只给界面用的大字段。"""
    return {k: v for k, v in envelope.items() if k not in _UI_ONLY_KEYS}
