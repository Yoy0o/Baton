"""模型接入：任意 OpenAI 兼容的 Chat Completions 接口（需支持 tools）。

- 默认流式输出（stream=true，SSE），边生成边把文字推给工作台；工具调用参数按块拼接。
- 接口不支持流式时自动退回一次性返回，并在本进程内记住。
- 支持 reasoning_content（如 DeepSeek、通义的思考模式）：只展示，不回传给模型。
直接走 HTTP，不依赖具体 SDK 版本。未配置模型时使用 rule_planner.RulePlanner（规则模式）。
"""
from __future__ import annotations

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


class LLM(Protocol):
    label: str

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], on_delta: OnDelta | None = None
    ) -> dict[str, Any]:
        """返回一条 assistant 消息：{"role","content","tool_calls"?,"reasoning"?,"suggestions"?}。"""
        ...


class OpenAICompatLLM:
    def __init__(self, settings: Settings, http: httpx.AsyncClient):
        self.s = settings
        self.http = http
        self.label = settings.llm_model
        self._stream_ok = settings.llm_stream

    # ------------------------------------------------------------------ 公共入口
    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], on_delta: OnDelta | None = None
    ) -> dict[str, Any]:
        if on_delta is not None and self._stream_ok:
            try:
                return await self._stream(messages, tools, on_delta)
            except _StreamUnsupported as e:
                log.warning("模型接口不支持流式输出，改为一次性返回：%s", e)
                self._stream_ok = False
        return await self._once(messages, tools)

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
            raise LLMError(f"无法连接模型接口 {url}：{e}") from e
        if resp.status_code >= 400:
            raise LLMError(f"模型接口返回 {resp.status_code}：{resp.text[:400]}")
        try:
            return _parse_message(resp.json()["choices"][0]["message"])
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise LLMError(f"模型返回格式无法解析：{resp.text[:300]}") from e

    async def _stream(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], on_delta: OnDelta) -> dict[str, Any]:
        url, payload, headers = self._request(messages, tools)
        payload["stream"] = True
        content: list[str] = []
        reasoning: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        try:
            async with self.http.stream("POST", url, json=payload, headers=headers, timeout=self.s.llm_timeout_s) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", "replace")[:400]
                    if resp.status_code in (400, 404, 415, 422, 501) and "stream" in body.lower():
                        raise _StreamUnsupported(body)
                    raise LLMError(f"模型接口返回 {resp.status_code}：{body}")
                if "text/event-stream" not in resp.headers.get("content-type", ""):
                    # 个别实现忽略 stream 参数，直接返回完整 JSON
                    raw = await resp.aread()
                    try:
                        msg = _parse_message(json.loads(raw)["choices"][0]["message"])
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
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        piece = delta.get("content")
                        if piece:
                            content.append(piece)
                            await on_delta("text", piece)
                        think = delta.get("reasoning_content") or delta.get("reasoning")
                        if isinstance(think, str) and think:
                            reasoning.append(think)
                            await on_delta("reasoning", think)
                        for tc in delta.get("tool_calls") or []:
                            _merge_tool_call(calls, tc)
        except httpx.HTTPError as e:
            raise LLMError(f"无法连接模型接口 {url}：{e}") from e

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
        return out


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


def make_llm(settings: Settings, http: httpx.AsyncClient) -> LLM:
    if settings.llm_enabled:
        return OpenAICompatLLM(settings, http)
    from .rule_planner import RulePlanner

    return RulePlanner(settings)
