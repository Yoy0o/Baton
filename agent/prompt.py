"""系统提示词：工作规则 + 当前环境（时间、已打开的界面、界面状态、已有结果）+ 本轮任务识别 + 相关系统的知识包。

每一步调用模型前重新组装：界面工具会随页面开关变化，结果列表会随工具调用增长。
"""
from __future__ import annotations

import json
from datetime import datetime

from .config import Settings
from .contracts import ToolSpec
from .gateway import Gateway
from .results import ResultStore

SYSTEM_PROMPT = """你是“{app_name}”，一个多系统编排助手：通过工具调度多个业务系统完成用户的任务。现在是 {now}。

工作规则：
1. 只依据工具返回的数据作答，不编造数据；数据不够时说明缺什么。
2. 列表数据只通过 result_id 在工具之间传递，不要手抄、改写或编造 result_id。
3. 写操作：先调用 *_preview 拿到变更和 confirm_token，再调用对应的 *_apply 并传入 confirm_token。运行时会向用户展示确认卡片并等待决定，你不要在文字里征求确认，也不要伪造 confirm_token。用户拒绝后不要重复提交。
4. 展示结果：用户已打开对应系统界面时，优先用该系统的界面工具（如 fleet__show_result_layer）；否则用 canvas__show 在工作台展示。
5. 汇总类问题（数量、分布、最大最小）用 results__stats 计算，不要心算。
6. 坐标统一 WGS84（经度, 纬度）。
7. 工具报错时先读 fix_hint，修正后最多重试一次；仍失败就如实告诉用户。互不依赖的查询可以在同一轮里一起调用。
8. 用中文回复，先给结论，简洁；可用 Markdown 列表；引用结果时写出 result_id（用反引号包起来）。

已打开的系统界面：{ui}
界面当前状态：{view_state}
本会话已有结果（最近 10 个）：{results}
{intent}{skills}"""


class PromptBuilder:
    def __init__(self, settings: Settings, gateway: Gateway, store: ResultStore):
        self.s = settings
        self.gw = gateway
        self.store = store

    def build(self, *, session_id: str, client_id: str, user: str, specs: list[ToolSpec], intent_hint: str = "") -> str:
        now = datetime.now(self.s.tzinfo)  # 与规则模式同一个时区来源（AGENT_TZ）
        ui_systems = sorted({s.system for s in specs if s.kind == "ui"})
        ui = "、".join(
            (self.gw.manifests[s].ui_bridge.title or self.gw.manifests[s].title) + f"（{s}）" for s in ui_systems
        ) or "无（界面工具不可用，展示请用 canvas__show）"
        views = self.gw.hub.view_states(client_id, user)
        results = [r.meta() for r in sorted(self.store.list_for_session(session_id), key=lambda r: r.created_at)[-10:]]
        brief = [{k: r[k] for k in ("result_id", "result_type", "summary")} for r in results]
        used = {s.system for s in specs}
        skills = "\n\n".join(
            f"## 系统知识：{m.title}（{m.system}）\n{strip_frontmatter(m.skill_text)}"
            for m in self.gw.manifests.values()
            if m.system in used and m.skill_text
        )
        return SYSTEM_PROMPT.format(
            app_name=self.s.app_name,
            now=f"{now:%Y-%m-%d %H:%M}（{now:%A}，{self.s.timezone}，UTC{now:%z}）",
            ui=ui,
            view_state=json.dumps(views, ensure_ascii=False) if views else "无",
            results=json.dumps(brief, ensure_ascii=False) if brief else "无",
            intent=f"\n{intent_hint}\n\n" if intent_hint else "",
            skills=skills,
        )


def strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4 :].strip()
    return text.strip()
