"""WebSocket 聊天端点"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from web.backend.app import agent_service

logger = logging.getLogger("novare.web.chat")
router = APIRouter()


@router.websocket("/ws/chat/{session_id}")
async def ws_chat(websocket: WebSocket, session_id: str):
    """WebSocket 聊天端点

    协议：
      客户端 → 服务端:
        {"type": "send", "content": "用户消息"}
        {"type": "send_with_refs", "content": "...", "references": [...]}

      服务端 → 客户端:
        {"type": "text_delta", "content": "..."}
        {"type": "tool_start", "tool": "...", "params": {...}}
        {"type": "tool_end", "tool": "...", "result": "...", "duration": 2.3}
        {"type": "tool_error", "tool": "...", "error": "..."}
        {"type": "done"}
        {"type": "error", "message": "..."}
    """
    await websocket.accept()
    logger.info("WebSocket connected: session=%s", session_id)

    session = agent_service.load_session(session_id)
    queue: asyncio.Queue = asyncio.Queue()

    try:
        while True:
            # 等待客户端消息
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "Invalid JSON"})
                continue

            msg_type = data.get("type", "send")
            content = data.get("content", "")

            if not content.strip():
                continue

            # 构建用户输入（含引用上下文）
            user_input = content
            if msg_type == "send_with_refs" and data.get("references"):
                refs_text = "\n\n参考文献：\n"
                for ref in data["references"]:
                    refs_text += f"- {ref.get('title', ref.get('id', ''))}\n"
                user_input = content + refs_text

            # 在后台任务中执行 agent.run_turn
            task = asyncio.create_task(
                agent_service.run_turn(session, user_input, queue)
            )

            # 从 queue 读取事件并推送给客户端
            try:
                while True:
                    event = await asyncio.wait_for(queue.get(), timeout=300)
                    await websocket.send_json(event)
                    if event.get("type") in ("done", "error"):
                        break
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "error", "message": "响应超时"})
                task.cancel()

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected: session=%s", session_id)
    except Exception as e:
        logger.exception("WebSocket error")
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
