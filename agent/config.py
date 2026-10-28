"""运行配置：全部来自环境变量，便于本地、容器和测试切换。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass
class Settings:
    # 产品名称只在这里定义：工作台标题、服务名、MCP serverInfo 都从这里读取
    app_name: str = field(default_factory=lambda: _env("APP_NAME", "Baton") or "Baton")
    app_tagline: str = field(default_factory=lambda: _env("APP_TAGLINE", "多系统编排台"))

    host: str = field(default_factory=lambda: _env("AGENT_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_env("AGENT_PORT", "8000")))
    # 浏览器访问 Agent 服务的地址：内嵌系统页面用它连接桥接中心、读取结果
    public_url: str = field(default_factory=lambda: _env("AGENT_PUBLIC_URL", "http://localhost:8000"))
    manifests_glob: str = field(
        default_factory=lambda: _env("AGENT_MANIFESTS", str(ROOT / "systems" / "*" / "manifest.yaml"))
    )
    data_dir: Path = field(default_factory=lambda: Path(_env("AGENT_DATA_DIR", str(ROOT / "data"))))
    # 工作台前端构建产物（web/ 下 npm run build 生成）
    web_dir: Path = field(default_factory=lambda: Path(_env("AGENT_WEB_DIR", str(ROOT / "web" / "dist"))))

    llm_base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL"))
    llm_api_key: str = field(default_factory=lambda: _env("LLM_API_KEY"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL"))
    llm_timeout_s: float = field(default_factory=lambda: float(_env("LLM_TIMEOUT_S", "90")))
    llm_temperature: float = field(default_factory=lambda: float(_env("LLM_TEMPERATURE", "0.2")))
    # 流式输出：模型接口不支持 stream 时自动退回一次性返回
    llm_stream: bool = field(default_factory=lambda: _env("LLM_STREAM", "true").lower() not in ("0", "false", "no", "off"))
    # 演示模式下模拟流式输出的字间隔（秒），0 表示不模拟
    demo_stream_delay_s: float = field(default_factory=lambda: float(_env("DEMO_STREAM_DELAY_S", "0.012")))

    max_steps: int = field(default_factory=lambda: int(_env("AGENT_MAX_STEPS", "12")))
    confirm_timeout_s: float = field(default_factory=lambda: float(_env("AGENT_CONFIRM_TIMEOUT_S", "600")))
    ui_call_timeout_s: float = field(default_factory=lambda: float(_env("AGENT_UI_CALL_TIMEOUT_S", "20")))
    http_timeout_s: float = field(default_factory=lambda: float(_env("AGENT_HTTP_TIMEOUT_S", "30")))
    result_ttl_s: int = field(default_factory=lambda: int(_env("AGENT_RESULT_TTL_S", str(24 * 3600))))
    # 工具总数不超过该值时全量加载；超过时按系统关键词路由，只加载相关系统
    full_catalog_limit: int = field(default_factory=lambda: int(_env("AGENT_FULL_CATALOG_LIMIT", "30")))
    demo_user: str = field(default_factory=lambda: _env("AGENT_DEMO_USER", "demo"))
    timezone: str = field(default_factory=lambda: _env("AGENT_TZ", "Asia/Shanghai"))

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_base_url and self.llm_model)


def load_dotenv(path: Path | None = None) -> None:
    """极简 .env 读取：KEY=VALUE，忽略注释；不覆盖已有环境变量。"""
    path = path or (ROOT / ".env")
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
