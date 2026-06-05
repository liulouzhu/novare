"""会话 CRUD 端点"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException

from novare.session import Session
from web.backend.app import agent_service
from web.backend.models import SessionDetail, SessionMeta

logger = logging.getLogger("novare.web.sessions")
router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("", response_model=list[SessionMeta])
async def list_sessions():
    """列出所有会话"""
    sessions = agent_service.list_sessions()
    return [SessionMeta(**s) for s in sessions]


@router.post("", response_model=SessionMeta)
async def create_session():
    """创建新会话"""
    session = agent_service.create_session()
    session.save()
    return SessionMeta(
        session_id=session.session_id,
        title="新会话",
        message_count=0,
        updated_at="",
    )


@router.get("/{session_id}", response_model=SessionDetail)
async def get_session(session_id: str):
    """获取会话详情（含消息列表）"""
    try:
        session = agent_service.load_session(session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")

    # 过滤掉纯 tool role 消息中的大段内容（前端不需要原始 tool result）
    messages = []
    for msg in session.messages:
        if msg["role"] == "tool":
            # 截断 tool result，避免响应过大
            messages.append({
                **msg,
                "content": msg["content"][:500] + ("..." if len(msg["content"]) > 500 else ""),
            })
        else:
            messages.append(msg)

    title = ""
    for msg in session.messages:
        if msg.get("role") == "user":
            title = msg.get("content", "")[:60].replace("\n", " ")
            break

    return SessionDetail(session_id=session_id, messages=messages, title=title)


@router.patch("/{session_id}")
async def update_session(session_id: str, body: dict):
    """更新会话标题（简化实现：写入 metadata 文件）"""
    # MVP: 暂不实现持久化标题，返回成功
    return {"ok": True}


@router.delete("/{session_id}")
async def delete_session(session_id: str):
    """删除会话"""
    try:
        agent_service.delete_session(session_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True}
