"""规则模式：未配置模型时，用规则脚本代替模型做“规划”，按步骤发出工具调用。

它和真实模型走完全相同的链路（运行时 → 网关 → 适配器 → 系统 / 界面 → 确认卡片 → 审计），
用来在没有模型密钥时验证架构与接入规则，也是端到端场景测试的驱动器。它只认识几类内置指令，见 HELP_TEXT。
最终答复按字逐段推送，模拟流式输出；并附带追问建议（suggestions，规则模式专有）。
"""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from .config import Settings

REGROUP_PROMPT = "把华东区明天早班的司机按当前位置重新分成 5 组，每组不超过 8 人，先给我看地图和名单，确认后同步到调度系统"
STOPS_PROMPT = "在地图上显示过去 24 小时停留超过 2 小时的挂车，按客户着色"
TRACK_PROMPT = "回放司机 D0012 今天的轨迹"
ADJUST_PROMPT = "把 D0012 换到第 3 组，然后同步到调度系统"

HELP_TEXT = f"""当前是**规则模式**（未配置模型），只识别以下几类指令。接入模型后可以自由提问。

- 司机组重组：{REGROUP_PROMPT}
- 微调方案：{ADJUST_PROMPT}
- 地图可视化：{STOPS_PROMPT}
- 调用系统原有功能：{TRACK_PROMPT}，或“在地图上定位 D0012”"""

_CN = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_NUM = r"([0-9]+(?:\.[0-9]+)?|[一二两三四五六七八九十])"


def _num(s: str) -> float:
    return float(s) if re.match(r"^[0-9.]+$", s) else float(_CN.get(s, 0))




def parse_region(text: str) -> str:
    return "north-china" if "华北" in text or "北京" in text else "east-china"


def parse_shift(text: str) -> str:
    return "late" if "晚班" in text else "early"


def parse_date(text: str, today: date) -> str:
    m = re.search(r"(20\d{2}-\d{2}-\d{2})", text)
    if m:
        return m.group(1)
    if "后天" in text:
        return (today + timedelta(days=2)).isoformat()
    if "明天" in text or "明日" in text:
        return (today + timedelta(days=1)).isoformat()
    if "昨天" in text:
        return (today - timedelta(days=1)).isoformat()
    return today.isoformat()


def parse_groups(text: str) -> int | None:
    m = re.search(r"分成\s*" + _NUM + r"\s*个?组", text) or re.search(_NUM + r"\s*个?组", text)
    return int(_num(m.group(1))) if m else None


def parse_max_size(text: str) -> int | None:
    m = re.search(r"(?:不超过|最多|上限|不多于)\s*" + _NUM + r"\s*(?:人|个)", text)
    return int(_num(m.group(1))) if m else None


def parse_lookback_h(text: str) -> int | None:
    m = re.search(r"(?:过去|最近|近)\s*" + _NUM + r"\s*(小时|天)", text)
    if not m:
        return None
    v = _num(m.group(1))
    return int(v * 24) if m.group(2) == "天" else int(v)


def parse_min_duration_min(text: str) -> int | None:
    m = re.search(r"(?:超过|大于|多于)\s*" + _NUM + r"\s*(小时|分钟)", text) or re.search(
        _NUM + r"\s*(小时|分钟)以上", text
    )
    if not m:
        return None
    v = _num(m.group(1))
    return int(v * 60) if m.group(2) == "小时" else int(v)


def parse_pins(text: str) -> list[dict[str, Any]]:
    pat = r"(D\d{4})[^，。,；;]{0,8}?(?:换到|移到|调到|放到|调整到|改到|分到)\s*第?\s*" + _NUM + r"\s*组"
    return [{"id": d, "group": int(_num(g))} for d, g in re.findall(pat, text)]


def detect_intent(text: str) -> str:
    if parse_pins(text):
        return "adjust"
    if re.search(r"分组|重组|分成|编组|划分", text):
        return "regroup"
    if re.search(r"同步|写回|提交", text):
        return "sync"
    if re.search(r"停留|挂车", text):
        return "stops"
    if re.search(r"轨迹|回放", text):
        return "track"
    if re.search(r"D\d{4}", text) and re.search(r"定位|在哪|位置", text):
        return "locate"
    return "help"


