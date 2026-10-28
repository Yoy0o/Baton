"""能力网关：工具生成、参数校验、结果引用、写操作确认、错误信封。"""
from __future__ import annotations

import asyncio

import pytest

from agent.providers import CallContext
from agent.server import Platform


def run(coro):
    return asyncio.run(coro)


async def _platform(settings, transport) -> Platform:
    p = Platform(settings, transport)
    await p.gateway.ensure_loaded(force=True)
    assert p.gateway.load_errors() == {}
    return p


def test_catalog_generated_from_openapi(settings, transport):
    async def body():
        p = await _platform(settings, transport)
        names = {s.name for s in p.gateway.catalog("s1", "demo")}
        assert {
            "fleet__search_drivers", "fleet__get_driver_positions", "fleet__search_stops", "fleet__list_regions",
            "tms__search_shift_drivers", "dispatch__get_groups", "dispatch__preview_regroup", "dispatch__apply_regroup",
            "analytics__run_grouping", "canvas__show", "results__preview", "results__stats",
        } <= names
        assert "fleet__get_track" not in names  # 未在白名单中的接口不暴露
        stops = p.gateway.find("fleet__search_stops", "s1", "demo")
        assert "asset_type" not in stops.input_schema["properties"]  # fixed 参数不暴露给模型
        apply = p.gateway.find("dispatch__apply_regroup", "s1", "demo")
        assert list(apply.input_schema["properties"]) == ["confirm_token"]
        assert apply.needs_confirmation
        await p.http.aclose()

    run(body())


def test_result_refs_and_grouping(settings, transport):
    async def body():
        p = await _platform(settings, transport)
        ctx = CallContext(user="demo", session_id="s1")
        r1 = await p.gateway.call(ctx, "tms__search_shift_drivers", {"region": "east-china", "date": "2026-10-28", "shift": "early"})
        assert r1["ok"] and r1["total"] == 38 and r1["result_type"] == "table"
        assert len(r1["preview"]) == 5
        r2 = await p.gateway.call(ctx, "fleet__get_driver_positions", {"driver_ids": r1["result_id"]})
        assert r2["ok"] and r2["result_type"] == "feature_collection" and r2["total"] == 38
        r3 = await p.gateway.call(ctx, "analytics__run_grouping", {"points": r2["result_id"], "groups": 5, "max_size": 8})
        assert r3["ok"] and r3["result_type"] == "group_plan"
        assert all(g["size"] <= 8 for g in r3["data"]["groups"])
        # 逗号分隔的字面值也能用
        r4 = await p.gateway.call(ctx, "fleet__get_driver_positions", {"driver_ids": "D0001,D0002"})
        assert r4["total"] == 2
        await p.http.aclose()

    run(body())


def test_errors_are_correctable(settings, transport):
    async def body():
        p = await _platform(settings, transport)
        ctx = CallContext(user="demo", session_id="s1")
        bad = await p.gateway.call(ctx, "fleet__get_driver_positions", {"driver_ids": 123})
        assert bad["error_code"] == "INVALID_ARGS" and "fix_hint" in bad
        unknown = await p.gateway.call(ctx, "fleet__search_driver", {})
        assert unknown["error_code"] == "UNKNOWN_TOOL" and "fleet__search_drivers" in unknown["fix_hint"]
        http = await p.gateway.call(ctx, "fleet__search_drivers", {"region": "huadong"})
        assert http["error_code"] == "HTTP_400" and "east-china" in http["message"]
        ui = await p.gateway.call(ctx, "fleet__show_result_layer", {"result_id": "res_x"})
        assert ui["error_code"] == "UI_NOT_CONNECTED"
        ref = await p.gateway.call(ctx, "analytics__run_grouping", {"points": "res_doesnotexist", "groups": 2})
        assert ref["error_code"] == "RESULT_NOT_FOUND"
        infeasible = await p.gateway.call(
            ctx, "analytics__run_grouping",
            {"points": (await p.gateway.call(ctx, "fleet__get_driver_positions", {"driver_ids": "D0001,D0002,D0003"}))["result_id"],
             "groups": 1, "max_size": 2},
        )
        assert infeasible["error_code"] == "INFEASIBLE" and "fix_hint" in infeasible
        assert len(p.audit.tail()) >= 6
        await p.http.aclose()

    run(body())


@pytest.mark.parametrize("approve", [True, False])
def test_write_requires_preview_and_confirmation(settings, transport, approve):
    async def body():
        p = await _platform(settings, transport)
        asked = []

        async def confirm(entry):
            asked.append(entry)
            return approve

        ctx = CallContext(user="demo", session_id="s1", confirm=confirm)
        plan = await _plan(p, ctx)
        before = (await p.http.get("http://localhost:8103/api/groups?region=east-china")).json()["version"]

        no_token = await p.gateway.call(ctx, "dispatch__apply_regroup", {"confirm_token": "cf_forged"})
        assert no_token["error_code"] == "INVALID_CONFIRM_TOKEN"

        pv = await p.gateway.call(ctx, "dispatch__preview_regroup", {"region": "east-china", "groups": plan})
        assert pv["ok"] and pv["confirm_token"].startswith("cf_") and pv["changes"]
        other = CallContext(user="demo", session_id="s2", confirm=confirm)
        stolen = await p.gateway.call(other, "dispatch__apply_regroup", {"confirm_token": pv["confirm_token"]})
        assert stolen["error_code"] == "CONFIRM_TOKEN_FORBIDDEN"

        ap = await p.gateway.call(ctx, "dispatch__apply_regroup", {"confirm_token": pv["confirm_token"]})
        assert len(asked) == 1 and asked[0].changes
        after = (await p.http.get("http://localhost:8103/api/groups?region=east-china")).json()["version"]
        if approve:
            assert ap["ok"] and after == before + 1
            again = await p.gateway.call(ctx, "dispatch__apply_regroup", {"confirm_token": pv["confirm_token"]})
            assert again["ok"] and "未重复执行" in again["summary"] and len(asked) == 1
            assert (await p.http.get("http://localhost:8103/api/groups?region=east-china")).json()["version"] == after
        else:
            assert ap["error_code"] == "USER_REJECTED" and after == before
        entries = [e for e in p.audit.tail() if e["tool"] == "dispatch__apply_regroup" and e["confirmed"] is not None]
        assert entries and entries[0]["confirmed"] is approve
        await p.http.aclose()

    run(body())


def test_write_without_ui_channel_is_refused(settings, transport):
    async def body():
        p = await _platform(settings, transport)
        ctx = CallContext(user="demo", session_id="s1")  # 无确认通道（如 MCP 调试端点）
        plan = await _plan(p, ctx)
        pv = await p.gateway.call(ctx, "dispatch__preview_regroup", {"region": "east-china", "groups": plan})
        ap = await p.gateway.call(ctx, "dispatch__apply_regroup", {"confirm_token": pv["confirm_token"]})
        assert ap["error_code"] == "CONFIRMATION_REQUIRED"
        await p.http.aclose()

    run(body())


async def _plan(p: Platform, ctx: CallContext) -> str:
    r1 = await p.gateway.call(ctx, "tms__search_shift_drivers", {"region": "east-china", "date": "2026-10-28", "shift": "early"})
    r2 = await p.gateway.call(ctx, "fleet__get_driver_positions", {"driver_ids": r1["result_id"]})
    r3 = await p.gateway.call(ctx, "analytics__run_grouping", {"points": r2["result_id"], "groups": 5, "max_size": 8})
    return r3["result_id"]
