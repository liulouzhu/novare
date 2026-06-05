"""文件上传端点"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File

from web.backend.app import agent_service
from web.backend.models import UploadResponse

logger = logging.getLogger("novare.web.upload")
router = APIRouter(prefix="/api/upload", tags=["upload"])


@router.post("", response_model=UploadResponse)
async def upload_file(file: UploadFile = File(...)):
    """上传文件（PDF / 数据文件）

    文件保存到 workspace 目录，返回本地路径供 paper_parse 使用。
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename")

    # 确定保存目录
    workspace = agent_service.config.workspace if agent_service.config else Path("./workspace")
    upload_dir = workspace / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    # 保存文件
    dest = upload_dir / file.filename
    try:
        with open(dest, "wb") as f:
            content = await file.read()
            f.write(content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {e}")

    logger.info("File uploaded: %s (%d bytes)", file.filename, len(content))

    return UploadResponse(
        filename=file.filename,
        file_path=str(dest.resolve()),
        message=f"文件已上传: {file.filename}",
    )
