"""模型对照评测（agent.evaluate）：用场景剧本的示例对照规则模式与被评测的模型。"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from agent.evaluate import EvaluationError, Trace, compare, evaluate, render
from agent.rule_planner import RulePlanner

from helpers import dispatch_version, fake_llm_app


def test_model_matching_the_playbooks_passes(settings, transport):
    """被评测的“模型”是藏在 OpenAI 兼容接口后面的规则规划器：工具序列应与参考完全一致。"""

    async def body():
        transport.routes["llm.test:9000"] = httpx.ASGITransport(app=fake_llm_app(RulePlanner(settings), []))
        settings.llm_base_url, settings.llm_model, settings.llm_api_key = "http://llm.test:9000/v1", "fake-model", "sk-test"
        model, reports = await evaluate(settings, transport)
        assert model == "fake-model" and [r.id for r in reports] == ["regroup", "stops", "track", "locate"]
        by_id = {r.id: r for r in reports}
        assert by_id["regroup"].passed and by_id["stops"].passed and not any(r.problems for r in reports)
        # 只靠界面工具的场景：评测环境没有系统页面，参考运行什么都没调用，标为未评测而不是“通过”
        assert by_id["track"].skipped and by_id["locate"].skipped and not by_id["track"].passed
        regroup = reports[0]
        assert regroup.model.tools == regroup.reference.tools and regroup.model.tools[-1] == "dispatch__apply_regroup"
        assert regroup.reference.confirm_requested and regroup.model.confirm_requested
        text = render(model, reports)
        assert "2/2 个场景通过，2 个未评测" in text and "– track" in text
        # 默认自动取消确认：流程走到了确认，但业务系统没有被修改；正式数据目录也没有被写入
        async with httpx.AsyncClient(transport=transport) as http:
            assert (await http.get("http://localhost:8103/api/groups?region=east-china")).json()["version"] == 1
        assert not (settings.data_dir / "conversations").exists()

    asyncio.run(body())


def test_model_skipping_the_write_fails(settings, transport):
    class SkipsApply:
        """除了不肯调用提交工具，其余与规则规划器相同。"""

        label = "skips-apply"

        def __init__(self):
            self.inner = RulePlanner(settings)

        async def complete(self, messages, tools, on_delta=None):
            msg = await self.inner.complete(messages, tools)
            calls = [c for c in msg.get("tool_calls") or [] if c["function"]["name"] != "dispatch__apply_regroup"]
            if msg.get("tool_calls") and not calls:
                return {"role": "assistant", "content": "已预览，请自行到调度系统提交。"}
            return {**msg, "tool_calls": calls} if calls else msg

    async def body():
        model, reports = await evaluate(settings, transport, only={"regroup", "stops"}, llm=SkipsApply())
        by_id = {r.id: r for r in reports}
        assert set(by_id) == {"regroup", "stops"} and by_id["stops"].passed
        bad = by_id["regroup"]
        assert not bad.passed and any("dispatch__apply_regroup" in p for p in bad.problems)
        assert any("没有走到写操作确认" in p for p in bad.problems)
        text = render(model, reports)
        assert "✗ regroup" in text and "1/2 个场景通过" in text and "参考：" in text

    asyncio.run(body())


def test_approve_commits_and_environment_errors(settings, transport):
    async def body():
        from agent.server import Platform

        # --approve：确认卡片点“确认”，参考和模型各提交一次
        _, reports = await evaluate(settings, transport, only={"regroup"}, approve=True, llm=RulePlanner(settings))
        assert reports[0].passed
        p = Platform(settings, transport)
        assert await dispatch_version(p) == 3
        await p.http.aclose()

        with pytest.raises(EvaluationError, match="没有配置模型"):
            await evaluate(settings, transport)
        with pytest.raises(EvaluationError, match="不在场景剧本里"):
            await evaluate(settings, transport, only={"nope"}, llm=RulePlanner(settings))
        # 系统没启动：明确报环境问题，而不是把每个场景都判成不通过
        down = httpx.MockTransport(lambda request: (_ for _ in ()).throw(httpx.ConnectError("refused", request=request)))
        with pytest.raises(EvaluationError, match="业务系统没有就绪"):
            await evaluate(settings, down, llm=RulePlanner(settings))

    asyncio.run(body())


def test_compare_rules():
    ref = Trace(tools=["a", "b", "c"], status="completed", steps=3)
    assert compare(ref, Trace(tools=["a", "b", "c"], status="completed", steps=3)) == ([], [])
    problems, notes = compare(ref, Trace(tools=["a", "x", "c", "b"], status="completed", steps=5, errors=["x（HTTP_400）"]))
    assert problems == [] and notes == ["调用顺序与参考不同", "多出的调用：x", "失败过的调用：x（HTTP_400）", "多用了 2 轮模型调用"]
    problems, _ = compare(ref, Trace(tools=["a"], status="error", error="模型接口返回 500", confirm_requested=True))
    assert problems == ["运行没有完成（error）：模型接口返回 500", "没有调用：b、c", "请求了写操作确认，但这个场景不应有写操作"]
