"""运行配置：时区解析（模型模式与规则模式共用 Settings.tzinfo）。"""
from __future__ import annotations

from datetime import datetime, timedelta

from agent.config import Settings


def _offset(s: Settings) -> timedelta | None:
    return datetime.now(s.tzinfo).utcoffset()


def test_tzinfo_follows_agent_tz(settings):
    assert settings.timezone == "Asia/Shanghai" and _offset(settings) == timedelta(hours=8)
    settings.timezone = "Asia/Tokyo"
    assert _offset(settings) == timedelta(hours=9)
    settings.timezone = "UTC"
    assert _offset(settings) == timedelta(0)


def test_tzinfo_falls_back_to_utc8_on_unknown_name(settings):
    settings.timezone = "Not/AZone"
    assert _offset(settings) == timedelta(hours=8)
