"""模型接入的健壮性：失败重试（已开始输出后不重试）、token 用量、按 token 预算裁剪历史。"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from agent.llm import LLMError, OpenAICompatLLM
from agent.runtime import AgentRuntime
from agent.server import Platform
from agent.transcript import estimate_tokens

from helpers import Driver, finished

USAGE = {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150}


def _sse(*chunks: dict) -> StreamingResponse:
    lines = ["data: " + json.dumps(c, ensure_ascii=False) + "\n\n" for c in chunks] + ["data: [DONE]\n\n"]
    return StreamingResponse(iter(lines), media_type="text/event-stream")


def _fake(script: list, seen: list[dict]) -> FastAPI:
    """按顺序回放 script：整数表示返回该状态码，"ok" 表示正常答复，其余按请求体决定。"""
    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def completions(request: Request):
        body = await request.json()
        seen.append(body)
        step = script.pop(0) if script else "ok"
        if isinstance(step, int):
            headers = {"Retry-After": "0"} if step == 429 else {}
            return JSONResponse({"error": {"message": "busy"}}, status_code=step, headers=headers)
        if step == "no_stream_options" and "stream_options" in body:
            return JSONResponse({"error": {"message": "Unrecognized request argument supplied: stream_options"}}, status_code=400)
        if body.get("stream"):
            tail = [{"choices": [], "usage": USAGE}] if "stream_options" in body else []
            return _sse({"choices": [{"delta": {"content": "你好"}}]}, {"choices": [{"delta": {"content": "，世界"}}]}, *tail)
        return {"choices": [{"message": {"role": "assistant", "content": "你好，世界"}}], "usage": USAGE}

    return app


def _llm(settings, transport: httpx.AsyncBaseTransport, stream: bool = True) -> OpenAICompatLLM:
    settings.llm_base_url, settings.llm_model, settings.llm_stream = "http://llm.test/v1", "fake-model", stream
    llm = OpenAICompatLLM(settings, httpx.AsyncClient(transport=transport))
    llm.retry_base_s = 0  # 测试不等待
    return llm


async def _collect(llm: OpenAICompatLLM, stream: bool = True) -> tuple[dict, list[str]]:
    deltas: list[str] = []

    async def on_delta(kind: str, piece: str) -> None:
        deltas.append(piece)

    reply = await llm.complete([{"role": "user", "content": "hi"}], [], on_delta=on_delta if stream else None)
    return reply, deltas


@pytest.mark.parametrize("stream", [True, False])
def test_transient_failures_are_retried(settings, stream):
    async def body():
        seen: list[dict] = []
        llm = _llm(settings, httpx.ASGITransport(app=_fake([503, 429], seen)), stream)
        reply, deltas = await _collect(llm, stream)
        assert reply["content"] == "你好，世界" and len(seen) == 3  # 503、429 之后第三次成功
        assert reply["usage"] == USAGE
        assert deltas == (["你好", "，世界"] if stream else [])  # 重试发生在输出之前，文字不重复

    asyncio.run(body())


def test_retries_are_bounded_and_client_errors_are_not_retried(settings):
    async def body():
        seen: list[dict] = []
        llm = _llm(settings, httpx.ASGITransport(app=_fake([503, 503, 503, 503], seen)))
        with pytest.raises(LLMError, match="503.*已重试 2 次"):
            await _collect(llm)
        assert len(seen) == 3  # 1 次 + LLM_MAX_RETRIES（默认 2）

        seen.clear()
        llm = _llm(settings, httpx.ASGITransport(app=_fake([401], seen)))
        with pytest.raises(LLMError, match="401"):
            await _collect(llm)
        assert len(seen) == 1  # 密钥错误之类重试也没用

        settings.llm_max_retries = 0
        seen.clear()
        llm = _llm(settings, httpx.ASGITransport(app=_fake([503], seen)))
        with pytest.raises(LLMError):
            await _collect(llm)
        assert len(seen) == 1

    asyncio.run(body())


class _Flaky(httpx.AsyncBaseTransport):
    """第一次请求：connect_error 为真时连不上；否则先吐一块文字再断开。之后的请求正常。"""

    def __init__(self, connect_error: bool):
        self.connect_error = connect_error
        self.requests = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        first = self.requests == 1
        if first and self.connect_error:
            raise httpx.ConnectError("connection refused", request=request)

        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield 'data: {"choices":[{"delta":{"content":"你好"}}]}\n\n'.encode()
                if first:
                    raise httpx.ReadError("connection reset", request=request)
                yield b"data: [DONE]\n\n"

        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Body())


def test_no_retry_once_output_has_started(settings):
    async def body():
        # 还没输出就连不上：重试，用户无感
        flaky = _Flaky(connect_error=True)
        reply, deltas = await _collect(_llm(settings, flaky))
        assert reply["content"] == "你好" and flaky.requests == 2 and deltas == ["你好"]

        # 已经输出了文字再断开：不重试（重发会让工作台上的文字重复），如实报错
        flaky = _Flaky(connect_error=False)
        llm = _llm(settings, flaky)
        deltas: list[str] = []

        async def on_delta(kind: str, piece: str) -> None:
            deltas.append(piece)

        with pytest.raises(LLMError, match="模型输出中断"):
            await llm.complete([{"role": "user", "content": "hi"}], [], on_delta=on_delta)
        assert flaky.requests == 1 and deltas == ["你好"]

    asyncio.run(body())


def test_stream_options_fallback_keeps_streaming(settings):
    async def body():
        seen: list[dict] = []
        llm = _llm(settings, httpx.ASGITransport(app=_fake(["no_stream_options"], seen)))
        reply, deltas = await _collect(llm)
        assert reply["content"] == "你好，世界" and deltas == ["你好", "，世界"] and "usage" not in reply
        assert "stream_options" in seen[0] and "stream_options" not in seen[1] and seen[1]["stream"] is True
        await _collect(llm)
        assert "stream_options" not in seen[2] and seen[2]["stream"] is True  # 本进程内记住，不再试

    asyncio.run(body())


def test_run_reports_token_usage(settings, transport):
    class Stub:
        label = "stub"

        async def complete(self, messages, tools, on_delta=None):
            if messages[-1]["role"] == "user":
                call = {"id": "call_1", "type": "function", "function": {"name": "fleet__list_regions", "arguments": "{}"}}
                return {"role": "assistant", "content": "", "tool_calls": [call], "usage": USAGE}
            return {"role": "assistant", "content": "完成", "usage": USAGE}

    async def body():
        p = Platform(settings, transport)
        p.runtime.llm = Stub()
        events = await Driver(p).say("有哪些区域")
        assert finished(events)["usage"] == {"prompt_tokens": 240, "completion_tokens": 60, "total_tokens": 300, "llm_calls": 2}
        # 规则模式没有用量：字段不出现
        p2 = Platform(settings, transport)
        assert "usage" not in finished(await Driver(p2).say("你好"))
        await p.http.aclose()
        await p2.http.aclose()

    asyncio.run(body())


def test_history_is_trimmed_by_token_budget():
    assert estimate_tokens([{"role": "user", "content": "你好世界"}]) == 8  # 4 个汉字 + 4 结构开销
    assert estimate_tokens([{"role": "user", "content": "a" * 40}]) == 14

    def turn(n: int) -> list[dict]:
        return [
            {"role": "user", "content": f"第 {n} 轮"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": f"c{n}", "function": {"name": "t", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": f"c{n}", "content": "数" * 1000},
            {"role": "assistant", "content": "好"},
        ]

    hist = turn(1) + turn(2) + turn(3)
    assert AgentRuntime._trimmed(hist, 100_000) == hist
    # 预算只够两轮：从最早的一轮开始丢，只在用户消息处下刀，工具调用和结果不拆开
    kept = AgentRuntime._trimmed(hist, 2200)
    assert kept == turn(2) + turn(3) and kept[0]["role"] == "user"
    # 当前这一轮自己就超出预算：整轮保留
    assert AgentRuntime._trimmed(hist, 10) == turn(3)
    assert AgentRuntime._trimmed(hist, 0) == hist  # 0 表示不按长度裁剪