@dataclass
class _Call:
    id: str
    name: str
    args: dict[str, Any]
    current: bool  # 是否属于本轮（最后一条用户消息之后）
    env: dict[str, Any] = field(default_factory=dict)

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


class _Convo:
    def __init__(self, messages: list[dict[str, Any]]):
        last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
        self.text = str(messages[last_user].get("content") or "") if last_user >= 0 else ""
        self.calls: list[_Call] = []
        by_id: dict[str, _Call] = {}
        for i, m in enumerate(messages):
            if m.get("role") == "assistant":
                for tc in m.get("tool_calls") or []:
                    try:
                        args = json.loads(tc["function"].get("arguments") or "{}")
                    except ValueError:
                        args = {}
                    call = _Call(tc["id"], tc["function"]["name"], args, current=i > last_user)
                    self.calls.append(call)
                    by_id[call.id] = call
            elif m.get("role") == "tool" and m.get("tool_call_id") in by_id:
                try:
                    by_id[m["tool_call_id"]].env = json.loads(m.get("content") or "{}")
                except ValueError:
                    pass

    def done(self, name: str) -> _Call | None:
        return next((c for c in reversed(self.calls) if c.current and c.name == name), None)

    def any_done(self, *names: str) -> bool:
        return any(self.done(n) for n in names)

    def latest(self, result_type: str, before_current: bool = False) -> _Call | None:
        return next(
            (
                c
                for c in reversed(self.calls)
                if c.ok and c.env.get("result_type") == result_type and not (before_current and c.current)
            ),
            None,
        )

    def latest_args(self, name: str) -> dict[str, Any]:
        c = next((c for c in reversed(self.calls) if c.name == name), None)
        return c.args if c else {}


def _calls(*pairs: tuple[str, dict[str, Any]], note: str = "") -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": note,
        "tool_calls": [
            {
                "id": "call_" + uuid.uuid4().hex[:10],
                "type": "function",
                "function": {"name": n, "arguments": json.dumps(a, ensure_ascii=False)},
            }
            for n, a in pairs
        ],
    }


def _final(text: str, suggestions: list[str] | None = None) -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "assistant", "content": text}
    if suggestions:
        msg["suggestions"] = suggestions[:3]
    return msg


