"""Novare 配置加载 — 环境变量 + .novare/config.json"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class McpServerConfig:
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class NovareConfig:
    api_key: str = ""
    base_url: str = "https://api.minimax.chat/v1"
    model: str = "MiniMax-Text-01"
    data_dir: Path = Path("./data")
    workspace: Path = Path("./workspace")
    mcp_servers: dict[str, McpServerConfig] = field(default_factory=dict)
    system_prompt: str = ""
    skill_dirs: list[Path] = field(default_factory=list)

    @classmethod
    def load(cls, config_path: str | Path | None = None) -> "NovareConfig":
        """从 .env 文件、环境变量和配置文件加载配置"""
        cfg = cls()

        # 加载 .env 文件（当前工作目录）
        load_dotenv(Path.cwd() / ".env")

        # 环境变量覆盖
        cfg.api_key = os.environ.get("NOVARE_API_KEY", cfg.api_key)
        cfg.base_url = os.environ.get("NOVARE_BASE_URL", cfg.base_url)
        cfg.model = os.environ.get("NOVARE_MODEL", cfg.model)

        data_dir = os.environ.get("NOVARE_DATA_DIR")
        if data_dir:
            cfg.data_dir = Path(data_dir).resolve()

        workspace = os.environ.get("NOVARE_WORKSPACE")
        if workspace:
            cfg.workspace = Path(workspace).resolve()

        # 配置文件
        path = Path(config_path).resolve() if config_path else cfg.workspace / ".novare" / "config.json"
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and "mcpServers" in data:
                    for name, srv in data["mcpServers"].items():
                        if isinstance(srv, dict) and "command" in srv:
                            cfg.mcp_servers[name] = McpServerConfig(
                                command=srv["command"],
                                args=srv.get("args", []),
                                env=srv.get("env", {}),
                            )
            except (json.JSONDecodeError, OSError):
                pass  # 配置文件损坏时使用默认值

        # 默认研究工具 MCP 服务器（在项目根目录查找）
        project_root = Path.cwd()
        if not cfg.mcp_servers and (project_root / "mcp-server").exists():
            venv_python = project_root / ".venv" / "Scripts" / "python.exe"
            if not venv_python.exists():
                venv_python = project_root / ".venv" / "bin" / "python"
            mcp_server_py = project_root / "mcp-server" / "research_server.py"
            if venv_python.exists() and mcp_server_py.exists():
                cfg.mcp_servers["research"] = McpServerConfig(
                    command=str(venv_python),
                    args=[str(mcp_server_py)],
                    env={"RESEARCH_DATA_DIR": str(cfg.data_dir)},
                )

        # 默认系统提示词
        if not cfg.system_prompt:
            cfg.system_prompt = _default_system_prompt(cfg.workspace)

        # Skill 目录：项目级 + 用户级
        skill_dirs_env = os.environ.get("NOVARE_SKILL_DIR")
        if skill_dirs_env:
            cfg.skill_dirs = [Path(p).resolve() for p in skill_dirs_env.split(os.pathsep)]
        else:
            cfg.skill_dirs = [
                cfg.workspace / ".novare" / "skills",
                Path.home() / ".novare" / "skills",
            ]

        return cfg


def _default_system_prompt(workspace: Path) -> str:
    return f"""你是 Novare 智能科研助手。

你的能力：
- 搜索学术论文（paper_search）
- 解析论文 PDF（paper_parse）
- 在已解析论文中语义检索（rag_query）
- 查询/更新论文知识图谱（knowledge_graph）
- 执行 Python 代码做数据分析（code_execute）
- 读写文件（read_file, write_file, edit_file）
- 搜索文件（glob_search, grep_search）

工作空间：{workspace}

工作流指引：
1. 先搜索获取论文列表和 ID
2. 用 paper_parse 解析感兴趣的论文 PDF
3. 用 rag_query 在已解析的论文库中语义问答
4. 用 knowledge_graph 构建概念关系图谱
5. 用 code_execute 进行数据分析和可视化

输出规范：
- 引用论文时提供标题、作者、年份
- 综述回答按主题组织，引用具体论文
- 区分已解析论文（有全文）和仅检索到的论文（仅有摘要）
- 使用中文与用户交互，搜索词建议使用英文以获得更好的检索效果
"""
