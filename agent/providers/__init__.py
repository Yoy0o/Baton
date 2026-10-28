"""三类能力来源：API（OpenAPI 生成）、UI（前端桥接注册）、内置能力。"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CallContext:
    """一次工具调用的上下文：谁在调、属于哪个会话，以及与工作台交互的回调。

    session_id 是会话（一段对话）ID：结果归属、confirm_token 绑定都以它为准。
    client_id 是工作台窗口 ID：界面工具优先路由到该窗口内嵌的系统页面。
    """

    user: str
    session_id: str
    client_id: str = ""
    run_id: str = ""
    request_id: str = ""
    initiator: str = "agent"  # agent：模型发起；user：用户在工作台直接操作
    # 向工作台推送事件（如画布渲染）；无工作台时为 None
    emit: Callable[[dict[str, Any]], Awaitable[None]] | None = None
    # 请求用户确认写操作，返回是否批准；无工作台时为 None（写操作将被拒绝）
    confirm: Callable[[Any], Awaitable[bool]] | None = None


@dataclass
class CallOutcome:
    envelope: dict[str, Any]
    raw: Any = None  # 系统原始返回（预览工具用它生成确认卡片）
    request_args: dict[str, Any] = field(default_factory=dict)  # 结果引用解析后的参数（apply 回放用）
