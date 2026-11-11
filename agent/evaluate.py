"""模型对照评测：用各系统场景剧本的示例，对照“规则模式”和“配置的模型”各跑一遍，比较工具调用。

    python -m agent.evaluate                       # 跑全部可单独发起的场景
    python -m agent.evaluate --only regroup,stops  # 只跑指定场景
    python -m agent.evaluate --json report.json    # 另存一份 JSON 报告
    python -m agent.evaluate --approve             # 确认卡片自动点“确认”（会真的提交到业务系统，只对测试环境用）

为什么这样比：规则模式按剧本执行，它的工具序列就是这个场景的参考答案；模型走的是同一条链路
（运行时 → 网关 → 系统），所以“模型有没有调到该调的工具、有没有走到确认”可以直接对照，不需要另外维护用例。
接入新系统并写了剧本后，评测自动覆盖它。

需要：业务系统已启动（与 agent.check 相同）、已配置 LLM_BASE_URL / LLM_MODEL。
两边各用一个临时数据目录，不读写正式的对话、结果、审计。评测环境里没有内嵌的系统页面：展示类步骤两边都走工作台画布；
完全依赖界面工具的场景（如轨迹回放）参考运行不会调用任何工具，这类场景标为“未评测”，不计入通过数。
确认卡片默认自动点“取消”：能验证流程走到了确认，又不修改业务系统。
退出码：0 评测到的场景全部通过，1 有场景未通过或参考运行失败，2 环境不满足（没配模型、系统没启动、没有可评测的场景）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

import httpx

from .config import Settings, load_dotenv
from .llm import LLM
from .server import Platform
from .transcript import REJECTED_CODES

_CLIENT = "evaluate"


@dataclass
class Trace:
    """一次运行里与评测有关的事实。"""

    tools: list[str] = field(default_factory=list)  # 按开始顺序
    errors: list[str] = field(default_factory=list)  # 失败的调用：工具名 + 错误码（用户取消确认不算）
    confirm_requested: bool = False
    status: str = ""
    error: str = ""
    steps: int = 0
    duration_ms: int = 0
    usage: dict[str, int] = field(default_factory=dict)
    answer: str = ""


@dataclass
class CaseReport:
    id: str
    title: str
    prompt: str
    reference: Trace
    model: Trace
    passed: bool = False
    skipped: bool = False  # 参考运行没有调用任何工具（需要系统页面），没有可对照的东西
    problems: list[str] = field(default_factory=list)  # 不通过的原因
    notes: list[str] = field(default_factory=list)  # 不影响通过、但值得看一眼的差异


class EvaluationError(Exception):
    """环境不满足，评测没法进行。"""


def _trace(events: list[dict[str, Any]]) -> Trace:
    t = Trace()
    for e in events:
        kind = e.get("type")
        if kind == "tool_call_started":
            t.tools.append(str(e.get("tool")))
        elif kind == "tool_call_finished" and not e.get("ok") and e.get("error_code") not in REJECTED_CODES:
            t.errors.append(f"{e.get('tool')}（{e.get('error_code')}）")
        elif kind == "confirm_request":
            t.confirm_requested = True
        elif kind == "message":
            t.answer = str(e.get("text") or "")
        elif kind == "run_error":
            t.error = str(e.get("message") or "")
        elif kind == "run_finished":
            t.status = str(e.get("status") or "")
            t.steps = int(e.get("steps") or 0)
            t.duration_ms = int(e.get("duration_ms") or 0)
            t.usage = dict(e.get("usage") or {})
    return t


def _in_order(needle: list[str], hay: list[str]) -> bool:
    it = iter(hay)
    return all(any(h == n for h in it) for n in needle)


def compare(ref: Trace, got: Trace) -> tuple[list[str], list[str]]:
    """返回（不通过的原因，提醒）。通过的条件：运行完成、参考里的工具都调到了、是否走到确认与参考一致。"""
    problems, notes = [], []
    if got.status != "completed":
        problems.append(f"运行没有完成（{got.status or '无结束事件'}）" + (f"：{got.error}" if got.error else ""))
    missing = [t for t in dict.fromkeys(ref.tools) if got.tools.count(t) < ref.tools.count(t)]
    if missing:
        problems.append("没有调用：" + "、".join(missing))
    elif not _in_order(ref.tools, got.tools):
        notes.append("调用顺序与参考不同")
    if ref.confirm_requested and not got.confirm_requested:
        problems.append("没有走到写操作确认（参考运行请求了确认）")
    if got.confirm_requested and not ref.confirm_requested:
        problems.append("请求了写操作确认，但这个场景不应有写操作")
    extra = [t for t in dict.fromkeys(got.tools) if got.tools.count(t) > ref.tools.count(t)]
    if extra:
        notes.append("多出的调用：" + "、".join(extra))
    if got.errors:
        notes.append("失败过的调用：" + "、".join(got.errors))
    if got.steps > ref.steps:
        notes.append(f"多用了 {got.steps - ref.steps} 轮模型调用")
    return problems, notes


async def _run(p: Platform, prompt: str, approve: bool) -> Trace:
    events: list[dict[str, Any]] = []

    async def send(client_id: str, event: dict[str, Any]) -> None:
        events.append(event)
        if event.get("type") == "confirm_request":
            sess = p.runtime.find(str(event["conversation_id"]))
            if sess is not None:
                p.runtime.resolve_confirmation(sess, str(event["confirm_id"]), approve)

    p.runtime.send = send
    conv = p.conversations.create(p.settings.default_user, prompt[:20])
    sess = p.runtime.session(conv)
    p.runtime.start(sess, prompt, _CLIENT)
    assert sess.task is not None
    await sess.task
    return _trace(events)


async def evaluate(
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
    *,
    only: set[str] | None = None,
    approve: bool = False,
    llm: LLM | None = None,
) -> tuple[str, list[CaseReport]]:
    """返回（被评测的模型名，各场景的报告）。llm 直接给定被评测的模型（测试用）；默认按配置创建。"""
    if llm is None and not settings.llm_enabled:
        raise EvaluationError("没有配置模型：在 .env 里设置 LLM_BASE_URL 和 LLM_MODEL 后再运行")
    work = Path(tempfile.mkdtemp(prefix="evaluate-"))
    # 参考：同样的清单和系统，只是去掉模型配置（即规则模式）；两边互不共享对话、结果、审计
    ref = Platform(replace(settings, llm_base_url="", llm_model="", rule_stream_delay_s=0, data_dir=work / "reference"), transport)
    cand = Platform(replace(settings, data_dir=work / "model"), transport)
    if llm is not None:
        cand.llm = cand.runtime.llm = llm
    try:
        for p in (ref, cand):
            await p.gateway.ensure_loaded(force=True)
        if ref.gateway.load_errors():
            lines = [f"{s}: {e}" for s, errs in ref.gateway.load_errors().items() for e in errs]
            raise EvaluationError("业务系统没有就绪（先 python run.py 启动）：\n  " + "\n  ".join(lines))
        cases = [c for c in ref.intents.presets() if not only or c["id"] in only]
        if not cases:
            raise EvaluationError("没有可评测的场景" + (f"：{', '.join(sorted(only))} 不在场景剧本里" if only else ""))
        reports = []
        for c in cases:
            reference = await _run(ref, c["prompt"], approve)
            model = await _run(cand, c["prompt"], approve)
            report = CaseReport(id=c["id"], title=c["title"], prompt=c["prompt"], reference=reference, model=model)
            if reference.status != "completed" or reference.errors:
                # 参考答案本身不成立（系统数据、剧本或环境有问题），这个场景没法判
                detail = reference.error or "、".join(reference.errors) or reference.status
                report.problems = [f"参考运行（规则模式）失败：{detail}"]
            elif not reference.tools:
                report.skipped = True
                report.notes = ["参考运行没有调用任何工具：这个场景需要已打开的系统页面，评测环境里没有，未评测"]
            else:
                report.problems, report.notes = compare(reference, model)
            report.passed = not report.problems and not report.skipped
            reports.append(report)
        return cand.llm.label, reports
    finally:
        await ref.http.aclose()
        await cand.http.aclose()


def render(model: str, reports: list[CaseReport]) -> str:
    lines = [f"模型对照评测：{model} 对照 规则模式，{len(reports)} 个场景"]
    for r in reports:
        m = r.model
        tokens = f"，{m.usage['total_tokens']} tokens" if m.usage.get("total_tokens") else ""
        mark = "–" if r.skipped else "✓" if r.passed else "✗"
        lines.append(
            f"\n{mark} {r.id} · {r.title}：工具 {len(m.tools)} 次（参考 {len(r.reference.tools)}），"
            f"模型 {m.steps} 轮（参考 {r.reference.steps}），{m.duration_ms / 1000:.1f} 秒{tokens}"
        )
        lines.append(f"    指令：{r.prompt}")
        for text in r.problems:
            lines.append(f"    ✗ {text}")
        for text in r.notes:
            lines.append(f"    ! {text}")
        if not r.skipped and (r.problems or r.notes):
            lines.append("    参考：" + (" → ".join(r.reference.tools) or "（无调用）"))
            lines.append("    模型：" + (" → ".join(m.tools) or "（无调用）"))
    judged = [r for r in reports if not r.skipped]
    skipped = f"，{len(reports) - len(judged)} 个未评测" if len(judged) < len(reports) else ""
    lines.append(f"\n结果：{sum(1 for r in judged if r.passed)}/{len(judged)} 个场景通过{skipped}")
    return "\n".join(lines)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):  # Windows 中文控制台默认 GBK，见 check.py
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_dotenv()
    parser = argparse.ArgumentParser(description="用场景剧本的示例对照规则模式与配置的模型")
    parser.add_argument("--only", default="", help="只跑这些场景（意图 id，逗号分隔）")
    parser.add_argument("--json", dest="json_path", default="", help="另存 JSON 报告的路径")
    parser.add_argument("--approve", action="store_true", help="确认卡片自动点“确认”（会修改业务系统；默认点“取消”）")
    args = parser.parse_args()
    only = {x.strip() for x in args.only.split(",") if x.strip()} or None
    try:
        model, reports = asyncio.run(evaluate(Settings(), only=only, approve=args.approve))
    except EvaluationError as e:
        print(f"✗ {e}")
        sys.exit(2)
    print(render(model, reports))
    if args.json_path:
        payload = {"model": model, "approve": args.approve, "cases": [asdict(r) for r in reports]}
        Path(args.json_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    judged = [r for r in reports if not r.skipped]
    if not judged:
        sys.exit(2)  # 一个都没评到（都需要系统页面）：不能当作通过
    sys.exit(0 if all(r.passed for r in judged) else 1)


if __name__ == "__main__":
    main()
