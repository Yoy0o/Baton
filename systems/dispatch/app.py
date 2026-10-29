"""模拟系统：调度系统（司机组管理）。

写操作按接入规则实现：预览（不落库）+ 提交（落库）成对接口，请求体一致；提交支持 Idempotency-Key。
"""
from __future__ import annotations

import threading

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from systems import _sample_data as sample

app = FastAPI(title="调度系统（模拟）", version="1.0.0", description="司机组（班组）划分的查询、预览与提交。")

_lock = threading.Lock()
_state: dict[str, dict] = {}
_idempotent: dict[str, dict] = {}


def _initial_state() -> None:
    """初始分组：大体按片区划分，少量人员交叉，制造真实的“现状”。"""
    _state.clear()
    _idempotent.clear()
    for region, n_groups, prefix in (("east-china", 6, "华东"), ("north-china", 4, "华北")):
        members = [d for d in sample.DRIVERS if d["region"] == region]
        assign = {}
        for i, d in enumerate(members):
            g = i % n_groups + 1
            if i % 5 == 0:  # 每 5 人有 1 人不在本片区的组
                g = (g % n_groups) + 1
            assign[d["driver_id"]] = g
        names = {g: f"{prefix}{'一二三四五六七八九十'[g - 1]}组" for g in range(1, n_groups + 1)}
        _state[region] = {"version": 1, "assign": assign, "names": names}


_initial_state()


class GroupIn(BaseModel):
    group_no: int = Field(..., ge=1, le=50, description="组号，从 1 开始")
    driver_ids: list[str] = Field(..., min_length=1, description="该组司机 ID")


class RegroupRequest(BaseModel):
    region: str = Field(..., description="区域编码，如 east-china")
    groups: list[GroupIn] = Field(..., min_length=1, description="新的分组方案")


def _check(req: RegroupRequest) -> dict:
    st = _state.get(req.region)
    if st is None:
        raise HTTPException(400, f"未知区域 {req.region}")
    seen: set[str] = set()
    for g in req.groups:
        for did in g.driver_ids:
            if did in seen:
                raise HTTPException(422, f"司机 {did} 出现在多个组中")
            seen.add(did)
            d = sample.DRIVER_BY_ID.get(did)
            if d is None:
                raise HTTPException(404, f"司机 {did} 不存在")
            if d["region"] != req.region:
                raise HTTPException(422, f"司机 {did} 不属于区域 {req.region}")
    return st


def _diff(req: RegroupRequest, st: dict) -> tuple[list[dict], int, int]:
    changes = []
    touched = set()
    for g in req.groups:
        for did in g.driver_ids:
            touched.add(did)
            before = st["assign"].get(did)
            if before != g.group_no:
                changes.append(
                    {
                        "driver_id": did,
                        "name": sample.DRIVER_BY_ID[did]["name"],
                        "from_group": st["names"].get(before, f"第{before}组") if before else "未分组",
                        "to_group": st["names"].get(g.group_no, f"第{g.group_no}组"),
                    }
                )
    unchanged = len(touched) - len(changes)
    untouched = sum(1 for did in st["assign"] if did not in touched)
    return changes, unchanged, untouched


@app.get("/api/groups", operation_id="listGroups", summary="查询区域当前的司机组划分")
def list_groups(region: str = Query(..., description="区域编码，如 east-china")) -> dict:
    st = _state.get(region)
    if st is None:
        raise HTTPException(400, f"未知区域 {region}")
    groups: dict[int, list[str]] = {}
    for did, g in st["assign"].items():
        groups.setdefault(g, []).append(did)
    return {
        "region": region,
        "version": st["version"],
        "groups": [
            {"group_no": g, "name": st["names"].get(g, f"第{g}组"), "size": len(ids), "driver_ids": sorted(ids)}
            for g, ids in sorted(groups.items())
        ],
    }


@app.post("/api/groups/preview", operation_id="previewRegroup", summary="预览分组调整（不落库）")
def preview_regroup(req: RegroupRequest) -> dict:
    with _lock:
        st = _check(req)
        changes, unchanged, untouched = _diff(req, st)
    summary = f"{len(changes)} 人换组、{unchanged} 人组别不变，新方案共 {len(req.groups)} 组"
    if untouched:
        summary += f"；方案未涉及的 {untouched} 人保持原组"
    return {
        "summary": summary,
        "changes": changes,
        "stats": {"moved": len(changes), "unchanged": unchanged, "untouched": untouched, "base_version": st["version"]},
    }


@app.post("/api/groups/apply", operation_id="applyRegroup", summary="提交分组调整（落库）")
def apply_regroup(req: RegroupRequest, idempotency_key: str | None = Header(None, alias="Idempotency-Key")) -> dict:
    if not idempotency_key:
        raise HTTPException(400, "提交需要 Idempotency-Key 请求头")
    with _lock:
        if idempotency_key in _idempotent:
            return {**_idempotent[idempotency_key], "replayed": True}
        st = _check(req)
        changes, _, _ = _diff(req, st)
        for g in req.groups:
            for did in g.driver_ids:
                st["assign"][did] = g.group_no
                st["names"].setdefault(g.group_no, f"第{g.group_no}组")
        st["version"] += 1
        resp = {
            "applied": True,
            "version": st["version"],
            "changed": len(changes),
            "summary": f"已更新 {len(changes)} 人的组别，调度系统分组版本 {st['version']}",
        }
        _idempotent[idempotency_key] = resp
    return resp


@app.post("/api/_reset", include_in_schema=False)
def reset() -> dict:
    """联调与测试用：恢复初始分组。"""
    with _lock:
        _initial_state()
    return {"ok": True}
