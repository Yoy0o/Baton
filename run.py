"""一键启动：三个模拟业务系统 + Agent 服务。

    python run.py              # 启动全部，浏览器打开 http://localhost:8000
    python run.py --agent-only # 只启动 Agent 服务（业务系统已在别处运行时）

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

from agent.config import Settings, load_dotenv  # noqa: E402

# 模拟业务系统：(名称, ASGI 入口, 地址变量, 默认端口)。地址变量属于接入清单（manifest.yaml 以 ${VAR:-默认值} 引用），
# 不在 Settings 里，所以这里按清单的方式直接读环境变量。
SYSTEMS = [
    ("车队追踪地图台", "systems.fleet.app:app", "FLEET_URL", 8101),
    ("排班系统", "systems.tms.app:app", "TMS_URL", 8102),
    ("调度系统", "systems.dispatch.app:app", "DISPATCH_URL", 8103),
]


def _port(url: str, default: int) -> int:
    return urlsplit(url).port or default


def main() -> None:
    load_dotenv()
    settings = Settings()
    parser = argparse.ArgumentParser(description=f"启动 {settings.app_name}：模拟业务系统 + Agent 服务")
    parser.add_argument("--agent-only", action="store_true", help="只启动 Agent 服务")
    args = parser.parse_args()

    # (名称, ASGI 入口, 监听地址, 端口)
    services: list[tuple[str, str, str, int]] = []
    if not args.agent_only:
        services += [(name, target, "127.0.0.1", _port(os.getenv(var, ""), port)) for name, target, var, port in SYSTEMS]
    services.append(("Agent 服务", "agent.main:app", settings.host, settings.port))

    procs: list[subprocess.Popen] = []
    try:
        for name, target, host, port in services:
            cmd = [sys.executable, "-m", "uvicorn", target, "--host", host, "--port", str(port), "--log-level", "warning"]
            procs.append(subprocess.Popen(cmd, cwd=ROOT))
            print(f"  启动 {name:<8} http://localhost:{port}")
            time.sleep(0.6)  # 让业务系统先就绪，Agent 启动时即可读取它们的 OpenAPI
        llm = f"已配置模型 {settings.llm_model}" if settings.llm_enabled else "规则模式（未配置模型）"
        print(f"\n  {settings.app_name} 工作台：http://localhost:{settings.port}    {llm}\n  按 Ctrl+C 退出\n")
        if not (settings.web_dir / "index.html").exists():
            print("  提示：工作台前端尚未构建，请先执行 cd web && npm ci && npm run build\n")
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
