"""会话 CRUD 端点"""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from web.backend.db.base import get_db
from web.backend.db.models import User, MessageModel
from web.backend.auth.dependencies import get_current_user
from web.backend.repositories import SessionRepository, MessageRepository
from web.backend.models import SessionDetail, SessionMeta

logger = logging.getLogger("novare.web.sessions")
router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("", response_model=list[SessionMeta])
async def list_sessions(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """列出当前用户的所有会话"""
    repo = SessionRepository(db, user.id)
    sessions = await repo.list_all()

    # 批量查询每个会话的消息数
    session_ids = [s.id for s in sessions]
    if session_ids:
        result = await db.execute(
            select(MessageModel.session_id, func.count(MessageModel.id))
            .where(MessageModel.session_id.in_(session_ids))
            .group_by(MessageModel.session_id)
        )
        counts = dict(result.all())
    else:
        counts = {}

    return [
        SessionMeta(
            session_id=s.id,
            title=s.title or "",
            message_count=counts.get(s.id, 0),
            updated_at=s.updated_at.isoformat() if s.updated_at else "",
        )
        for s in sessions
    ]


@router.post("", response_model=SessionMeta)
async def create_session(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """创建新会话，ID 格式: {timestamp}-{uuid_hex[:8]}"""
    session_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    repo = SessionRepository(db, user.id)
    s = await repo.create(session_id=session_id, title="新会话")
    await db.commit()
    return SessionMeta(
        session_id=s.id,
        title=s.title or "新会话",
        message_count=0,
        updated_at=s.updated_at.isoformat() if s.updated_at else "",
    )


@router.get("/{session_id}", response_model=SessionDetail)
async def get_session(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取会话详情（含消息列表）"""
    session_repo = SessionRepository(db, user.id)
    s = await session_repo.get_by_id(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")

    msg_repo = MessageRepository(db, user.id)
    messages = await msg_repo.get_messages(session_id)

    result_messages = []
    for msg in messages:
        content = msg.content or ""
        if msg.role == "tool":
            content = content[:500] + ("..." if len(content) > 500 else "")

        item = {
            "role": msg.role,
            "content": content,
        }
        if msg.tool_calls:
            item["tool_calls"] = msg.tool_calls
        if msg.tool_call_id:
            item["tool_call_id"] = msg.tool_call_id
        result_messages.append(item)

    return SessionDetail(
        session_id=s.id,
        messages=result_messages,
        title=s.title or "",
    )


@router.patch("/{session_id}")
async def update_session(
    session_id: str,
    body: dict,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """更新会话标题"""
    repo = SessionRepository(db, user.id)
    title = body.get("title")
    if title:
        updated = await repo.update_title(session_id, title)
        if not updated:
            raise HTTPException(status_code=404, detail="Session not found")
        await db.commit()
    return {"ok": True}


@router.delete("/{session_id}")
async def delete_session(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """删除会话及关联消息"""
    session_repo = SessionRepository(db, user.id)
    deleted = await session_repo.delete(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")

    msg_repo = MessageRepository(db, user.id)
    await msg_repo.delete_by_session(session_id)

    await db.commit()
    return {"ok": True}
