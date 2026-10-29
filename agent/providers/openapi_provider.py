"""API 适配：读取系统现有的 OpenAPI 文档，按接入清单的白名单生成工具，并负责实际 HTTP 调用。

系统侧零代码改动：只要有 OpenAPI 文档和 operationId。格式差异用清单里的 hooks 处理。
"""
from __future__ import annotations

import copy
import importlib
import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from ..contracts import ToolError, ToolSpec, ok_envelope
from ..manifest import ApiTool, Manifest, ResultRef, full_name
from ..results import (
    ResultStore,
    classify_payload,
    convert,
    feature_rows,
    field_names,
    is_result_id,
    preview_rows,
)
from . import CallContext, CallOutcome

log = logging.getLogger("agent.openapi")

_HTTP_METHODS = ("get", "post", "put", "patch", "delete")
_INLINE_OBJECT_LIMIT = 4000  # 小对象直接内联返回，大对象存入结果仓库


@dataclass
class OpParam:
    name: str
    location: str  # path | query
    required: bool
    schema: dict[str, Any]
    description: str = ""


@dataclass
class Operation:
    operation_id: str
    method: str
    path: str
    summary: str
    params: list[OpParam] = field(default_factory=list)
    body_schema: dict[str, Any] | None = None
    body_props: list[str] | None = None  # 请求体为对象时，其属性被展开为工具顶层参数
    body_required: list[str] = field(default_factory=list)

    def param_names(self) -> set[str]:
        names = {p.name for p in self.params}
        if self.body_props is not None:
            names |= set(self.body_props)
        elif self.body_schema is not None:
            names.add("body")
        return names


@dataclass
class BoundTool:
    spec: ToolSpec
    decl: ApiTool
    op: Operation


def _load_hook(path: str) -> Callable[[dict[str, Any], dict[str, Any]], Any]:
    module_name, _, func_name = path.partition(":")
    if not module_name or not func_name:
        raise ValueError(f"hook 格式应为 module:function，实际为 {path!r}")
    return getattr(importlib.import_module(module_name), func_name)


