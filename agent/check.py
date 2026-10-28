"""接入检查工具：校验接入清单、读取系统 OpenAPI、打印将生成的工具。

    python -m agent.check                         # 检查 systems/*/manifest.yaml
    python -m agent.check systems/fleet/manifest.yaml -v   # 检查单个系统并打印参数 schema

需要被检查的系统已启动（要读取其 OpenAPI 文档）。退出码：0 通过，1 有错误。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

import httpx

from .config import Settings, load_dotenv
from .contracts import TOOL_NAME_RE
from .manifest import ManifestError, load_manifests
from .providers.openapi_provider import OpenAPIProvider
from .results import ResultStore


async def run(pattern: str, verbose: bool) -> int:
    try:
        manifests = load_manifests(pattern)
    except ManifestError as e:
        print("✗ 接入清单校验失败：")
        for line in str(e).splitlines():
            print("   ", line)
        return 1
    if not manifests:
        print(f"✗ 没有找到接入清单：{pattern}")
        return 1

    errors = warnings = 0
    store = ResultStore(Path(tempfile.mkdtemp()))
    async with httpx.AsyncClient() as http:
        for m in manifests:
            print(f"\n== {m.system} · {m.title}  ({m.path})")
            if m.api:
                provider = OpenAPIProvider(m, http, store)
                await provider.load()
                for err in provider.errors:
                    print("  ✗", err)
                    errors += 1
                for spec in provider.specs():
                    params = ", ".join(spec.input_schema.get("properties", {})) or "—"
                    pair = f"  → 确认后提交 {spec.pairs_with}" if spec.pairs_with else ""
                    print(f"  ✓ {spec.name:<34} {spec.risk:<6} 参数：{params}{pair}")
                    if verbose:
                        print("    " + json.dumps(spec.input_schema, ensure_ascii=False))
                    if not TOOL_NAME_RE.match(spec.name):
                        print(f"  ✗ 工具名不合规：{spec.name}")
                        errors += 1
            if m.ui_bridge.enabled:
                tools = ", ".join(f"{t.tool}({t.risk})" for t in m.ui_bridge.tools)
                print(f"  · 界面工具白名单（页面运行时注册）：{tools}")
                if not m.ui_bridge.embed_url:
                    print("  ! 未声明 ui_bridge.embed_url，工作台无法内嵌该系统页面")
                    warnings += 1
            if not m.skill_text:
                print("  ! 未提供 SKILL.md 知识包（建议，R6.2）")
                warnings += 1
    print(f"\n结果：{len(manifests)} 个系统，{errors} 个错误，{warnings} 个提醒")
    return 1 if errors else 0


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="检查系统接入清单")
    parser.add_argument("pattern", nargs="?", default=Settings().manifests_glob, help="清单路径或 glob")
    parser.add_argument("-v", "--verbose", action="store_true", help="打印每个工具的参数 schema")
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args.pattern, args.verbose)))


if __name__ == "__main__":
    main()
