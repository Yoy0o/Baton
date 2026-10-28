"""Agent 运行时：每个会话一个编排循环。

循环：选工具 → 调模型（流式）→ 执行工具调用（只读 / 只改界面的可并行）→ 结果回填 → 直到模型给出最终答复。
- 写操作的确认由网关回调 _ask_confirm 发起：工作台弹确认卡片，用户决定后循环继续。
- 停止：取消该会话的运行任务；未确认的写操作不会执行；模型历史会补齐后再保存，下一轮可以继续对话。
- 重新生成：撤掉最后一次运行（事件与历史），用同一句话重新运行。
事件发给“当前查看该会话的工作台窗口”（client_id），同时写入会话事件用于重放。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import Settings
from .contracts import ToolError, ToolSpec, error_envelope, for_model
from .conversations import (
    EPHEMERAL_EVENTS,
    Conversation,
    ConversationStore,
    repair_history,
)
from .gateway import Gateway, PendingConfirm
from .llm import LLM, LLMError
from .providers import CallContext
from .results import ResultStore

log = logging.getLogger("agent.runtime")

Send = Callable[[str, dict[str, Any]], Awaitable[None]]
_HISTORY_LIMIT = 60  # 发给模型的历史消息条数上限（按用户消息边界裁剪）
_SAVE_ON = {"run_started", "confirm_request", "confirm_resolved"}


@dataclass
class Session:
    conv: Conversation
    client_id: str = ""
    waiters: dict[str, asyncio.Future] = field(default_factory=dict)
    task: asyncio.Task | None = None
    run_id: str | None = None
    stopping: bool = False

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    @property
    def pending_confirm(self) -> bool:
        return any(not f.done() for f in self.waiters.values())


SYSTEM_PROMPT = """你是“{app_name}”，一个多系统编排助手：通过工具调度多个业务系统完成用户的任务。现在是 {now}。

工作规则：
1. 只依据工具返回的数据作答，不编造数据；数据不够时说明缺什么。
2. 列表数据只通过 result_id 在工具之间传递，不要手抄、改写或编造 result_id。
3. 写操作：先调用 *_preview 拿到变更和 confirm_token，再调用对应的 *_apply 并传入 confirm_token。运行时会向用户展示确认卡片并等待决定，你不要在文字里征求确认，也不要伪造 confirm_token。用户拒绝后不要重复提交。
4. 展示结果：用户已打开对应系统界面时，优先用该系统的界面工具（如 fleet__show_result_layer）；否则用 canvas__show 在工作台展示。
5. 汇总类问题（数量、分布、最大最小）用 results__stats 计算，不要心算。
6. 坐标统一 WGS84（经度, 纬度）。
7. 工具报错时先读 fix_hint，修正后最多重试一次；仍失败就如实告诉用户。
8. 用中文回复，先给结论，简洁；可用 Markdown 列表；引用结果时写出 result_id（用反引号包起来）。

