"""意图识别记录：每次运行一行 JSON（data/intents.jsonl），记下这句话识别成了哪个场景、还是没识别到。

用途：补剧本的依据。规则识别不理解同义改写，没识别到的提问在规则模式下只会得到帮助文本，
在模型模式下模型没有任何提示；把它们汇总出来，才知道该给哪个场景补触发词、该新写哪个场景。
文件与审计日志一样按大小轮转、不删除（audit.JsonlLog）。
"""
from __future__ import annotations

import time
from datetime import tzinfo
from pathlib import Path
from typing import Any

from .audit import JsonlLog

_TEXT_LIMIT = 500  # 提问只留前 500 字：够看出意思，不把长文本整段写进日志


class IntentLog(JsonlLog):
    def __init__(self, data_dir: Path, keep: int = 1000, rotate_bytes: int = 0, tz: tzinfo | None = None):
        super().__init__(Path(data_dir) / "intents.jsonl", keep, rotate_bytes, tz)

    def record(
        self, *, user: str, conversation_id: str, run_id: str, planner: str, text: str, intent: dict[str, Any] | None
    ) -> dict[str, Any]:
        """intent 为 IntentMatch.brief()（{id, title, system, score}），没识别到时为 None。"""
        return self.append(
            {
                "ts": round(time.time(), 3),
                "user": user,
                "conversation_id": conversation_id,
                "run_id": run_id,
                "planner": planner,
                "text": text[:_TEXT_LIMIT],
                "intent": intent,
            }
        )

    def report(self, user: str, limit: int = 100, matched: bool | None = None) -> dict[str, Any]:
        """最近若干次运行（内存里保留的窗口）的识别情况：命中率、各场景命中次数、没识别到的提问（相同的合并计数）。"""
        mine = [e for e in self.recent if e.get("user") == user]
        hit = [e for e in mine if e.get("intent")]
        miss = [e for e in mine if not e.get("intent")]
        by_intent: dict[str, dict[str, Any]] = {}
        for e in hit:
            it = e["intent"]
            row = by_intent.setdefault(it.get("id"), {"id": it.get("id"), "title": it.get("title"), "system": it.get("system"), "count": 0})
            row["count"] += 1
        grouped: dict[str, dict[str, Any]] = {}
        for e in miss:
            key = " ".join(str(e.get("text") or "").split())
            row = grouped.setdefault(key, {"text": key, "count": 0, "last_ts": 0.0})
            row["count"] += 1
            row["last_ts"] = max(row["last_ts"], e.get("ts") or 0.0)
        pool = mine if matched is None else (hit if matched else miss)
        return {
            "stats": {
                "runs": len(mine),
                "matched": len(hit),
                "unmatched": len(miss),
                "match_rate": round(len(hit) / len(mine), 3) if mine else None,
            },
            "by_intent": sorted(by_intent.values(), key=lambda r: -r["count"]),
            "unmatched": sorted(grouped.values(), key=lambda r: (-r["count"], -r["last_ts"]))[:limit],
            "entries": pool[-limit:][::-1],
        }
