"""Python 代码执行工具 - subprocess 超时控制"""

import asyncio
import logging
import os
import sys
import tempfile

logger = logging.getLogger("research-server.code_execute")

# 默认超时
DEFAULT_TIMEOUT = 30
MAX_TIMEOUT = 300


async def handle_code_execute(args: dict) -> str:
    """执行 Python 代码"""
    code = args.get("code", "").strip()
    if not code:
        return "错误：请提供要执行的 Python 代码。"

    timeout = min(args.get("timeout", DEFAULT_TIMEOUT), MAX_TIMEOUT)

    # 将代码写入临时文件
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write(code)
        script_path = f.name

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, script_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return f"错误：代码执行超时（{timeout} 秒）。"

        stdout_str = stdout.decode("utf-8", errors="replace").strip()
        stderr_str = stderr.decode("utf-8", errors="replace").strip()

        # 构建输出
        parts = []
        if proc.returncode == 0:
            parts.append("✅ 代码执行成功\n")
        else:
            parts.append(f"❌ 代码执行失败（退出码: {proc.returncode}）\n")

        if stdout_str:
            parts.append("### stdout")
            parts.append(f"```\n{stdout_str}\n```")

        if stderr_str:
            parts.append("### stderr")
            parts.append(f"```\n{stderr_str}\n```")

        if not stdout_str and not stderr_str:
            parts.append("(无输出)")

        return "\n".join(parts)

    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass
