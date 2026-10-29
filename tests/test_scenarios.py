"""端到端场景：运行时 + 网关 + 模拟系统 + 模拟界面页面，分别用规则规划器和 OpenAI 兼容接口（流式 / 非流式）驱动。"""
from __future__ import annotations

import asyncio
import json
from datetime import date

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from agent.llm import OpenAICompatLLM
from agent.rule_planner import RulePlanner
from agent.server import Platform

from helpers import (
    REGROUP,
    Driver,
    FakePage,
    dispatch_version,
    final_text,
    finished,
    tools_called,
)


def test_regroup_with_map_and_writeback(settings, transport):
    async def body():
        p = Platform(settings, transport)
        page = FakePage(p)
        d = Driver(p)
        v0 = await dispatch_version(p)
        events = await d.say(REGROUP, confirm=True)
        assert tools_called(events) == [
            "tms__search_shift_drivers", "fleet__get_driver_positions", "analytics__run_grouping",
            "fleet__show_result_layer", "canvas__show", "dispatch__preview_regroup", "dispatch__apply_regroup",
        ]
        assert all(e.get("ok") for e in events if e["type"] == "tool_call_finished")
        assert all(e["conversation_id"] == d.conv.id for e in events)
        started = [e for e in events if e["type"] == "tool_call_started"]
        assert started[0]["system_title"] == "排班系统" and started[0]["color"] == "#7C3AED"
        assert started[2]["system"] == "analytics" and started[2]["title"] == "空间分组"
        assert [t for t, _ in page.calls] == ["show_result_layer"]
        assert any(e["type"] == "render" and e["run_id"] for e in events)
        confirm = next(e for e in events if e["type"] == "confirm_request")
        assert confirm["changes"] and "from_group" in confirm["columns"] and confirm["title"]
        assert await dispatch_version(p) == v0 + 1
        assert "已提交" in final_text(events)
        # 流式输出：增量拼起来等于最终答复
        deltas = "".join(e["delta"] for e in events if e["type"] == "text_delta")
        assert deltas == final_text(events)
        assert finished(events)["status"] == "completed" and finished(events)["suggestions"]
        # 会话事件已保存（增量不保存）
        assert not any(e["type"] == "text_delta" for e in d.conv.events)
        assert [e["type"] for e in d.conv.events][0] == "run_started"

        # 微调后再同步：沿用上一轮方案，D0012 固定到第 3 组；这次拒绝确认
        events2 = await d.say("把 D0012 换到第 3 组，然后同步到调度系统", confirm=False)
        grouping = next(e for e in events2 if e["type"] == "tool_call_started" and e["tool"] == "analytics__run_grouping")
        assert grouping["args"]["pin"] == [{"id": "D0012", "group": 3}] and grouping["args"]["base_plan"]
        assert "未做任何修改" in final_text(events2)
        assert await dispatch_version(p) == v0 + 1  # 拒绝后未写入
        await p.http.aclose()

    asyncio.run(body())


def test_canvas_fallback_when_ui_closed(settings, transport):
    async def body():
        p = Platform(settings, transport)  # 没有地图页面连接
        events = await Driver(p).say("把华东区明天早班的司机分成 5 组，每组不超过 8 人")
        called = tools_called(events)
        assert "fleet__show_result_layer" not in called and "canvas__show" in called
        assert "dispatch__preview_regroup" not in called  # 没要求同步就不写
        await p.http.aclose()

    asyncio.run(body())


def test_stops_and_track(settings, transport):
    async def body():
        p = Platform(settings, transport)
        page = FakePage(p)
        d = Driver(p)
        events = await d.say("在地图上显示过去 24 小时停留超过 2 小时的挂车，按客户着色")
        assert tools_called(events) == ["fleet__search_stops", "results__stats", "fleet__show_result_layer"]
        stops_call = next(e for e in events if e["type"] == "tool_call_started")
        assert stops_call["args"] == {"lookback_h": 24, "min_duration_min": 120}
        assert "按 customer" in final_text(events)
        await d.say("回放司机 D0012 今天的轨迹")
        assert page.calls[-1][0] == "play_track" and page.calls[-1][1]["driver_id"] == "D0012"
        assert abs((date.fromisoformat(page.calls[-1][1]["date"]) - date.today()).days) <= 1
        await p.http.aclose()

    asyncio.run(body())


