"""对话视图：把 OpenAI 格式的消息历史解析成“本轮做过哪些工具调用、结果如何”。

规则模式的剧本执行器每一步都从消息历史重新推导进度（它和真实模型一样只拿到 messages + tools，
所以能经由 OpenAI 兼容接口驱动，见 ADR-0009）；运行时也用它判断最近用过哪些系统，供工具路由参考。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# 视为“用户没有同意”的错误码：写操作据此区分“被拒绝”和“提交失败”
REJECTED_CODES = frozenset({"USER_REJECTED", "CONFIRM_TIMEOUT"})


@dataclass
class Call:
    id: str
    name: str
    args: dict[str, Any]
    current: bool  # 是否属于本轮（最后一条用户消息之后）
    env: dict[str, Any] = field(default_factory=dict)
    turn: int = 0  # 属于第几轮（之前出现过几条用户消息）

    @property
    def system(self) -> str:
        return self.name.partition("__")[0]

    @property
    def ok(self) -> bool:
        return bool(self.env.get("ok"))

    @property
    def result_id(self) -> str | None:
        return self.env.get("result_id")

    def why(self) -> str:
        msg = self.env.get("message") or "未知错误"
        hint = self.env.get("fix_hint")
        return f"{msg}（{hint}）" if hint else msg

    def facts(self) -> dict[str, Any]:
        """模板里可引用的字段：信封原样 + error（失败原因，含修正提示）。"""
        return {**self.env, "error": "" if self.ok else self.why(), "tool": self.name}


class Transcript:
    def __init__(self, messages: list[dict[str, Any]]):
        last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
        self.text = str(messages[last_user].get("content") or "") if last_user >= 0 else ""
        self.calls: list[Call] = []
        self._taken: set[str] = set()
        by_id: dict[str, Call] = {}
        turn = 0
        for i, m in enumerate(messages):
            if m.get("role") == "user":
                turn += 1
            elif m.get("role") == "assistant":
                for tc in m.get("tool_calls") or []:
                    fn = tc.get("function") or {}
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except ValueError:
                        args = {}
                    call = Call(
                        str(tc.get("id")), str(fn.get("name", "")), args if isinstance(args, dict) else {}, i > last_user, turn=turn
                    )
                    self.calls.append(call)
                    by_id[call.id] = call
            elif m.get("role") == "tool" and m.get("tool_call_id") in by_id:
                try:
                    env = json.loads(m.get("content") or "{}")
                except ValueError:
                    env = {}
                by_id[m["tool_call_id"]].env = env if isinstance(env, dict) else {}

    # ------------------------------------------------------------------ 本轮进度
    def take(self, name: str) -> Call | None:
        """取本轮中最早一次尚未被认领的 name 调用并认领。剧本按步骤顺序认领，同一工具调用多次也能对上号。"""
        for c in self.calls:
            if c.current and c.name == name and c.id not in self._taken:
                self._taken.add(c.id)
                return c
        return None

    def current_calls(self) -> list[Call]:
        return [c for c in self.calls if c.current]

    # ------------------------------------------------------------------ 历史
    def latest_result(self, result_type: str, scope: str = "any") -> Call | None:
        """最近一次成功产出 result_type 的调用；scope=previous 时只看之前的轮次。"""
        return next(
            (
                c
                for c in reversed(self.calls)
                if c.ok and c.env.get("result_type") == result_type and not (scope == "previous" and c.current)
            ),
            None,
        )

    def latest_args(self, name: str) -> dict[str, Any]:
        c = next((c for c in reversed(self.calls) if c.name == name), None)
        return c.args if c else {}

    def recent_systems(self) -> list[str]:
        """之前最近一轮有工具调用的那一轮用过的系统，按首次出现排序。追问（“把第 2 组列出来”）时工具路由沿用它们。"""
        previous = [c for c in self.calls if not c.current]
        if not previous:
            return []
        last_turn = previous[-1].turn
        seen: list[str] = []
        for c in previous:
            if c.turn == last_turn and c.system not in seen:
                seen.append(c.system)
        return seen
