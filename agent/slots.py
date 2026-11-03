"""槽位提取：从用户原话里取出参数（区域、日期、数量、时长、对象 ID……），供意图识别和场景剧本使用。

剧本在 YAML 里按“类型 + 参数”声明槽位，提取逻辑按类型注册在这里、全平台复用。
新系统多数情况下只写 YAML；确有新的参数形态时，用 @extractor("类型名", {参数名…}) 注册一个函数，
剧本加载时会校验类型是否存在、参数是否认识（R11.5）。
"""
from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

_CN = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
NUM = r"([0-9]+(?:\.[0-9]+)?|[一二两三四五六七八九十])"
# 所有槽位都认识的通用参数：default 未提取到时的取值；from_call 先沿用本对话里某个工具调用的参数（见 playbook.py）；
# description 写给模型看的说明（意图提示里显示）
COMMON_PARAMS = frozenset({"type", "default", "from_call", "description"})

# (原话, 槽位声明, 今天) -> 提取值；没有提取到返回 None
Extractor = Callable[[str, dict[str, Any], date], Any]
_EXTRACTORS: dict[str, tuple[Extractor, frozenset[str]]] = {}


def extractor(name: str, params: set[str] | frozenset[str] = frozenset()) -> Callable[[Extractor], Extractor]:
    """注册一种槽位类型。params 是该类型认识的参数名（通用参数不用列）。"""

    def deco(fn: Extractor) -> Extractor:
        _EXTRACTORS[name] = (fn, frozenset(params) | COMMON_PARAMS)
        return fn

    return deco


def slot_types() -> list[str]:
    return sorted(_EXTRACTORS)


def check_slot(name: str, spec: Any) -> list[str]:
    """检查一个槽位声明，返回问题列表（空表示合法）。"""
    if not isinstance(spec, dict) or "type" not in spec:
        return [f"槽位 {name} 须为带 type 的对象（R11.5）"]
    entry = _EXTRACTORS.get(str(spec["type"]))
    if entry is None:
        return [f"槽位 {name} 的类型 {spec['type']!r} 不存在，可用：{', '.join(slot_types())}（R11.5）"]
    unknown = sorted(set(spec) - entry[1])
    problems = [f"槽位 {name} 不认识参数 {', '.join(unknown)}（R11.5）"] if unknown else []
    for key in ("pattern", "id"):
        if isinstance(spec.get(key), str):
            try:
                re.compile(spec[key])
            except re.error as e:
                problems.append(f"槽位 {name} 的 {key} 不是合法正则：{e}（R11.5）")
    return problems


def extract(spec: dict[str, Any], text: str, today: date) -> Any:
    """按声明提取一个槽位；没有提取到时返回 default（未声明为 None）。"""
    fn, _ = _EXTRACTORS[str(spec["type"])]
    value = fn(text, spec, today)
    return spec.get("default") if value is None else value


def to_number(s: str) -> float:
    return float(s) if re.match(r"^[0-9.]+$", s) else float(_CN.get(s, 0))


def _alt(words: Any) -> str:
    """词表 → 正则分支（按字面匹配）。"""
    if isinstance(words, str):
        words = [words]
    return "|".join(re.escape(str(w)) for w in words or [])


# ---------------------------------------------------------------- 内置类型


@extractor("choice", {"options"})
def _choice(text: str, spec: dict[str, Any], today: date) -> Any:
    """options: {取值: [触发词…]}，按声明顺序取第一个命中的取值。"""
    for value, words in (spec.get("options") or {}).items():
        if any(str(w) in text for w in ([words] if isinstance(words, str) else words or [])):
            return value
    return None


@extractor("flag", {"any"})
def _flag(text: str, spec: dict[str, Any], today: date) -> Any:
    """any: [触发词…]，命中任意一个为 true，否则为 false。"""
    return any(str(w) in text for w in spec.get("any") or [])


@extractor("date")
def _date(text: str, spec: dict[str, Any], today: date) -> Any:
    """ISO 日期或“今天 / 明天 / 后天 / 昨天”；都没有时为今天（今天按 AGENT_TZ 计算）。"""
    m = re.search(r"(20\d{2}-\d{2}-\d{2})", text)
    if m:
        return m.group(1)
    for words, delta in ((("后天",), 2), (("明天", "明日"), 1), (("昨天",), -1)):
        if any(w in text for w in words):
            return (today + timedelta(days=delta)).isoformat()
    return today.isoformat()


@extractor("number", {"before", "unit", "loose", "decimal"})
def _number(text: str, spec: dict[str, Any], today: date) -> Any:
    """before 前导词 + 数字 + unit 单位，如“不超过 8 人”；loose 为 true 时也接受没有前导词的“5 组”。

    数字可以是阿拉伯数字或一到十的汉字；数字和单位之间可以有量词“个”。默认取整，decimal 为 true 时保留小数。
    """
    unit = _alt(spec.get("unit"))
    tail = rf"\s*个?\s*(?:{unit})" if unit else ""
    patterns = []
    if spec.get("before"):
        patterns.append(rf"(?:{_alt(spec['before'])})\s*{NUM}{tail}")
    if spec.get("loose") or not spec.get("before"):
        if tail:
            patterns.append(NUM + tail)
    for p in patterns:
        m = re.search(p, text)
        if m:
            v = to_number(m.group(1))
            return v if spec.get("decimal") else int(v)
    return None


_DURATION_UNITS = {"分钟": 1, "小时": 60, "天": 1440}


@extractor("duration", {"unit", "before", "after"})
def _duration(text: str, spec: dict[str, Any], today: date) -> Any:
    """时长，换算成 unit（min 或 h，默认 min）。before 前导词（“过去 24 小时”），after 后缀词（“2 小时以上”）。"""
    units = "|".join(_DURATION_UNITS)
    patterns = []
    if spec.get("before"):
        patterns.append(rf"(?:{_alt(spec['before'])})\s*{NUM}\s*({units})")
    if spec.get("after"):
        patterns.append(rf"{NUM}\s*({units})(?:{_alt(spec['after'])})")
    for p in patterns:
        m = re.search(p, text)
        if m:
            minutes = to_number(m.group(1)) * _DURATION_UNITS[m.group(2)]
            return int(minutes / 60) if spec.get("unit") == "h" else int(minutes)
    return None


@extractor("pattern", {"pattern", "group", "all"})
def _pattern(text: str, spec: dict[str, Any], today: date) -> Any:
    """正则提取，如司机 ID D\\d{4}。group 取第几个分组（默认整体），all 为 true 时返回全部匹配。"""
    rx, group = re.compile(str(spec.get("pattern") or "")), int(spec.get("group") or 0)
    if spec.get("all"):
        found = [m.group(group) for m in rx.finditer(text)]
        return found or None
    m = rx.search(text)
    return m.group(group) if m else None


@extractor("assignments", {"id", "verbs", "unit", "key"})
def _assignments(text: str, spec: dict[str, Any], today: date) -> Any:
    """“把 D0012 换到第 3 组”这类指派，返回 [{"id": "D0012", "<key>": 3}]（key 默认 group）。"""
    verbs = _alt(spec.get("verbs") or ["换到", "移到", "调到", "放到", "调整到", "改到", "分到"])
    unit = _alt(spec.get("unit") or "组")
    rx = rf"({spec.get('id') or '[A-Za-z0-9_-]+'})[^，。,；;]{{0,8}}?(?:{verbs})\s*第?\s*{NUM}\s*(?:{unit})"
    key = str(spec.get("key") or "group")
    found = [{"id": i, key: int(to_number(n))} for i, n in re.findall(rx, text)]
    return found or None
