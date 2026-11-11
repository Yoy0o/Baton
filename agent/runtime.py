"""Agent 运行时：每个会话一个编排循环。

一次运行（run）：
1. 识别意图（intents.py，按场景剧本规则匹配，不调用模型）→ 按意图和上一轮用过的系统选工具（网关路由）；
2. 循环：组装系统提示词（prompt.py，含本轮任务识别）→ 调模型（流式）→ 执行工具调用（executor.py，
   相邻的只读 / 只改界面调用并行，写操作单独执行）→ 结果回填 → 直到模型给出最终答复。
规则模式下“模型”就是按场景剧本执行的 RulePlanner，链路完全相同（ADR-0009、ADR-0011）。
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
from datetime import datetime
from typing import Any

from .config import Settings
from .contracts import ToolError, ToolSpec, for_model
from .conversations import (
    EPHEMERAL_EVENTS,
    Conversation,
    ConversationStore,
    repair_history,
)
from .executor import ToolExecutor
from .gateway import Gateway, PendingConfirm
from .intents import IntentLibrary, IntentMatch
from .llm import LLM, LLMError
from .playbook import describe
from .prompt import PromptBuilder
from .providers import CallContext
from .results import ResultStore
from .transcript import Transcript, estimate_tokens

log = logging.getLogger("agent.runtime")

Send = Callable[[str, dict[str, Any]], Awaitable[None]]
_HISTORY_LIMIT = 60  # 发给模型的历史消息条数上限（按用户消息边界裁剪）；长度另按 AGENT_HISTORY_TOKENS 裁剪
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


@dataclass
class Turn:
    """一次运行的规划上下文：识别出的意图、路由偏好、当前可用的工具。"""

    text: str
    matches: list[IntentMatch]
    prefer: set[str]  # 路由时优先保留的系统：意图引用的系统 + 上一轮用过的系统
    specs: list[ToolSpec] = field(default_factory=list)

    @property
    def by_name(self) -> dict[str, ToolSpec]:
        return {s.name: s for s in self.specs}

    @property
    def intent(self) -> dict[str, Any] | None:
        return self.matches[0].brief() if self.matches else None


class AgentRuntime:
    def __init__(
        self,
        settings: Settings,
        gateway: Gateway,
        store: ResultStore,
        llm: LLM,
        conversations: ConversationStore,
        send: Send,
        intents: IntentLibrary | None = None,
    ):
        self.s = settings
        self.gw = gateway
        self.store = store
        self.llm = llm
        self.conversations = conversations
        self.send = send
        self.intents = intents if intents is not None else IntentLibrary(gateway.manifests.values())
        self.prompts = PromptBuilder(settings, gateway, store)
        self.executor = ToolExecutor(gateway)
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
        usage: dict[str, int] = {}  # 本次运行各轮模型调用的 token 用量之和（接口给了才有）
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
            turn = self._plan_turn(conv, text)
            await emit(
                {"type": "run_started", "text": text, "planner": self.llm.label, "intent": turn.intent, "started_at": time.time()}
            )
            await self.gw.ensure_loaded()
            self._refresh_tools(ctx, turn)
            final_text, final_reasoning = "", ""
            while steps < self.s.max_steps:
                steps += 1
                partial.clear()
                thinking: list[str] = []

                async def on_delta(kind: str, piece: str, thinking: list[str] = thinking) -> None:
                    (thinking if kind == "reasoning" else partial).append(piece)
                    await emit({"type": "reasoning_delta" if kind == "reasoning" else "text_delta", "delta": piece})

                reply = await self.llm.complete(self._messages(sess, turn), [s.to_openai() for s in turn.specs], on_delta=on_delta)
                _add_usage(usage, reply.get("usage"))
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
                for call, env in await self.executor.run(ctx, calls, turn.by_name):
                    conv.history.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "content": json.dumps(for_model(env), ensure_ascii=False, default=str),
                        }
                    )
                self._refresh_tools(ctx, turn)  # 界面工具随页面开关而变化，每一步刷新可用工具
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
                        **({"usage": usage} if usage else {}),
                    }
                )
            finally:
                self.conversations.save(conv)
                if sess.run_id == run_id:
                    sess.run_id = None

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
            raise ToolError("CONFIRM_TIMEOUT", "用户未在规定时间内确认，变更未提交", "告诉用户变更未提交，可稍后重新发起") from None
        except asyncio.CancelledError:
            reason = "interrupted" if self.closing else "stopped"
            await emit({"type": "confirm_resolved", "confirm_id": entry.token, "approved": False, "reason": reason})
            raise
        finally:
            sess.waiters.pop(entry.token, None)
        await emit({"type": "confirm_resolved", "confirm_id": entry.token, "approved": approved})
        return approved

    # ------------------------------------------------------------------ 规划与上下文
    def _plan_turn(self, conv: Conversation, text: str) -> Turn:
        """识别意图并确定路由偏好。只做规则匹配，不调用模型；识别不到时 matches 为空，模型照常自由规划。"""
        transcript = Transcript(conv.history)
        today = datetime.now(self.s.tzinfo).date()
        try:
            matches = self.intents.classify(text, today=today, transcript=transcript)
        except Exception:  # 识别只是参考：出错时让模型自由规划，不中断运行
            log.exception("intent classification failed")
            matches = []
        prefer = set(transcript.recent_systems())
        if matches:
            prefer |= set(matches[0].intent.systems())
        return Turn(text=text, matches=matches, prefer=prefer)

    def _refresh_tools(self, ctx: CallContext, turn: Turn) -> None:
        turn.specs = self.gw.select(ctx.client_id, ctx.user, turn.text, prefer=turn.prefer)

    def _messages(self, sess: Session, turn: Turn) -> list[dict[str, Any]]:
        hint = describe(self.intents, turn.matches, set(turn.by_name))
        system = self.prompts.build(
            session_id=sess.conv.id, client_id=sess.client_id, user=sess.conv.user, specs=turn.specs, intent_hint=hint
        )
        return [{"role": "system", "content": system}] + self._trimmed(sess.conv.history, self.s.history_tokens)

    @staticmethod
    def _trimmed(hist: list[dict[str, Any]], token_budget: int = 0) -> list[dict[str, Any]]:
        """发给模型的历史：从最早的一轮开始丢，直到条数和估算的 token 都在限度内。

        只在用户消息处下刀（工具调用和它的结果不能拆开）；当前这一轮即使超出预算也整轮保留。
        """

        def fits(part: list[dict[str, Any]]) -> bool:
            return len(part) <= _HISTORY_LIMIT and (token_budget <= 0 or estimate_tokens(part) <= token_budget)

        if fits(hist):
            return list(hist)
        starts = [i for i, m in enumerate(hist) if m.get("role") == "user"]
        for i in starts:
            if fits(hist[i:]):
                return hist[i:]
        return hist[starts[-1]:] if starts else hist[-_HISTORY_LIMIT:]


def _add_usage(total: dict[str, int], usage: Any) -> None:
    if not isinstance(usage, dict):
        return
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        if isinstance(usage.get(key), int):
            total[key] = total.get(key, 0) + usage[key]
    total["llm_calls"] = total.get("llm_calls", 0) + 1
