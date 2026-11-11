"""审计日志：每次工具调用一行 JSON（data/audit.jsonl），工作台“审计日志”页读取最近记录。

- 启动时从文件读回最近的记录，服务重启后审计页签不会变空。
- 文件超过上限时改名为 audit-<时间>.jsonl 留存，再从空文件写起；审计是留痕，平台不自动删除，保留多久由运维决定。
"""
from __future__ import annotations

import json
import logging
import time
from collections import deque
from datetime import datetime, tzinfo
from pathlib import Path
from typing import Any

log = logging.getLogger("agent.audit")

_TAIL_BYTES = 2 * 1024 * 1024  # 启动时最多读文件末尾这么多字节来恢复最近记录


class JsonlLog:
    """一行一条 JSON 的追加日志：内存里留最近 keep 条（启动时从文件读回），文件按大小轮转、不删除。

    审计（audit.jsonl）和意图识别记录（intents.jsonl）共用。
    """

    def __init__(self, path: Path, keep: int = 500, rotate_bytes: int = 0, tz: tzinfo | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.rotate_bytes = rotate_bytes  # 0 表示不轮转
        self.tz = tz
        self.recent: deque[dict[str, Any]] = deque(self._load_recent(keep), maxlen=keep)

    def _load_recent(self, keep: int) -> list[dict[str, Any]]:
        try:
            with self.path.open("rb") as f:
                size = f.seek(0, 2)
                start = max(size - _TAIL_BYTES, 0)
                f.seek(start)
                lines = f.read().decode("utf-8", "replace").splitlines()
        except OSError:
            return []
        if start > 0:
            lines = lines[1:]  # 从文件中间读起：第一行可能不完整
        out: list[dict[str, Any]] = []
        for line in reversed(lines):  # 从最新的往回取，凑够 keep 条为止
            if len(out) >= keep:
                break
            try:
                entry = json.loads(line)
            except ValueError:
                continue  # 进程被强杀时可能留下半行
            if isinstance(entry, dict):
                out.append(entry)
        return out[::-1]

    def append(self, entry: dict[str, Any]) -> dict[str, Any]:
        self.recent.append(entry)
        try:
            self._rotate_if_needed()
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except OSError as e:
            log.warning("%s 写入失败：%s", self.path.name, e)
        return entry

    def _rotate_if_needed(self) -> None:
        if self.rotate_bytes <= 0 or not self.path.exists() or self.path.stat().st_size < self.rotate_bytes:
            return
        stamp = datetime.now(self.tz).strftime("%Y%m%d-%H%M%S")
        target = self.path.with_name(f"{self.path.stem}-{stamp}{self.path.suffix}")
        n = 1
        while target.exists():  # 同一秒内轮转两次
            n += 1
            target = self.path.with_name(f"{self.path.stem}-{stamp}-{n}{self.path.suffix}")
        self.path.rename(target)
        log.info("%s 已轮转为 %s", self.path.name, target.name)

    def tail(self, limit: int = 100) -> list[dict[str, Any]]:
        return list(self.recent)[-limit:][::-1]


class AuditLog(JsonlLog):
    def __init__(self, data_dir: Path, keep: int = 500, rotate_bytes: int = 0, tz: tzinfo | None = None):
        super().__init__(Path(data_dir) / "audit.jsonl", keep, rotate_bytes, tz)

    def record(self, **entry: Any) -> dict[str, Any]:
        entry = {"ts": round(time.time(), 3), **entry}
        if "args" in entry:
            blob = json.dumps(entry["args"], ensure_ascii=False, default=str)
            entry["args"] = blob if len(blob) <= 1000 else blob[:1000] + "…"
        return self.append(entry)
