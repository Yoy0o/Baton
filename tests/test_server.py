"""HTTP / WebSocket 层：配置、工具目录、桥接注册白名单、工作台对话协议、会话 API、反馈、界面直连、MCP 调试端点。"""
from __future__ import annotations

import threading
import time

from fastapi.testclient import TestClient

from agent.server import create_app

CLIENT = "wb_conv01"


def _client(settings, transport) -> TestClient:
    return TestClient(create_app(settings, transport))


def _run_until_finished(wb, approve: bool | None = True, timeout: float = 20) -> list[dict]:
    seen, deadline = [], time.time() + timeout
    while time.time() < deadline:
        ev = wb.receive_json()
        seen.append(ev)
        if ev["type"] == "confirm_request" and approve is not None:
            wb.send_json({"type": "confirm_decision", "conversation_id": ev["conversation_id"],
                          "confirm_id": ev["confirm_id"], "approved": approve})
        if ev["type"] == "run_finished":
            return seen
    raise AssertionError("run did not finish")


def test_config_and_tools(settings, transport):
    with _client(settings, transport) as c:
        cfg = c.get("/api/config").json()
        assert cfg["mode"] == "rule" and cfg["load_errors"] == {}
        assert cfg["app"]["name"] == "Baton"
        fleet = next(s for s in cfg["systems"] if s["system"] == "fleet")
        assert fleet["embed_url"].endswith("/map") and fleet["color"] == "#2563EB"
        assert {"tool": "play_track", "title": "轨迹回放"} in fleet["ui_tools"]
        assert cfg["systems_meta"]["analytics"]["builtin"] is True
        tools = {t["name"]: t for t in c.get("/api/tools").json()["tools"]}
        assert tools["dispatch__apply_regroup"]["risk"] == "write"
        assert tools["tms__search_shift_drivers"]["system_title"] == "排班系统"
        assert c.get("/", follow_redirects=False).status_code in (302, 307)
        # 前端未构建时给出构建提示
        page = c.get("/workbench/")
        assert page.status_code == 503 and "npm run build" in page.text


def test_bridge_whitelist_and_ui_call(settings, transport):
    with _client(settings, transport) as c:
        with c.websocket_connect(f"/ws/bridge?system=fleet&client={CLIENT}&page=/map") as page:
            page.send_json(
                {
                    "type": "register",
                    "tools": [
                        {"name": "show_result_layer", "title": "页面自带标题", "description": "叠加结果图层",
                         "inputSchema": {"type": "object", "properties": {"result_id": {"type": "string"}}, "required": ["result_id"]}},
                        {"name": "delete_everything", "description": "不在白名单里的工具"},
                    ],
                }
            )
            reg = page.receive_json()
            assert reg["accepted"] == ["show_result_layer"]
            assert reg["rejected"][0]["tool"] == "delete_everything" and "R5.2" in reg["rejected"][0]["reason"]
            tools = {t["name"]: t for t in c.get(f"/api/tools?client={CLIENT}").json()["tools"]}
            assert "fleet__show_result_layer" in tools and "fleet__delete_everything" not in tools
            assert tools["fleet__show_result_layer"]["title"] == "叠加结果图层"  # 清单里的标题优先

            def respond():
                msg = page.receive_json()
                assert msg["type"] == "call" and msg["tool"] == "show_result_layer"
                page.send_json({"type": "result", "call_id": msg["call_id"], "result": {"ok": True, "summary": "已叠加"}})

            # 用户在工作台直接调用界面工具（不经过模型）
            t = threading.Thread(target=respond)
            t.start()
            r = c.post("/api/ui-actions", json={"client_id": CLIENT, "tool": "fleet__show_result_layer", "args": {"result_id": "res_any"}}).json()
            t.join(timeout=5)
            assert r["ok"] is True and r["summary"] == "已叠加"
            audit = c.get("/api/audit").json()["entries"]
            assert audit[0]["tool"] == "fleet__show_result_layer" and audit[0]["initiator"] == "user"

            # 只能直连界面工具
            bad = c.post("/api/ui-actions", json={"client_id": CLIENT, "tool": "dispatch__apply_regroup", "args": {}})
            assert bad.status_code == 404

        # 未启用桥接的系统不能注册
        with c.websocket_connect(f"/ws/bridge?system=tms&client={CLIENT}") as bad:
            assert bad.receive_json()["type"] == "error"


