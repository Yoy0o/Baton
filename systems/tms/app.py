"""模拟系统：排班系统（TMS）。只有后端 API，没有需要接入的界面。"""
from __future__ import annotations

import re

from fastapi import FastAPI, HTTPException, Query

from systems import _sample_data as sample

app = FastAPI(title="排班系统（模拟）", version="1.0.0", description="司机班次与排班名单。")

SHIFTS = {
    "early": {"name": "早班", "start": "06:00", "end": "14:00"},
    "late": {"name": "晚班", "start": "14:00", "end": "22:00"},
}


@app.get("/api/shifts/drivers", operation_id="searchShiftDrivers", summary="按区域、日期、班次查询排班司机")
def search_shift_drivers(
    region: str = Query(..., description="区域编码，如 east-china"),
    date: str = Query(..., description="日期 YYYY-MM-DD"),
    shift: str = Query("early", description="班次：early（早班）或 late（晚班）"),
) -> dict:
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise HTTPException(400, "date 格式应为 YYYY-MM-DD")
    if shift not in SHIFTS:
        raise HTTPException(400, f"未知班次 {shift}，可用：early、late")
    info = SHIFTS[shift]
    items = [
        {
            "driver_id": d["driver_id"],
            "name": d["name"],
            "shift": shift,
            "shift_name": info["name"],
            "shift_start": f"{date}T{info['start']}:00+08:00",
            "shift_end": f"{date}T{info['end']}:00+08:00",
            "skills": d["skills"],
        }
        for d in sample.DRIVERS
        if d["region"] == region and d["shift"] == shift
    ]
    return {"region": region, "date": date, "shift": shift, "total": len(items), "items": items}