已打开的系统界面：{ui}
界面当前状态：{view_state}
本会话已有结果（最近 10 个）：{results}
{skills}"""


class AgentRuntime:
    def __init__(
        self,
        settings: Settings,
        gateway: Gateway,
        store: ResultStore,
        llm: LLM,
        conversations: ConversationStore,
        send: Send,
    ):
        self.s = settings
        self.gw = gateway
        self.store = store
        self.llm = llm
        self.conversations = conversations
        self.send = send
        self.sessions: dict[str, Session] = {}
        self.closing = False  # 服务正在关闭：被取消的任务记为“中断”而不是“用户停止”

    # ------------------------------------------------------------------ 会话
    def session(self, conv: Conversation) -> Session:
        sess = self.sessions.get(conv.id)
        if sess is None or sess.conv is not conv:
            sess = Session(conv=conv)
            self.sessions[conv.id] = sess
        return sess

    def find(self, conversation_id: str) -> Session | None:
        return self.sessions.get(conversation_id)

    async def shutdown(self, timeout_s: float = 5.0) -> None:
        """服务关闭时调用：取消正在执行的任务，等它们记下“中断”并保存会话。"""
        self.closing = True
        tasks = [s.task for s in self.sessions.values() if s.running]
        for t in tasks:
            t.cancel()  # type: ignore[union-attr]
        if tasks:
            await asyncio.wait(tasks, timeout=timeout_s)  # type: ignore[arg-type]

    def forget(self, conversation_id: str) -> None:
        sess = self.sessions.pop(conversation_id, None)
        if sess and sess.running:
            sess.task.cancel()  # type: ignore[union-attr]

    async def emit(self, sess: Session, event: dict[str, Any]) -> None:
        event = {**event, "conversation_id": sess.conv.id}
        if event.get("type") not in EPHEMERAL_EVENTS:
            sess.conv.append_event(event)
            if event.get("type") in _SAVE_ON:
                self.conversations.save(sess.conv)
        if sess.client_id:
            await self.send(sess.client_id, event)

    # ------------------------------------------------------------------ 控制
    def start(self, sess: Session, text: str, client_id: str) -> bool:
        if sess.running:
            return False
        sess.client_id = client_id
        run_id = "run_" + uuid.uuid4().hex[:8]
        sess.run_id = run_id
        sess.stopping = False
        sess.task = asyncio.create_task(self._run(sess, text, run_id))
        return True

    def stop(self, sess: Session) -> bool:
        if not sess.running or sess.stopping:
            return False
        sess.stopping = True  # 只取消一次，避免打断停止后的收尾（补齐历史、保存）
        sess.task.cancel()  # type: ignore[union-attr]
        return True

    async def regenerate(self, sess: Session, client_id: str) -> bool:
        """撤掉最后一次运行，用同一句话重新运行。写操作仍需重新预览和确认。"""
        if sess.running:
            return False
        sess.client_id = client_id
        conv = sess.conv
        last = next((e for e in reversed(conv.events) if e.get("type") == "run_started"), None)
        if last is None:
            return False
        run_id = last["run_id"]
        conv.events = [e for e in conv.events if e.get("run_id") != run_id]
        users = [i for i, m in enumerate(conv.history) if m.get("role") == "user"]
        if users:
            conv.history = conv.history[: users[-1]]
        conv.feedback.pop(run_id, None)
        await self.emit(sess, {"type": "run_removed", "run_id": run_id})
        return self.start(sess, str(last.get("text", "")), client_id)

    def resolve_confirmation(self, sess: Session, confirm_id: str, approved: bool) -> bool:
        fut = sess.waiters.get(confirm_id)
        if fut and not fut.done():
            fut.set_result(bool(approved))
            return True
        return False

    # ------------------------------------------------------------------ 主循环
    async def _run(self, sess: Session, text: str, run_id: str) -> None:
        conv = sess.conv
        t0 = time.perf_counter()
        steps, status, suggestions = 0, "completed", []
        partial: list[str] = []  # 当前这一步已流式输出的文字（停止时保留）

        async def emit(event: dict[str, Any]) -> None:
            await self.emit(sess, {**event, "run_id": run_id})

        ctx = CallContext(
            user=conv.user,
            session_id=conv.id,
            client_id=sess.client_id,
            run_id=run_id,
            emit=emit,
            confirm=lambda entry: self._ask_confirm(sess, emit, entry),
        )
        try:
            conv.history.append({"role": "user", "content": text})
            await emit({"type": "run_started", "text": text, "planner": self.llm.label, "started_at": time.time()})
            await self.gw.ensure_loaded()
            specs = self.gw.select(ctx.client_id, conv.user, text)
            final_text, final_reasoning = "", ""
            while steps < self.s.max_steps:
                steps += 1
                partial.clear()
                thinking: list[str] = []

                async def on_delta(kind: str, piece: str, thinking: list[str] = thinking) -> None:
                    (thinking if kind == "reasoning" else partial).append(piece)
                    await emit({"type": "reasoning_delta" if kind == "reasoning" else "text_delta", "delta": piece})

                messages = [{"role": "system", "content": self._system_prompt(sess, specs)}] + self._trimmed(conv.history)
                reply = await self.llm.complete(messages, [s.to_openai() for s in specs], on_delta=on_delta)
                reasoning = reply.get("reasoning") or "".join(thinking)
                calls = reply.get("tool_calls") or []
                content = (reply.get("content") or "").strip()
                if not calls:
                    final_text = content or "（模型没有给出答复）"
                    final_reasoning = reasoning
                    suggestions = [s for s in (reply.get("suggestions") or []) if isinstance(s, str)][:3]
                    conv.history.append({"role": "assistant", "content": final_text})
                    break
                conv.history.append({"role": "assistant", "content": content or None, "tool_calls": calls})
                if content or reasoning:
                    await emit({"type": "thought", "text": content, "reasoning": reasoning})
                partial.clear()
                by_name = {s.name: s for s in specs}
                for call, env in await self._execute(ctx, calls, by_name):
                    conv.history.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "content": json.dumps(for_model(env), ensure_ascii=False, default=str),
                        }
                    )
                # 界面工具随页面开关而变化，每一步刷新可用工具
                specs = self.gw.select(ctx.client_id, conv.user, text)
            else:
                final_text = f"已达到单次任务的最大步数（{self.s.max_steps}），请缩小问题范围或分步提问。"
                conv.history.append({"role": "assistant", "content": final_text})
            await emit({"type": "message", "text": final_text, "reasoning": final_reasoning})
        except asyncio.CancelledError:
            status = "interrupted" if self.closing else "stopped"
            text_so_far = "".join(partial).strip()
            if text_so_far:
                await emit({"type": "message", "text": text_so_far, "partial": True})
            note = "（服务重启，本次任务已中断）" if self.closing else "（用户已停止本次任务）"
            repair_history(conv.history, (text_so_far + "\n\n" if text_so_far else "") + note)
        except LLMError as e:
            status = "error"
            log.warning("llm error: %s", e)
            repair_history(conv.history, "（模型调用失败，本次任务中止）")
            await emit({"type": "run_error", "message": str(e)})
        except Exception as e:
            status = "error"
            log.exception("run failed")
            repair_history(conv.history, "（运行出错，本次任务中止）")
            await emit({"type": "run_error", "message": f"运行出错：{type(e).__name__}: {e}"})
        finally:
            conv.updated_at = time.time()
            try:
                await emit(
                    {
                        "type": "run_finished",
                        "status": status,
                        "steps": steps,
                        "duration_ms": round((time.perf_counter() - t0) * 1000),
                        "suggestions": suggestions if status == "completed" else [],
                    }
                )
            finally:
                self.conversations.save(conv)
                if sess.run_id == run_id:
                    sess.run_id = None

    async def _execute(
        self, ctx: CallContext, calls: list[dict[str, Any]], by_name: dict[str, ToolSpec]
    ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        parsed = []
        for call in calls:
            fn = call.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
                if not isinstance(args, dict):
                    raise ValueError("arguments 不是对象")
                parsed.append((call, fn.get("name", ""), args, None))
            except ValueError as e:
                bad = error_envelope("INVALID_ARGS_JSON", f"参数不是合法 JSON：{e}", "重新生成合法的 JSON 参数")
                parsed.append((call, fn.get("name", ""), {}, bad))

        async def one(call: dict[str, Any], name: str, args: dict[str, Any], bad: dict[str, Any] | None):
            spec = by_name.get(name)
            info = self.gw.describe(spec) if spec else {
                "title": name,
                "system": name.partition("__")[0],
                "system_title": self.gw.system_title(name.partition("__")[0]),
                "color": self.gw.system_color(name.partition("__")[0]),
                "kind": None,
                "risk": None,
            }
            assert ctx.emit is not None
            await ctx.emit({"type": "tool_call_started", "call_id": call["id"], "tool": name, "args": args, **info})
            t0 = time.perf_counter()
            try:
                env = bad or await self.gw.call(ctx, name, args)
            except asyncio.CancelledError:
                await ctx.emit(
                    {"type": "tool_call_finished", "call_id": call["id"], "tool": name, "ok": False,
                     "error_code": "CANCELLED", "message": "已停止", "duration_ms": round((time.perf_counter() - t0) * 1000)}
                )
                raise
            await ctx.emit(
                {
                    "type": "tool_call_finished",
                    "call_id": call["id"],
                    "tool": name,
                    "duration_ms": round((time.perf_counter() - t0) * 1000),
                    **{
                        k: env.get(k)
                        for k in (
                            "ok", "summary", "error_code", "message", "fix_hint", "result_id", "result_type",
                            "total", "preview", "fields", "changes", "columns", "confirm_token",
                        )
                        if env.get(k) is not None
                    },
                }
            )
            return call, env

        # 全部是只读或只改界面的调用时并行执行，否则按顺序执行
        safe = all(by_name.get(n) and by_name[n].risk in ("read", "view") for _, n, _, _ in parsed)
        if safe and len(parsed) > 1:
            return list(await asyncio.gather(*(one(*p) for p in parsed)))
        return [await one(*p) for p in parsed]

    async def _ask_confirm(self, sess: Session, emit: Callable[[dict[str, Any]], Awaitable[None]], entry: PendingConfirm) -> bool:
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        sess.waiters[entry.token] = fut
        spec = self.gw.find(entry.apply_tool, sess.client_id, sess.conv.user)
        await emit(
            {
                "type": "confirm_request",
                "confirm_id": entry.token,
                "tool": entry.apply_tool,
                "title": spec.title if spec else entry.apply_tool,
                "system_title": self.gw.system_title(entry.apply_tool.partition("__")[0]),
                "risk": spec.risk if spec else "write",
                "summary": entry.summary,
                "changes": entry.changes,
                "columns": entry.columns,
                "expires_at": entry.expires_at,
            }
        )
        try:
            approved = await asyncio.wait_for(fut, timeout=self.s.confirm_timeout_s)
        except asyncio.TimeoutError:
            await emit({"type": "confirm_resolved", "confirm_id": entry.token, "approved": False, "reason": "timeout"})
            raise ToolError("CONFIRM_TIMEOUT", "用户未在规定时间内确认，变更未提交", "告诉用户变更未提交，可稍后重新发起")
        except asyncio.CancelledError:
            reason = "interrupted" if self.closing else "stopped"
            await emit({"type": "confirm_resolved", "confirm_id": entry.token, "approved": False, "reason": reason})
            raise
        finally:
            sess.waiters.pop(entry.token, None)
        await emit({"type": "confirm_resolved", "confirm_id": entry.token, "approved": approved})
        return approved

    # ------------------------------------------------------------------ 上下文
    @staticmethod
    def _trimmed(hist: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(hist) <= _HISTORY_LIMIT:
            return list(hist)
        starts = [i for i, m in enumerate(hist) if m.get("role") == "user"]
        for i in starts:
            if len(hist) - i <= _HISTORY_LIMIT:
                return hist[i:]
        return hist[starts[-1]:] if starts else hist[-_HISTORY_LIMIT:]

    def _system_prompt(self, sess: Session, specs: list[ToolSpec]) -> str:
        now = datetime.now(timezone(timedelta(hours=8)))
        ui_systems = sorted({s.system for s in specs if s.kind == "ui"})
        ui = "、".join(
            (self.gw.manifests[s].ui_bridge.title or self.gw.manifests[s].title) + f"（{s}）" for s in ui_systems
        ) or "无（界面工具不可用，展示请用 canvas__show）"
        views = self.gw.hub.view_states(sess.client_id, sess.conv.user)
        results = [r.meta() for r in sorted(self.store.list_for_session(sess.conv.id), key=lambda r: r.created_at)[-10:]]
        brief = [{k: r[k] for k in ("result_id", "result_type", "summary")} for r in results]
        used = {s.system for s in specs}
        skills = "\n\n".join(
            f"## 系统知识：{m.title}（{m.system}）\n{_strip_frontmatter(m.skill_text)}"
            for m in self.gw.manifests.values()
            if m.system in used and m.skill_text
        )
        return SYSTEM_PROMPT.format(
            app_name=self.s.app_name,
            now=now.strftime("%Y-%m-%d %H:%M（%A，东八区）"),
            ui=ui,
            view_state=json.dumps(views, ensure_ascii=False) if views else "无",
            results=json.dumps(brief, ensure_ascii=False) if brief else "无",
            skills=skills,
        )


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4 :].strip()
    return text.strip()
