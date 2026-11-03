"""意图识别与场景剧本：槽位提取、识别排序、剧本校验规则（R11.x）、模板渲染。"""
from __future__ import annotations

import textwrap
from datetime import date

import pytest

from agent.intents import IntentLibrary
from agent.manifest import ManifestError, load_manifest, load_manifests
from agent.playbook import render, render_args
from agent.slots import extract
from agent.transcript import Transcript

TODAY = date(2026, 11, 3)


@pytest.fixture
def library(settings) -> IntentLibrary:
    return IntentLibrary.load(settings)


# ---------------------------------------------------------------- 槽位


@pytest.mark.parametrize(
    "spec, text, expected",
    [
        ({"type": "number", "before": ["分成"], "unit": ["组"], "loose": True}, "把司机分成五组", 5),
        ({"type": "number", "before": ["分成"], "unit": ["组"], "loose": True}, "要 3 个组", 3),
        ({"type": "number", "before": ["不超过", "最多"], "unit": ["人", "个"]}, "每组最多 8 个人", 8),
        ({"type": "number", "before": ["不超过"], "unit": ["人"]}, "分成 5 组", None),
        ({"type": "duration", "unit": "h", "before": ["过去", "最近"]}, "最近 2 天停留的挂车", 48),
        ({"type": "duration", "unit": "min", "before": ["超过"], "after": ["以上"]}, "停了 30 分钟以上", 30),
        ({"type": "duration", "unit": "min", "before": ["超过"]}, "停留超过 1.5 小时", 90),
        ({"type": "date"}, "后天早班", "2026-11-05"),
        ({"type": "date"}, "2026-12-01 的排班", "2026-12-01"),
        ({"type": "date"}, "现在的排班", "2026-11-03"),
        ({"type": "choice", "options": {"north-china": ["华北", "北京"]}, "default": "east-china"}, "北京的司机", "north-china"),
        ({"type": "choice", "options": {"north-china": ["华北", "北京"]}, "default": "east-china"}, "司机", "east-china"),
        ({"type": "flag", "any": ["同步", "写回"]}, "然后写回", True),
        ({"type": "pattern", "pattern": r"D\d{4}", "all": True}, "D0001 和 D0002", ["D0001", "D0002"]),
        (
            {"type": "assignments", "id": r"D\d{4}"},
            "把 D0012 换到第 3 组，D0005 调到第二组",
            [{"id": "D0012", "group": 3}, {"id": "D0005", "group": 2}],
        ),
    ],
)
def test_slot_extractors(spec, text, expected):
    assert extract(spec, text, TODAY) == expected


# ---------------------------------------------------------------- 识别


@pytest.mark.parametrize(
    "text, intent, slots",
    [
        (
            "把华东区明天早班的司机按当前位置重新分成 5 组，每组不超过 8 人，先给我看地图和名单，确认后同步到调度系统",
            "regroup",
            {"region": "east-china", "shift": "early", "date": "2026-11-04", "groups": 5, "max_size": 8, "sync": True},
        ),
        ("把北京晚班的司机分成四组", "regroup", {"region": "north-china", "shift": "late", "groups": 4, "max_size": None, "sync": False}),
        ("把 D0012 换到第 3 组，然后同步到调度系统", "adjust", {"pins": [{"id": "D0012", "group": 3}], "sync": True}),
        ("同步到调度系统", "sync", {}),
        ("在地图上显示过去 24 小时停留超过 2 小时的挂车，按客户着色", "stops", {"lookback_h": 24, "min_duration_min": 120}),
        ("回放司机 D0012 今天的轨迹", "track", {"driver": "D0012", "date": "2026-11-03"}),
        ("在地图上定位 D0012", "locate", {"driver": "D0012"}),
    ],
)
def test_classify_sample_prompts(library, text, intent, slots):
    matches = library.classify(text, today=TODAY)
    assert matches and matches[0].intent.id == intent
    assert slots.items() <= matches[0].slots.items()


def test_classify_ranking_and_fallback(library):
    # 重组的原话里也有“同步”：优先级高的意图排在前面，其余作为“其他可能”
    matches = library.classify("把司机重新分成 5 组并同步到调度系统", today=TODAY)
    assert [m.intent.id for m in matches] == ["regroup", "sync"]
    assert matches[0].hits == ["分成"]
    # 没有司机 ID 时不算“定位”（match.slots）
    assert library.classify("司机现在在哪", today=TODAY) == []
    assert library.classify("介绍一下你能做什么", today=TODAY) == []


def test_from_call_slot_uses_history(library):
    history = [
        {"role": "user", "content": "把北京早班的司机分成 5 组"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "tms__search_shift_drivers", "arguments": '{"region": "north-china"}'}}
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": '{"ok": true}'},
        {"role": "assistant", "content": "好的"},
        {"role": "user", "content": "同步到调度系统"},
    ]
    t = Transcript(history)
    assert t.recent_systems() == ["tms"]
    match = library.classify(t.text, today=TODAY, transcript=t)[0]
    assert match.intent.id == "sync" and match.slots["region"] == "north-china"  # 沿用上一轮的区域，而不是默认华东


def test_help_and_presets(library):
    help_text = library.help_text()
    assert "规则模式" in help_text and "回放司机 D0012 今天的轨迹" in help_text
    presets = library.presets()
    # 只列可单独发起的场景（微调、同步依赖已有方案），按清单加载顺序
    assert [p["id"] for p in presets] == ["regroup", "stops", "track", "locate"]
    assert presets[0]["systems"][0] == "tms" and "dispatch" in presets[0]["systems"]


