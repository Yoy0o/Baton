"""会话存储：一个会话一个 JSON 文件（data/conversations/c_xxx.json）。

保存三样东西：
- 元数据：标题、时间、反馈
- history：发给模型的消息历史（继续对话用）
- events：工作台事件（打开历史会话时按顺序重放，还原执行过程、确认卡片和答复）

流式增量（text_delta 等）不保存，最终文本由 thought / message 事件保存。
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("agent.conversations")

CONV_ID_RE = re.compile(r"^c_[0-9a-f]{12}$")
DEFAULT_TITLE = "新对话"
TITLE_LIMIT = 40
# 只用于实时显示、不需要重放的事件
EPHEMERAL_EVENTS = {"text_delta", "reasoning_delta", "run_removed", "bridge_status", "conversation_created", "error"}


def make_title(text: str) -> str:
    t = " ".join(text.split())
    return t if len(t) <= 24 else t[:24] + "…"


@dataclass
class Conversation:
    id: str
    user: str
    title: str = DEFAULT_TITLE
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    history: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    feedback: dict[str, str] = field(default_factory=dict)  # run_id -> like | dislike
    seq: int = 0  # 事件序号，只增不减（重新生成撤掉的事件不回收序号）
    deleted: bool = False

    def meta(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "created_at": self.created_at, "updated_at": self.updated_at}

    def append_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """保存一个需要重放的事件并编号。工作台用 seq 去重：打开会话时拉取的快照和实时事件可能有重叠。"""
        self.seq += 1
        event["seq"] = self.seq
        self.events.append(event)
        return event


def repair_history(history: list[dict[str, Any]], note: str) -> None:
    """补齐悬空的工具调用结果，并以 assistant 消息收尾，保证下一轮发给模型的历史合法。

    OpenAI 兼容接口要求：带 tool_calls 的 assistant 消息之后，必须有每个调用对应的 tool 消息。
    任务被停止或中断时，这些消息可能缺失。
    """
    if not history:
        return
    answered: set[str] = set()
    pending: dict[str, Any] | None = None
    for m in reversed(history):
        role = m.get("role")
        if role == "tool":
            answered.add(str(m.get("tool_call_id")))
            continue
        if role == "assistant" and m.get("tool_calls"):
            pending = m
        break
    if pending is not None:
        for tc in pending["tool_calls"]:
            if tc.get("id") not in answered:
                history.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id"),
                        "content": json.dumps(
                            {"ok": False, "error_code": "CANCELLED", "message": "任务已停止，该调用未完成"}, ensure_ascii=False
                        ),
                    }
                )
    last = history[-1]
    if last.get("role") != "assistant" or last.get("tool_calls"):
        history.append({"role": "assistant", "content": note})


class ConversationStore:
    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir) / "conversations"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._mem: dict[str, Conversation] = {}
        self._load_all()

    # ------------------------------------------------------------------ 读写
    def _path(self, cid: str) -> Path:
        return self.dir / f"{cid}.json"

    def _load_all(self) -> None:
        for p in sorted(self.dir.glob("c_*.json")):
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
                conv = Conversation(**{k: v for k, v in raw.items() if k in Conversation.__dataclass_fields__})
            except Exception as e:  # 单个文件损坏不影响其他会话
                log.warning("skip broken conversation file %s: %s", p.name, e)
                continue
            if self._recover(conv):
                self.save(conv)
            self._mem[conv.id] = conv

    @staticmethod
    def _recover(conv: Conversation) -> bool:
        """服务重启后：未结束的运行标记为中断，未决的确认标记为未提交，并修复模型历史。"""
        finished = {e.get("run_id") for e in conv.events if e.get("type") == "run_finished"}
        resolved = {e.get("confirm_id") for e in conv.events if e.get("type") == "confirm_resolved"}
        changed = False
        for e in list(conv.events):
            if e.get("type") == "confirm_request" and e.get("run_id") not in finished and e.get("confirm_id") not in resolved:
                conv.append_event(
                    {"type": "confirm_resolved", "conversation_id": conv.id, "run_id": e.get("run_id"),
                     "confirm_id": e.get("confirm_id"), "approved": False, "reason": "interrupted"}
                )
                changed = True
        for e in list(conv.events):
            if e.get("type") == "run_started" and e.get("run_id") not in finished:
                conv.append_event(
                    {"type": "run_finished", "conversation_id": conv.id, "run_id": e.get("run_id"),
                     "status": "interrupted", "steps": 0, "suggestions": []}
                )
                changed = True
        if changed:
            repair_history(conv.history, "（服务重启，本次任务已中断）")
        return changed

    def save(self, conv: Conversation) -> None:
        if conv.deleted:
            return
        try:
            tmp = self._path(conv.id).with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(conv), ensure_ascii=False, default=str), encoding="utf-8")
            tmp.replace(self._path(conv.id))
        except OSError as e:
            log.warning("save conversation %s failed: %s", conv.id, e)

    # ------------------------------------------------------------------ 操作
    def create(self, user: str, title: str = DEFAULT_TITLE) -> Conversation:
        conv = Conversation(id="c_" + uuid.uuid4().hex[:12], user=user, title=title[:TITLE_LIMIT] or DEFAULT_TITLE)
        self._mem[conv.id] = conv
        self.save(conv)
        return conv

    def get(self, cid: str, user: str) -> Conversation | None:
        if not CONV_ID_RE.match(cid or ""):
            return None
        conv = self._mem.get(cid)
        if conv is None or conv.deleted or conv.user != user:
            return None
        return conv

    def list(self, user: str) -> list[Conversation]:
        convs = [c for c in self._mem.values() if c.user == user and not c.deleted]
        return sorted(convs, key=lambda c: c.updated_at, reverse=True)

    def rename(self, cid: str, user: str, title: str) -> Conversation | None:
        conv = self.get(cid, user)
        title = " ".join(title.split())[:TITLE_LIMIT]
        if conv is None or not title:
            return None
        conv.title = title
        self.save(conv)
        return conv

    def delete(self, cid: str, user: str) -> bool:
        conv = self.get(cid, user)
        if conv is None:
            return False
        conv.deleted = True
        self._mem.pop(cid, None)
        try:
            self._path(cid).unlink(missing_ok=True)
        except OSError:
            pass
        return True
