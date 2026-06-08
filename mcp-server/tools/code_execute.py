"""Python 代码执行工具 - Docker 沙箱隔离执行"""

import asyncio
import logging

logger = logging.getLogger("research-server.code_execute")


async def handle_code_execute(args: dict, user_id: str = None) -> str:
    """Execute Python code in a Docker sandbox."""
    code = args.get("code", "").strip()
    if not code:
        return "Error: empty code"

    timeout = min(args.get("timeout", 60), 300)

    if not user_id:
        return "Error: user context required for code execution"

    try:
        from web.backend.sandbox.manager import sandbox_manager
        result = await sandbox_manager.execute(user_id, code, timeout)
        output = ""
        if result["stdout"]:
            output += result["stdout"]
        if result["stderr"]:
            output += f"\n[stderr]\n{result['stderr']}"
        if result["exit_code"] != 0:
            output += f"\n[exit code: {result['exit_code']}]"
        return output.strip() or "(no output)"
    except Exception as e:
        return f"Error executing code: {e}"
