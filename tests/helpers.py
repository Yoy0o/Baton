"""测试辅助：模拟内嵌的车队地图页面，以及不经过 WebSocket 直接驱动运行时。"""
from __future__ import annotations

import asyncio
from typing import Any

from agent.providers.bridge_provider import BridgeConn
from agent.server import Platform

CLIENT = "wb_test01"
REGROUP = "把华东区明天早班的司机按当前位置重新分成 5 组，每组不超过 8 人，先给我看地图和名单，确认后同步到调度系统"


class FakePage:
    """模拟内嵌在工作台里的车队地图页面：收到界面工具调用就立即返回结果。"""

    TOOLS = {
        "show_result_layer": {"type": "object", "properties": {"result_id": {"type": "string"}, "color_by": {"type": "string"}}, "required": ["result_id"]},
        "clear_result_layers": {"type": "object", "properties": {}},
        "focus_driver": {"type": "object", "properties": {"driver_id": {"type": "string"}}, "required": ["driver_id"]},
        "play_track": {"type": "object", "properties": {"driver_id": {"type": "string"}, "date": {"type": "string"}}, "required": ["driver_id", "date"]},
    }

    def __init__(self, platform: Platform, client_id: str = CLIENT):
        self.calls: list[tuple[str, dict]] = []
        self.conn = BridgeConn(
            id="page1", system="fleet", client_id=client_id, user="demo", page="/map", ws=self,
            tools={k: {"title": k, "description": f"地图界面工具 {k}", "inputSchema": v} for k, v in self.TOOLS.items()},
        )
        platform.hub.conns[self.conn.id] = self.conn

    async def send_json(self, msg: dict) -> None:
        if msg.get("type") != "call":
            return
        self.calls.append((msg["tool"], msg["args"]))
        fut = self.conn.pending[msg["call_id"]]
        asyncio.get_running_loop().call_soon(fut.set_result, {"ok": True, "summary": f"界面已执行 {msg['tool']}"})


class Driver:
    """直接驱动运行时并收集事件；confirm 为 True/False 时自动处理确认卡片，为 None 时不处理。"""

    def __init__(self, platform: Platform, client_id: str = CLIENT):
        self.p = platform
        self.client_id = client_id
        self.events: list[dict[str, Any]] = []
        self.confirm: bool | None = True
        self.on_event = None
        platform.runtime.send = self._send
        self.conv = None

    async def _send(self, client_id: str, event: dict[str, Any]) -> None:
        assert client_id == self.client_id
        self.events.append(event)
        if event["type"] == "confirm_request" and self.confirm is not None:
            sess = self.p.runtime.find(event["conversation_id"])
            self.p.runtime.resolve_confirmation(sess, event["confirm_id"], self.confirm)
        if self.on_event:
            await self.on_event(event)

    async def say(self, text: str, confirm: bool | None = True) -> list[dict[str, Any]]:
        self.confirm = confirm
        await self.p.gateway.ensure_loaded(force=True)
        if self.conv is None:
            self.conv = self.p.conversations.create("demo", text[:20])
        sess = self.p.runtime.session(self.conv)
        start = len(self.events)
        assert self.p.runtime.start(sess, text, self.client_id)
        await sess.task
        return self.events[start:]


def tools_called(events: list[dict[str, Any]]) -> list[str]:
    return [e["tool"] for e in events if e["type"] == "tool_call_started"]


def final_text(events: list[dict[str, Any]]) -> str:
    return next(e["text"] for e in events if e["type"] == "message")


def finished(events: list[dict[str, Any]]) -> dict[str, Any]:
    return next(e for e in events if e["type"] == "run_finished")


async def dispatch_version(p: Platform) -> int:
    return (await p.http.get("http://localhost:8103/api/groups?region=east-china")).json()["version"]
