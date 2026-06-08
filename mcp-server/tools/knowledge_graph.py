"""知识图谱工具 - NetworkX DiGraph，JSON 序列化"""

import json
import logging
import os
import re
from typing import Optional

import networkx as nx

from core.database import get_connection, get_paper

logger = logging.getLogger("research-server.knowledge_graph")

KG_PATH = os.path.join(os.environ.get("RESEARCH_DATA_DIR", "./data"), "knowledge_graph.json")

# ── 实体提取词典 ────────────────────────────────────────────────────────────
# 每个类别：(canonical_name, [alias1, alias2, ...])
# 匹配时忽略大小写，匹配到后统一使用 canonical_name

METHODS = [
    ("Transformer", ["transformer", "self-attention", "self attention"]),
    ("CNN", ["cnn", "convolutional neural network", "convolutional network"]),
    ("RNN", ["rnn", "recurrent neural network"]),
    ("LSTM", ["lstm", "long short-term memory"]),
    ("GAN", ["gan", "generative adversarial network"]),
    ("Autoencoder", ["autoencoder", "auto-encoder", "vae", "variational autoencoder"]),
    ("Graph Neural Network", ["gnn", "graph neural network", "gcn", "graph convolutional"]),
    ("Attention Mechanism", ["attention mechanism", "attention model", "multi-head attention", "multihead attention"]),
    ("Contrastive Learning", ["contrastive learning", "contrastive loss", "simclr", "moco"]),
    ("Self-Supervised Learning", ["self-supervised learning", "self supervised learning"]),
    ("Transfer Learning", ["transfer learning", "pre-trained", "pretrained", "fine-tuning", "finetuning"]),
    ("Few-Shot Learning", ["few-shot learning", "few shot learning", "meta-learning", "meta learning"]),
    ("Reinforcement Learning", ["reinforcement learning", "rl", "policy gradient", "q-learning"]),
    ("Object Detection", ["object detection", "yolo", "faster r-cnn", "ssd"]),
    ("Segmentation", ["segmentation", "semantic segmentation", "instance segmentation", "panoptic segmentation"]),
    ("Multiple Instance Learning", ["multiple instance learning", "mil"]),
    ("Knowledge Distillation", ["knowledge distillation", "teacher-student", "teacher student"]),
    ("Diffusion Model", ["diffusion model", "diffusion", "ddpm", "score-based"]),
    ("Vision-Language Model", ["vision-language", "vision language", "clip", "blip"]),
    ("Large Language Model", ["large language model", "llm", "gpt", "bert", "llama"]),
    ("Vision Transformer", ["vision transformer", "vit", "swin transformer", "deit"]),
    ("Optical Flow", ["optical flow", "motion estimation"]),
    ("3D Convolution", ["3d convolution", "3d cnn", "c3d", "i3d"]),
    ("Temporal Modeling", ["temporal modeling", "temporal convolution", "temporal attention"]),
    ("Anomaly Scoring", ["anomaly scoring", "anomaly score", "abnormality score"]),
    ("Future Frame Prediction", ["future frame prediction", "frame prediction"]),
]

DATASETS = [
    ("UCF-Crime", ["ucf-crime", "ucf crime"]),
    ("ShanghaiTech", ["shanghaitech", "shanghai tech"]),
    ("Pedestrian", ["ped2", "avenue", "pedestrian"]),
    ("Kinetics", ["kinetics", "kinetics-400", "kinetics-600"]),
    ("ImageNet", ["imagenet", "ilsvrc"]),
    ("COCO", ["coco", "ms-coco", "ms coco"]),
    ("ActivityNet", ["activitynet", "activity net"]),
    ("THUMOS", ["thumos"]),
    ("MNIST", ["mnist"]),
    ("CIFAR", ["cifar", "cifar-10", "cifar-100"]),
    ("Cityscapes", ["cityscapes"]),
    ("ADE20K", ["ade20k"]),
    ("VOC", ["pascal voc", "voc2007", "voc2012"]),
    ("HMDB", ["hmdb", "hmdb51"]),
    ("UCF101", ["ucf101", "ucf-101"]),
    ("Something-Something", ["something-something", "something something"]),
    ("Sports-1M", ["sports-1m"]),
    ("YouTube-BoundingBoxes", ["youtube-bounding"]),
]

TASKS = [
    ("Video Anomaly Detection", ["video anomaly detection", "video anomalous", "vad"]),
    ("Anomaly Detection", ["anomaly detection", "anomaly detection", "outlier detection", "abnormal event detection"]),
    ("Object Detection", ["object detection", "object recognition"]),
    ("Image Classification", ["image classification", "visual recognition"]),
    ("Action Recognition", ["action recognition", "activity recognition", "temporal action detection"]),
    ("Semantic Segmentation", ["semantic segmentation"]),
    ("Image Generation", ["image generation", "image synthesis"]),
    ("Text-to-Image", ["text-to-image", "text to image"]),
    ("Object Tracking", ["object tracking", "multi-object tracking", "mot"]),
    ("Pose Estimation", ["pose estimation", "human pose"]),
    ("Scene Understanding", ["scene understanding", "scene recognition"]),
    ("Representation Learning", ["representation learning", "feature learning"]),
    ("Domain Adaptation", ["domain adaptation", "domain generalization"]),
    ("Weakly Supervised Learning", ["weakly supervised", "weak supervision", "semi-supervised"]),
    ("Zero-Shot Learning", ["zero-shot learning", "zero shot"]),
    ("Video Understanding", ["video understanding", "video recognition"]),
    ("Person Re-Identification", ["person re-identification", "re-id", "reid"]),
    ("Change Detection", ["change detection"]),
    ("Medical Image Analysis", ["medical image", "medical imaging"]),
]


