"""能力网关：工具生成、参数校验、结果引用、写操作确认、错误信封。"""
from __future__ import annotations

import asyncio

import pytest

from agent.contracts import ToolSpec
from agent.executor import batches, parse_calls
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
        names = {s.name for s in p.gateway.catalog("s1", "local")}
        assert {
            "fleet__search_drivers", "fleet__get_driver_positions", "fleet__search_stops", "fleet__list_regions",
            "tms__search_shift_drivers", "dispatch__get_groups", "dispatch__preview_regroup", "dispatch__apply_regroup",
            "analytics__run_grouping", "canvas__show", "results__preview", "results__stats",
        } <= names
        assert "fleet__get_track" not in names  # 未在白名单中的接口不暴露
        stops = p.gateway.find("fleet__search_stops", "s1", "local")
        assert "asset_type" not in stops.input_schema["properties"]  # fixed 参数不暴露给模型
        apply = p.gateway.find("dispatch__apply_regroup", "s1", "local")
        assert list(apply.input_schema["properties"]) == ["confirm_token"]
        assert apply.needs_confirmation
        await p.http.aclose()

    run(body())


def test_result_refs_and_grouping(settings, transport):
    async def body():
        p = await _platform(settings, transport)
        ctx = CallContext(user="local", session_id="s1")
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
        ctx = CallContext(user="local", session_id="s1")
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

        ctx = CallContext(user="local", session_id="s1", confirm=confirm)
        plan = await _plan(p, ctx)
        before = (await p.http.get("http://localhost:8103/api/groups?region=east-china")).json()["version"]

        no_token = await p.gateway.call(ctx, "dispatch__apply_regroup", {"confirm_token": "cf_forged"})
        assert no_token["error_code"] == "INVALID_CONFIRM_TOKEN"

        pv = await p.gateway.call(ctx, "dispatch__preview_regroup", {"region": "east-china", "groups": plan})
        assert pv["ok"] and pv["confirm_token"].startswith("cf_") and pv["changes"]
        other = CallContext(user="local", session_id="s2", confirm=confirm)
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


def test_stale_preview_is_not_committed(settings, transport):
    """预览之后数据变了（R3.7）：提交带回预览时的版本，系统拒绝，网关报 PREVIEW_STALE 并作废令牌，不落库。"""

    async def body():
        p = await _platform(settings, transport)
        asked = []

        async def confirm(entry):
            asked.append(entry)
            return True

        async def version() -> int:
            return (await p.http.get("http://localhost:8103/api/groups?region=east-china")).json()["version"]

        ctx = CallContext(user="local", session_id="s1", confirm=confirm)
        plan = await _plan(p, ctx)
        first = await p.gateway.call(ctx, "dispatch__preview_regroup", {"region": "east-china", "groups": plan})
        second = await p.gateway.call(ctx, "dispatch__preview_regroup", {"region": "east-china", "groups": plan})
        assert p.gateway.confirms[first["confirm_token"]].etag == "east-china-v1"
        assert "etag" not in (first.get("data") or {})  # 版本标识由网关保管，不进模型上下文
        before = await version()

        # 两份预览基于同一个版本；第一份提交后，第二份用户确认的清单已经过时
        assert (await p.gateway.call(ctx, "dispatch__apply_regroup", {"confirm_token": first["confirm_token"]}))["ok"]
        stale = await p.gateway.call(ctx, "dispatch__apply_regroup", {"confirm_token": second["confirm_token"]})
        assert stale["error_code"] == "PREVIEW_STALE" and "dispatch__preview_regroup" in stale["fix_hint"]
        assert len(asked) == 2 and await version() == before + 1  # 用户确认过，但没有落库
        entry = p.audit.tail()[0]
        assert entry["error_code"] == "PREVIEW_STALE" and entry["confirmed"] is True and entry["ok"] is False

        # 令牌已作废：换一次运行再提交，直接拒绝，不再打扰用户
        later = CallContext(user="local", session_id="s1", confirm=confirm)
        again = await p.gateway.call(later, "dispatch__apply_regroup", {"confirm_token": second["confirm_token"]})
        assert again["error_code"] == "PREVIEW_STALE" and len(asked) == 2 and await version() == before + 1

        # 重新预览拿到新版本，照常提交
        fresh = await p.gateway.call(later, "dispatch__preview_regroup", {"region": "east-china", "groups": plan})
        assert p.gateway.confirms[fresh["confirm_token"]].etag == "east-china-v2"
        assert (await p.gateway.call(later, "dispatch__apply_regroup", {"confirm_token": fresh["confirm_token"]}))["ok"]
        await p.http.aclose()

    run(body())


