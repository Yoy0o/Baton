"""调度系统的适配钩子：把平台的分组方案格式转换成调度系统接口要求的格式。

平台 group_plan 转换结果：[{"group_no": 1, "member_ids": [...]}]
调度系统接口要求：      [{"group_no": 1, "driver_ids": [...]}]

钩子签名：hook(args: dict, ctx: dict) -> dict。ctx 含 tool、user、session_id、system。
只做格式映射，不写业务逻辑（接入规则 R2.8）。
"""
from __future__ import annotations

from typing import Any


def group_plan_to_body(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    groups = args.get("groups")
    if not isinstance(groups, list):
        return args
    out = dict(args)
    out["groups"] = [
        {"group_no": g["group_no"], "driver_ids": list(g.get("member_ids") or g.get("driver_ids") or [])}
        for g in groups
        if isinstance(g, dict)
    ]
    return out
