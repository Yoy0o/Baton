"""模型接入：任意 OpenAI 兼容的 Chat Completions 接口（需支持 tools）。

- 默认流式输出（stream=true，SSE），边生成边把文字推给工作台；工具调用参数按块拼接。
- 接口不支持流式时自动退回一次性返回，并在本进程内记住。
- 支持 reasoning_content（如 DeepSeek、通义的思考模式）：只展示，不回传给模型。
- 限流（429）、服务端错误（5xx）、连接失败按 LLM_MAX_RETRIES 退避重试；已经把文字推给工作台之后不重试（否则会重复输出）。
- 接口返回 usage 时带回 token 用量（流式请求 stream_options.include_usage，接口不认时自动去掉）。
直接走 HTTP，不依赖具体 SDK 版本。未配置模型时使用 rule_planner.RulePlanner（规则模式）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import httpx

from .config import Settings

log = logging.getLogger("agent.llm")

# (kind, piece)：kind 为 "text"（回复正文）或 "reasoning"（思考过程）
OnDelta = Callable[[str, str], Awaitable[None]]


class LLMError(Exception):
    pass


class _StreamUnsupported(Exception):
    pass


class _UsageUnsupported(Exception):
    """接口不认 stream_options：去掉后重发。"""


class _Retryable(LLMError):
    """可以原样重发的失败：限流、服务端错误、连接失败。"""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


_RETRY_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})
_RETRY_AFTER_CAP_S = 20.0


class LLM(Protocol):
    label: str

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], on_delta: OnDelta | None = None
    ) -> dict[str, Any]:
        """返回一条 assistant 消息：{"role","content","tool_calls"?,"reasoning"?,"suggestions"?,"usage"?}。

        usage 为这次调用的 token 用量 {"prompt_tokens","completion_tokens","total_tokens"}，接口没给时没有。
        """
        ...


class OpenAICompatLLM:
    def __init__(self, settings: Settings, http: httpx.AsyncClient):
        self.s = settings
        self.http = http
        self.label = settings.llm_model
        self._stream_ok = settings.llm_stream
        self._usage_ok = True  # 流式请求是否带 stream_options.include_usage
        self.retry_base_s = 0.5  # 退避基数：0.5、1、2…秒，上限 8 秒

    # ------------------------------------------------------------------ 公共入口
    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], on_delta: OnDelta | None = None
    ) -> dict[str, Any]:
        while on_delta is not None and self._stream_ok:
            try:
                return await self._retrying(lambda: self._stream(messages, tools, on_delta))
            except _UsageUnsupported:
                log.warning("模型接口不支持 stream_options，流式输出将不带 token 用量")
                self._usage_ok = False
            except _StreamUnsupported as e:
                log.warning("模型接口不支持流式输出，改为一次性返回：%s", e)
                self._stream_ok = False
        return await self._retrying(lambda: self._once(messages, tools))

    async def _retrying(self, call: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
        attempts = max(self.s.llm_max_retries, 0) + 1
        for attempt in range(attempts):
            try:
                return await call()
            except _Retryable as e:
                if attempt + 1 >= attempts:
                    suffix = f"（已重试 {attempt} 次）" if attempt else ""
                    raise LLMError(f"{e}{suffix}") from e
                delay = min(self.retry_base_s * 2**attempt, 8.0)
                if e.retry_after is not None:
                    delay = max(delay, min(e.retry_after, _RETRY_AFTER_CAP_S))
                log.warning("模型调用失败，%.1f 秒后重试（第 %d 次）：%s", delay, attempt + 1, e)
                await asyncio.sleep(delay)
        raise AssertionError("unreachable")

    # ------------------------------------------------------------------ 请求
    def _request(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> tuple[str, dict, dict]:
        payload: dict[str, Any] = {"model": self.s.llm_model, "messages": messages, "temperature": self.s.llm_temperature}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        headers = {"Content-Type": "application/json"}
        if self.s.llm_api_key:
            headers["Authorization"] = f"Bearer {self.s.llm_api_key}"
        return self.s.llm_base_url.rstrip("/") + "/chat/completions", payload, headers

    async def _once(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        url, payload, headers = self._request(messages, tools)
        try:
            resp = await self.http.post(url, json=payload, headers=headers, timeout=self.s.llm_timeout_s)
        except httpx.HTTPError as e:
            raise _Retryable(f"无法连接模型接口 {url}：{type(e).__name__} {e}".rstrip()) from e
        if resp.status_code >= 400:
            raise _http_failure(resp.status_code, resp.text[:400], resp.headers)
        try:
            data = resp.json()
            return _with_usage(_parse_message(data["choices"][0]["message"]), data.get("usage"))
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise LLMError(f"模型返回格式无法解析：{resp.text[:300]}") from e

    async def _stream(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], on_delta: OnDelta) -> dict[str, Any]:
        url, payload, headers = self._request(messages, tools)
        payload["stream"] = True
        if self._usage_ok:
            payload["stream_options"] = {"include_usage": True}
        content: list[str] = []
        reasoning: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        usage: Any = None
        emitted = False  # 已经把内容推给工作台：之后再失败不能重发，否则文字会重复
        try:
            async with self.http.stream("POST", url, json=payload, headers=headers, timeout=self.s.llm_timeout_s) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", "replace")[:400]
                    if resp.status_code in (400, 404, 415, 422, 501):
                        if self._usage_ok and "stream_options" in body.lower():
                            raise _UsageUnsupported(body)
                        if "stream" in body.lower():
                            raise _StreamUnsupported(body)
                    raise _http_failure(resp.status_code, body, resp.headers)
                if "text/event-stream" not in resp.headers.get("content-type", ""):
                    # 个别实现忽略 stream 参数，直接返回完整 JSON
                    raw = await resp.aread()
                    try:
                        data = json.loads(raw)
                        msg = _with_usage(_parse_message(data["choices"][0]["message"]), data.get("usage"))
                    except (ValueError, KeyError, IndexError, TypeError) as e:
                        raise LLMError(f"模型返回格式无法解析：{raw[:300]!r}") from e
                    if msg.get("content"):
                        await on_delta("text", msg["content"])
                    return msg
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except ValueError:
                        continue
                    if chunk.get("error"):
                        raise LLMError(f"模型接口返回错误：{json.dumps(chunk['error'], ensure_ascii=False)[:300]}")
                    if chunk.get("usage"):
                        usage = chunk["usage"]  # 通常在最后一块（choices 为空）
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        piece = delta.get("content")
                        if piece:
                            content.append(piece)
                            emitted = True
                            await on_delta("text", piece)
                        think = delta.get("reasoning_content") or delta.get("reasoning")
                        if isinstance(think, str) and think:
                            reasoning.append(think)
                            emitted = True
                            await on_delta("reasoning", think)
                        for tc in delta.get("tool_calls") or []:
                            _merge_tool_call(calls, tc)
        except httpx.HTTPError as e:
            message = f"无法连接模型接口 {url}：{type(e).__name__} {e}".rstrip()
            if emitted:
                raise LLMError(f"模型输出中断：{message}") from e
            raise _Retryable(message) from e

        out: dict[str, Any] = {"role": "assistant", "content": "".join(content)}
        tool_calls = [
            {
                "id": c["id"] or "call_" + uuid.uuid4().hex[:10],
                "type": "function",
                "function": {"name": c["name"], "arguments": c["arguments"] or "{}"},
            }
            for _, c in sorted(calls.items())
            if c["name"]
        ]
        if tool_calls:
            out["tool_calls"] = tool_calls
        if reasoning:
            out["reasoning"] = "".join(reasoning)
        return _with_usage(out, usage)


def _http_failure(status: int, body: str, headers: Any) -> LLMError:
    message = f"模型接口返回 {status}：{body}"
    if status not in _RETRY_STATUS:
        return LLMError(message)
    try:
        retry_after: float | None = float(headers.get("retry-after", ""))
    except (TypeError, ValueError):
        retry_after = None  # 没给，或给的是日期格式：按默认退避
    return _Retryable(message, retry_after)


def _with_usage(msg: dict[str, Any], usage: Any) -> dict[str, Any]:
    """把接口返回的 usage 规整成三个整数字段；没有或格式不对时不带。"""
    if isinstance(usage, dict):
        clean = {k: usage[k] for k in ("prompt_tokens", "completion_tokens", "total_tokens") if isinstance(usage.get(k), int)}
        if clean:
            clean.setdefault("total_tokens", clean.get("prompt_tokens", 0) + clean.get("completion_tokens", 0))
            msg["usage"] = clean
    return msg


def _merge_tool_call(calls: dict[int, dict[str, Any]], tc: dict[str, Any]) -> None:
    """按 index 拼接流式工具调用：id、name 通常在第一块，arguments 分多块到达。"""
    idx = tc.get("index")
    tid = tc.get("id")
    if not isinstance(idx, int):
        same = [i for i, c in calls.items() if tid and c["id"] == tid]
        idx = same[0] if same else (len(calls) if (tid or not calls) else max(calls))
    cur = calls.setdefault(idx, {"id": None, "name": "", "arguments": ""})
    if tid:
        cur["id"] = tid
    fn = tc.get("function") or {}
    name = fn.get("name")
    if name:
        cur["name"] = name if (not cur["name"] or name.startswith(cur["name"])) else cur["name"] + name
    args = fn.get("arguments")
    if isinstance(args, dict):
        cur["arguments"] = json.dumps(args, ensure_ascii=False)
    elif isinstance(args, str):
        cur["arguments"] += args


def _parse_message(msg: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"role": "assistant", "content": msg.get("content") or ""}
    calls = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        arguments = fn.get("arguments")
        if isinstance(arguments, dict):  # 个别实现直接返回对象
            arguments = json.dumps(arguments, ensure_ascii=False)
        calls.append(
            {
                "id": tc.get("id") or "call_" + uuid.uuid4().hex[:10],
                "type": "function",
                "function": {"name": fn.get("name", ""), "arguments": arguments or "{}"},
            }
        )
    if calls:
        out["tool_calls"] = calls
    think = msg.get("reasoning_content") or msg.get("reasoning")
    if isinstance(think, str) and think:
        out["reasoning"] = think
    return out


def make_llm(settings: Settings, http: httpx.AsyncClient, library: Any = None) -> LLM:
    """配置了模型时用 OpenAI 兼容接口；否则用规则模式，按 library（IntentLibrary）中的场景剧本执行。"""
    if settings.llm_enabled:
        return OpenAICompatLLM(settings, http)
    from .rule_planner import RulePlanner

    return RulePlanner(settings, library)