def _extract_entities_from_text(text: str) -> list[dict]:
    """从文本中提取实体（方法/数据集/任务），返回 [{name, type}]"""
    text_lower = text.lower()
    found = []
    seen = set()

    for category, dictionary in [("Method", METHODS), ("Dataset", DATASETS), ("Task", TASKS)]:
        for canonical, aliases in dictionary:
            if canonical.lower() in seen:
                continue
            for alias in aliases:
                # 用 word boundary 匹配，避免子串误匹配
                pattern = r'\b' + re.escape(alias) + r'\b'
                if re.search(pattern, text_lower):
                    found.append({"name": canonical, "type": category})
                    seen.add(canonical.lower())
                    break

    return found


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


def _action_extract_from_abstract(G: nx.DiGraph, args: dict) -> str:
    """从论文摘要中自动提取实体并构建知识图谱

    支持两种模式：
    1. 自动模式：提供 paper_id，从数据库读取摘要，用词典匹配提取
    2. 手动模式：提供 paper_id + entities 列表，由 LLM 提供的实体
    """
    paper_id = args.get("paper_id")
    if not paper_id:
        return "错误：请提供 paper_id。"

    # 确保论文节点存在
    with get_connection() as conn:
        paper = get_paper(conn, paper_id)

    if not paper:
        return f"错误：论文 {paper_id} 不存在于数据库中。请先使用 paper_search 搜索。"

    # 确保 Paper 节点在图谱中
    if not G.has_node(paper_id):
        G.add_node(paper_id, type="Paper", title=paper.get("title", ""),
                   year=paper.get("year"))

    # 作者节点
    authors = json.loads(paper.get("authors", "[]")) if isinstance(paper.get("authors"), str) else paper.get("authors", [])
    for author_name in authors:
        if not author_name:
            continue
        author_id = f"author:{author_name.strip().lower().replace(' ', '_')}"
        if not G.has_node(author_id):
            G.add_node(author_id, type="Author", name=author_name.strip())
        if not G.has_edge(paper_id, author_id):
            G.add_edge(paper_id, author_id, type="authored_by")

    # 获取实体列表：优先使用手动提供的 entities，否则从摘要自动提取
    manual_entities = args.get("entities", [])
    if manual_entities:
        entities = [
            {"name": e["name"], "type": e.get("type", "Concept")}
            for e in manual_entities if e.get("name")
        ]
        source = "手动提供"
    else:
        abstract = paper.get("abstract", "")
        if not abstract:
            # 尝试从已解析的分块中获取摘要
            with get_connection() as conn:
                chunks = conn.execute(
                    "SELECT text FROM chunks WHERE paper_id=? AND section LIKE '%abstract%' ORDER BY ordinal LIMIT 1",
                    (paper_id,)
                ).fetchall()
            if chunks:
                abstract = chunks[0]["text"]
        if not abstract:
            _save_graph(G)
            return f"论文 {paper_id} 没有摘要，无法自动提取实体。请手动提供 entities 参数。"
        entities = _extract_entities_from_text(abstract)
        source = "摘要自动提取"

    if not entities:
        _save_graph(G)
        return f"未从论文 {paper_id} 中提取到已知实体。\n图谱总计: {G.number_of_nodes()} 节点, {G.number_of_edges()} 边"

    # 关系映射：实体类型 → 论文到实体的关系名
    RELATION_MAP = {
        "Method": "uses_method",
        "Dataset": "evaluated_on",
        "Task": "addresses_task",
    }

    added = []
    for ent in entities:
        name = ent["name"]
        etype = ent["type"]
        concept_id = f"concept:{name.lower().replace(' ', '_')}"

        # 创建概念节点
        if not G.has_node(concept_id):
            G.add_node(concept_id, type=etype, name=name)
        elif G.nodes[concept_id].get("type") == "Unknown":
            G.nodes[concept_id]["type"] = etype
            G.nodes[concept_id]["name"] = name

        # 创建关系
        relation = RELATION_MAP.get(etype, "related_to")
        if not G.has_edge(paper_id, concept_id):
            G.add_edge(paper_id, concept_id, type=relation)
            added.append(f"  {paper.get('title', paper_id)[:40]} --[{relation}]--> {name}")

    # 自动建立 Method 之间的 "related_to" 关系
    method_ids = [
        f"concept:{e['name'].lower().replace(' ', '_')}"
        for e in entities if e["type"] == "Method"
    ]
    for i, m1 in enumerate(method_ids):
        for m2 in method_ids[i+1:]:
            if not G.has_edge(m1, m2) and not G.has_edge(m2, m1):
                G.add_edge(m1, m2, type="related_to")

    _save_graph(G)

    node_count = G.number_of_nodes()
    edge_count = G.number_of_edges()
    lines = [
        f"✅ 实体提取完成（{source}）: {paper.get('title', paper_id)}",
        f"   提取实体: {len(entities)} 个",
    ]
    lines.extend(added)
    lines.append(f"   图谱总计: {node_count} 节点, {edge_count} 边")
    return "\n".join(lines)


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
    "extract_from_abstract": _action_extract_from_abstract,
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


def extract_from_abstract_sync(paper_id: str, entities: list[dict] | None = None) -> str:
    """同步版本，供 paper_parse 直接调用"""
    G = _load_graph()
    args = {"paper_id": paper_id}
    if entities:
        args["entities"] = entities
    return _action_extract_from_abstract(G, args)
