"""一键启动演示：三个模拟业务系统 + Agent 服务。

    python run_demo.py              # 启动全部，浏览器打开 http://localhost:8000
    python run_demo.py --agent-only # 只启动 Agent 服务（业务系统已在别处运行时）

每个服务是独立进程，和真实部署一致；Ctrl+C 全部退出。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from agent.config import load_dotenv  # noqa: E402


def _port(url: str, default: int) -> int:
    return urlsplit(url).port or default


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="启动 Baton 演示")
    parser.add_argument("--agent-only", action="store_true", help="只启动 Agent 服务")
    args = parser.parse_args()

    services = []
    if not args.agent_only:
        services += [
            ("车队追踪地图台", "systems.fleet.app:app", _port(os.getenv("FLEET_URL", ""), 8101)),
            ("排班系统", "systems.tms.app:app", _port(os.getenv("TMS_URL", ""), 8102)),
            ("调度系统", "systems.dispatch.app:app", _port(os.getenv("DISPATCH_URL", ""), 8103)),
        ]
    agent_port = int(os.getenv("AGENT_PORT", "8000"))
    agent_host = os.getenv("AGENT_HOST", "127.0.0.1")
    services.append(("Agent 服务", "agent.main:app", agent_port))

    procs: list[subprocess.Popen] = []
    try:
        for name, target, port in services:
            host = agent_host if target.startswith("agent.") else "127.0.0.1"
            cmd = [sys.executable, "-m", "uvicorn", target, "--host", host, "--port", str(port), "--log-level", "warning"]
            procs.append(subprocess.Popen(cmd, cwd=ROOT))
            print(f"  启动 {name:<8} http://localhost:{port}")
            time.sleep(0.6)  # 让业务系统先就绪，Agent 启动时即可读取它们的 OpenAPI
        llm = "已配置模型 " + os.getenv("LLM_MODEL", "") if os.getenv("LLM_BASE_URL") and os.getenv("LLM_MODEL") else "演示模式（未配置模型）"
        name = os.getenv("APP_NAME", "Baton")
        print(f"\n  {name} 工作台：http://localhost:{agent_port}    {llm}\n  按 Ctrl+C 退出\n")
        if not (ROOT / "web" / "dist" / "index.html").exists():
            print("  提示：工作台前端尚未构建，请先执行 cd web && npm install && npm run build\n")
        while all(p.poll() is None for p in procs):
            time.sleep(0.5)
        print("有服务意外退出，正在关闭其余服务…")
    except KeyboardInterrupt:
        print("\n正在退出…")
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    main()
