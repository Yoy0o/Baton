"""审计日志：每次工具调用一行 JSON（data/audit.jsonl），工作台“审计日志”页读取最近记录。"""
from __future__ import annotations

import json
import time
from collections import deque
from pathlib import Path
from typing import Any


class AuditLog:
    def __init__(self, data_dir: Path, keep: int = 500):
        self.path = Path(data_dir) / "audit.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.recent: deque[dict[str, Any]] = deque(maxlen=keep)

    def record(self, **entry: Any) -> dict[str, Any]:
        entry = {"ts": round(time.time(), 3), **entry}
        if "args" in entry:
            blob = json.dumps(entry["args"], ensure_ascii=False, default=str)
            entry["args"] = blob if len(blob) <= 1000 else blob[:1000] + "…"
        self.recent.append(entry)
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except OSError:
            pass
        return entry

    def tail(self, limit: int = 100) -> list[dict[str, Any]]:
        return list(self.recent)[-limit:][::-1]
