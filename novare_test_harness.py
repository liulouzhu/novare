"""novare_test_harness.py — 手动集成测试脚本

用法: d:/project/research-agent/mcp-server/.venv/Scripts/python.exe novare_test_harness.py
需要: NOVARE_API_KEY 环境变量
"""

import asyncio
import sys
import os

# 确保可以 import novare
sys.path.insert(0, os.path.dirname(__file__))


async def test_llm_only():
    """测试 1: 纯 LLM 对话（无工具）"""
    from novare.config import NovareConfig
    from novare.llm_client import LLMClient
    from novare.session import Session

    config = NovareConfig.load()
    if not config.api_key:
        print("SKIP: NOVARE_API_KEY not set")
        return

    llm = LLMClient(api_key=config.api_key, base_url=config.base_url, model=config.model)
    session = Session()

    resp = await llm.chat([{"role": "user", "content": "1+1等于几？只回答数字。"}])
    print(f"LLM 响应: {resp.content}")
    assert resp.content.strip() == "2", f"Expected '2', got '{resp.content}'"
    print("PASS: test_llm_only")
    await llm.close()


async def test_tool_dispatch():
    """测试 2: 工具分发（不调 LLM，直接执行工具）"""
    from novare.tools.registry import ToolRegistry
    from pathlib import Path

    registry = ToolRegistry(workspace=Path("."))

    # 测试 glob_search
    result = await registry.execute("glob_search", {"pattern": "*.py", "path": "."})
    print(f"glob_search 结果: {result[:200]}")
    assert "novare" in result or "mcp" in result
    print("PASS: test_tool_dispatch")


async def test_full_agent_loop():
    """测试 3: 完整 agent 循环（LLM + 工具）"""
    from novare.config import NovareConfig
    from novare.llm_client import LLMClient
    from novare.session import Session
    from novare.tools.registry import ToolRegistry
    from novare.agent_loop import AgentLoop

    config = NovareConfig.load()
    if not config.api_key:
        print("SKIP: NOVARE_API_KEY not set")
        return

    llm = LLMClient(api_key=config.api_key, base_url=config.base_url, model=config.model)
    registry = ToolRegistry(workspace=Path(config.workspace))
    agent = AgentLoop(llm_client=llm, tool_registry=registry, system_prompt=config.system_prompt)
    session = Session(workspace=Path(config.workspace))

    result = await agent.run_turn(session, "用 glob_search 列出当前目录下的所有 Python 文件")
    print(f"Agent 响应: {result}")
    print("PASS: test_full_agent_loop")
    await llm.close()


async def main():
    print("=" * 50)
    print("Novare Integration Tests")
    print("=" * 50)

    await test_llm_only()
    print()
    await test_tool_dispatch()
    print()
    await test_full_agent_loop()
    print()
    print("All tests completed!")


if __name__ == "__main__":
    asyncio.run(main())
