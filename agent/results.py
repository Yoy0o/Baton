"""结果仓库：工具产出的列表数据都存成带类型的结果对象，模型只拿 result_id 句柄。

结果类型：
- table               行列数据            data = {"rows": [...]}
- feature_collection  GeoJSON 要素集合     data = FeatureCollection
- group_plan          分组方案            data = FeatureCollection + groups + params
- object              单个对象            data = {...}
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .contracts import PREVIEW_ROWS, ToolError

RESULT_TYPES = ("table", "feature_collection", "group_plan", "object")
MAX_ROWS = 5000  # 单个结果的行数上限（接入规则 R2.4）

_LNG_KEYS = ("lng", "lon", "longitude")
_LAT_KEYS = ("lat", "latitude")
_LIST_KEYS = ("items", "data", "results", "rows", "records", "list")


@dataclass
class Result:
    id: str
    type: str
    owner: str
    session_id: str
    summary: str
    data: Any
    total: int | None = None
    fields: list[str] = field(default_factory=list)
    source: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0

    def meta(self) -> dict[str, Any]:
        return {
            "result_id": self.id,
            "result_type": self.type,
            "summary": self.summary,
            "total": self.total,
            "fields": self.fields,
            "source": self.source,
            "created_at": self.created_at,
        }


class ResultStore:
    def __init__(self, data_dir: Path, ttl_s: int = 24 * 3600):
        self.dir = Path(data_dir) / "results"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.ttl_s = ttl_s
        self._mem: dict[str, Result] = {}

    def put(
        self,
        type_: str,
        data: Any,
        *,
        owner: str,
        session_id: str,
        summary: str,
        source: dict[str, Any] | None = None,
        total: int | None = None,
        fields: list[str] | None = None,
    ) -> Result:
        if type_ not in RESULT_TYPES:
            raise ValueError(f"unknown result type {type_}")
        res = Result(
            id="res_" + uuid.uuid4().hex[:8],
            type=type_,
            owner=owner,
            session_id=session_id,
            summary=summary,
            data=data,
            total=total,
            fields=fields or [],
            source=source or {},
        )
        res.expires_at = res.created_at + self.ttl_s
        self._mem[res.id] = res
        try:
            (self.dir / f"{res.id}.json").write_text(json.dumps(asdict(res), ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass  # 持久化失败不影响本次会话
        return res

    def get(self, result_id: str, owner: str | None = None) -> Result:
        res = self._mem.get(result_id)
        if res is None:
            path = self.dir / f"{result_id}.json"
            if path.exists() and path.parent == self.dir:
                res = Result(**json.loads(path.read_text(encoding="utf-8")))
                self._mem[res.id] = res
        if res is None:
            raise ToolError(
                "RESULT_NOT_FOUND",
                f"结果 {result_id} 不存在",
                "使用本会话工具返回的 result_id，不要自行编造或改写 ID",
            )
        if owner is not None and res.owner != owner:
            raise ToolError("RESULT_FORBIDDEN", f"无权访问结果 {result_id}")
        if res.expires_at and res.expires_at < time.time():
            raise ToolError("RESULT_EXPIRED", f"结果 {result_id} 已过期", "重新调用产生该结果的工具")
        return res

    def list_for_session(self, session_id: str) -> list[Result]:
        return [r for r in self._mem.values() if r.session_id == session_id]


# ---------------------------------------------------------------- 格式转换


def is_result_id(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("res_") and len(value) <= 40


def _coord_keys(row: dict[str, Any]) -> tuple[str, str] | None:
    for lk in _LNG_KEYS:
        for ak in _LAT_KEYS:
            if isinstance(row.get(lk), (int, float)) and isinstance(row.get(ak), (int, float)):
                return lk, ak
    return None


def rows_to_features(rows: list[dict[str, Any]], lng_key: str, lat_key: str) -> list[dict[str, Any]]:
    feats = []
    for row in rows:
        lng, lat = row.get(lng_key), row.get(lat_key)
        if not isinstance(lng, (int, float)) or not isinstance(lat, (int, float)):
            continue
        props = {k: v for k, v in row.items() if k not in (lng_key, lat_key)}
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [lng, lat]}, "properties": props})
    return feats


def feature_rows(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for f in features:
        row = dict(f.get("properties") or {})
        geom = f.get("geometry") or {}
        if geom.get("type") == "Point":
            lng, lat = geom["coordinates"][:2]
            row["lng"], row["lat"] = round(lng, 6), round(lat, 6)
        elif geom:
            row["geometry_type"] = geom.get("type")
        rows.append(row)
    return rows


def rows_of(res: Result) -> list[dict[str, Any]]:
    if res.type == "table":
        return list(res.data.get("rows", []))
    if res.type in ("feature_collection", "group_plan"):
        return feature_rows(res.data.get("features", []))
    if res.type == "object":
        return [res.data] if isinstance(res.data, dict) else []
    return []


def geojson_of(res: Result) -> dict[str, Any]:
    if res.type in ("feature_collection", "group_plan"):
        return {"type": "FeatureCollection", "features": res.data.get("features", [])}
    rows = rows_of(res)
    keys = _coord_keys(rows[0]) if rows else None
    if not keys:
        raise ToolError(
            "RESULT_NOT_SPATIAL",
            f"结果 {res.id}（{res.type}）不含坐标，无法作为空间数据使用",
            "改用带坐标的结果，如位置查询返回的 result_id",
        )
    return {"type": "FeatureCollection", "features": rows_to_features(rows, *keys)}


def ids_of(res: Result, field_name: str) -> list[Any]:
    rows = rows_of(res)
    ids = [r[field_name] for r in rows if field_name in r and r[field_name] is not None]
    if not ids:
        available = sorted({k for r in rows[:20] for k in r})
        raise ToolError(
            "RESULT_FIELD_MISSING",
            f"结果 {res.id} 中没有字段 {field_name}",
            f"可用字段：{', '.join(available) or '无'}；请换用包含 {field_name} 的结果",
        )
    return ids


def group_plan_of(res: Result) -> list[dict[str, Any]]:
    if res.type != "group_plan":
        raise ToolError(
            "RESULT_TYPE_MISMATCH",
            f"需要分组方案（group_plan），但 {res.id} 是 {res.type}",
            "先调用 analytics__run_grouping 生成分组方案",
        )
    return [{"group_no": g["group_no"], "member_ids": list(g["member_ids"])} for g in res.data.get("groups", [])]


def convert(res: Result, as_: str, field_name: str | None = None) -> Any:
    """把结果对象转换成某个参数需要的形态（接入规则 R4.2）。"""
    if as_ == "ids":
        return ids_of(res, field_name or "id")
    if as_ == "rows":
        return rows_of(res)
    if as_ == "geojson":
        return geojson_of(res)
    if as_ == "group_plan":
        return group_plan_of(res)
    raise ToolError("BAD_RESULT_CONVERSION", f"不支持的结果转换方式 {as_}")


# ---------------------------------------------------------------- API 返回值归类


def _dig(payload: Any, path: str) -> Any:
    cur = payload
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def classify_payload(payload: Any, list_path: str | None = None, forced: str = "auto") -> tuple[str, Any]:
    """把系统接口的返回值归类为结果类型。返回 (type, data)。"""
    if forced == "object":
        return "object", payload
    if isinstance(payload, dict) and payload.get("type") == "FeatureCollection" and forced in ("auto", "feature_collection"):
        return "feature_collection", {"type": "FeatureCollection", "features": payload.get("features", [])}

    items: Any = None
    if list_path:
        items = _dig(payload, list_path)
    elif isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        for key in _LIST_KEYS:
            if isinstance(payload.get(key), list):
                items = payload[key]
                break
    if not isinstance(items, list):
        return "object", payload

    items = items[:MAX_ROWS]
    dict_rows = [r for r in items if isinstance(r, dict)]
    if not dict_rows:
        return "table", {"rows": [{"value": v} for v in items]}
    keys = _coord_keys(dict_rows[0])
    if keys and forced in ("auto", "feature_collection"):
        return "feature_collection", {"type": "FeatureCollection", "features": rows_to_features(dict_rows, *keys)}
    return "table", {"rows": dict_rows}


def field_names(rows: list[dict[str, Any]]) -> list[str]:
    seen: dict[str, None] = {}
    for row in rows[:50]:
        for k in row:
            seen.setdefault(k, None)
    return list(seen)


def preview_rows(rows: list[dict[str, Any]], n: int = PREVIEW_ROWS) -> list[dict[str, Any]]:
    out = []
    for row in rows[:n]:
        out.append({k: (round(v, 5) if isinstance(v, float) else v) for k, v in row.items()})
    return out
