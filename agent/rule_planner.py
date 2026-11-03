"""规则模式：未配置模型时，用“意图识别 + 场景剧本”代替模型做规划，按步骤发出工具调用。

它和真实模型走完全相同的链路（运行时 → 网关 → 适配器 → 系统 / 界面 → 确认卡片 → 审计），
用来在没有模型密钥时验证架构与接入规则，也是端到端场景测试的驱动器。
能识别哪些指令完全由各系统清单里的场景剧本（playbooks.yaml）决定，这里不写任何具体场景（ADR-0011）。
最终答复按字逐段推送，模拟流式输出；并附带追问建议（suggestions，规则模式专有）。
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime
from typing import Any

from .config import Settings
from .intents import IntentLibrary
from .playbook import PlaybookRunner, final
from .transcript import Transcript


class RulePlanner:
    label = "规则模式（未配置模型）"

    def __init__(self, settings: Settings, library: IntentLibrary | None = None):
        self.settings = settings  # 时区取 settings.tzinfo，与模型模式的系统提示词一致
        self.delay = settings.rule_stream_delay_s
        self.library = library if library is not None else IntentLibrary.load(settings)

    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], on_delta=None) -> dict[str, Any]:
        names = {t["function"]["name"] for t in tools}
        transcript = Transcript(messages)
        today = self._today()
        matches = self.library.classify(transcript.text, today=today, transcript=transcript, limit=1)
        if matches:
            msg = PlaybookRunner(self.library, matches[0], transcript, names, today).next_message()
        else:
            msg = final(self.library.help_text(), [p["prompt"] for p in self.library.presets()[:3]])
        if on_delta is not None and msg.get("content") and not msg.get("tool_calls"):
            await self._stream(msg["content"], on_delta)
        return msg

    def _today(self) -> date:
        return datetime.now(self.settings.tzinfo).date()

    async def _stream(self, text: str, on_delta) -> None:
        """按 1–4 个字一段推送，模拟模型逐字输出。"""
        i, step = 0, 0
        while i < len(text):
            n = 1 + (step % 4)
            await on_delta("text", text[i : i + n])
            i += n
            step += 1
            if self.delay > 0:
                await asyncio.sleep(self.delay)
