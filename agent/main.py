"""服务入口：uvicorn agent.main:app"""
from __future__ import annotations

import logging

from .config import load_dotenv
from .server import create_app

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # 每次系统调用都已写入审计日志
app = create_app()
