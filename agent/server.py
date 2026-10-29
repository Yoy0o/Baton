"""服务入口：工作台页面、工作台 WebSocket、前端桥接 WebSocket、会话 / 结果 / 审计 API、MCP 调试端点。

启动：python run.py（同时启动模拟系统），或 uvicorn agent.main:app --port 8000
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from fastapi import (
    FastAPI,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .audit import AuditLog
from .config import ROOT, Settings
from .contracts import ToolError, for_model
from .conversations import ConversationStore, make_title
from .gateway import Gateway
from .llm import make_llm
from .manifest import load_manifests
from .providers import CallContext
from .providers.bridge_provider import BridgeHub
from .results import ResultStore, geojson_of, rows_of
from .runtime import AgentRuntime

log = logging.getLogger("agent.server")
_CLIENT_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
_TEXT_LIMIT = 4000


class Platform:
    """把各组件装配在一起；测试时可注入 http transport 直连模拟系统。"""

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.manifests = load_manifests(settings.manifests_glob)
        self.http = httpx.AsyncClient(transport=transport, timeout=settings.http_timeout_s)
        self.store = ResultStore(settings.data_dir, settings.result_ttl_s)
        self.audit = AuditLog(settings.data_dir)
        self.conversations = ConversationStore(settings.data_dir)
        self.clients: dict[str, WebSocket] = {}
        self._send_locks: dict[str, asyncio.Lock] = {}
        self.hub = BridgeHub({m.system: m for m in self.manifests}, on_change=self.broadcast_bridge_status)
        self.gateway = Gateway(settings, self.manifests, self.store, self.hub, self.audit, self.http)
        self.llm = make_llm(settings, self.http)
        self.runtime = AgentRuntime(settings, self.gateway, self.store, self.llm, self.conversations, self.send)

    async def send(self, client_id: str, event: dict[str, Any]) -> None:
        """把事件发给某个工作台窗口；窗口不在线时丢弃（事件已写入会话，重新打开时重放）。"""
        ws = self.clients.get(client_id)
        if ws is None:
            return
        lock = self._send_locks.setdefault(client_id, asyncio.Lock())
        try:
            async with lock:
                await ws.send_json(event)
        except Exception:
            pass

    async def broadcast_bridge_status(self) -> None:
        msg = {"type": "bridge_status", "bridges": self.hub.status()}
        for cid in list(self.clients):
            await self.send(cid, msg)

    def allowed_origins(self) -> list[str]:
        origins = {self.settings.public_url.rstrip("/")}
        for m in self.manifests:
            for url in (m.base_url, m.ui_bridge.embed_url or ""):
                parts = urlsplit(url)
                if parts.scheme and parts.netloc:
                    origins.add(f"{parts.scheme}://{parts.netloc}")
                    if parts.hostname in ("localhost", "127.0.0.1"):
                        alt = "127.0.0.1" if parts.hostname == "localhost" else "localhost"
                        origins.add(f"{parts.scheme}://{alt}:{parts.port}" if parts.port else f"{parts.scheme}://{alt}")
        return sorted(origins)

    def conversation_meta(self, conv) -> dict[str, Any]:
        sess = self.runtime.find(conv.id)
        return {
            **conv.meta(),
            "running": bool(sess and sess.running),
            "pending_confirm": bool(sess and sess.pending_confirm),
        }


class RenameBody(BaseModel):
    title: str = Field(..., min_length=1, max_length=80)


class FeedbackBody(BaseModel):
    conversation_id: str
    run_id: str
    value: Literal["like", "dislike", "default"]


class UiActionBody(BaseModel):
    client_id: str
    conversation_id: str | None = None
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


def create_app(settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    settings = settings or Settings()
    platform = Platform(settings, transport)
    user = settings.default_user  # 未接 SSO：所有请求归属同一用户；接入后从 SSO 令牌解析

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        asyncio.create_task(platform.gateway.ensure_loaded(force=True))
        yield
        await platform.runtime.shutdown()  # 正在执行的任务记为“中断”并保存，重启后工作台如实显示
        await platform.http.aclose()

    app = FastAPI(title=f"{settings.app_name} {__version__}", version=__version__, lifespan=lifespan)
    app.state.platform = platform
    app.add_middleware(
        CORSMiddleware,
        allow_origins=platform.allowed_origins(),
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["*"],
    )
    app.mount("/sdk", StaticFiles(directory=ROOT / "sdk"), name="sdk")
    _mount_workbench(app, settings)

    @app.get("/", include_in_schema=False)
    async def index() -> RedirectResponse:
        return RedirectResponse("/workbench/")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> Response:
        return Response(status_code=204)

    # ------------------------------------------------------------------ 查询接口
    @app.get("/api/config")
    async def config() -> dict[str, Any]:
        await platform.gateway.ensure_loaded()
        return {
            "app": {"name": settings.app_name, "tagline": settings.app_tagline, "version": __version__},
            "version": __version__,
            "mode": "llm" if settings.llm_enabled else "rule",
            "planner": platform.llm.label,
            "agent_url": settings.public_url.rstrip("/"),
            "systems": [
                {
                    "system": m.system,
                    "title": m.title,
                    "description": m.description,
                    "color": platform.gateway.system_color(m.system),
                    "ui_title": m.ui_bridge.title or m.title,
                    "embed_url": m.ui_bridge.embed_url if m.ui_bridge.enabled else None,
                    "api_tools": len(m.api.expose) if m.api else 0,
                    "ui_tools": [{"tool": t.tool, "title": t.title or t.tool} for t in m.ui_bridge.tools],
                }
                for m in platform.manifests
            ],
            "systems_meta": platform.gateway.systems_meta(),
            "load_errors": platform.gateway.load_errors(),
            "bridges": platform.hub.status(),
        }

    @app.get("/api/tools")
    async def tools(client: str = Query("", max_length=64), session: str = Query("", max_length=64)) -> dict[str, Any]:
        await platform.gateway.ensure_loaded()
        specs = platform.gateway.catalog(client or session, user)
        return {
            "tools": [{**s.to_catalog(), **platform.gateway.describe(s)} for s in specs],
            "load_errors": platform.gateway.load_errors(),
        }

    @app.get("/api/results/{result_id}")
    async def get_result(result_id: str, format: str = Query("full", pattern="^(full|geojson|rows)$")) -> JSONResponse:
        try:
            res = platform.store.get(result_id, owner=user)
            if format == "geojson":
                return JSONResponse(geojson_of(res))
            if format == "rows":
                return JSONResponse({"rows": rows_of(res)})
        except ToolError as e:
            raise HTTPException(status_code=404 if e.code == "RESULT_NOT_FOUND" else 400, detail=e.message) from e
        return JSONResponse({**res.meta(), "data": res.data, "rows": rows_of(res)[:2000]})

    @app.get("/api/audit")
    async def audit(limit: int = Query(100, ge=1, le=500), conversation_id: str = Query("", max_length=64)) -> dict[str, Any]:
        entries = platform.audit.tail(500)
        if conversation_id:
            entries = [e for e in entries if e.get("conversation_id") == conversation_id]
        return {"entries": entries[:limit]}

    # ------------------------------------------------------------------ 会话
    @app.get("/api/conversations")
    async def list_conversations() -> dict[str, Any]:
        return {"items": [platform.conversation_meta(c) for c in platform.conversations.list(user)]}

    @app.get("/api/conversations/{cid}")
    async def get_conversation(cid: str) -> dict[str, Any]:
        conv = platform.conversations.get(cid, user)
        if conv is None:
            raise HTTPException(404, "会话不存在或已删除")
        return {**platform.conversation_meta(conv), "events": conv.events, "feedback": conv.feedback}

    @app.patch("/api/conversations/{cid}")
    async def rename_conversation(cid: str, body: RenameBody) -> dict[str, Any]:
        conv = platform.conversations.rename(cid, user, body.title)
        if conv is None:
            raise HTTPException(404, "会话不存在或标题为空")
        return platform.conversation_meta(conv)

    @app.delete("/api/conversations/{cid}")
    async def delete_conversation(cid: str) -> dict[str, Any]:
        if platform.conversations.get(cid, user) is None:
            raise HTTPException(404, "会话不存在或已删除")
        platform.runtime.forget(cid)  # 正在运行的任务会被停止
        platform.conversations.delete(cid, user)
        return {"ok": True}

    @app.post("/api/feedback")
    async def feedback(body: FeedbackBody) -> dict[str, Any]:
        conv = platform.conversations.get(body.conversation_id, user)
        if conv is None:
            raise HTTPException(404, "会话不存在或已删除")
        if body.value == "default":
            conv.feedback.pop(body.run_id, None)
        else:
            conv.feedback[body.run_id] = body.value
        platform.conversations.save(conv)
        line = {"ts": round(time.time(), 3), "user": user, **body.model_dump()}
        try:
            with (Path(settings.data_dir) / "feedback.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        except OSError:
            pass
        return {"ok": True}

    @app.post("/api/ui-actions")
    async def ui_action(body: UiActionBody) -> dict[str, Any]:
        """用户在工作台直接调用某个系统的界面工具（如把结果显示到车队地图），不经过模型。"""
        await platform.gateway.ensure_loaded()
        spec = platform.gateway.find(body.tool, body.client_id, user)
        if spec is None or spec.kind != "ui":
            raise HTTPException(404, f"界面工具 {body.tool} 不可用：对应系统页面未打开")
        ctx = CallContext(
            user=user,
            session_id=body.conversation_id or "workbench",
            client_id=body.client_id,
            run_id="user_action",
            initiator="user",
        )
        return for_model(await platform.gateway.call(ctx, body.tool, body.args))

    # ------------------------------------------------------------------ 工作台 WebSocket
    @app.websocket("/ws/workbench")
    async def workbench_ws(ws: WebSocket, client: str = "", session: str = "") -> None:
        client_id = client or session
        await ws.accept()
        if not _CLIENT_RE.match(client_id):
            await ws.send_json({"type": "error", "message": "缺少或非法的 client 参数"})
            await ws.close()
            return
        platform.clients[client_id] = ws
        await platform.send(client_id, {"type": "hello", "client_id": client_id, "bridges": platform.hub.status()})
        try:
            while True:
                msg = await ws.receive_json()
                await _handle(client_id, msg)
        except (WebSocketDisconnect, RuntimeError, ValueError):
            pass
        finally:
            # 窗口关闭不影响正在运行的任务；待确认的写操作在超时前仍可在重新打开后确认
            if platform.clients.get(client_id) is ws:
                platform.clients.pop(client_id, None)
                platform._send_locks.pop(client_id, None)

    async def _notify(client_id: str, conversation_id: str | None, message: str) -> None:
        await platform.send(client_id, {"type": "error", "conversation_id": conversation_id, "message": message})

    async def _handle(client_id: str, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        cid = msg.get("conversation_id")
        if kind == "ping":
            await platform.send(client_id, {"type": "pong"})
            return
        if kind == "user_message":
            text = str(msg.get("text") or "").strip()[:_TEXT_LIMIT]
            if not text:
                return
            if cid:
                conv = platform.conversations.get(str(cid), user)
                if conv is None:
                    await _notify(client_id, cid, "会话不存在或已删除")
                    return
            else:
                conv = platform.conversations.create(user, make_title(text))
                await platform.send(
                    client_id, {"type": "conversation_created", "conversation_id": conv.id, **conv.meta(), "running": True}
                )
            sess = platform.runtime.session(conv)
            if not platform.runtime.start(sess, text, client_id):
                await _notify(client_id, conv.id, "该会话正在执行任务，请等它结束或先停止")
            return
        conv = platform.conversations.get(str(cid or ""), user)
        if conv is None:
            await _notify(client_id, cid, "会话不存在或已删除")
            return
        sess = platform.runtime.session(conv)
        if kind == "open":
            sess.client_id = client_id  # 该会话之后的事件发给当前窗口
        elif kind == "stop":
            platform.runtime.stop(sess)
        elif kind == "regenerate":
            if not await platform.runtime.regenerate(sess, client_id):
                await _notify(client_id, conv.id, "当前无法重新生成：任务仍在执行，或还没有可重新生成的回复")
        elif kind == "confirm_decision":
            if not platform.runtime.resolve_confirmation(sess, str(msg.get("confirm_id", "")), bool(msg.get("approved"))):
                await _notify(client_id, conv.id, "这次确认已失效（已处理、已超时或任务已停止）")

    # ------------------------------------------------------------------ 前端桥接
    @app.websocket("/ws/bridge")
    async def bridge_ws(ws: WebSocket, system: str = "", client: str = "", session: str = "", page: str = "") -> None:
        cid = client or session
        await platform.hub.serve(ws, system, cid if _CLIENT_RE.match(cid or "") else "standalone", user, page[:200])

    # ------------------------------------------------------------------ MCP 调试端点（最小子集）
    @app.post("/mcp")
    async def mcp(request: Request) -> Response:
        """把网关的工具目录以 MCP 形式暴露，便于用 MCP 客户端调试。仅实现 initialize / tools/list / tools/call。"""
        try:
            req = await request.json()
        except ValueError:
            return JSONResponse(_rpc_error(None, -32700, "Parse error"))
        if not isinstance(req, dict):
            return JSONResponse(_rpc_error(None, -32600, "批量请求暂不支持"))
        rid, method, params = req.get("id"), req.get("method"), req.get("params") or {}
        if rid is None:  # 通知
            return Response(status_code=202)
        if method == "initialize":
            return JSONResponse(
                _rpc_result(
                    rid,
                    {
                        "protocolVersion": params.get("protocolVersion") or "2025-11-25",
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": f"{settings.app_name.lower()}-gateway", "version": __version__},
                    },
                )
            )
        if method == "ping":
            return JSONResponse(_rpc_result(rid, {}))
        if method == "tools/list":
            await platform.gateway.ensure_loaded()
            specs = platform.gateway.catalog("mcp", user)
            return JSONResponse(_rpc_result(rid, {"tools": [s.to_mcp() for s in specs]}))
        if method == "tools/call":
            ctx = CallContext(user=user, session_id="mcp", client_id="mcp", run_id="mcp", initiator="mcp")
            env = await platform.gateway.call(ctx, str(params.get("name", "")), params.get("arguments") or {})
            body = for_model(env)
            return JSONResponse(
                _rpc_result(
                    rid,
                    {
                        "content": [{"type": "text", "text": json.dumps(body, ensure_ascii=False)}],
                        "structuredContent": body,
                        "isError": not env.get("ok"),
                    },
                )
            )
        return JSONResponse(_rpc_error(rid, -32601, f"Method not found: {method}"))

    return app


def _mount_workbench(app: FastAPI, settings: Settings) -> None:
    web_dir = Path(settings.web_dir)
    if (web_dir / "index.html").exists():
        app.mount("/workbench", StaticFiles(directory=web_dir, html=True), name="workbench")
        return

    @app.get("/workbench/", include_in_schema=False)
    async def workbench_missing() -> HTMLResponse:
        return HTMLResponse(
            "<!doctype html><meta charset='utf-8'><title>工作台未构建</title>"
            "<body style='font:15px/1.6 system-ui;padding:40px;max-width:640px'>"
            f"<h2>{settings.app_name} 工作台前端尚未构建</h2>"
            "<p>在项目目录执行：</p><pre>cd web\nnpm install\nnpm run build</pre>"
            "<p>然后刷新本页。开发时也可以用 <code>npm run dev</code>，按提示打开 Vite 地址。</p></body>",
            status_code=503,
        )


def _rpc_result(rid: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "result": {**result, "resultType": "complete"}}


def _rpc_error(rid: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}
