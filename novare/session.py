"""novare/session.py — 对话历史 + JSONL 持久化"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path


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
