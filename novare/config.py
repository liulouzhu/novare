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

    # 评审模型配置（可选，用于双模型对抗评审）
    # 留空时禁用双模型模式，所有阶段使用主模型
    reviewer_api_key: str = ""
    reviewer_base_url: str = ""
    reviewer_model: str = ""

    # 上下文管理
    auto_compact_threshold: int = 100_000   # 累积 input tokens 超过此值触发自动压缩
    preserve_recent_messages: int = 4       # 压缩时保留最近 N 条消息

    # 长期记忆
    enable_long_term_memory: bool = True    # 是否启用长期记忆
    max_memories_per_user: int = 50         # 每个用户最大记忆条数

    # 迭代次数限制
    max_iterations: int = 20                # 主 agent 最大工具调用轮次
    subagent_max_iterations: int = 16       # 子智能体最大工具调用轮次

    # 超时（秒）
    turn_timeout: int = 300                 # 主 agent 单轮超时（默认 5 分钟）
    subagent_turn_timeout: int = 600        # 子智能体单轮超时（默认 10 分钟）

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

        # 评审模型（可选）
        cfg.reviewer_api_key = os.environ.get("NOVARE_REVIEWER_API_KEY", cfg.reviewer_api_key)
        cfg.reviewer_base_url = os.environ.get("NOVARE_REVIEWER_BASE_URL", cfg.reviewer_base_url)
        cfg.reviewer_model = os.environ.get("NOVARE_REVIEWER_MODEL", cfg.reviewer_model)

        data_dir = os.environ.get("NOVARE_DATA_DIR")
        if data_dir:
            cfg.data_dir = Path(data_dir).resolve()

        workspace = os.environ.get("NOVARE_WORKSPACE")
        if workspace:
            cfg.workspace = Path(workspace).resolve()

        # 迭代次数
        max_iter = os.environ.get("NOVARE_MAX_ITERATIONS")
        if max_iter:
            cfg.max_iterations = int(max_iter)
        sub_max_iter = os.environ.get("NOVARE_SUBAGENT_MAX_ITERATIONS")
        if sub_max_iter:
            cfg.subagent_max_iterations = int(sub_max_iter)

        turn_timeout = os.environ.get("NOVARE_TURN_TIMEOUT")
        if turn_timeout:
            cfg.turn_timeout = int(turn_timeout)
        sub_turn_timeout = os.environ.get("NOVARE_SUBAGENT_TURN_TIMEOUT")
        if sub_turn_timeout:
            cfg.subagent_turn_timeout = int(sub_turn_timeout)

        # 长期记忆
        enable_memory = os.environ.get("NOVARE_ENABLE_LONG_TERM_MEMORY")
        if enable_memory is not None:
            cfg.enable_long_term_memory = enable_memory.lower() in ("1", "true", "yes")
        max_mem = os.environ.get("NOVARE_MAX_MEMORIES_PER_USER")
        if max_mem:
            cfg.max_memories_per_user = int(max_mem)

        # 上下文管理
        compact_threshold = os.environ.get("NOVARE_AUTO_COMPACT_THRESHOLD")
        if compact_threshold:
            cfg.auto_compact_threshold = int(compact_threshold)
        preserve_recent = os.environ.get("NOVARE_PRESERVE_RECENT_MESSAGES")
        if preserve_recent:
            cfg.preserve_recent_messages = int(preserve_recent)

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
                    env={
                        "RESEARCH_DATA_DIR": str(cfg.data_dir),
                        "DATABASE_URL": os.getenv(
                            "DATABASE_URL",
                            "postgresql://postgres:123456@localhost:5432/research_agent",
                        ),
                    },
                )

        # 默认系统提示词
        if not cfg.system_prompt:
            cfg.system_prompt = _default_system_prompt(cfg.workspace)

        # Skill 目录：系统公共 + 用户私有（~/.novare/skills 兜底）
        skill_dirs_env = os.environ.get("NOVARE_SKILL_DIR")
        if skill_dirs_env:
            cfg.skill_dirs = [Path(p).resolve() for p in skill_dirs_env.split(os.pathsep)]
        else:
            cfg.skill_dirs = [
                project_root / "system" / "skills",
                Path.home() / ".novare" / "skills",
            ]

        return cfg


def get_user_workspace(user_id: str) -> str:
    """Return isolated workspace path for a user."""
    import os
    base = os.getenv("NOVARE_WORKSPACE", "workspace")
    path = os.path.join(base, user_id)
    os.makedirs(os.path.join(path, ".novare", "sessions"), exist_ok=True)
    os.makedirs(os.path.join(path, "uploads"), exist_ok=True)
    return path


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

如果评审模型已配置，你还拥有：
- reviewer_evaluate：用独立的评审模型对候选创新点做对抗评审（双模型模式）

工作空间：{workspace}

工作流指引：
1. 先搜索获取论文列表和 ID
2. 用 paper_parse 解析感兴趣的论文 PDF（解析完成后会自动从摘要提取实体到知识图谱）
3. 用 rag_query 在已解析的论文库中语义问答
4. 用 knowledge_graph 构建概念关系图谱（查询自动构建的实体，或手动补充更多关系）
5. 用 code_execute 进行数据分析和可视化

知识图谱指引：
- paper_parse 解析完成后会自动提取摘要中的方法、数据集、任务实体
- 可以用 knowledge_graph(action="query") 查看已有实体和关系
- 可以用 knowledge_graph(action="extract_from_abstract", paper_id="...", entities=[...]) 手动补充实体
- 可以用 knowledge_graph(action="find_path") 发现概念之间的关联路径

输出规范：
- 引用论文时提供标题、作者、年份
- 综述回答按主题组织，引用具体论文
- 区分已解析论文（有全文）和仅检索到的论文（仅有摘要）
- 使用中文与用户交互，搜索词建议使用英文以获得更好的检索效果

任务状态指引：
- 你会看到 [当前任务状态] 块，包含目标、已完成步骤、待办步骤、关键发现
- 每次工具调用后审视状态：信息是否足够回答用户？是否有明显缺失？
- 如果关键发现已覆盖用户问题的核心维度，直接综合回答，不要过度搜索
- 如果发现缺失重要信息（如只找到 1-2 篇论文但用户要求综述），继续检索
- 不要重复已经完成的步骤（如已经搜索过的查询词）
"""
