"""工具执行器：把模型一次返回的若干工具调用交给网关执行，并推送执行过程事件。

调度规则：按模型给出的顺序，把相邻的只读 / 只改界面调用合成一批并行执行；
写操作（需要确认）和不认识的工具单独成批，作为屏障——“先查后写、写完再展示”的顺序不会被并行打乱。
返回结果的顺序与调用顺序一致（OpenAI 兼容接口要求每个调用都有对应的 tool 消息）。
所有调用都经 Gateway.call()（ADR-0002），执行器本身不碰业务系统。
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

from .contracts import ToolSpec, error_envelope
from .gateway import Gateway
from .providers import CallContext

# tool_call_finished 事件从信封里带出的字段（工作台展示用）
_FINISHED_KEYS = (
    "ok", "summary", "error_code", "message", "fix_hint", "result_id", "result_type",
    "total", "preview", "fields", "changes", "columns", "confirm_token",
)
_PARALLEL_RISKS = ("read", "view")


@dataclass
class PlannedCall:
    call: dict[str, Any]  # 模型给出的原始 tool_call
    name: str
    args: dict[str, Any]
    error: dict[str, Any] | None = None  # 参数解析失败时的错误信封，不再调用网关

    @property
    def id(self) -> str:
        return str(self.call.get("id"))


def parse_calls(calls: list[dict[str, Any]]) -> list[PlannedCall]:
    out = []
    for call in calls:
        fn = call.get("function") or {}
        name = str(fn.get("name") or "")
        raw = fn.get("arguments")
        try:
            args = raw if isinstance(raw, dict) else json.loads(raw or "{}")
            if args is None:  # 个别模型对无参工具给出 "null"
                args = {}
            if not isinstance(args, dict):
                raise ValueError("arguments 不是对象")
            out.append(PlannedCall(call, name, args))
        except ValueError as e:
            bad = error_envelope("INVALID_ARGS_JSON", f"参数不是合法 JSON：{e}", "重新生成合法的 JSON 参数")
            out.append(PlannedCall(call, name, {}, bad))
    return out


def batches(planned: list[PlannedCall], by_name: dict[str, ToolSpec]) -> list[list[PlannedCall]]:
    """相邻的只读 / 只改界面调用合成一批；其余调用（写操作、未知工具、参数有误）各自成批。"""
    out: list[list[PlannedCall]] = []
    for p in planned:
        if _parallel(p, by_name) and out and all(_parallel(q, by_name) for q in out[-1]):
            out[-1].append(p)
        else:
            out.append([p])
    return out


def _parallel(p: PlannedCall, by_name: dict[str, ToolSpec]) -> bool:
    spec = by_name.get(p.name)
    return p.error is None and spec is not None and spec.risk in _PARALLEL_RISKS


class ToolExecutor:
    def __init__(self, gateway: Gateway):
        self.gw = gateway

    async def run(
        self, ctx: CallContext, calls: list[dict[str, Any]], by_name: dict[str, ToolSpec]
    ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        results: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for batch in batches(parse_calls(calls), by_name):
            if len(batch) == 1:
                results.append(await self._one(ctx, batch[0], by_name))
            else:
                results += await asyncio.gather(*(self._one(ctx, p, by_name) for p in batch))
        return results

    def _describe(self, name: str, spec: ToolSpec | None) -> dict[str, Any]:
        if spec is not None:
            return self.gw.describe(spec)
        system = name.partition("__")[0]
        return {
            "title": name,
            "system": system,
            "system_title": self.gw.system_title(system),
            "color": self.gw.system_color(system),
            "kind": None,
            "risk": None,
        }

    async def _one(
        self, ctx: CallContext, p: PlannedCall, by_name: dict[str, ToolSpec]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        assert ctx.emit is not None
        await ctx.emit(
            {"type": "tool_call_started", "call_id": p.id, "tool": p.name, "args": p.args, **self._describe(p.name, by_name.get(p.name))}
        )
        t0 = time.perf_counter()
        try:
            env = p.error or await self.gw.call(ctx, p.name, p.args)
        except asyncio.CancelledError:
            await ctx.emit(
                {"type": "tool_call_finished", "call_id": p.id, "tool": p.name, "ok": False, "error_code": "CANCELLED",
                 "message": "已停止", "duration_ms": round((time.perf_counter() - t0) * 1000)}
            )
            raise
        await ctx.emit(
            {
                "type": "tool_call_finished",
                "call_id": p.id,
                "tool": p.name,
                "duration_ms": round((time.perf_counter() - t0) * 1000),
                **{k: env.get(k) for k in _FINISHED_KEYS if env.get(k) is not None},
            }
        )
        return p.call, env
