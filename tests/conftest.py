"""测试夹具：不占端口，用 ASGI transport 把对模拟系统的 HTTP 请求直接路由到各系统的 app。"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.config import Settings  # noqa: E402
from systems.dispatch import app as dispatch_mod  # noqa: E402
from systems.fleet.app import app as fleet_app  # noqa: E402
from systems.tms.app import app as tms_app  # noqa: E402


class RouterTransport(httpx.AsyncBaseTransport):
    def __init__(self, routes: dict[str, object]):
        self.routes = {k: httpx.ASGITransport(app=v) for k, v in routes.items()}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        key = f"{request.url.host}:{request.url.port}"
        transport = self.routes.get(key)
        if transport is None:
            raise httpx.ConnectError(f"no route for {key}", request=request)
        return await transport.handle_async_request(request)


@pytest.fixture
def transport() -> RouterTransport:
    dispatch_mod._initial_state()
    return RouterTransport(
        {
            "localhost:8101": fleet_app,
            "localhost:8102": tms_app,
            "localhost:8103": dispatch_mod.app,
        }
    )


@pytest.fixture
def settings(tmp_path, monkeypatch) -> Settings:
    for var in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY", "LLM_STREAM", "FLEET_URL", "TMS_URL", "DISPATCH_URL", "APP_NAME"):
        monkeypatch.delenv(var, raising=False)
    s = Settings()
    s.data_dir = tmp_path / "data"
    s.web_dir = tmp_path / "no-web"  # 测试不依赖前端构建产物
    s.confirm_timeout_s = 5
    s.ui_call_timeout_s = 3
    s.demo_stream_delay_s = 0
    return s
