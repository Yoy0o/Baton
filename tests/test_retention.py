"""数据留存：审计重启后可见并按大小轮转、过期结果清理、过期确认令牌清理。"""
from __future__ import annotations

import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from agent.audit import AuditLog
from agent.contracts import ToolError
from agent.providers import CallContext
from agent.results import ResultStore
from agent.server import Platform, create_app


def test_audit_survives_restart_and_rotates(tmp_path):
    log = AuditLog(tmp_path)
    for i in range(5):
        log.record(tool=f"t{i}", args={"n": i}, ok=True)
    with log.path.open("a", encoding="utf-8") as f:
        f.write('{"ts": 1, "tool": "half')  # 进程被强杀留下的半行

    again = AuditLog(tmp_path, keep=3)
    assert [e["tool"] for e in again.tail()] == ["t4", "t3", "t2"]  # 最近的在前，半行被跳过

    # 超过上限：旧文件改名留存，不删除；最近记录仍在内存里
    small = AuditLog(tmp_path / "rot", rotate_bytes=300)
    for i in range(12):
        small.record(tool=f"t{i}", args={"n": i}, ok=True)
    rotated = sorted(p.name for p in small.path.parent.glob("audit-*.jsonl"))
    assert rotated and small.path.stat().st_size < 600
    lines = [json.loads(x) for p in small.path.parent.glob("audit*.jsonl") for x in p.read_text(encoding="utf-8").splitlines()]
    assert sorted(e["tool"] for e in lines) == sorted(f"t{i}" for i in range(12))  # 一条不丢
    assert [e["tool"] for e in small.tail(2)] == ["t11", "t10"]

    assert AuditLog(tmp_path / "empty").tail() == []


def test_audit_panel_shows_history_after_restart(settings, transport):
    rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "fleet__list_regions", "arguments": {}}}
    with TestClient(create_app(settings, transport)) as c:
        assert c.post("/mcp", json=rpc).json()["result"]["isError"] is False
    with TestClient(create_app(settings, transport)) as c:  # 同一个数据目录，相当于重启
        entries = c.get("/api/audit").json()["entries"]
        assert entries and entries[0]["tool"] == "fleet__list_regions" and entries[0]["initiator"] == "mcp"


def test_expired_results_are_swept(tmp_path):
    store = ResultStore(tmp_path, ttl_s=3600)
    keep = store.put("table", {"rows": [{"a": 1}]}, owner="u", session_id="s1", summary="留着")
    gone = store.put("table", {"rows": [{"a": 2}]}, owner="u", session_id="s1", summary="过期")
    gone_file = store.dir / f"{gone.id}.json"
    raw = json.loads(gone_file.read_text(encoding="utf-8"))
    raw["expires_at"] = time.time() - 1
    gone_file.write_text(json.dumps(raw), encoding="utf-8")
    (store.dir / "res_broken00.json").write_text("{not json", encoding="utf-8")

    # 重启：未过期的读回内存（“本会话已有结果”照常），过期的文件删掉，损坏的文件跳过
    restarted = ResultStore(tmp_path, ttl_s=3600)
    assert [r.id for r in restarted.list_for_session("s1")] == [keep.id]
    assert restarted.get(keep.id, owner="u").summary == "留着"
    assert not gone_file.exists()
    with pytest.raises(ToolError) as missing:
        restarted.get(gone.id)
    assert missing.value.code == "RESULT_NOT_FOUND"

    # 运行中：过期后从内存和磁盘清掉；写入新结果时顺带触发（最多每 10 分钟一次）
    assert restarted.sweep(time.time() + 7200) == 1
    assert restarted.list_for_session("s1") == [] and not (store.dir / f"{keep.id}.json").exists()
    old = restarted.put("table", {"rows": []}, owner="u", session_id="s2", summary="旧")
    restarted._mem[old.id].expires_at = time.time() - 1
    restarted._last_sweep = time.time() - 601
    new = restarted.put("table", {"rows": []}, owner="u", session_id="s2", summary="新")
    assert [r.id for r in restarted.list_for_session("s2")] == [new.id]


def test_expired_confirm_tokens_are_pruned(settings, transport):
    async def body():
        p = Platform(settings, transport)
        await p.gateway.ensure_loaded(force=True)
        ctx = CallContext(user="local", session_id="s1")
        roster = await p.gateway.call(ctx, "tms__search_shift_drivers", {"region": "east-china", "date": "2026-10-28", "shift": "early"})
        points = await p.gateway.call(ctx, "fleet__get_driver_positions", {"driver_ids": roster["result_id"]})
        plan = await p.gateway.call(ctx, "analytics__run_grouping", {"points": points["result_id"], "groups": 5, "max_size": 8})
        args = {"region": "east-china", "groups": plan["result_id"]}
        first = (await p.gateway.call(ctx, "dispatch__preview_regroup", args))["confirm_token"]
        second = (await p.gateway.call(ctx, "dispatch__preview_regroup", args))["confirm_token"]
        p.gateway.confirms[first].expires_at = time.time() - 3601  # 过期已超过一小时
        p.gateway.confirms[second].expires_at = time.time() - 60  # 刚过期：留着，重复提交能得到明确的“已过期”
        third = (await p.gateway.call(ctx, "dispatch__preview_regroup", args))["confirm_token"]
        assert set(p.gateway.confirms) == {second, third}
        late = await p.gateway.call(CallContext(user="local", session_id="s1"), "dispatch__apply_regroup", {"confirm_token": second})
        assert late["error_code"] == "CONFIRM_TOKEN_EXPIRED"
        await p.http.aclose()

    asyncio.run(body())