def test_workbench_protocol_and_conversations(settings, transport):
    with _client(settings, transport) as c:
        with c.websocket_connect(f"/ws/workbench?client={CLIENT}") as wb:
            hello = wb.receive_json()
            assert hello["type"] == "hello" and hello["client_id"] == CLIENT
            wb.send_json({"type": "user_message", "conversation_id": None,
                          "text": "把华东区明天早班的司机分成 5 组，每组不超过 8 人，然后同步到调度系统"})
            created = wb.receive_json()
            assert created["type"] == "conversation_created" and created["title"].startswith("把华东区明天早班")
            cid = created["conversation_id"]
            seen = _run_until_finished(wb, approve=True)
            types = [e["type"] for e in seen]
            assert "confirm_request" in types and "render" in types and "text_delta" in types
            assert all(e.get("conversation_id") == cid for e in seen)
            final = next(e["text"] for e in seen if e["type"] == "message")
            assert "已提交" in final
            run_id = seen[0]["run_id"]

            # 列表、详情（事件可重放）、改名、反馈
            items = c.get("/api/conversations").json()["items"]
            assert items[0]["id"] == cid and items[0]["running"] is False
            detail = c.get(f"/api/conversations/{cid}").json()
            assert detail["events"][0]["type"] == "run_started" and detail["events"][-1]["type"] == "run_finished"
            assert not any(e["type"] == "text_delta" for e in detail["events"])
            # 需要重放的事件带递增序号，实时推送的同一事件序号相同（工作台据此去重）
            seqs = [e["seq"] for e in detail["events"]]
            assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
            live = {e["seq"]: e["type"] for e in seen if "seq" in e}
            assert live == {e["seq"]: e["type"] for e in detail["events"]}
            assert not any("seq" in e for e in seen if e["type"] == "text_delta")
            assert c.patch(f"/api/conversations/{cid}", json={"title": "华东早班重组"}).json()["title"] == "华东早班重组"
            assert c.post("/api/feedback", json={"conversation_id": cid, "run_id": run_id, "value": "like"}).json()["ok"]
            assert c.get(f"/api/conversations/{cid}").json()["feedback"] == {run_id: "like"}

            # 停止：在确认卡片出现时停止，调度系统不写入（下面用审计核对）
            wb.send_json({"type": "user_message", "conversation_id": cid, "text": "同步到调度系统"})
            while True:
                ev = wb.receive_json()
                if ev["type"] == "confirm_request":
                    wb.send_json({"type": "stop", "conversation_id": cid})
                if ev["type"] == "run_finished":
                    assert ev["status"] == "stopped"
                    break

            # 重新生成最后一次运行
            wb.send_json({"type": "regenerate", "conversation_id": cid})
            removed = wb.receive_json()
            assert removed["type"] == "run_removed"
            _run_until_finished(wb, approve=False)

            # 未知会话、空操作给出提示
            wb.send_json({"type": "stop", "conversation_id": "c_000000000000"})
            assert wb.receive_json()["type"] == "error"

        audit = c.get(f"/api/audit?conversation_id={cid}").json()["entries"]
        assert any(e["tool"] == "dispatch__apply_regroup" and e["confirmed"] for e in audit)
        assert any(e["tool"] == "dispatch__apply_regroup" and e["error_code"] == "CANCELLED" for e in audit)
        assert c.delete(f"/api/conversations/{cid}").json()["ok"]
        assert c.get(f"/api/conversations/{cid}").status_code == 404


def test_mcp_endpoint(settings, transport):
    with _client(settings, transport) as c:
        init = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}}).json()
        assert init["result"]["serverInfo"]["name"] == "baton-gateway"
        tools = c.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).json()["result"]["tools"]
        stops = next(t for t in tools if t["name"] == "fleet__search_stops")
        assert stops["annotations"]["readOnlyHint"] is True and "inputSchema" in stops
        call = c.post("/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "fleet__search_stops", "arguments": {"lookback_h": 24, "min_duration_min": 120}}}).json()
        sc = call["result"]["structuredContent"]
        assert call["result"]["isError"] is False and sc["result_id"].startswith("res_")
        res = c.get(f"/api/results/{sc['result_id']}?format=geojson").json()
        assert res["type"] == "FeatureCollection" and len(res["features"]) == sc["total"]
        denied = c.post("/mcp", json={"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "dispatch__apply_regroup", "arguments": {"confirm_token": "cf_x"}}}).json()
        assert denied["result"]["isError"] is True
        assert c.post("/mcp", json={"jsonrpc": "2.0", "id": 5, "method": "nope"}).json()["error"]["code"] == -32601