class RulePlanner:
    label = "规则模式（未配置模型）"

    def __init__(self, settings: Settings):
        self.settings = settings  # 时区取 settings.tzinfo，与模型模式的系统提示词一致
        self.delay = settings.rule_stream_delay_s

    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], on_delta=None) -> dict[str, Any]:
        names = {t["function"]["name"] for t in tools}
        c = _Convo(messages)
        msg = getattr(self, f"_plan_{detect_intent(c.text)}")(c, names)
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

    # ------------------------------------------------------------------ 通用步骤
    @staticmethod
    def _show(c: _Convo, names: set[str], result_id: str, color_by: str, title: str, canvas_too: bool) -> dict | None:
        """展示结果：车队地图开着就回填地图；canvas_too 时同时在画布给出表格。"""
        if c.any_done("fleet__show_result_layer", "canvas__show"):
            return None
        pairs = []
        if "fleet__show_result_layer" in names:
            pairs.append(("fleet__show_result_layer", {"result_id": result_id, "color_by": color_by}))
        if canvas_too or not pairs:
            pairs.append(("canvas__show", {"result_id": result_id, "color_by": color_by, "title": title}))
        return _calls(*pairs)

    @staticmethod
    def _where_shown(c: _Convo) -> str:
        parts = []
        fl = c.done("fleet__show_result_layer")
        if fl and fl.ok:
            parts.append("车队地图")
        cv = c.done("canvas__show")
        if cv and cv.ok:
            parts.append("工作台结果面板")
        return "和".join(parts) or "（展示失败）"

    @staticmethod
    def _sync(c: _Convo, region: str, plan_id: str) -> dict | None:
        p = c.done("dispatch__preview_regroup")
        if p is None:
            return _calls(("dispatch__preview_regroup", {"region": region, "groups": plan_id}))
        if not p.ok:
            return None
        if c.done("dispatch__apply_regroup") is None:
            return _calls(("dispatch__apply_regroup", {"confirm_token": p.env["confirm_token"]}))
        return None

    @staticmethod
    def _sync_state(c: _Convo, wants_sync: bool) -> str:
        if not wants_sync:
            return "skipped"
        p, a = c.done("dispatch__preview_regroup"), c.done("dispatch__apply_regroup")
        if p and not p.ok:
            return "preview_failed"
        if a is None:
            return "not_applied"
        if a.ok:
            return "applied"
        if a.env.get("error_code") in ("USER_REJECTED", "CONFIRM_TIMEOUT"):
            return "rejected"
        return "failed"

    def _sync_line(self, c: _Convo, wants_sync: bool) -> str:
        state = self._sync_state(c, wants_sync)
        p, a = c.done("dispatch__preview_regroup"), c.done("dispatch__apply_regroup")
        return {
            "skipped": "调度系统：未写回。确认方案后可以说“同步到调度系统”。",
            "preview_failed": f"调度系统：预览失败，{p.why() if p else ''}",
            "not_applied": "调度系统：未提交。",
            "applied": f"调度系统：已提交。{a.env.get('summary', '') if a else ''}",
            "rejected": "调度系统：你没有确认这次提交，调度系统未做任何修改。",
            "failed": f"调度系统：提交失败，{a.why() if a else ''}",
        }[state]

    # ------------------------------------------------------------------ 场景
    def _plan_regroup(self, c: _Convo, names: set[str]) -> dict[str, Any]:
        text = c.text
        region, shift = parse_region(text), parse_shift(text)
        day = parse_date(text, self._today())
        k, cap = parse_groups(text) or 5, parse_max_size(text)
        wants_sync = bool(re.search(r"同步|写回|提交|调度系统", text))

        r1 = c.done("tms__search_shift_drivers")
        if r1 is None:
            return _calls(("tms__search_shift_drivers", {"region": region, "date": day, "shift": shift}))
        if not r1.ok:
            return _final(f"查询排班名单失败：{r1.why()}")
        r2 = c.done("fleet__get_driver_positions")
        if r2 is None:
            return _calls(("fleet__get_driver_positions", {"driver_ids": r1.result_id}))
        if not r2.ok:
            return _final(f"获取司机位置失败：{r2.why()}")
        r3 = c.done("analytics__run_grouping")
        if r3 is None:
            args: dict[str, Any] = {"points": r2.result_id, "groups": k}
            if cap:
                args["max_size"] = cap
            return _calls(("analytics__run_grouping", args))
        if not r3.ok:
            return _final(f"分组未完成：{r3.why()}")
        step = self._show(c, names, r3.result_id, "group", "司机分组方案", canvas_too=True)
        if step:
            return step
        if wants_sync:
            step = self._sync(c, region, r3.result_id)
            if step:
                return step
        state = self._sync_state(c, wants_sync)
        follow = {
            "skipped": ["把 D0012 换到第 3 组", "同步到调度系统"],
            "applied": [ADJUST_PROMPT, STOPS_PROMPT],
            "rejected": ["把 D0012 换到第 3 组", "同步到调度系统"],
        }.get(state, ["同步到调度系统"])
        return _final(
            f"{r3.env['summary']}。\n\n"
            f"- 名单：{r1.env['summary']}（排班系统），位置：{r2.env['summary']}（车队系统）。\n"
            f"- 展示：已在{self._where_shown(c)}按组着色显示，分组方案 `{r3.result_id}`。\n"
            f"- {self._sync_line(c, wants_sync)}",
            follow,
        )

    def _plan_adjust(self, c: _Convo, names: set[str]) -> dict[str, Any]:
        base = c.latest("group_plan", before_current=True)
        if base is None:
            return _final("还没有可以调整的分组方案。请先生成方案，例如：\n\n" + REGROUP_PROMPT, [REGROUP_PROMPT])
        params = (base.env.get("data") or {}).get("params") or {}
        region = c.latest_args("tms__search_shift_drivers").get("region") or parse_region(c.text)
        wants_sync = bool(re.search(r"同步|写回|提交|调度系统", c.text))
        r = c.done("analytics__run_grouping")
        if r is None:
            args: dict[str, Any] = {
                "points": base.result_id,
                "groups": params.get("groups") or len((base.env.get("data") or {}).get("groups") or []) or 5,
                "base_plan": base.result_id,
                "pin": parse_pins(c.text),
            }
            if params.get("max_size"):
                args["max_size"] = params["max_size"]
            return _calls(("analytics__run_grouping", args))
        if not r.ok:
            return _final(f"调整未完成：{r.why()}")
        step = self._show(c, names, r.result_id, "group", "调整后的分组方案", canvas_too=True)
        if step:
            return step
        if wants_sync:
            step = self._sync(c, region, r.result_id)
            if step:
                return step
        state = self._sync_state(c, wants_sync)
        follow = [TRACK_PROMPT] if state == "applied" else ["同步到调度系统"]
        return _final(
            f"已按要求调整：{r.env['summary']}。\n\n"
            f"- 展示：已在{self._where_shown(c)}更新，新方案 `{r.result_id}`（基于 `{base.result_id}`，组号不变）。\n"
            f"- {self._sync_line(c, wants_sync)}",
            follow,
        )

    def _plan_sync(self, c: _Convo, names: set[str]) -> dict[str, Any]:
        base = c.latest("group_plan")
        if base is None:
            return _final("还没有可同步的分组方案。请先生成方案。\n\n" + HELP_TEXT, [REGROUP_PROMPT])
        region = c.latest_args("tms__search_shift_drivers").get("region") or parse_region(c.text)
        step = self._sync(c, region, base.result_id)
        if step:
            return step
        state = self._sync_state(c, True)
        follow = [TRACK_PROMPT, STOPS_PROMPT] if state == "applied" else ["把 D0012 换到第 3 组"]
        return _final(f"分组方案 `{base.result_id}`。\n\n- {self._sync_line(c, True)}", follow)

    def _plan_stops(self, c: _Convo, names: set[str]) -> dict[str, Any]:
        hours, min_dur = parse_lookback_h(c.text) or 24, parse_min_duration_min(c.text) or 120
        r = c.done("fleet__search_stops")
        if r is None:
            return _calls(("fleet__search_stops", {"lookback_h": hours, "min_duration_min": min_dur}))
        if not r.ok:
            return _final(f"查询停留记录失败：{r.why()}")
        s = c.done("results__stats")
        if s is None:
            return _calls(("results__stats", {"result_id": r.result_id, "group_by": "customer", "value_field": "duration_min"}))
        step = self._show(c, names, r.result_id, "customer", f"停留超过 {min_dur} 分钟的挂车", canvas_too=False)
        if step:
            return step
        stat = s.env.get("summary", "") if s.ok else ""
        return _final(
            f"过去 {hours} 小时停留超过 {min_dur} 分钟的挂车：{r.env['summary']}。\n\n"
            f"- 分布：{stat}\n"
            f"- 展示：已在{self._where_shown(c)}按客户着色，结果 `{r.result_id}`。",
            [TRACK_PROMPT, "把华东区明天早班的司机分成 5 组，每组不超过 8 人"],
        )

    def _plan_track(self, c: _Convo, names: set[str]) -> dict[str, Any]:
        m = re.search(r"D\d{4}", c.text)
        driver = m.group(0) if m else "D0012"
        day = parse_date(c.text, self._today())
        if "fleet__play_track" not in names:
            return _final("车队地图未打开。轨迹回放是车队地图台自带的界面功能，请先在右侧工作区打开“车队地图”，状态显示已接入后再试。")
        r = c.done("fleet__play_track")
        if r is None:
            return _calls(("fleet__play_track", {"driver_id": driver, "date": day}))
        if not r.ok:
            return _final(f"回放失败：{r.why()}")
        return _final(r.env.get("summary", ""), [f"在地图上定位 {driver}", STOPS_PROMPT])

    def _plan_locate(self, c: _Convo, names: set[str]) -> dict[str, Any]:
        driver = re.search(r"D\d{4}", c.text).group(0)  # detect_intent 已保证存在
        if "fleet__focus_driver" not in names:
            return _final("车队地图未打开，无法定位。请先在右侧工作区打开“车队地图”。")
        r = c.done("fleet__focus_driver")
        if r is None:
            return _calls(("fleet__focus_driver", {"driver_id": driver}))
        if not r.ok:
            return _final(f"定位失败：{r.why()}")
        return _final(r.env.get("summary", ""), [f"回放司机 {driver} 今天的轨迹"])

    def _plan_help(self, c: _Convo, names: set[str]) -> dict[str, Any]:
        return _final(HELP_TEXT, [REGROUP_PROMPT, STOPS_PROMPT, TRACK_PROMPT])
