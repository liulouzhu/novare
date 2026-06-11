"""记忆 CRUD 端点"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from web.backend.db.base import get_db
from web.backend.db.models import User
from web.backend.auth.dependencies import get_current_user
from web.backend.repositories.memory_repo import MemoryRepository
from web.backend.models import MemoryOut, MemoryUpdate

logger = logging.getLogger("novare.web.memories")
router = APIRouter(prefix="/api/memories", tags=["memories"])


def _to_out(m) -> MemoryOut:
    return MemoryOut(
        id=m.id,
        category=m.category,
        key=m.key,
        value=m.value,
        confidence=m.confidence,
        pinned=m.pinned,
        tags=m.tags or [],
        source=m.source or "auto",
        created_at=m.created_at.isoformat() if m.created_at else None,
        updated_at=m.updated_at.isoformat() if m.updated_at else None,
    )


@router.get("", response_model=list[MemoryOut])
async def list_memories(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    repo = MemoryRepository(db, user.id)
    return [_to_out(m) for m in repo.get_all()]


@router.patch("/{memory_id}", response_model=MemoryOut)
async def update_memory(
    memory_id: int,
    body: MemoryUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    repo = MemoryRepository(db, user.id)
    # get_all + filter，因为 repo 没有 get_by_id
    mem = next((m for m in repo.get_all() if m.id == memory_id), None)
    if not mem:
        raise HTTPException(404, "Memory not found")

    if body.value is not None:
        mem.value = body.value
    if body.tags is not None:
        mem.tags = body.tags
    if body.confidence is not None:
        mem.confidence = max(0.0, min(1.0, body.confidence))

    db.commit()
    db.refresh(mem)
    return _to_out(mem)


@router.patch("/{memory_id}/pin", response_model=MemoryOut)
async def toggle_pin(
    memory_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    repo = MemoryRepository(db, user.id)
    mem = next((m for m in repo.get_all() if m.id == memory_id), None)
    if not mem:
        raise HTTPException(404, "Memory not found")

    mem.pinned = not mem.pinned
    db.commit()
    db.refresh(mem)
    return _to_out(mem)


@router.delete("/{memory_id}")
async def delete_memory(
    memory_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    repo = MemoryRepository(db, user.id)
    deleted = repo.delete(memory_id)
    if not deleted:
        raise HTTPException(404, "Memory not found")
    db.commit()
    return {"ok": True}


@router.delete("")
async def clear_memories(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    repo = MemoryRepository(db, user.id)
    count = repo.delete_all()
    db.commit()
    return {"deleted": count}