def test_write_without_ui_channel_is_refused(settings, transport):
    async def body():
        p = await _platform(settings, transport)
        ctx = CallContext(user="local", session_id="s1")  # 无确认通道（如 MCP 调试端点）
        plan = await _plan(p, ctx)
        pv = await p.gateway.call(ctx, "dispatch__preview_regroup", {"region": "east-china", "groups": plan})
        ap = await p.gateway.call(ctx, "dispatch__apply_regroup", {"confirm_token": pv["confirm_token"]})
        assert ap["error_code"] == "CONFIRMATION_REQUIRED"
        await p.http.aclose()

    run(body())


def test_repeated_failure_is_refused_within_a_run(settings, transport):
    """同一次运行里原样重复一个失败的调用：网关直接拒绝（REPEATED_CALL）并记审计，改了参数或换了运行不受影响。"""

    async def body():
        p = await _platform(settings, transport)
        ctx = CallContext(user="local", session_id="s1", run_id="r1")
        first = await p.gateway.call(ctx, "fleet__search_drivers", {"region": "huadong"})
        assert first["error_code"] == "HTTP_400"
        again = await p.gateway.call(ctx, "fleet__search_drivers", {"region": "huadong"})
        assert again["error_code"] == "REPEATED_CALL" and "east-china" in again["message"] and again["fix_hint"]
        assert (await p.gateway.call(ctx, "fleet__search_drivers", {"region": "east-china"}))["ok"]
        fresh = CallContext(user="local", session_id="s1", run_id="r2")
        assert (await p.gateway.call(fresh, "fleet__search_drivers", {"region": "huadong"}))["error_code"] == "HTTP_400"
        codes = [e["error_code"] for e in p.audit.tail() if e["tool"] == "fleet__search_drivers"]
        assert codes.count("REPEATED_CALL") == 1 and codes.count("HTTP_400") == 2
        await p.http.aclose()

    run(body())


def test_select_routes_by_keywords_and_preferred_systems(settings, transport):
    """工具多时只加载相关系统：关键词命中的系统 + 运行时给出的偏好（识别出的意图、上一轮用过的系统）。"""

    async def body():
        p = await _platform(settings, transport)
        settings.full_catalog_limit = 5
        everything = p.gateway.catalog("s1", "local")
        assert p.gateway.select("s1", "local", "你好") == everything  # 什么都没命中时全量加载
        follow_up = {s.system for s in p.gateway.select("s1", "local", "就按这个来", prefer={"dispatch", "canvas"})}
        assert follow_up == {"dispatch", "analytics", "canvas", "results"}
        mixed = {s.system for s in p.gateway.select("s1", "local", "看看明天的排班", prefer={"dispatch"})}
        assert mixed == {"tms", "dispatch", "analytics", "canvas", "results"}
        await p.http.aclose()

    run(body())


def test_executor_batches_reads_and_isolates_writes():
    """相邻的只读 / 只改界面调用并行；写操作、未知工具、参数有误的调用各自单独执行，顺序不变。"""
    risks = {"a__r1": "read", "a__r2": "read", "a__w": "write", "b__v1": "view", "b__v2": "view"}
    specs = {n: ToolSpec(name=n, system=n[0], kind="api", description="样例工具", risk=r) for n, r in risks.items()}
    names = ["a__r1", "a__r2", "a__w", "b__v1", "b__v2", "zz__unknown", "a__r1"]
    calls = [{"id": str(i), "function": {"name": n, "arguments": "{}"}} for i, n in enumerate(names)]
    calls += [
        {"id": "bad", "function": {"name": "a__r2", "arguments": "{oops"}},
        {"id": "null", "function": {"name": "b__v1", "arguments": "null"}},
    ]
    planned = parse_calls(calls)
    assert planned[-2].error["error_code"] == "INVALID_ARGS_JSON" and planned[-1].args == {}
    groups = [[p.name for p in b] for b in batches(planned, specs)]
    assert groups == [["a__r1", "a__r2"], ["a__w"], ["b__v1", "b__v2"], ["zz__unknown"], ["a__r1"], ["a__r2"], ["b__v1"]]


async def _plan(p: Platform, ctx: CallContext) -> str:
    r1 = await p.gateway.call(ctx, "tms__search_shift_drivers", {"region": "east-china", "date": "2026-10-28", "shift": "early"})
    r2 = await p.gateway.call(ctx, "fleet__get_driver_positions", {"driver_ids": r1["result_id"]})
    r3 = await p.gateway.call(ctx, "analytics__run_grouping", {"points": r2["result_id"], "groups": 5, "max_size": 8})
    return r3["result_id"]
