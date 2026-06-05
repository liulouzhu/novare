"""novare/cli.py — 交互式 REPL 入口"""

from __future__ import annotations

import asyncio
import logging
import sys

from novare.config import NovareConfig
from novare.llm_client import LLMClient
from novare.session import Session
from novare.tools.registry import ToolRegistry, ToolDef
from novare.mcp_client import McpClient
from novare.agent_loop import AgentLoop


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stderr,
    )
    logger = logging.getLogger("novare")

    # 加载配置
    config = NovareConfig.load()
    if not config.api_key:
        print("Error: NOVARE_API_KEY not set.", file=sys.stderr)
        print("Set it via: export NOVARE_API_KEY=your-key", file=sys.stderr)
        sys.exit(1)

    logger.info("Novare starting — model=%s, workspace=%s", config.model, config.workspace)

    # 初始化 LLM 客户端
    llm_client = LLMClient(
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
    )

    # 初始化工具注册表
    tool_registry = ToolRegistry(workspace=config.workspace)

    # 连接 MCP 服务器（研究工具）
    mcp_clients: list[McpClient] = []
    for name, srv_config in config.mcp_servers.items():
        logger.info("Connecting to MCP server: %s", name)
        client = McpClient(
            command=srv_config.command,
            args=srv_config.args,
            env=srv_config.env,
        )
        try:
            await client.connect()
            tools = await client.list_tools()
            for tool in tools:
                tool_name = tool["name"]

                async def make_handler(c: McpClient, tn: str):
                    async def handler(args, workspace=None):
                        return await c.call_tool(tn, args)
                    return handler

                handler = await make_handler(client, tool_name)
                tool_registry.register_tool(ToolDef(
                    name=tool_name,
                    description=tool.get("description", ""),
                    parameters=tool.get("inputSchema", {"type": "object", "properties": {}}),
                    handler=handler,
                    source=f"mcp:{name}",
                ))
            logger.info("MCP server '%s': %d tools registered", name, len(tools))
            mcp_clients.append(client)
        except Exception as e:
            logger.error("Failed to connect to MCP server '%s': %s", name, e)

    # 创建 AgentLoop
    agent = AgentLoop(
        llm_client=llm_client,
        tool_registry=tool_registry,
        system_prompt=config.system_prompt,
    )

    # 创建默认 session
    session = Session(workspace=config.workspace)

    # REPL
    print("Novare 科研智能体 (输入 /help 查看命令)")
    print(f"模型: {config.model} | 工具: {len(tool_registry.list_tools())} 个")
    print("-" * 50)

    try:
        while True:
            try:
                user_input = input("\nnovare> ").strip()
            except EOFError:
                break

            if not user_input:
                continue

            # 内置命令
            if user_input in ("/exit", "/quit", "/q"):
                break
            elif user_input == "/help":
                _print_help()
                continue
            elif user_input == "/sessions":
                sessions = Session.list_sessions(workspace=config.workspace)
                for s in sessions:
                    marker = " ← current" if s == session.session_id else ""
                    print(f"  {s}{marker}")
                continue
            elif user_input.startswith("/session "):
                sid = user_input.split(" ", 1)[1].strip()
                try:
                    session = Session.load(sid, workspace=config.workspace)
                    print(f"Loaded session: {session.session_id} ({len(session.messages)} messages)")
                except FileNotFoundError:
                    print(f"Session not found: {sid}")
                continue
            elif user_input == "/new":
                session = Session(workspace=config.workspace)
                print(f"New session: {session.session_id}")
                continue
            elif user_input.startswith("/"):
                print(f"Unknown command: {user_input}. Type /help for available commands.")
                continue

            # 正常对话
            try:
                result = await agent.run_turn(session, user_input)
                print(f"\n{result}")
                session.save()
            except KeyboardInterrupt:
                print("\n[Interrupted]")
            except Exception as e:
                logger.exception("Error in turn")
                print(f"\nError: {e}")
    finally:
        for client in mcp_clients:
            await client.close()
        await llm_client.close()
        session.save()
        print("\n再见！")


def _print_help():
    print("""
Novare 命令:
  /help          显示此帮助
  /sessions      列出所有会话
  /session <id>  加载指定会话
  /new           创建新会话
  /exit          退出

直接输入文字开始对话，Novare 会自动调用工具完成科研任务。
""")


if __name__ == "__main__":
    asyncio.run(main())
