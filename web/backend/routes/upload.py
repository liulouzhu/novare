"""文件上传端点"""

from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File

from novare.config import get_user_workspace
from web.backend.auth.dependencies import get_current_user
from web.backend.db.models import User
from web.backend.models import UploadResponse

logger = logging.getLogger("novare.web.upload")
router = APIRouter(prefix="/api/upload", tags=["upload"])


def _safe_filename(name: str) -> str:
    """剥离路径，只保留纯文件名，防止路径穿越。"""
    name = Path(name).name  # 去掉目录部分 (../../etc/passwd → passwd)
    name = re.sub(r"[^\w.\-]", "_", name)  # 仅保留安全字符
    return name or "unnamed"


@router.post("", response_model=UploadResponse)
async def upload_file(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    """上传文件（PDF / 数据文件）

    文件保存到 workspace/<user_id>/uploads/ 目录，返回本地路径供 paper_parse 使用。
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename")

    # 使用统一的用户隔离 workspace
    upload_dir = Path(get_user_workspace(str(user.id))) / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    # 用 UUID 前缀防冲突 + 安全文件名防穿越
    safe_name = _safe_filename(file.filename)
    dest = upload_dir / f"{uuid.uuid4().hex}_{safe_name}"

    try:
        content = await file.read()
        dest.write_bytes(content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {e}")

    logger.info("File uploaded by user %s: %s (%d bytes)", user.id, safe_name, len(content))

    return UploadResponse(
        filename=safe_name,
        file_path=str(dest.resolve()),
        message=f"文件已上传: {safe_name}",
    )
