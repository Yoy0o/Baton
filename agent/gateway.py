"""能力网关：Agent 调用工具的唯一入口。

负责：工具目录（命名空间）、按需选择、参数校验、风险策略（写操作必须预览 + 用户确认）、
确认令牌签发与回放、审计。任何异常都转成可纠正的错误信封返回给模型，不向上抛。
用户停止任务时：未确认的写操作不会执行；已经确认、正在提交的写操作会完成并记入审计。
"""
from __future__ import annotations

import asyncio
import difflib
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
from jsonschema import Draft202012Validator

from .audit import AuditLog
from .config import Settings
from .contracts import ToolError, ToolSpec, error_envelope, ok_envelope
from .manifest import Manifest
from .providers import CallContext, CallOutcome
from .providers.bridge_provider import BridgeHub
from .providers.builtin_provider import BuiltinProvider
from .providers.openapi_provider import OpenAPIProvider
from .results import ResultStore

log = logging.getLogger("agent.gateway")

CONFIRM_TTL_S = 600  # confirm_token 有效期（R3.2）
_RELOAD_INTERVAL_S = 5

# 内置工具所属的“平台”系统
BUILTIN_SYSTEMS = {"analytics": "内置计算", "canvas": "工作台画布", "results": "结果仓库"}
PLATFORM_COLOR = "#64748B"
# 清单未声明 color 时按顺序分配的系统标识色（避开表示写操作的琥珀色、成功绿、错误红）
LINE_COLORS = ["#2563EB", "#7C3AED", "#C026D3", "#0891B2", "#4F46E5", "#0D9488", "#9333EA", "#1D4ED8"]


@dataclass
class PendingConfirm:
    token: str
    user: str
    session_id: str
    preview_tool: str
    apply_tool: str
    request_args: dict[str, Any]
    summary: str
    changes: list[dict[str, Any]]
    columns: list[str]
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    status: str = "pending"  # pending | approved | rejected | consumed
    result: dict[str, Any] | None = None


