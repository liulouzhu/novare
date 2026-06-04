"""知识图谱工具 - NetworkX DiGraph，JSON 序列化"""

import json
import logging
import os
from typing import Optional

import networkx as nx

from core.database import get_connection, get_paper

logger = logging.getLogger("research-server.knowledge_graph")

KG_PATH = os.path.join(os.environ.get("RESEARCH_DATA_DIR", "./data"), "knowledge_graph.json")


def _load_graph() -> nx.DiGraph:
    """加载知识图谱"""
    if os.path.exists(KG_PATH):
        try:
            with open(KG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return nx.node_link_graph(data, directed=True)
        except Exception as e:
            logger.warning("Failed to load knowledge graph: %s", e)
    return nx.DiGraph()


def _save_graph(G: nx.DiGraph) -> None:
    """保存知识图谱"""
    os.makedirs(os.path.dirname(KG_PATH), exist_ok=True)
    data = nx.node_link_data(G)
    with open(KG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _action_add_paper(G: nx.DiGraph, args: dict) -> str:
    """添加论文节点及其关系"""
    paper_id = args.get("paper_id")
    if not paper_id:
        return "错误：请提供 paper_id。"

    with get_connection() as conn:
        paper = get_paper(conn, paper_id)
        if not paper:
            return f"错误：论文 {paper_id} 不存在于数据库中。请先使用 paper_search 搜索。"

        from core.database import get_citations
        citations = get_citations(conn, paper_id)

    # 创建 Paper 节点
    G.add_node(paper_id, type="Paper", title=paper["title"],
               year=paper.get("year"), citation_count=paper.get("citation_count", 0))

    # 解析作者，创建 Author 节点 + authored_by 边
    authors = json.loads(paper.get("authors", "[]")) if isinstance(paper.get("authors"), str) else paper.get("authors", [])
    for author_name in authors:
        if not author_name:
            continue
        author_id = f"author:{author_name.strip().lower().replace(' ', '_')}"
        if not G.has_node(author_id):
            G.add_node(author_id, type="Author", name=author_name.strip())
        G.add_edge(paper_id, author_id, type="authored_by")

    # 引用关系
    for cited_id in citations.get("citing", []):
        if not G.has_node(cited_id):
            G.add_node(cited_id, type="Paper", title=cited_id)
        G.add_edge(paper_id, cited_id, type="cites")

    _save_graph(G)

    node_count = G.number_of_nodes()
    edge_count = G.number_of_edges()
    return (
        f"✅ 已添加论文到知识图谱: {paper['title']}\n"
        f"   作者节点: {len(authors)} 个\n"
        f"   引用关系: {len(citations.get('citing', []))} 条\n"
        f"   图谱总计: {node_count} 节点, {edge_count} 边"
    )


def _action_add_concept(G: nx.DiGraph, args: dict) -> str:
    """添加概念节点"""
    name = args.get("name")
    if not name:
        return "错误：请提供概念名称 (name)。"

    concept_id = f"concept:{name.strip().lower().replace(' ', '_')}"
    G.add_node(concept_id, type="Concept", name=name.strip(),
               description=args.get("description", ""))
    _save_graph(G)
    return f"✅ 已添加概念: {name}"


def _action_add_relation(G: nx.DiGraph, args: dict) -> str:
    """添加关系边"""
    subject = args.get("subject")
    predicate = args.get("predicate")
    obj = args.get("object")

    if not all([subject, predicate, obj]):
        return "错误：请提供 subject、predicate、object。"

    # 确保节点存在
    if not G.has_node(subject):
        G.add_node(subject, type="Unknown")
    if not G.has_node(obj):
        G.add_node(obj, type="Unknown")

    G.add_edge(subject, obj, type=predicate)
    _save_graph(G)
    return f"✅ 已添加关系: {subject} --[{predicate}]--> {obj}"


def _action_query(G: nx.DiGraph, args: dict) -> str:
    """查询子图"""
    subject = args.get("subject")
    predicate = args.get("predicate")
    obj = args.get("object")

    if not any([subject, predicate, obj]):
        # 返回图谱概况
        return _format_graph_stats(G)

    matching_edges = []
    for u, v, data in G.edges(data=True):
        match = True
        if subject and u != subject:
            # 也检查节点名称
            u_data = G.nodes.get(u, {})
            if u_data.get("name", "").lower() != subject.lower():
                match = False
        if predicate and data.get("type") != predicate:
            match = False
        if obj and v != obj:
            v_data = G.nodes.get(v, {})
            if v_data.get("name", "").lower() != obj.lower():
                match = False
        if match:
            matching_edges.append((u, v, data))

    if not matching_edges:
        return "未找到匹配的关系。"

    lines = [f"查询结果（{len(matching_edges)} 条关系）：\n"]
    for u, v, data in matching_edges[:20]:
        u_label = G.nodes.get(u, {}).get("name", G.nodes.get(u, {}).get("title", u))
        v_label = G.nodes.get(v, {}).get("name", G.nodes.get(v, {}).get("title", v))
        lines.append(f"  {u_label} --[{data.get('type', '?')}]--> {v_label}")

    if len(matching_edges) > 20:
        lines.append(f"\n  ... 共 {len(matching_edges)} 条，仅显示前 20 条")

    return "\n".join(lines)


def _action_find_path(G: nx.DiGraph, args: dict) -> str:
    """查找两个实体之间的路径"""
    source = args.get("subject")
    target = args.get("target")

    if not source or not target:
        return "错误：请提供 subject 和 target。"

    # 尝试直接匹配或按名称匹配
    source_id = _find_node(G, source)
    target_id = _find_node(G, target)

    if not source_id:
        return f"错误：未找到节点 {source}。"
    if not target_id:
        return f"错误：未找到节点 {target}。"

    try:
        path = nx.shortest_path(G, source_id, target_id)
        lines = [f"路径: {' → '.join(_node_label(G, n) for n in path)}\n"]
        for i in range(len(path) - 1):
            edge_data = G.edges[path[i], path[i + 1]]
            lines.append(
                f"  {_node_label(G, path[i])} --[{edge_data.get('type', '?')}]--> "
                f"{_node_label(G, path[i + 1])}"
            )
        return "\n".join(lines)
    except nx.NetworkXNoPath:
        return f"未找到从 {source} 到 {target} 的路径。"
    except Exception as e:
        return f"错误：{str(e)}"


def _action_stats(G: nx.DiGraph, args: dict) -> str:
    """图谱统计"""
    return _format_graph_stats(G)


def _find_node(G: nx.DiGraph, name: str) -> Optional[str]:
    """按 ID 或名称查找节点"""
    if G.has_node(name):
        return name
    name_lower = name.lower()
    for node, data in G.nodes(data=True):
        if data.get("name", "").lower() == name_lower:
            return node
        if data.get("title", "").lower() == name_lower:
            return node
    return None


def _node_label(G: nx.DiGraph, node: str) -> str:
    """获取节点显示标签"""
    data = G.nodes.get(node, {})
    return data.get("name") or data.get("title") or node


def _format_graph_stats(G: nx.DiGraph) -> str:
    """格式化图谱统计信息"""
    if G.number_of_nodes() == 0:
        return "知识图谱为空。使用 add_paper 或 add_concept 添加节点。"

    # 节点类型统计
    type_counts = {}
    for _, data in G.nodes(data=True):
        t = data.get("type", "Unknown")
        type_counts[t] = type_counts.get(t, 0) + 1

    # 边类型统计
    edge_counts = {}
    for _, _, data in G.edges(data=True):
        t = data.get("type", "Unknown")
        edge_counts[t] = edge_counts.get(t, 0) + 1

    lines = [
        f"## 知识图谱统计",
        f"",
        f"节点总数: {G.number_of_nodes()}",
        f"边总数: {G.number_of_edges()}",
        f"",
        f"### 节点类型分布",
    ]
    for t, count in sorted(type_counts.items(), key=lambda x: -x[1]):
        lines.append(f"  {t}: {count}")

    lines.append(f"\n### 边类型分布")
    for t, count in sorted(edge_counts.items(), key=lambda x: -x[1]):
        lines.append(f"  {t}: {count}")

    return "\n".join(lines)


# ── 路由 ──────────────────────────────────────────────────────────────────

ACTION_MAP = {
    "add_paper": _action_add_paper,
    "add_concept": _action_add_concept,
    "add_relation": _action_add_relation,
    "query": _action_query,
    "find_path": _action_find_path,
    "stats": _action_stats,
}


async def handle_knowledge_graph(args: dict) -> str:
    """知识图谱工具入口"""
    action = args.get("action")
    if not action or action not in ACTION_MAP:
        return f"错误：未知的 action '{action}'。支持: {', '.join(ACTION_MAP.keys())}"

    G = _load_graph()
    handler = ACTION_MAP[action]
    return handler(G, args)
