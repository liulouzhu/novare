"""novare/session.py — 对话历史 + JSONL 持久化 + SessionStore 抽象"""

from __future__ import annotations

import json
import os
import time
import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.orm import Session as SaSession

from web.backend.db.models import MessageModel, SessionModel, User


# ── 抽象接口 ─────────────────────────────────────────────────

class SessionStore(ABC):
    """会话持久化抽象接口"""

    @abstractmethod
    def save_messages(self, session_id: str, messages: list[dict]) -> None:
        """持久化会话消息（JSONL: 全量覆盖；DB: 增量追加）"""

    @abstractmethod
    def load_messages(self, session_id: str) -> list[dict]:
        """加载会话消息"""

    @abstractmethod
    def delete_session(self, session_id: str) -> bool:
        """删除会话"""

    @abstractmethod
    def list_sessions(self, user_id: str | None = None) -> list[dict]:
        """列出会话（user_id 可选，DB 实现用于过滤）"""


class Session:
    def __init__(self, session_id: str | None = None, workspace: Path = Path(".")):
        self.session_id = session_id or self._generate_id()
        self.workspace = workspace
        self.messages: list[dict] = []
        self._dir = workspace / ".novare" / "sessions"

    @staticmethod
    def _generate_id() -> str:
        ts = int(time.time())
        short = uuid.uuid4().hex[:8]
        return f"{ts}-{short}"

    @property
    def _path(self) -> Path:
        return self._dir / f"{self.session_id}.jsonl"

    @property
    def has_compacted(self) -> bool:
        """会话是否包含压缩过的消息"""
        return any(m.get("_compacted") for m in self.messages)

    @property
    def compacted_count(self) -> int:
        """压缩消息的数量"""
        return sum(1 for m in self.messages if m.get("_compacted"))

    def add_user_message(self, content: str):
        self.messages.append({"role": "user", "content": content})

    def add_assistant_message(self, content: str, tool_calls: list[dict] | None = None):
        msg: dict = {"role": "assistant", "content": content}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        self.messages.append(msg)

    def add_tool_result(self, tool_call_id: str, content: str):
        self.messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content,
        })

    def save(self):
        """保存消息到 JSONL 文件

        压缩后的消息（带 _compacted 标记）也会被保存，
        确保 CLI 模式重新加载时上下文已压缩。
        """
        if not self.messages:
            return  # 空会话不写文件
        self._dir.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            for msg in self.messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")

    @classmethod
    def load(cls, session_id: str = "latest", workspace: Path = Path(".")) -> Session:
        sessions_dir = workspace / ".novare" / "sessions"
        if session_id == "latest":
            files = sorted(sessions_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
            if not files:
                raise FileNotFoundError("No sessions found")
            target = files[-1]
            sid = target.stem
        else:
            sid = session_id
            target = sessions_dir / f"{sid}.jsonl"

        session = cls(session_id=sid, workspace=workspace)
        with open(target, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    session.messages.append(json.loads(line))
        return session

    @classmethod
    def list_sessions(cls, workspace: Path = Path(".")) -> list[str]:
        sessions_dir = workspace / ".novare" / "sessions"
        if not sessions_dir.exists():
            return []
        return [p.stem for p in sorted(sessions_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)]

    def delete(self):
        if self._path.exists():
            self._path.unlink()


# ── JSONL 实现 ────────────────────────────────────────────────

class JsonlSessionStore(SessionStore):
    """基于 JSONL 文件的会话存储，封装 Session 的读写逻辑"""

    def __init__(self, workspace: Path = Path(".")):
        self.workspace = workspace
        self._dir = workspace / ".novare" / "sessions"

    def save_messages(self, session_id: str, messages: list[dict]) -> None:
        if not messages:
            return
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{session_id}.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            for msg in messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")

    def load_messages(self, session_id: str) -> list[dict]:
        path = self._dir / f"{session_id}.jsonl"
        if not path.exists():
            return []
        result: list[dict] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    result.append(json.loads(line))
        return result

    def delete_session(self, session_id: str) -> bool:
        path = self._dir / f"{session_id}.jsonl"
        if path.exists():
            path.unlink()
            return True
        return False

    def list_sessions(self, user_id: str | None = None) -> list[dict]:
        if not self._dir.exists():
            return []
        result = []
        for p in sorted(self._dir.glob("*.jsonl"), key=lambda x: x.stat().st_mtime, reverse=True):
            messages = self.load_messages(p.stem)
            title = "新会话"
            for m in messages:
                if m.get("role") == "user":
                    title = m.get("content", "")[:60].replace("\n", " ")
                    if len(m.get("content", "")) > 60:
                        title += "..."
                    break
            result.append({
                "session_id": p.stem,
                "title": title,
                "message_count": len(messages),
                "updated_at": datetime.fromtimestamp(p.stat().st_mtime).isoformat(),
            })
        return result


# ── PostgreSQL 实现 ───────────────────────────────────────────

class DbSessionStore(SessionStore):
    """基于 PostgreSQL 的会话存储（通过 SQLAlchemy）"""

    def __init__(self, db: SaSession, user_id: str):
        self.db = db
        self.user_id = user_id

    def save_messages(self, session_id: str, messages: list[dict]) -> None:
        """增量追加：仅追加新消息到数据库

        调用方应仅传入本轮新增的消息（而非全部历史）。
        ID 由数据库自增生成。
        """
        for msg in messages:
            new_msg = MessageModel(
                session_id=session_id,
                role=msg["role"],
                content=msg.get("content"),
                tool_calls=msg.get("tool_calls"),
                tool_call_id=msg.get("tool_call_id"),
                name=msg.get("name"),
            )
            self.db.add(new_msg)
        self.db.flush()

    def load_messages(self, session_id: str) -> list[dict]:
        rows = (
            self.db.query(MessageModel)
            .filter(MessageModel.session_id == session_id)
            .order_by(MessageModel.id)
            .all()
        )
        return [
            {
                "role": r.role,
                "content": r.content or "",
                **({"tool_calls": r.tool_calls} if r.tool_calls else {}),
                **({"tool_call_id": r.tool_call_id} if r.tool_call_id else {}),
            }
            for r in rows
        ]

    def delete_session(self, session_id: str) -> bool:
        session = (
            self.db.query(SessionModel)
            .filter(
                SessionModel.id == session_id,
                SessionModel.user_id == self.user_id,
            )
            .first()
        )
        if session:
            self.db.query(MessageModel).filter(
                MessageModel.session_id == session_id
            ).delete()
            self.db.delete(session)
            self.db.flush()
            return True
        return False

    def list_sessions(self, user_id: str | None = None) -> list[dict]:
        uid = user_id or self.user_id
        sessions = (
            self.db.query(SessionModel)
            .filter(SessionModel.user_id == uid)
            .order_by(SessionModel.updated_at.desc())
            .all()
        )
        result = []
        for s in sessions:
            count = (
                self.db.query(func.count(MessageModel.id))
                .filter(MessageModel.session_id == s.id)
                .scalar()
            ) or 0
            result.append({
                "session_id": s.id,
                "title": s.title or "新会话",
                "message_count": count,
                "updated_at": s.updated_at.isoformat() if s.updated_at else "",
            })
        return result