class OpenAPIProvider:
    def __init__(self, manifest: Manifest, http: httpx.AsyncClient, store: ResultStore, http_timeout_s: float = 30):
        self.m = manifest
        self.http = http
        self.store = store
        self.http_timeout_s = http_timeout_s
        self.doc: dict[str, Any] | None = None
        self.tools: dict[str, BoundTool] = {}
        self.errors: list[str] = []
        self.loaded = False
        self.last_attempt = 0.0
        self._hooks: dict[str, Callable] = {}

    # ------------------------------------------------------------------ 加载
    async def load(self) -> bool:
        self.last_attempt = time.time()
        if not self.m.api:
            self.loaded = True
            return True
        try:
            self.doc = await self._fetch_doc(self.m.api.openapi)
        except Exception as e:  # 系统未启动等
            self.errors = [f"无法读取 OpenAPI 文档 {self.m.api.openapi}：{e}"]
            self.loaded = False
            return False
        self.errors = []
        self.tools = {}
        ops = self._index_operations(self.doc)
        for decl in self.m.api.expose:
            op = ops.get(decl.operation)
            if op is None:
                self.errors.append(f"{self.m.system}: OpenAPI 中找不到 operationId={decl.operation}（R2.1）")
                continue
            try:
                for hook_path in decl.hooks.values():
                    self._hooks[hook_path] = _load_hook(hook_path)
            except Exception as e:
                self.errors.append(f"{self.m.system}.{decl.tool}: hook 加载失败：{e}")
                continue
            spec = ToolSpec(
                name=full_name(self.m.system, decl.tool),
                system=self.m.system,
                kind="api",
                title=decl.title or op.summary or decl.tool,
                description=decl.description,
                input_schema=self._input_schema(decl, op),
                risk=decl.risk,
                pairs_with=full_name(self.m.system, decl.pairs_with) if decl.pairs_with else None,
                examples=decl.examples,
            )
            self.tools[spec.name] = BoundTool(spec, decl, op)
        # 预览与提交必须参数一致，网关才能安全回放（R3.2）
        for bound in list(self.tools.values()):
            if bound.decl.pairs_with:
                apply_name = full_name(self.m.system, bound.decl.pairs_with)
                apply = self.tools.get(apply_name)
                if apply and apply.op.param_names() != bound.op.param_names():
                    self.errors.append(
                        f"{bound.spec.name} 与 {apply_name} 的参数不一致："
                        f"{sorted(bound.op.param_names())} vs {sorted(apply.op.param_names())}（R3.2）"
                    )
                    self.tools.pop(bound.spec.name, None)
                    self.tools.pop(apply_name, None)
        self.loaded = True
        return True

    async def _fetch_doc(self, location: str) -> dict[str, Any]:
        if location.startswith(("http://", "https://")):
            resp = await self.http.get(location, timeout=10)
            resp.raise_for_status()
            return resp.json()
        path = Path(location)
        if not path.is_absolute() and self.m.path:
            path = self.m.path.parent / path
        return json.loads(path.read_text(encoding="utf-8"))

    def _index_operations(self, doc: dict[str, Any]) -> dict[str, Operation]:
        ops: dict[str, Operation] = {}
        for path, item in (doc.get("paths") or {}).items():
            shared = item.get("parameters", [])
            for method in _HTTP_METHODS:
                raw = item.get(method)
                if not raw or not raw.get("operationId"):
                    continue
                op = Operation(
                    operation_id=raw["operationId"],
                    method=method.upper(),
                    path=path,
                    summary=raw.get("summary") or "",
                )
                for p in shared + raw.get("parameters", []):
                    p = self._resolve(p)
                    if p.get("in") not in ("path", "query"):
                        continue
                    op.params.append(
                        OpParam(
                            name=p["name"],
                            location=p["in"],
                            required=bool(p.get("required")) or p["in"] == "path",
                            schema=self._clean(p.get("schema") or {}),
                            description=p.get("description") or "",
                        )
                    )
                body = raw.get("requestBody")
                if body:
                    body = self._resolve(body)
                    schema = ((body.get("content") or {}).get("application/json") or {}).get("schema")
                    if schema:
                        schema = self._clean(schema)
                        op.body_schema = schema
                        if schema.get("type") == "object" and schema.get("properties"):
                            op.body_props = list(schema["properties"])
                            op.body_required = list(schema.get("required", []))
                ops[op.operation_id] = op
        return ops

    # ------------------------------------------------------------------ Schema 处理
    def _resolve(self, node: Any, depth: int = 0) -> Any:
        if isinstance(node, dict) and "$ref" in node and depth < 20:
            ref = node["$ref"]
            if not ref.startswith("#/"):
                raise ValueError(f"暂不支持外部引用 {ref}")
            target: Any = self.doc
            for part in ref[2:].split("/"):
                target = target[part]
            return self._resolve(target, depth + 1)
        return node

    def _clean(self, schema: Any, depth: int = 0) -> Any:
        """解析 $ref、去掉 title、把 Optional（anyOf + null）收拢成单一类型，让模型更好读。"""
        if depth > 12:
            return {}
        schema = copy.deepcopy(self._resolve(schema))
        if not isinstance(schema, dict):
            return schema
        schema.pop("title", None)
        if "default" in schema and schema["default"] is None:
            schema.pop("default")
        if "anyOf" in schema:
            options = [o for o in schema["anyOf"] if not (isinstance(o, dict) and o.get("type") == "null")]
            if len(options) == 1:
                merged = self._clean(options[0], depth + 1)
                merged.update({k: v for k, v in schema.items() if k not in ("anyOf",)})
                schema = merged
        if "properties" in schema:
            schema["properties"] = {k: self._clean(v, depth + 1) for k, v in schema["properties"].items()}
        if "items" in schema:
            schema["items"] = self._clean(schema["items"], depth + 1)
        return schema

    def _input_schema(self, decl: ApiTool, op: Operation) -> dict[str, Any]:
        if decl.risk in ("write", "critical"):
            # 写操作只接收 confirm_token：参数由网关回放预览时的请求（R3.2）
            return {
                "type": "object",
                "properties": {
                    "confirm_token": {
                        "type": "string",
                        "description": "成对预览工具返回的 confirm_token。网关会回放预览时的参数，并先请用户确认。",
                    }
                },
                "required": ["confirm_token"],
            }
        props: dict[str, Any] = {}
        required: list[str] = []

        def add(name: str, schema: dict[str, Any], description: str, is_required: bool) -> None:
            ov = decl.params.get(name)
            if ov and ov.fixed is not None:
                return
            schema = copy.deepcopy(schema)
            desc = (ov.description if ov and ov.description else description or schema.get("description") or "").strip()
            if ov and ov.accepts_result:
                schema = {"type": "string"}
                desc = self._ref_description(desc, ov.accepts_result)
            if ov and ov.default is not None:
                schema["default"] = ov.default
            if desc:
                schema["description"] = desc
            props[name] = schema
            if is_required and "default" not in schema:
                required.append(name)

        for p in op.params:
            add(p.name, p.schema, p.description, p.required)
        if op.body_props is not None and op.body_schema is not None:
            for name in op.body_props:
                add(name, op.body_schema["properties"][name], "", name in op.body_required)
        elif op.body_schema is not None:
            add("body", op.body_schema, "请求体", True)
        schema: dict[str, Any] = {"type": "object", "properties": props}
        if required:
            schema["required"] = required
        return schema

    @staticmethod
    def _ref_description(desc: str, ref: ResultRef) -> str:
        tail = {
            "ids": f"传入上一步结果的 result_id（网关自动取出其中的 {ref.field or 'id'} 列），或逗号分隔的值",
            "rows": "传入上一步结果的 result_id（网关自动展开为行数据）",
            "geojson": "传入带坐标结果的 result_id（网关自动转为 GeoJSON）",
            "group_plan": "传入分组方案（group_plan）的 result_id",
        }[ref.as_]
        return f"{desc}；{tail}" if desc else tail

    # ------------------------------------------------------------------ 调用
    def specs(self) -> list[ToolSpec]:
        return [b.spec for b in self.tools.values()]

    async def call(
        self,
        name: str,
        args: dict[str, Any],
        ctx: CallContext,
        *,
        idempotency_key: str | None = None,
        replay: bool = False,
    ) -> CallOutcome:
        bound = self.tools.get(name)
        if bound is None:
            raise ToolError("UNKNOWN_TOOL", f"没有工具 {name}")
        decl, op = bound.decl, bound.op
        args = dict(args)

        if not replay:
            for pname, ov in decl.params.items():
                if ov.fixed is not None:
                    args[pname] = ov.fixed
                elif pname not in args and ov.default is not None:
                    args[pname] = ov.default
                if pname in args and ov.accepts_result:
                    args[pname] = self._resolve_ref(args[pname], ov.accepts_result, ctx)
        request_args = copy.deepcopy(args)

        hook_ctx = {"tool": name, "user": ctx.user, "session_id": ctx.session_id, "system": self.m.system}
        if "request" in decl.hooks:
            args = self._hooks[decl.hooks["request"]](args, hook_ctx)

        path = op.path
        query: dict[str, Any] = {}
        for p in op.params:
            if p.name not in args or args[p.name] is None:
                continue
            value = args[p.name]
            if p.location == "path":
                path = path.replace("{" + p.name + "}", quote(str(value), safe=""))
            else:
                if isinstance(value, list) and p.schema.get("type") != "array":
                    value = ",".join(str(v) for v in value)  # R2.6：数组以逗号分隔传入字符串参数
                query[p.name] = value
        body: Any = None
        if op.body_props is not None:
            body = {k: args[k] for k in op.body_props if k in args}
        elif op.body_schema is not None:
            body = args.get("body")

        headers = {
            "X-Agent-User": quote(ctx.user),
            "X-Agent-Session": ctx.session_id,
            "X-Request-Id": ctx.request_id or "",
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        if self.m.auth.mode == "static_bearer" and self.m.auth.token_env:
            headers["Authorization"] = f"Bearer {os.environ.get(self.m.auth.token_env, '')}"

        url = self.m.api_base_url + path
        try:
            resp = await self.http.request(
                op.method, url, params=query or None, json=body, headers=headers, timeout=self.http_timeout_s
            )
        except httpx.TimeoutException:
            raise ToolError("SYSTEM_TIMEOUT", f"{self.m.title} 响应超时", "稍后重试，或缩小查询范围", retryable=True) from None
        except httpx.HTTPError as e:
            raise ToolError("SYSTEM_UNREACHABLE", f"无法连接 {self.m.title}：{e}", "确认该系统已启动", retryable=True) from e

        if resp.status_code >= 400:
            raise self._http_error(resp)
        try:
            payload: Any = resp.json()
        except ValueError:
            payload = {"text": resp.text[:2000]}
        if "response" in decl.hooks:
            payload = self._hooks[decl.hooks["response"]](payload, hook_ctx)
        envelope = self._wrap(bound, payload, request_args, ctx)
        return CallOutcome(envelope=envelope, raw=payload, request_args=request_args)

    def _resolve_ref(self, value: Any, ref: ResultRef, ctx: CallContext) -> Any:
        if is_result_id(value):
            return convert(self.store.get(value, owner=ctx.user), ref.as_, ref.field)
        if ref.as_ == "ids" and isinstance(value, str):
            return [v.strip() for v in value.split(",") if v.strip()]
        if ref.as_ in ("group_plan", "geojson", "rows") and isinstance(value, str):
            raise ToolError(
                "BAD_RESULT_REF",
                f"参数需要 result_id，收到的是 {value[:40]!r}",
                "传入本会话中工具返回的 result_id（形如 res_xxxxxxxx）",
            )
        return value

    def _http_error(self, resp: httpx.Response) -> ToolError:
        detail: Any
        try:
            data = resp.json()
            detail = data.get("detail") or data.get("message") or data.get("error") or data
        except ValueError:
            detail = resp.text[:300]
        if isinstance(detail, list):  # FastAPI/pydantic 校验错误
            detail = "；".join(
                f"{'.'.join(str(x) for x in d.get('loc', [])[1:])}: {d.get('msg')}" if isinstance(d, dict) else str(d)
                for d in detail[:5]
            )
        message = f"{self.m.title} 返回 {resp.status_code}：{str(detail)[:300]}"
        status = resp.status_code
        hint = {
            400: "检查参数取值是否符合工具描述",
            401: "认证失败：检查清单 auth 配置",
            403: "当前用户无权执行该操作，请如实告知用户，不要换工具绕过",
            404: "对象不存在：先用 search/get 类工具确认 ID",
            409: "数据已被修改：重新预览后再提交",
            422: "参数不合法：对照工具 schema 修正后重试",
        }.get(status, "稍后重试" if status >= 500 else None)
        return ToolError(f"HTTP_{status}", message, hint, retryable=status in (408, 429) or status >= 500)

    def _wrap(self, bound: BoundTool, payload: Any, request_args: dict[str, Any], ctx: CallContext) -> dict[str, Any]:
        spec, decl = bound.spec, bound.decl
        type_, data = classify_payload(payload, decl.result.list_path, decl.result.type)

        if type_ == "object":
            summary = self._object_summary(payload, decl)
            blob = json.dumps(data, ensure_ascii=False, default=str)
            if len(blob) <= _INLINE_OBJECT_LIMIT:
                return ok_envelope(summary, data=data)
            res = self.store.put(
                "object", data, owner=ctx.user, session_id=ctx.session_id, summary=summary,
                source={"tool": spec.name, "args": _short_args(request_args)},
            )
            return ok_envelope(summary, result_id=res.id, result_type="object")

        rows = data["rows"] if type_ == "table" else feature_rows(data["features"])
        total = len(rows)
        if decl.result.summary:
            summary = decl.result.summary.format(total=total)
        else:
            summary = f"返回 {total} 条记录" + ("（含坐标，可在地图展示）" if type_ == "feature_collection" else "")
        fields = field_names(rows)
        res = self.store.put(
            type_, data, owner=ctx.user, session_id=ctx.session_id, summary=summary,
            source={"tool": spec.name, "args": _short_args(request_args)}, total=total, fields=fields,
        )
        hints = []
        if type_ == "feature_collection":
            hints.append("可把 result_id 交给系统界面工具或 canvas__show 在地图上展示")
        return ok_envelope(
            summary, result_id=res.id, result_type=type_, total=total,
            preview=preview_rows(rows), fields=fields, next_hints=hints or None,
        )

    @staticmethod
    def _object_summary(payload: Any, decl: ApiTool) -> str:
        if decl.result.summary:
            return decl.result.summary.format(total=1)
        if isinstance(payload, dict) and isinstance(payload.get("summary"), str):
            return payload["summary"]
        return "调用成功"


def _short_args(args: dict[str, Any]) -> dict[str, Any]:
    """来源记录里只保留简短参数，避免把大列表写进元数据。"""
    out = {}
    for k, v in args.items():
        if isinstance(v, list) and len(v) > 10:
            out[k] = f"[{len(v)} 项]"
        else:
            out[k] = v
    return out
