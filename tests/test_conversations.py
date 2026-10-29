"""会话存储：增删改查、持久化、重启后中断运行的恢复、模型历史修复。"""
from __future__ import annotations

import json

from agent.conversations import ConversationStore, make_title, repair_history


def test_crud_and_persistence(tmp_path):
    store = ConversationStore(tmp_path)
    a = store.create("local", make_title("把华东区明天早班的司机按当前位置重新分成 5 组，每组不超过 8 人"))
    b = store.create("local", "第二个")
    store.create("other", "别人的会话")
    assert a.title.endswith("…") and len(a.title) == 25
    a.updated_at += 10
    store.save(a)
    assert [c.id for c in store.list("local")] == [a.id, b.id]
    assert store.get(a.id, "other") is None and store.get("../etc/passwd", "local") is None
    assert store.rename(b.id, "local", "  新   标题 ").title == "新 标题"
    reloaded = ConversationStore(tmp_path)
    assert {c.title for c in reloaded.list("local")} == {a.title, "新 标题"}
    assert reloaded.delete(b.id, "local") and reloaded.get(b.id, "local") is None
    assert not (tmp_path / "conversations" / f"{b.id}.json").exists()


def test_recover_interrupted_run(tmp_path):
    store = ConversationStore(tmp_path)
    conv = store.create("local", "中断")
    conv.history = [
        {"role": "user", "content": "同步到调度系统"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function",
                                                               "function": {"name": "dispatch__apply_regroup", "arguments": "{}"}}]},
    ]
    conv.events = [
        {"type": "run_started", "run_id": "run_1", "text": "同步到调度系统"},
        {"type": "confirm_request", "run_id": "run_1", "confirm_id": "cf_1"},
    ]
    store.save(conv)
    again = ConversationStore(tmp_path).get(conv.id, "local")
    types = [e["type"] for e in again.events]
    assert types[-2:] == ["confirm_resolved", "run_finished"]
    assert again.events[-1]["status"] == "interrupted" and again.events[-2]["reason"] == "interrupted"
    assert again.history[-2]["role"] == "tool" and json.loads(again.history[-2]["content"])["error_code"] == "CANCELLED"
    assert again.history[-1] == {"role": "assistant", "content": "（服务重启，本次任务已中断）"}


def test_repair_history_cases():
    h = [{"role": "user", "content": "hi"}]
    repair_history(h, "停")
    assert h[-1] == {"role": "assistant", "content": "停"}
    h = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "好的"}]
    repair_history(h, "停")
    assert len(h) == 2  # 已经以 assistant 收尾，不追加
    h = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "a"}, {"id": "b"}]},
        {"role": "tool", "tool_call_id": "a", "content": "{}"},
    ]
    repair_history(h, "停")
    assert [m["role"] for m in h] == ["user", "assistant", "tool", "tool", "assistant"] and h[3]["tool_call_id"] == "b"


def test_event_seq_is_monotonic(tmp_path):
    """工作台用 seq 去重；撤掉的事件（重新生成）不回收序号，重启恢复补的事件也编号。"""
    store = ConversationStore(tmp_path)
    conv = store.create("local", "序号")
    for t in ("run_started", "message", "run_finished"):
        conv.append_event({"type": t, "run_id": "run_1"})
    assert [e["seq"] for e in conv.events] == [1, 2, 3]
    conv.events = [e for e in conv.events if e["run_id"] != "run_1"]  # 重新生成撤掉上一轮
    conv.append_event({"type": "run_started", "run_id": "run_2"})
    assert conv.events[-1]["seq"] == 4
    store.save(conv)
    again = ConversationStore(tmp_path).get(conv.id, "local")  # run_2 未结束 → 恢复时补 run_finished
    assert [(e["type"], e["seq"]) for e in again.events] == [("run_started", 4), ("run_finished", 5)]
    assert again.seq == 5