# ---------------------------------------------------------------- 模板


def test_template_rendering():
    facts = {"plan": {"result_id": "res_1", "data": {"params": {"groups": 4}}}, "cap": None, "flag": True}
    assert render("方案 `{plan.result_id}`，{plan.data.params.groups} 组", facts) == "方案 `res_1`，4 组"
    assert render("{missing|plan.result_id}", facts) == "res_1"
    assert render("{flag}/{missing}", facts) == "是/"
    args = render_args({"groups": "{plan.data.params.groups|5}", "max_size": "{cap}", "title": "第 {plan.data.params.groups} 组", "n": "{nope|5}"}, facts)
    assert args == {"groups": 4, "title": "第 4 组", "n": 5}  # 整段占位符保留类型，空值参数不传


# ---------------------------------------------------------------- 剧本校验（R11.x）

MANIFEST = """
manifest_version: 1
system: sample
title: 样例系统
base_url: http://localhost:9999
api:
  openapi: http://localhost:9999/openapi.json
  expose:
    - {operation: getX, tool: get_x, description: 查询某个对象的详情}
    - {operation: previewX, tool: preview_x, pairs_with: apply_x, description: 预览变更，不落库}
    - {operation: applyX, tool: apply_x, risk: write, description: 提交变更到系统落库}
    - {operation: previewY, tool: preview_y, description: 另一个预览接口，不落库}
ui_bridge:
  enabled: true
  tools: [{tool: show_layer, risk: view}]
playbooks: playbooks.yaml
"""

GOOD_INTENT = """
- id: lookup
  title: 查询对象
  examples: [查一下 X1]
  match: {any: [查一下]}
  slots:
    obj: {type: pattern, pattern: "X\\\\d"}
  steps:
    - id: found
      call: sample__get_x
      args: {id: "{obj}"}
    - id: show
      show: {result: found, ui: [sample__show_layer]}
  answer: "{found.summary}"
"""


def _write(tmp_path, intents: str, manifest: str = MANIFEST):
    (tmp_path / "playbooks.yaml").write_text("intents:\n" + textwrap.indent(textwrap.dedent(intents), "  "), encoding="utf-8")
    p = tmp_path / "manifest.yaml"
    p.write_text(textwrap.dedent(manifest), encoding="utf-8")
    return p


def test_playbook_ok(tmp_path):
    m = load_manifest(_write(tmp_path, GOOD_INTENT))
    assert [i.id for i in m.intents] == ["lookup"]
    lib = IntentLibrary([m])
    assert lib.intents[0].systems() == ["sample", "canvas"] and lib.warnings() == []


@pytest.mark.parametrize(
    "change, rule",
    [
        (("id: lookup", "id: Lookup"), "R11.1"),
        (("match: {any: [查一下]}", "match: {}"), "R11.2"),
        (("match: {any: [查一下]}", "match: {any: ['re:(']}"), "R11.2"),
        (("call: sample__get_x", "call: sample__getx!"), "R11.3"),
        (("ui: [sample__show_layer]", "ui: [sample__hide_layer]"), "R11.3"),
        (("{type: pattern,", "{type: regex,"), "R11.5"),
        (("{type: pattern,", "{type: pattern, bogus: 1,"), "R11.5"),
        (('answer: "{found.summary}"', 'answer: "{fonud.summary}"'), "R11.4"),
        (("call: sample__get_x", "call: sample__apply_x"), "R11.6"),
        (
            ("    - id: show\n      show: {result: found, ui: [sample__show_layer]}",
             "    - id: show\n      write: {preview: sample__preview_y, apply: sample__apply_x}"),
            "R11.6",
        ),
        (
            ("    - id: show\n      show: {result: found, ui: [sample__show_layer]}",
             "    - id: show\n      write: {preview: sample__preview_x, apply: sample__apply_x, args: {confirm_token: abc}}"),
            "R11.6",
        ),
    ],
)
def test_playbook_rule_violations(tmp_path, change, rule):
    old, new = change
    assert old in GOOD_INTENT
    with pytest.raises(ManifestError) as e:
        load_manifest(_write(tmp_path, GOOD_INTENT.replace(old, new)))
    assert rule in str(e.value)


def test_playbook_missing_file_and_duplicates(tmp_path):
    p = tmp_path / "manifest.yaml"
    p.write_text(textwrap.dedent(MANIFEST), encoding="utf-8")
    with pytest.raises(ManifestError, match="R11.1"):
        load_manifest(p)
    with pytest.raises(ManifestError, match="R11.1"):
        load_manifest(_write(tmp_path, GOOD_INTENT + GOOD_INTENT))


def test_intent_referencing_missing_system_is_unavailable(tmp_path):
    """引用了未接入系统的意图不报错，只是不可用（不参与识别、不列为常用任务），check 会提醒。"""
    intent = GOOD_INTENT.replace("call: sample__get_x", "call: other__get_y") + "  suggestions: ['{examples.nowhere}']\n"
    m = load_manifests(str(_write(tmp_path, intent)))[0]
    lib = IntentLibrary([m])
    assert lib.intents[0].unavailable and lib.active() == [] and lib.presets() == []
    assert lib.classify("查一下 X1", today=TODAY) == []
    warnings = "\n".join(lib.warnings())
    assert "other" in warnings and "examples.nowhere" in warnings
