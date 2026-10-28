"""内置计算：带人数上限的空间分组（capacitated k-means + 局部搜索）。纯 Python，无第三方依赖。

确定性：同样输入得到同样结果（固定随机种子），便于复现和评测。
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

from .contracts import ToolError

Point = tuple[str, float, float]  # (id, lng, lat)
XY = tuple[float, float]


@dataclass
class Grouping:
    assignment: dict[str, int]  # id -> 组号（从 1 开始）
    stats: list[dict]  # 每组统计
    cost_km: float  # 各点到所在组中心的距离之和


class _Projector:
    """局部等距投影：经纬度 → 公里，适合城市/区域尺度的距离计算。"""

    def __init__(self, points: list[Point]):
        self.lng0 = sum(p[1] for p in points) / len(points)
        self.lat0 = sum(p[2] for p in points) / len(points)
        self.kx = 111.32 * math.cos(math.radians(self.lat0))
        self.ky = 110.574

    def xy(self, lng: float, lat: float) -> XY:
        return (lng - self.lng0) * self.kx, (lat - self.lat0) * self.ky

    def lnglat(self, x: float, y: float) -> tuple[float, float]:
        return self.lng0 + x / self.kx, self.lat0 + y / self.ky


def capacitated_kmeans(
    points: list[Point],
    k: int,
    max_size: int | None = None,
    pins: dict[str, int] | None = None,
    init_centroids: dict[int, tuple[float, float]] | None = None,
    seed: int = 7,
    iters: int = 40,
) -> Grouping:
    n = len(points)
    pins = dict(pins or {})
    if n == 0:
        raise ToolError("NO_POINTS", "没有可分组的点位", "确认输入结果包含坐标和 ID 字段")
    if k < 1 or k > n:
        raise ToolError("BAD_GROUP_COUNT", f"分组数 {k} 不合理（点位共 {n} 个）", f"groups 取 1 到 {n} 之间")
    cap = max_size or n
    if k * cap < n:
        raise ToolError(
            "INFEASIBLE",
            f"{n} 个点分成 {k} 组、每组最多 {cap} 个，放不下",
            f"至少需要 {math.ceil(n / cap)} 组，或把每组上限提高到 {math.ceil(n / k)}",
        )
    ids = {p[0] for p in points}
    for pid, g in pins.items():
        if pid not in ids:
            raise ToolError("PIN_UNKNOWN_ID", f"固定对象 {pid} 不在本次分组的点位中", "检查 ID 是否正确，或先确认该对象在名单内")
        if not 1 <= g <= k:
            raise ToolError("PIN_BAD_GROUP", f"组号 {g} 超出范围 1–{k}", f"组号取 1 到 {k}")
    groups = list(range(1, k + 1))
    pinned_count = {g: sum(1 for v in pins.values() if v == g) for g in groups}
    for g, c in pinned_count.items():
        if c > cap:
            raise ToolError("PIN_OVER_CAPACITY", f"第 {g} 组固定了 {c} 个对象，超过上限 {cap}", "减少固定对象或提高每组上限")

    proj = _Projector(points)
    xy = {pid: proj.xy(lng, lat) for pid, lng, lat in points}
    order = [p[0] for p in points]

    if init_centroids and len(init_centroids) == k:
        starts = [{g: proj.xy(*init_centroids[g]) for g in groups}]  # 微调：沿用原方案中心，组号不变
    else:
        restarts = 8 if n <= 500 else 2
        starts = [_kmeanspp(order, xy, k, random.Random(seed + r)) for r in range(restarts)]
        for centers in starts:  # 有固定成员的组，以固定成员为锚点
            for g in groups:
                members = [xy[pid] for pid, pg in pins.items() if pg == g]
                if members:
                    centers[g] = _mean(members)

    best: tuple[float, dict[str, int], dict[int, XY]] | None = None
    for centers in starts:
        assign, centers = _lloyd(order, xy, groups, cap, pins, pinned_count, dict(centers), iters)
        assign, centers = _local_search(order, xy, groups, cap, pins, assign, centers)
        cost = sum(_dist(xy[p], centers[assign[p]]) for p in order)
        if best is None or cost < best[0] - 1e-9:
            best = (cost, assign, centers)
    assert best is not None
    cost, assignment, centers = best

    if not pins and not init_centroids:  # 自西向东编号，结果更直观
        ranked = sorted(groups, key=lambda g: (round(centers[g][0], 3), centers[g][1]))
        remap = {old: i + 1 for i, old in enumerate(ranked)}
        assignment = {pid: remap[g] for pid, g in assignment.items()}
        centers = {remap[g]: c for g, c in centers.items()}

    stats = []
    for g in groups:
        members = [pid for pid in order if assignment.get(pid) == g]
        dists = [_dist(xy[pid], centers[g]) for pid in members]
        lng, lat = proj.lnglat(*centers[g])
        stats.append(
            {
                "group_no": g,
                "size": len(members),
                "member_ids": members,
                "centroid": [round(lng, 6), round(lat, 6)],
                "avg_dist_km": round(sum(dists) / len(dists), 2) if dists else 0.0,
                "max_dist_km": round(max(dists), 2) if dists else 0.0,
            }
        )
    return Grouping(assignment=assignment, stats=stats, cost_km=round(cost, 2))


# ---------------------------------------------------------------------- 内部步骤


def _kmeanspp(order: list[str], xy: dict[str, XY], k: int, rng: random.Random) -> dict[int, XY]:
    chosen = [xy[order[rng.randrange(len(order))]]]
    while len(chosen) < k:
        d2 = [min(_d2(xy[pid], c) for c in chosen) for pid in order]
        total = sum(d2)
        if total == 0:
            chosen.append(xy[order[len(chosen) % len(order)]])
            continue
        r, acc = rng.random() * total, 0.0
        for pid, w in zip(order, d2, strict=True):
            acc += w
            if acc >= r:
                chosen.append(xy[pid])
                break
    return {g + 1: c for g, c in enumerate(chosen)}


def _lloyd(order, xy, groups, cap, pins, pinned_count, centers, iters):
    """带容量约束的 Lloyd 迭代：按“点-中心”距离从近到远贪心分配，组满即跳过。"""
    assignment: dict[str, int] = {}
    for _ in range(iters):
        new = dict(pins)
        remaining = {g: cap - pinned_count[g] for g in groups}
        pairs = sorted((_d2(xy[pid], centers[g]), pid, g) for pid in order if pid not in pins for g in groups)
        for _, pid, g in pairs:
            if pid not in new and remaining[g] > 0:
                new[pid] = g
                remaining[g] -= 1
        _fill_empty_groups(new, groups, centers, xy, pins)
        _recenter(new, groups, centers, xy)
        if new == assignment:
            break
        assignment = new
    return assignment, centers


def _local_search(order, xy, groups, cap, pins, assign, centers, rounds: int = 25):
    """局部搜索：单点迁移 + 两点交换，只接受让总距离下降的调整。"""
    assign = dict(assign)
    free = [p for p in order if p not in pins]
    allow_swaps = len(free) <= 800
    for _ in range(rounds):
        improved = False
        sizes = {g: 0 for g in groups}
        for g in assign.values():
            sizes[g] += 1
        for p in free:
            g = assign[p]
            if sizes[g] <= 1:
                continue
            here = _dist(xy[p], centers[g])
            target = min((h for h in groups if h != g and sizes[h] < cap), key=lambda h: _dist(xy[p], centers[h]), default=None)
            if target is not None and _dist(xy[p], centers[target]) < here - 1e-9:
                assign[p] = target
                sizes[g] -= 1
                sizes[target] += 1
                improved = True
        if allow_swaps:
            for i, p in enumerate(free):
                for q in free[i + 1 :]:
                    g, h = assign[p], assign[q]
                    if g == h:
                        continue
                    delta = (
                        _dist(xy[p], centers[h]) + _dist(xy[q], centers[g])
                        - _dist(xy[p], centers[g]) - _dist(xy[q], centers[h])
                    )
                    if delta < -1e-9:
                        assign[p], assign[q] = h, g
                        improved = True
        _recenter(assign, groups, centers, xy)
        if not improved:
            break
    return assign, centers


def _recenter(assign, groups, centers, xy) -> None:
    for g in groups:
        members = [xy[pid] for pid, pg in assign.items() if pg == g]
        if members:
            centers[g] = _mean(members)


def _fill_empty_groups(assign: dict[str, int], groups: list[int], centers, xy, pins) -> None:
    """避免出现空组：从人数大于 1 的组里，挑离本组中心最远的非固定成员挪过去。"""
    for g in groups:
        if any(v == g for v in assign.values()):
            continue
        sizes = {h: sum(1 for v in assign.values() if v == h) for h in groups}
        candidates = [(_d2(xy[pid], centers[h]), pid) for pid, h in assign.items() if pid not in pins and sizes[h] > 1]
        if not candidates:
            return
        _, pid = max(candidates)
        assign[pid] = g
        centers[g] = xy[pid]


def _d2(a: XY, b: XY) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def _dist(a: XY, b: XY) -> float:
    return math.sqrt(_d2(a, b))


def _mean(pts: list[XY]) -> XY:
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