def test_stop_while_waiting_for_confirmation(settings, transport):
    """停止任务：未确认的写操作不执行，确认卡片标记为已停止，历史补齐后可以继续对话。"""

    async def body():
        p = Platform(settings, transport)
        FakePage(p)
        d = Driver(p)
        v0 = await dispatch_version(p)

        async def stop_on_confirm(event):
            if event["type"] == "confirm_request":
                p.runtime.stop(p.runtime.find(event["conversation_id"]))

        d.on_event = stop_on_confirm
        events = await d.say(REGROUP, confirm=None)
        resolved = next(e for e in events if e["type"] == "confirm_resolved")
        assert resolved["approved"] is False and resolved["reason"] == "stopped"
        apply_done = next(e for e in events if e["type"] == "tool_call_finished" and e["tool"] == "dispatch__apply_regroup")
        assert apply_done["error_code"] == "CANCELLED"
        assert finished(events)["status"] == "stopped" and finished(events)["suggestions"] == []
        assert await dispatch_version(p) == v0
        hist = d.conv.history
        assert hist[-1]["role"] == "assistant" and "停止" in hist[-1]["content"]
        tool_ids = {m["tool_call_id"] for m in hist if m["role"] == "tool"}
        call_ids = {c["id"] for m in hist if m.get("tool_calls") for c in m["tool_calls"]}
        assert call_ids == tool_ids  # 每个工具调用都有结果
        audit = [e for e in p.audit.tail() if e["tool"] == "dispatch__apply_regroup"]
        assert audit and audit[0]["error_code"] == "CANCELLED" and audit[0]["confirmed"] is None

        d.on_event = None
        events2 = await d.say("同步到调度系统", confirm=True)  # 停止后继续对话
        assert finished(events2)["status"] == "completed"
        assert await dispatch_version(p) == v0 + 1
        await p.http.aclose()

    asyncio.run(body())


def test_shutdown_marks_running_task_interrupted(settings, transport):
    """服务关闭时正在等确认的任务：记为“中断”（不是“用户停止”），已保存，重启后工作台如实显示。"""

    async def body():
        p = Platform(settings, transport)
        FakePage(p)
        d = Driver(p)
        v0 = await dispatch_version(p)
        await p.gateway.ensure_loaded(force=True)
        d.conv = p.conversations.create("local", "关闭测试")
        sess = p.runtime.session(d.conv)
        assert p.runtime.start(sess, REGROUP, d.client_id)
        d.confirm = None
        while not sess.pending_confirm:
            await asyncio.sleep(0.01)
        await p.runtime.shutdown()
        resolved = next(e for e in d.events if e["type"] == "confirm_resolved")
        assert resolved["reason"] == "interrupted" and resolved["approved"] is False
        assert finished(d.events)["status"] == "interrupted"
        assert await dispatch_version(p) == v0
        assert "服务重启" in d.conv.history[-1]["content"]
        saved = json.loads((settings.data_dir / "conversations" / f"{d.conv.id}.json").read_text(encoding="utf-8"))
        assert [e["type"] for e in saved["events"]][-1] == "run_finished"
        await p.http.aclose()

    asyncio.run(body())


def test_regenerate_replaces_last_run(settings, transport):
    async def body():
        p = Platform(settings, transport)
        FakePage(p)
        d = Driver(p)
        await d.say("在地图上显示过去 24 小时停留超过 2 小时的挂车，按客户着色")
        first_run = d.conv.events[0]["run_id"]
        sess = p.runtime.find(d.conv.id)
        start = len(d.events)
        assert await p.runtime.regenerate(sess, d.client_id)
        await sess.task
        new = d.events[start:]
        assert new[0] == {"type": "run_removed", "run_id": first_run, "conversation_id": d.conv.id}
        runs = {e["run_id"] for e in d.conv.events}
        assert first_run not in runs and len(runs) == 1
        assert sum(1 for m in d.conv.history if m["role"] == "user") == 1
        await p.http.aclose()

    asyncio.run(body())


