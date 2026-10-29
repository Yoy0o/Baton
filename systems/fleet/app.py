"""模拟系统：车队追踪地图台。

代表一个“已有系统”：有自己的 REST API（自动生成 OpenAPI）和地图页面。
接入 Agent 时它的后端代码不需要任何改动；地图页面只加了一段桥接注册代码（见 static/map.html 末尾）。
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

from systems import _sample_data as sample

app = FastAPI(title="车队追踪地图台（模拟）", version="1.0.0", description="司机与车辆位置、挂车停留、轨迹查询。")
STATIC = Path(__file__).parent / "static"


@app.get("/api/regions", operation_id="listRegions", summary="区域列表")
def list_regions() -> list[dict]:
    return [{"code": r["code"], "name": r["name"]} for r in sample.REGIONS]


@app.get("/api/drivers", operation_id="listDrivers", summary="按区域、状态查询司机")
def list_drivers(
    region: str = Query(..., description="区域编码，如 east-china、north-china"),
    status: str | None = Query(None, description="on_duty（在岗）或 resting（休息）"),
) -> dict:
    if region not in {r["code"] for r in sample.REGIONS}:
        raise HTTPException(400, f"未知区域 {region}，可用：east-china、north-china")
    items = [
        {k: d[k] for k in ("driver_id", "name", "region", "home_area", "status", "lng", "lat")}
        for d in sample.DRIVERS
        if d["region"] == region and (status is None or d["status"] == status)
    ]
    return {"total": len(items), "items": items}


@app.get("/api/positions", operation_id="getPositions", summary="查询司机最新位置")
def get_positions(driver_ids: str = Query(..., description="逗号分隔的司机 ID，如 D0001,D0002")) -> dict:
    ids = [i.strip() for i in driver_ids.split(",") if i.strip()]
    if len(ids) > 2000:
        raise HTTPException(400, "一次最多查询 2000 个司机")
    unknown = [i for i in ids if i not in sample.DRIVER_BY_ID]
    if unknown and len(unknown) == len(ids):
        raise HTTPException(404, f"司机不存在：{', '.join(unknown[:5])}")
    items = sample.positions(ids)
    return {"total": len(items), "items": items}


@app.get("/api/stops", operation_id="searchStops", summary="查询资产停留记录")
def search_stops(
    asset_type: str = Query("trailer", description="资产类型，目前支持 trailer（挂车）"),
    lookback_h: int = Query(24, ge=1, le=168, description="回看最近多少小时"),
    min_duration_min: int = Query(60, ge=1, description="只返回停留时长不少于该分钟数的记录"),
) -> dict:
    if asset_type != "trailer":
        raise HTTPException(400, "asset_type 目前只支持 trailer")
    items = sample.stops(asset_type, lookback_h, min_duration_min)
    return {"total": len(items), "items": items}


@app.get("/api/tracks/{driver_id}", operation_id="getTrack", summary="查询司机某天的轨迹")
def get_track(driver_id: str, date: str = Query(..., description="日期 YYYY-MM-DD")) -> dict:
    if driver_id not in sample.DRIVER_BY_ID:
        raise HTTPException(404, f"司机 {driver_id} 不存在")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise HTTPException(400, "date 格式应为 YYYY-MM-DD")
    return {"driver_id": driver_id, "date": date, "points": sample.track(driver_id, date)}


@app.get("/map", include_in_schema=False)
def map_page() -> FileResponse:
    return FileResponse(STATIC / "map.html")
