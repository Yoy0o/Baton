"""空间分组算法：容量约束、固定成员、微调保持组号、不可行时给出可纠正的错误。"""
from __future__ import annotations

import pytest

from agent.analytics import capacitated_kmeans
from agent.contracts import ToolError
from systems import _sample_data as sample

POINTS = [(d["driver_id"], d["lng"], d["lat"]) for d in sample.DRIVERS if d["region"] == "east-china" and d["shift"] == "early"]


def test_capacity_and_determinism():
    a = capacitated_kmeans(POINTS, 5, 8)
    b = capacitated_kmeans(POINTS, 5, 8)
    assert a.assignment == b.assignment
    assert sum(g["size"] for g in a.stats) == len(POINTS) == 38
    assert all(1 <= g["size"] <= 8 for g in a.stats)


def test_natural_clusters_are_found():
    g = capacitated_kmeans(POINTS, 6, 7)  # 6 个片区、各 6–7 人
    assert max(s["avg_dist_km"] for s in g.stats) < 6


def test_pin_with_base_plan_keeps_numbering():
    base = capacitated_kmeans(POINTS, 5, 8)
    target = 3 if base.assignment["D0012"] != 3 else 2
    centroids = {s["group_no"]: tuple(s["centroid"]) for s in base.stats}
    adj = capacitated_kmeans(POINTS, 5, 8, pins={"D0012": target}, init_centroids=centroids)
    assert adj.assignment["D0012"] == target
    moved = [p for p in base.assignment if base.assignment[p] != adj.assignment[p]]
    assert len(moved) <= 4  # 微调只影响少数人，组号不重排


@pytest.mark.parametrize(
    "kwargs, code",
    [
        ({"k": 2, "max_size": 10}, "INFEASIBLE"),
        ({"k": 0}, "BAD_GROUP_COUNT"),
        ({"k": 5, "max_size": 8, "pins": {"D9999": 1}}, "PIN_UNKNOWN_ID"),
        ({"k": 5, "max_size": 8, "pins": {"D0012": 9}}, "PIN_BAD_GROUP"),
    ],
)
def test_errors(kwargs, code):
    with pytest.raises(ToolError) as e:
        capacitated_kmeans(POINTS, **kwargs)
    assert e.value.code == code and e.value.fix_hint