def _fake_llm_app(planner: RulePlanner, seen: list[dict]) -> FastAPI:
    """假的 OpenAI 兼容接口：决策仍由规则规划器完成，按请求返回 SSE 流或完整 JSON。"""
    fake = FastAPI()

    @fake.post("/v1/chat/completions")
    async def completions(request: Request):
        body = await request.json()
        seen.append(body)
        assert request.headers["authorization"] == "Bearer sk-test"
        assert body["model"] == "fake-model" and body["tool_choice"] == "auto"
        for t in body["tools"]:
            assert t["type"] == "function" and set(t["function"]) == {"name", "description", "parameters"}
        assert body["messages"][0]["role"] == "system"
        for i, m in enumerate(body["messages"]):
            if m["role"] == "tool":  # 工具结果必须对应前面 assistant 消息里声明的调用
                prev = [x for x in body["messages"][:i] if x["role"] == "assistant" and x.get("tool_calls")][-1]
                assert m["tool_call_id"] in {c["id"] for c in prev["tool_calls"]}
                json.loads(m["content"])
            assert "reasoning" not in m and "suggestions" not in m
        msg = await planner.complete(body["messages"], body["tools"])
        msg.pop("suggestions", None)
        if not body.get("stream"):
            return {"id": "x", "object": "chat.completion", "choices": [{"index": 0, "message": msg, "finish_reason": "stop"}]}

        def sse():
            def chunk(delta):
                return "data: " + json.dumps({"choices": [{"index": 0, "delta": delta}]}, ensure_ascii=False) + "\n\n"

            yield chunk({"role": "assistant", "reasoning_content": "先想一想。"})
            text = msg.get("content") or ""
            for i in range(0, len(text), 5):
                yield chunk({"content": text[i : i + 5]})
            for idx, tc in enumerate(msg.get("tool_calls") or []):
                args = tc["function"]["arguments"]
                half = len(args) // 2
                yield chunk({"tool_calls": [{"index": idx, "id": tc["id"], "type": "function",
                                             "function": {"name": tc["function"]["name"], "arguments": args[:half]}}]})
                yield chunk({"tool_calls": [{"index": idx, "function": {"arguments": args[half:]}}]})
            yield "data: [DONE]\n\n"

        return StreamingResponse(sse(), media_type="text/event-stream")

    return fake


def _run_with_fake_llm(settings, transport, stream: bool):
    planner = RulePlanner(settings)
    seen: list[dict] = []

    async def body():
        transport.routes["llm.test:9000"] = httpx.ASGITransport(app=_fake_llm_app(planner, seen))
        settings.llm_base_url, settings.llm_model, settings.llm_api_key = "http://llm.test:9000/v1", "fake-model", "sk-test"
        settings.llm_stream = stream
        p = Platform(settings, transport)
        assert isinstance(p.llm, OpenAICompatLLM)
        FakePage(p)
        events = await Driver(p).say(REGROUP, confirm=True)
        assert "已提交" in final_text(events)
        assert len(seen) == 7  # 6 轮工具调用（展示那一轮并行调用 2 个工具）+ 最终答复
        assert all(bool(b.get("stream")) is stream for b in seen)
        assert "fleet__show_result_layer" in {t["function"]["name"] for t in seen[0]["tools"]}
        await p.http.aclose()
        return events, seen

    return asyncio.run(body())


def test_openai_compatible_streaming(settings, transport):
    events, _ = _run_with_fake_llm(settings, transport, stream=True)
    deltas = "".join(e["delta"] for e in events if e["type"] == "text_delta")
    assert deltas == final_text(events)
    assert any(e["type"] == "reasoning_delta" for e in events)
    message = next(e for e in events if e["type"] == "message")
    assert message["reasoning"] == "先想一想。"
    # 分块到达的工具调用参数被正确拼接
    grouping = next(e for e in events if e["type"] == "tool_call_started" and e["tool"] == "analytics__run_grouping")
    assert grouping["args"]["groups"] == 5 and grouping["args"]["max_size"] == 8


def test_openai_compatible_non_streaming(settings, transport):
    settings.timezone = "Asia/Tokyo"
    events, seen = _run_with_fake_llm(settings, transport, stream=False)
    assert not any(e["type"] == "text_delta" for e in events)
    # 模型模式的系统提示词按 AGENT_TZ 给出“现在”，不再固定东八区
    system = seen[0]["messages"][0]
    assert system["role"] == "system" and "Asia/Tokyo，UTC+0900" in system["content"]
