"""前端桥接中心：系统页面通过 agent-bridge.js 连上来，把界面上的已有操作注册成工具。

协议（JSON over WebSocket，/ws/bridge?system=&client=&page=）：
  页面 → 中心  {"type":"register","tools":[{name,title?,description,inputSchema}]}
                {"type":"view_state","state":{...}}
                {"type":"result","call_id":"...","result":{ok,summary,data?,error?}}
  中心 → 页面  {"type":"registered","accepted":[...],"rejected":[{tool,reason}]}
                {"type":"call","call_id":"...","tool":"show_result_layer","args":{...}}

client 是工作台窗口 ID：页面被哪个工作台窗口内嵌，就优先为那个窗口的对话服务。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from ..contracts import LOCAL_TOOL_RE, ToolError, ToolSpec, ok_envelope
from ..manifest import Manifest, full_name

log = logging.getLogger("agent.bridge")

VIEW_STATE_LIMIT = 2048  # 界面状态上限（R5.4）
_DEFAULT_SCHEMA = {"type": "object", "properties": {}}


@dataclass
class BridgeConn:
    id: str
    system: str
    client_id: str
    user: str
    page: str
    ws: Any
    tools: dict[str, dict[str, Any]] = field(default_factory=dict)
    view_state: dict[str, Any] = field(default_factory=dict)
    connected_at: float = field(default_factory=time.time)
    pending: dict[str, asyncio.Future] = field(default_factory=dict)


class BridgeHub:
    def __init__(self, manifests: dict[str, Manifest], on_change: Callable[[], Awaitable[None]] | None = None):
        self.manifests = manifests
        self.conns: dict[str, BridgeConn] = {}
        self.on_change = on_change

    async def _changed(self) -> None:
        if self.on_change:
            try:
                await self.on_change()
            except Exception:  # 通知失败不影响桥接
                log.exception("bridge on_change failed")

    # ------------------------------------------------------------------ 连接
    async def serve(self, ws: WebSocket, system: str, client_id: str, user: str, page: str) -> None:
        await ws.accept()
        manifest = self.manifests.get(system)
        if manifest is None or not manifest.ui_bridge.enabled:
            await ws.send_json({"type": "error", "message": f"系统 {system} 未在接入清单中启用前端桥接（R5.2）"})
            await ws.close()
            return
        conn = BridgeConn(id=uuid.uuid4().hex[:8], system=system, client_id=client_id, user=user, page=page, ws=ws)
        self.conns[conn.id] = conn
        log.info("bridge connected: %s client=%s page=%s", system, client_id, page)
        try:
            while True:
                msg = await ws.receive_json()
                kind = msg.get("type")
                if kind == "register":
                    await self._register(conn, manifest, msg.get("tools") or [])
                elif kind == "view_state":
                    state = msg.get("state") or {}
                    if len(json.dumps(state, ensure_ascii=False)) <= VIEW_STATE_LIMIT:
                        conn.view_state = state
                    else:
                        conn.view_state = {"warning": "界面状态超过 2KB，已忽略（R5.4）"}
                elif kind == "result":
                    fut = conn.pending.pop(msg.get("call_id", ""), None)
                    if fut and not fut.done():
                        fut.set_result(msg.get("result") or {})
                elif kind == "ping":
                    await ws.send_json({"type": "pong"})
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            self.conns.pop(conn.id, None)
            for fut in conn.pending.values():
                if not fut.done():
                    fut.set_exception(ToolError("UI_DISCONNECTED", f"{manifest.title} 页面已断开", "请重新打开该系统页面", True))
            log.info("bridge disconnected: %s client=%s", system, client_id)
            await self._changed()

    async def _register(self, conn: BridgeConn, manifest: Manifest, tools: list[dict[str, Any]]) -> None:
        accepted, rejected = [], []
        conn.tools = {}
        for t in tools:
            name = str(t.get("name", ""))
            decl = manifest.ui_tool(name)
            if not LOCAL_TOOL_RE.match(name):
                rejected.append({"tool": name, "reason": "工具名须为小写 snake_case（R1.2）"})
            elif decl is None:
                rejected.append({"tool": name, "reason": "未在接入清单 ui_bridge.tools 白名单中声明（R5.2）"})
            elif not str(t.get("description", "")).strip():
                rejected.append({"tool": name, "reason": "缺少 description（R6.1）"})
            else:
                conn.tools[name] = {
                    "title": decl.title or str(t.get("title") or "").strip()[:40] or name,
                    "description": str(t["description"]).strip()[:400],
                    "inputSchema": t.get("inputSchema") or _DEFAULT_SCHEMA,
                }
                accepted.append(name)
        await conn.ws.send_json({"type": "registered", "accepted": accepted, "rejected": rejected})
        if rejected:
            log.warning("bridge %s rejected tools: %s", manifest.system, rejected)
        await self._changed()

    # ------------------------------------------------------------------ 选择与调用
    def pick(self, system: str, client_id: str, user: str) -> BridgeConn | None:
        """优先选内嵌在当前工作台窗口里的页面；没有则选同一用户最近连接的页面。"""
        candidates = [c for c in self.conns.values() if c.system == system and c.tools]
        same_client = [c for c in candidates if client_id and c.client_id == client_id]
        if same_client:
            return max(same_client, key=lambda c: c.connected_at)
        same_user = [c for c in candidates if c.user == user]
        return max(same_user, key=lambda c: c.connected_at) if same_user else None

    def specs_for(self, client_id: str, user: str) -> list[ToolSpec]:
        specs = []
        for system, manifest in self.manifests.items():
            conn = self.pick(system, client_id, user)
            if not conn:
                continue
            for local, info in conn.tools.items():
                decl = manifest.ui_tool(local)
                specs.append(
                    ToolSpec(
                        name=full_name(system, local),
                        system=system,
                        kind="ui",
                        title=info["title"],
                        description=info["description"],
                        input_schema=info["inputSchema"],
                        risk=decl.risk if decl else "view",
                    )
                )
        return specs

    def view_states(self, client_id: str, user: str) -> dict[str, Any]:
        out = {}
        for system in self.manifests:
            conn = self.pick(system, client_id, user)
            if conn:
                out[system] = conn.view_state
        return out

    def status(self) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {s: [] for s, m in self.manifests.items() if m.ui_bridge.enabled}
        for c in self.conns.values():
            out.setdefault(c.system, []).append(
                {
                    "client_id": c.client_id,
                    "page": c.page,
                    "tools": sorted(c.tools),
                    "titles": {k: v["title"] for k, v in c.tools.items()},
                    "connected_at": c.connected_at,
                }
            )
        return out

    async def call(self, name: str, args: dict[str, Any], client_id: str, user: str, timeout_s: float) -> dict[str, Any]:
        system, _, local = name.partition("__")
        manifest = self.manifests.get(system)
        title = (manifest.ui_bridge.title or manifest.title) if manifest else system
        conn = self.pick(system, client_id, user)
        if conn is None or local not in conn.tools:
            raise ToolError(
                "UI_NOT_CONNECTED",
                f"{title} 界面当前未打开，无法执行 {name}",
                "请用户先在工作台打开该系统页面；或改用 canvas__show 在工作台画布展示",
            )
        call_id = uuid.uuid4().hex[:10]
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        conn.pending[call_id] = fut
        try:
            await conn.ws.send_json({"type": "call", "call_id": call_id, "tool": local, "args": args})
            result = await asyncio.wait_for(fut, timeout=timeout_s)
        except asyncio.TimeoutError:
            raise ToolError("UI_TIMEOUT", f"{title} 界面 {timeout_s:.0f} 秒内未完成 {local}", "稍后重试；耗时操作应改为后台任务（R5.3）", True) from None
        finally:
            conn.pending.pop(call_id, None)
        if not isinstance(result, dict):
            result = {"ok": True, "summary": str(result)}
        if result.get("ok") is False:
            err = result.get("error") or {}
            raise ToolError(
                str(err.get("code") or "UI_ERROR"),
                str(err.get("message") or result.get("summary") or "界面操作失败"),
                err.get("fix_hint"),
            )
        return ok_envelope(str(result.get("summary") or "界面操作已完成"), data=result.get("data"))
