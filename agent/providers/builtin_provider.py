"""内置能力：平台自带、与具体业务系统无关的工具——空间分组、画布展示、结果查看与统计。"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from ..analytics import capacitated_kmeans
from ..contracts import ToolError, ToolSpec, ok_envelope
from ..results import ResultStore, geojson_of, rows_of
from . import CallContext, CallOutcome

_SPECS: list[ToolSpec] = [
    ToolSpec(
        name="analytics__run_grouping",
        system="analytics",
        kind="builtin",
        title="空间分组",
        risk="read",
        description=(
            "按地理位置把点位分成若干组（带每组人数上限的 k-means），返回分组方案 group_plan 的 result_id 和每组统计。"
            "用于司机组重组、片区划分。不修改任何业务数据；要写回业务系统，把 result_id 交给对应系统的 preview 工具。"
            "微调已有方案时传 base_plan（保持组号）和 pin。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "points": {"type": "string", "description": "要分组的点位结果 result_id（需含经纬度，如司机位置查询结果或已有分组方案）"},
                "groups": {"type": "integer", "minimum": 1, "maximum": 50, "description": "分成几组"},
                "max_size": {"type": "integer", "minimum": 1, "maximum": 500, "description": "每组人数上限，可选"},
                "id_field": {"type": "string", "default": "driver_id", "description": "点位的唯一标识字段"},
                "pin": {
                    "type": "array",
                    "description": "把指定对象固定到某组，如 [{\"id\": \"D0012\", \"group\": 3}]",
                    "items": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}, "group": {"type": "integer", "minimum": 1}},
                        "required": ["id", "group"],
                    },
                },
                "base_plan": {"type": "string", "description": "在已有分组方案（group_plan 的 result_id）上微调，保持原组号"},
            },
            "required": ["points", "groups"],
        },
        examples=[
            {"points": "res_1a2b3c4d", "groups": 5, "max_size": 8},
            {"points": "res_5e6f7a8b", "groups": 5, "max_size": 8, "base_plan": "res_5e6f7a8b", "pin": [{"id": "D0012", "group": 3}]},
        ],
    ),
    ToolSpec(
        name="canvas__show",
        system="canvas",
        kind="builtin",
        title="画布展示",
        risk="view",
        description=(
            "在 Agent 工作台的结果画布中展示一个结果：带坐标的显示为地图点位加表格，其他显示为表格。"
            "用于跨系统结果，或对应系统界面没有打开时。只改界面，不改业务数据。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "result_id": {"type": "string", "description": "要展示的结果 ID"},
                "color_by": {"type": "string", "description": "按该字段分类着色，如 group、customer，可选"},
                "title": {"type": "string", "description": "画布标题，可选"},
            },
            "required": ["result_id"],
        },
    ),
    ToolSpec(
        name="results__preview",
        system="results",
        kind="builtin",
        title="查看结果",
        risk="read",
        description="查看某个结果的更多行（每次最多 50 行），用于核对数据或回答具体对象的问题。统计汇总请用 results__stats。",
        input_schema={
            "type": "object",
            "properties": {
                "result_id": {"type": "string"},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
                "fields": {"type": "array", "items": {"type": "string"}, "description": "只返回这些字段，可选"},
            },
            "required": ["result_id"],
        },
    ),
    ToolSpec(
        name="results__stats",
        system="results",
        kind="builtin",
        title="结果统计",
        risk="read",
        description="对某个结果做分组计数和数值统计（最小、平均、最大），如“按客户统计停留挂车数”。比逐行查看更准确，回答汇总类问题优先用它。",
        input_schema={
            "type": "object",
            "properties": {
                "result_id": {"type": "string"},
                "group_by": {"type": "string", "description": "按该字段分组计数，可选"},
                "value_field": {"type": "string", "description": "对该数值字段求最小、平均、最大，可选"},
            },
            "required": ["result_id"],
        },
    ),
]


class BuiltinProvider:
    def __init__(self, store: ResultStore):
        self.store = store
        self._specs = {s.name: s for s in _SPECS}

    def specs(self) -> list[ToolSpec]:
        return list(self._specs.values())

    async def call(self, name: str, args: dict[str, Any], ctx: CallContext) -> CallOutcome:
        handler = {
            "analytics__run_grouping": self._run_grouping,
            "canvas__show": self._canvas_show,
            "results__preview": self._results_preview,
            "results__stats": self._results_stats,
        }.get(name)
        if handler is None:
            raise ToolError("UNKNOWN_TOOL", f"没有工具 {name}")
        return CallOutcome(envelope=await handler(args, ctx), request_args=args)

    # ------------------------------------------------------------------ 分组
    async def _run_grouping(self, args: dict[str, Any], ctx: CallContext) -> dict[str, Any]:
        src = self.store.get(args["points"], owner=ctx.user)
        fc = geojson_of(src)
        id_field = args.get("id_field") or "driver_id"
        points, props = [], {}
        for f in fc["features"]:
            geom, p = f.get("geometry") or {}, f.get("properties") or {}
            if geom.get("type") != "Point" or p.get(id_field) is None:
                continue
            pid = str(p[id_field])
            lng, lat = geom["coordinates"][:2]
            points.append((pid, float(lng), float(lat)))
            props[pid] = p
        if not points:
            fields = sorted({k for f in fc["features"][:20] for k in (f.get("properties") or {})})
            raise ToolError("NO_POINTS", f"结果 {src.id} 中没有带 {id_field} 的点位", f"检查 id_field，可用字段：{', '.join(fields)}")

        k = int(args["groups"])
        max_size = args.get("max_size")
        pins = {str(p["id"]): int(p["group"]) for p in (args.get("pin") or [])}
        init = None
        if args.get("base_plan"):
            base = self.store.get(args["base_plan"], owner=ctx.user)
            if base.type != "group_plan":
                raise ToolError("RESULT_TYPE_MISMATCH", f"base_plan 需要分组方案，{base.id} 是 {base.type}")
            base_groups = base.data.get("groups", [])
            if len(base_groups) != k:
                raise ToolError("BASE_PLAN_MISMATCH", f"原方案有 {len(base_groups)} 组，与 groups={k} 不一致", f"微调时 groups 传 {len(base_groups)}")
            init = {g["group_no"]: tuple(g["centroid"]) for g in base_groups}

        result = capacitated_kmeans(points, k, max_size, pins, init)
        coords = {pid: (lng, lat) for pid, lng, lat in points}
        features = []
        for pid, _, _ in points:
            p = props[pid]
            out = {id_field: pid}
            if "name" in p:
                out["name"] = p["name"]
            out["group"] = result.assignment[pid]
            features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": list(coords[pid])}, "properties": out})

        sizes = [g["size"] for g in result.stats]
        avgs = [g["avg_dist_km"] for g in result.stats if g["size"]]
        summary = (
            f"已将 {len(points)} 个对象分为 {k} 组（每组 {min(sizes)}–{max(sizes)} 个），"
            f"组内平均距离 {min(avgs):.1f}–{max(avgs):.1f} km"
        )
        if pins:
            summary += f"；已按要求固定 {len(pins)} 个对象（{', '.join(f'{i}→第{g}组' for i, g in pins.items())}）"
        params = {"groups": k, "max_size": max_size, "pin": args.get("pin") or [], "points": src.id, "base_plan": args.get("base_plan")}
        data = {"type": "FeatureCollection", "features": features, "groups": result.stats, "params": params, "id_field": id_field}
        res = self.store.put(
            "group_plan", data, owner=ctx.user, session_id=ctx.session_id, summary=summary,
            source={"tool": "analytics__run_grouping", "args": params}, total=len(features),
            fields=[id_field] + (["name"] if any("name" in props[p] for p in props) else []) + ["group", "lng", "lat"],
        )
        brief = [{k2: g[k2] for k2 in ("group_no", "size", "avg_dist_km", "max_dist_km")} for g in result.stats]
        return ok_envelope(
            summary,
            result_id=res.id,
            result_type="group_plan",
            total=len(features),
            data={"groups": brief, "params": {"groups": k, "max_size": max_size}},
            fields=res.fields,
            next_hints=[
                "展示：系统界面工具或 canvas__show（color_by=group）",
                "写回业务系统：把该 result_id 交给对应系统的 preview 工具",
            ],
        )

    # ------------------------------------------------------------------ 画布
    async def _canvas_show(self, args: dict[str, Any], ctx: CallContext) -> dict[str, Any]:
        res = self.store.get(args["result_id"], owner=ctx.user)
        color_by = args.get("color_by")
        if color_by and res.fields and color_by not in res.fields:
            raise ToolError("FIELD_NOT_FOUND", f"结果 {res.id} 没有字段 {color_by}", f"可用字段：{', '.join(res.fields)}")
        if ctx.emit is None:
            raise ToolError("NO_WORKBENCH", "当前没有连接工作台，无法在画布展示", "在 Agent 工作台中发起该请求")
        await ctx.emit(
            {
                "type": "render",
                "result_id": res.id,
                "result_type": res.type,
                "title": args.get("title") or res.summary,
                "color_by": color_by,
            }
        )
        return ok_envelope(f"已在工作台画布展示 {res.id}（{res.total if res.total is not None else 1} 条）")

    # ------------------------------------------------------------------ 结果查看与统计
    async def _results_preview(self, args: dict[str, Any], ctx: CallContext) -> dict[str, Any]:
        res = self.store.get(args["result_id"], owner=ctx.user)
        rows = rows_of(res)
        offset, limit = int(args.get("offset") or 0), min(int(args.get("limit") or 20), 50)
        chunk = rows[offset : offset + limit]
        fields = args.get("fields")
        if fields:
            chunk = [{k: r.get(k) for k in fields} for r in chunk]
        if not chunk:
            return ok_envelope(f"{res.id} 共 {len(rows)} 行，offset={offset} 之后没有数据", data={"rows": []})
        return ok_envelope(
            f"{res.id} 第 {offset + 1}–{offset + len(chunk)} 行，共 {len(rows)} 行", data={"rows": chunk}
        )

    async def _results_stats(self, args: dict[str, Any], ctx: CallContext) -> dict[str, Any]:
        res = self.store.get(args["result_id"], owner=ctx.user)
        rows = rows_of(res)
        group_by, value_field = args.get("group_by"), args.get("value_field")
        for f in (group_by, value_field):
            if f and res.fields and f not in res.fields:
                raise ToolError("FIELD_NOT_FOUND", f"结果 {res.id} 没有字段 {f}", f"可用字段：{', '.join(res.fields)}")

        def numeric(rs: list[dict[str, Any]]) -> dict[str, float] | None:
            vals = [r.get(value_field) for r in rs if isinstance(r.get(value_field), (int, float))]
            if not value_field or not vals:
                return None
            return {"min": round(min(vals), 2), "avg": round(sum(vals) / len(vals), 2), "max": round(max(vals), 2)}

        if not group_by:
            stat = numeric(rows)
            summary = f"共 {len(rows)} 条" + (f"；{value_field} 最小 {stat['min']}、平均 {stat['avg']}、最大 {stat['max']}" if stat else "")
            return ok_envelope(summary, data={"count": len(rows), value_field or "value": stat})

        buckets: dict[Any, list[dict[str, Any]]] = defaultdict(list)
        for r in rows:
            buckets[r.get(group_by)].append(r)
        counts = Counter({k: len(v) for k, v in buckets.items()})
        groups = []
        for key, cnt in counts.most_common():
            item: dict[str, Any] = {"key": key, "count": cnt}
            stat = numeric(buckets[key])
            if stat:
                item.update(stat)
            groups.append(item)
        top = "、".join(f"{g['key']} {g['count']}" for g in groups[:8])
        more = f" 等 {len(groups)} 类" if len(groups) > 8 else f"（共 {len(groups)} 类）"
        return ok_envelope(f"共 {len(rows)} 条，按 {group_by}：{top}{more}", data={"groups": groups[:50]})