class Gateway:
    def __init__(
        self,
        settings: Settings,
        manifests: list[Manifest],
        store: ResultStore,
        hub: BridgeHub,
        audit: AuditLog,
        http: httpx.AsyncClient,
    ):
        self.settings = settings
        self.manifests = {m.system: m for m in manifests}
        self.store = store
        self.hub = hub
        self.audit = audit
        self.http = http
        self.apis = {m.system: OpenAPIProvider(m, http, store, settings.http_timeout_s) for m in manifests if m.api}
        self.builtin = BuiltinProvider(store)
        self.confirms: dict[str, PendingConfirm] = {}
        self._validators: dict[str, Draft202012Validator] = {}
        self._colors = {}
        for i, m in enumerate(manifests):
            self._colors[m.system] = m.color or LINE_COLORS[i % len(LINE_COLORS)]

    # ------------------------------------------------------------------ 系统信息
    def system_title(self, system: str) -> str:
        m = self.manifests.get(system)
        return m.title if m else BUILTIN_SYSTEMS.get(system, system)

    def system_color(self, system: str) -> str:
        return self._colors.get(system, PLATFORM_COLOR)

    def systems_meta(self) -> dict[str, dict[str, Any]]:
        meta = {
            s: {"title": m.title, "ui_title": m.ui_bridge.title or m.title, "color": self.system_color(s), "builtin": False}
            for s, m in self.manifests.items()
        }
        for s, title in BUILTIN_SYSTEMS.items():
            meta[s] = {"title": title, "ui_title": title, "color": PLATFORM_COLOR, "builtin": True}
        return meta

    def describe(self, spec: ToolSpec) -> dict[str, Any]:
        """工作台展示一个工具所需的信息。"""
        return {
            "title": spec.title or spec.name,
            "system": spec.system,
            "system_title": self.system_title(spec.system),
            "color": self.system_color(spec.system),
            "kind": spec.kind,
            "risk": spec.risk,
        }

    # ------------------------------------------------------------------ 目录
    async def ensure_loaded(self, force: bool = False) -> None:
        now = time.time()
        for p in self.apis.values():
            if force or (not p.loaded and now - p.last_attempt > _RELOAD_INTERVAL_S):
                await p.load()

    def load_errors(self) -> dict[str, list[str]]:
        return {s: p.errors for s, p in self.apis.items() if p.errors}

    def catalog(self, client_id: str, user: str) -> list[ToolSpec]:
        specs: list[ToolSpec] = []
        for p in self.apis.values():
            specs += p.specs()
        specs += self.hub.specs_for(client_id, user)
        specs += self.builtin.specs()
        return specs

    def select(self, client_id: str, user: str, text: str) -> list[ToolSpec]:
        """工具少时全量加载；多时按清单 keywords 路由，只加载相关系统 + 内置工具（设计文档 4.8）。"""
        specs = self.catalog(client_id, user)
        if len(specs) <= self.settings.full_catalog_limit:
            return specs
        hit = {s for s, m in self.manifests.items() if any(k and k in text for k in m.keywords)}
        if not hit:
            return specs
        return [s for s in specs if s.kind == "builtin" or s.system in hit]

    def find(self, name: str, client_id: str, user: str) -> ToolSpec | None:
        return next((s for s in self.catalog(client_id, user) if s.name == name), None)

    # ------------------------------------------------------------------ 调用
    async def call(self, ctx: CallContext, name: str, args: dict[str, Any] | None) -> dict[str, Any]:
        started = time.perf_counter()
        ctx.request_id = ctx.request_id or uuid.uuid4().hex[:12]
        args = args or {}
        spec: ToolSpec | None = None
        envelope: dict[str, Any] | None = None
        try:
            await self.ensure_loaded()
            spec = self.find(name, ctx.client_id, ctx.user)
            if spec is None:
                raise self._unknown_tool(name, ctx)
            self._validate(spec, args)
            if spec.needs_confirmation:
                envelope = await self._call_confirmed(ctx, spec, args)
            else:
                outcome = await self._dispatch(ctx, spec, args)
                envelope = outcome.envelope
                if spec.pairs_with and envelope.get("ok"):
                    envelope = self._issue_confirm_token(ctx, spec, outcome)
        except ToolError as e:
            envelope = e.envelope()
        except asyncio.CancelledError:
            envelope = error_envelope("CANCELLED", "任务已停止，本次调用未完成")
            raise
        except Exception as e:  # 兜底：不把堆栈暴露给模型
            log.exception("tool %s failed", name)
            envelope = error_envelope("INTERNAL_ERROR", f"{type(e).__name__}: {e}"[:300], "稍后重试，或换一种方式完成任务", True)
        finally:
            self._audit(ctx, name, spec, args, envelope or error_envelope("CANCELLED", "任务已停止"), started)
        return envelope

    def _audit(
        self,
        ctx: CallContext,
        name: str,
        spec: ToolSpec | None,
        args: dict[str, Any],
        envelope: dict[str, Any],
        started: float,
        note: str = "",
    ) -> None:
        confirmed = None
        if spec is not None and spec.needs_confirmation:
            entry = self.confirms.get(str(args.get("confirm_token", "")))
            if entry is not None and entry.status in ("approved", "consumed"):
                confirmed = True
            elif (entry is not None and entry.status == "rejected") or envelope.get("error_code") in (
                "USER_REJECTED",
                "CONFIRM_TIMEOUT",
            ):
                confirmed = False
        summary = envelope.get("summary") or envelope.get("message") or ""
        self.audit.record(
            user=ctx.user,
            conversation_id=ctx.session_id,
            client_id=ctx.client_id,
            run_id=ctx.run_id,
            request_id=ctx.request_id,
            initiator=ctx.initiator,
            tool=name,
            title=spec.title if spec else None,
            system=spec.system if spec else name.partition("__")[0],
            kind=spec.kind if spec else None,
            risk=spec.risk if spec else None,
            args=args,
            ok=bool(envelope.get("ok")),
            error_code=envelope.get("error_code"),
            summary=(note + summary)[:200],
            result_id=envelope.get("result_id"),
            confirmed=confirmed,
            duration_ms=round((time.perf_counter() - started) * 1000),
        )

    def _unknown_tool(self, name: str, ctx: CallContext) -> ToolError:
        names = [s.name for s in self.catalog(ctx.client_id, ctx.user)]
        close = difflib.get_close_matches(name, names, n=3, cutoff=0.4)
        system = name.partition("__")[0]
        m = self.manifests.get(system)
        if m and m.ui_tool(name.partition("__")[2]):
            title = m.ui_bridge.title or m.title
            return ToolError(
                "UI_NOT_CONNECTED",
                f"{title} 界面当前未打开，{name} 不可用",
                "请用户先在工作台打开该系统页面；或改用 canvas__show 在画布展示",
            )
        return ToolError("UNKNOWN_TOOL", f"没有工具 {name}", f"相近的工具：{', '.join(close)}" if close else "只使用工具列表中的名称")

    def _validate(self, spec: ToolSpec, args: dict[str, Any]) -> None:
        key = spec.name + str(id(spec.input_schema))
        validator = self._validators.get(key)
        if validator is None:
            validator = Draft202012Validator(spec.input_schema)
            self._validators[key] = validator
        errors = sorted(validator.iter_errors(args), key=lambda e: list(e.path))
        if errors:
            parts = []
            for e in errors[:3]:
                where = ".".join(str(p) for p in e.path) or "参数"
                parts.append(f"{where}: {e.message}")
            raise ToolError("INVALID_ARGS", "；".join(parts), "对照工具的参数说明修正后重试")

    async def _dispatch(self, ctx: CallContext, spec: ToolSpec, args: dict[str, Any]) -> CallOutcome:
        if spec.kind == "api":
            return await self.apis[spec.system].call(spec.name, args, ctx)
        if spec.kind == "ui":
            env = await self.hub.call(spec.name, args, ctx.client_id, ctx.user, self.settings.ui_call_timeout_s)
            return CallOutcome(envelope=env, request_args=args)
        return await self.builtin.call(spec.name, args, ctx)

    # ------------------------------------------------------------------ 写操作：预览 → 确认 → 提交
    def _issue_confirm_token(self, ctx: CallContext, spec: ToolSpec, outcome: CallOutcome) -> dict[str, Any]:
        raw = outcome.raw if isinstance(outcome.raw, dict) else {}
        changes = raw.get("changes") if isinstance(raw.get("changes"), list) else []
        changes = [c for c in changes if isinstance(c, dict)][:200]
        columns = list(dict.fromkeys(k for c in changes[:50] for k in c))
        summary = raw.get("summary") if isinstance(raw.get("summary"), str) else outcome.envelope.get("summary", "")
        token = "cf_" + uuid.uuid4().hex[:12]
        assert spec.pairs_with
        self.confirms[token] = PendingConfirm(
            token=token,
            user=ctx.user,
            session_id=ctx.session_id,
            preview_tool=spec.name,
            apply_tool=spec.pairs_with,
            request_args=outcome.request_args,
            summary=summary,
            changes=changes,
            columns=columns,
            expires_at=time.time() + CONFIRM_TTL_S,
        )
        extra = {k: v for k, v in raw.items() if k not in ("changes", "summary")}
        return ok_envelope(
            f"{summary}（预览，未落库）",
            data=extra or None,
            total=len(changes),
            preview=changes,
            confirm_token=token,
            next_hints=[f"调用 {spec.pairs_with}(confirm_token=\"{token}\") 提交；运行时会先弹出确认卡片请用户确认"],
            changes=changes,
            columns=columns,
        )

    async def _call_confirmed(self, ctx: CallContext, spec: ToolSpec, args: dict[str, Any]) -> dict[str, Any]:
        token = str(args.get("confirm_token", ""))
        entry = self.confirms.get(token)
        preview_hint = f"先调用与 {spec.name} 成对的预览工具获取 confirm_token"
        if entry is None:
            raise ToolError("INVALID_CONFIRM_TOKEN", "confirm_token 无效或不存在", preview_hint)
        if entry.user != ctx.user or entry.session_id != ctx.session_id:
            raise ToolError("CONFIRM_TOKEN_FORBIDDEN", "confirm_token 不属于当前会话", preview_hint)
        if entry.apply_tool != spec.name:
            raise ToolError("CONFIRM_TOKEN_MISMATCH", f"该 confirm_token 属于 {entry.apply_tool}，不能用于 {spec.name}")
        if entry.status == "consumed" and entry.result:
            return {**entry.result, "summary": entry.result.get("summary", "") + "（此前已提交，未重复执行）"}
        if entry.status == "rejected":
            raise ToolError("USER_REJECTED", "用户已拒绝这次变更", "不要重复提交；询问用户需要如何调整")
        if time.time() > entry.expires_at:
            raise ToolError("CONFIRM_TOKEN_EXPIRED", "confirm_token 已过期", "重新调用预览工具")
        if ctx.confirm is None:
            raise ToolError("CONFIRMATION_REQUIRED", "该写操作需要用户在工作台确认，当前通道无法确认", "请在工作台中发起该操作")

        approved = await ctx.confirm(entry)  # 由运行时展示确认卡片并等待用户决定；停止任务会在这里中断
        if not approved:
            entry.status = "rejected"
            raise ToolError("USER_REJECTED", "用户拒绝了这次变更", "不要重复提交；询问用户需要如何调整")
        entry.status = "approved"
        # 用户已确认：提交不随“停止”中断，避免出现“系统已改、平台不知道”的状态
        commit = asyncio.ensure_future(self._commit(ctx, spec, entry))
        try:
            return await asyncio.shield(commit)
        except asyncio.CancelledError:
            started = time.perf_counter()
            commit.add_done_callback(lambda t: self._audit_late_commit(ctx, spec, entry, t, started))
            raise

    async def _commit(self, ctx: CallContext, spec: ToolSpec, entry: PendingConfirm) -> dict[str, Any]:
        outcome = await self.apis[spec.system].call(
            spec.name, entry.request_args, ctx, idempotency_key=entry.token, replay=True
        )
        entry.status = "consumed"
        entry.result = outcome.envelope
        return outcome.envelope

    def _audit_late_commit(self, ctx: CallContext, spec: ToolSpec, entry: PendingConfirm, task: asyncio.Future, started: float) -> None:
        if task.cancelled():
            envelope = error_envelope("CANCELLED", "提交被中断")
        elif isinstance(task.exception(), ToolError):
            envelope = task.exception().envelope()  # type: ignore[union-attr]
        elif task.exception() is not None:
            envelope = error_envelope("INTERNAL_ERROR", str(task.exception())[:300])
        else:
            envelope = task.result()
        self._audit(ctx, spec.name, spec, {"confirm_token": entry.token}, envelope, started, note="（任务停止后完成）")
