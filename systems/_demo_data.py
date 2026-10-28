"""三个模拟系统共用的确定性演示数据：司机名册、排班、挂车与停留记录、轨迹。

坐标为 WGS84。数据由固定种子生成，每次启动一致；时间相对“现在”生成。
"""
from __future__ import annotations

import math
import random
import zlib
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=8))

REGIONS = [
    {"code": "east-china", "name": "华东", "center": [121.40, 31.22]},
    {"code": "north-china", "name": "华北", "center": [116.45, 39.92]},
]

# 片区中心（用于生成聚集的位置，分组才有意义）
EAST_HUBS = [
    ("嘉定", 121.25, 31.38),
    ("宝山", 121.45, 31.40),
    ("浦东", 121.62, 31.22),
    ("闵行", 121.38, 31.11),
    ("松江", 121.23, 31.03),
    ("青浦", 121.10, 31.16),
]
NORTH_HUBS = [("朝阳", 116.48, 39.92), ("海淀", 116.30, 39.96), ("丰台", 116.29, 39.86), ("通州", 116.66, 39.91)]

_SURNAMES = "王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾田董袁潘蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏付方白邹孟熊秦邱江尹薛段雷侯龙史陶黎贺顾毛郝龚邵万钱严武戴孔汤"
_GIVEN = [
    "伟", "芳", "敏", "静", "磊", "强", "军", "洋", "勇", "杰", "涛", "明", "超", "华", "平", "刚", "鹏", "辉",
    "建华", "建国", "晓东", "志强", "海燕", "国庆", "文博", "子豪", "俊杰", "浩然", "宇航", "思远", "明轩",
    "一鸣", "天佑", "立新", "红梅", "春生", "永刚", "德胜", "学军", "振华",
]
CUSTOMERS = ["华东冷链", "沪通物流", "顺达快运", "申港货运", "长江仓储", "东方速递"]
SKILLS = ["冷链", "危化品", "大件", "城配", "夜间配送"]


def _rng(salt: int) -> random.Random:
    return random.Random(20261027 + salt)


def _build_drivers() -> list[dict]:
    rng = _rng(1)
    drivers = []
    idx = 1
    for region, hubs, count in (("east-china", EAST_HUBS, 56), ("north-china", NORTH_HUBS, 24)):
        for i in range(count):
            hub, hx, hy = hubs[i % len(hubs)]
            drivers.append(
                {
                    "driver_id": f"D{idx:04d}",
                    "name": rng.choice(_SURNAMES) + rng.choice(_GIVEN),
                    "region": region,
                    "home_area": hub,
                    "lng": round(hx + rng.gauss(0, 0.035), 6),
                    "lat": round(hy + rng.gauss(0, 0.028), 6),
                    "status": "on_duty" if i % 7 else "resting",
                    # 按“行”轮换晚班，保证各片区都有早班司机；华东早班 38 人
                    "shift": "late" if (i // len(hubs)) % 3 == 2 else "early",
                    "skills": sorted(rng.sample(SKILLS, k=1 + i % 2)),
                    "phone": f"138{rng.randrange(10**7, 10**8)}",
                }
            )
            idx += 1
    return drivers


DRIVERS = _build_drivers()
DRIVER_BY_ID = {d["driver_id"]: d for d in DRIVERS}


def now() -> datetime:
    return datetime.now(TZ)


def positions(driver_ids: list[str]) -> list[dict]:
    rng = _rng(2)
    t = now()
    out = []
    for d in DRIVERS:
        jitter = (rng.gauss(0, 0.004), rng.gauss(0, 0.004), rng.randrange(1, 25), rng.uniform(0, 60))
        if d["driver_id"] not in driver_ids:
            continue
        out.append(
            {
                "driver_id": d["driver_id"],
                "name": d["name"],
                "lng": round(d["lng"] + jitter[0], 6),
                "lat": round(d["lat"] + jitter[1], 6),
                "speed_kmh": round(jitter[3], 1),
                "ts": (t - timedelta(minutes=jitter[2])).isoformat(timespec="seconds"),
            }
        )
    return out


def _build_trailer_stops() -> list[dict]:
    """挂车停留记录：每台挂车 0–2 次停留，时间相对现在。"""
    rng = _rng(3)
    stops = []
    for i in range(120):
        hub, hx, hy = EAST_HUBS[i % len(EAST_HUBS)]
        customer = CUSTOMERS[(i * 7 + i // 6) % len(CUSTOMERS)]
        for _ in range(rng.choice([0, 1, 1, 2])):
            stops.append(
                {
                    "asset_id": f"T-{2001 + i}",
                    "asset_type": "trailer",
                    "customer": customer,
                    "lng": round(hx + rng.gauss(0, 0.05), 6),
                    "lat": round(hy + rng.gauss(0, 0.04), 6),
                    "started_min_ago": rng.randrange(30, 47 * 60),
                    "duration_min": int(rng.choice([15, 25, 40, 60, 90, 130, 150, 200, 260, 320, 480, 600])),
                    "address": f"上海市{hub}区物流园 {rng.randrange(1, 99)} 号",
                }
            )
    return stops


TRAILER_STOPS = _build_trailer_stops()


def stops(asset_type: str, lookback_h: int, min_duration_min: int) -> list[dict]:
    t = now()
    out = []
    for s in TRAILER_STOPS:
        if s["asset_type"] != asset_type or s["duration_min"] < min_duration_min:
            continue
        if s["started_min_ago"] > lookback_h * 60:
            continue
        started = t - timedelta(minutes=s["started_min_ago"])
        out.append(
            {
                "asset_id": s["asset_id"],
                "customer": s["customer"],
                "lng": s["lng"],
                "lat": s["lat"],
                "started_at": started.isoformat(timespec="minutes"),
                "duration_min": s["duration_min"],
                "address": s["address"],
            }
        )
    return sorted(out, key=lambda x: -x["duration_min"])


def track(driver_id: str, day: str) -> list[dict]:
    """一天的轨迹：围绕司机常驻片区的闭合路线，60 个点。"""
    d = DRIVER_BY_ID[driver_id]
    rng = random.Random(zlib.crc32(f"{driver_id}|{day}".encode()))
    base = datetime.fromisoformat(day + "T06:30:00+08:00")
    pts = []
    radius_lng, radius_lat = 0.06 + rng.random() * 0.05, 0.04 + rng.random() * 0.04
    phase = rng.random() * math.tau
    for k in range(60):
        a = phase + k / 60 * math.tau
        wobble = 1 + 0.25 * math.sin(3 * a + phase)
        pts.append(
            {
                "lng": round(d["lng"] + radius_lng * wobble * math.cos(a) - radius_lng * math.cos(phase), 6),
                "lat": round(d["lat"] + radius_lat * wobble * math.sin(a) - radius_lat * math.sin(phase), 6),
                "ts": (base + timedelta(minutes=8 * k)).isoformat(timespec="minutes"),
            }
        )
    return pts
