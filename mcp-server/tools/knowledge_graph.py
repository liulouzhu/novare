"""知识图谱工具 - NetworkX DiGraph，JSON 序列化"""

import json
import logging
import os
import re
from typing import Optional

import httpx
import networkx as nx

from core.database import get_connection, get_paper
from tools.result import ok, fail

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

METRICS = [
    ("AUC-ROC", ["auc-roc", "auroc", "area under roc"]),
    ("Average Precision", ["mean average precision", "average precision"]),
    ("F1 Score", ["f1-score", "f1 score"]),
    ("PSNR", ["psnr", "peak signal-to-noise"]),
    ("SSIM", ["ssim", "structural similarity"]),
    ("FID", ["fid", "fréchet inception", "frechet inception"]),
    ("IoU", ["iou", "intersection over union"]),
    ("RMSE", ["rmse", "root mean square error"]),
    ("MAE", ["mean absolute error"]),
    ("BLEU", ["bleu score", "bleu metric"]),
    ("ROUGE", ["rouge score", "rouge metric", "rouge-l"]),
    ("CIDEr", ["cider score", "cider metric"]),
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
    """从文本中提取实体（方法/数据集/任务/指标），返回 [{name, type}]"""
    text_lower = text.lower()
    found = []
    seen = set()

    for category, dictionary in [("Method", METHODS), ("Dataset", DATASETS), ("Task", TASKS), ("Metric", METRICS)]:
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


# ── LLM 实体抽取 ──────────────────────────────────────────────────────────

_EXTRACTION_PROMPT = """你是一个学术论文实体抽取助手。请从以下论文摘要中抽取研究实体，输出 JSON 数组。

抽取类型和说明：
- Method: 模型架构、算法、技术方法（如 Transformer, CNN, Contrastive Learning 等）
- Task: 研究任务或问题（如 Object Detection, Anomaly Detection 等）
- Dataset: 数据集名称（如 UCF-Crime, ImageNet, COCO 等）
- Metric: 评估指标（如 AUC-ROC, PSNR, mAP, F1 Score 等）
- Contribution: 论文的主要贡献或创新点，用简洁短语描述（如 "提出时序建模框架", "首次将对比学习用于异常检测"）
- Limitation: 论文明确提到的局限性，用简洁短语描述（如 "依赖光流输入", "无法处理实时场景"）

规则：
1. 每个实体用 canonical name（英文），不要用缩写作主名
2. Contribution 和 Limitation 必须是论文摘要中明确提及的，不要推测
3. 每种类型最多提取 5 个
4. 只输出 JSON 数组，不要输出其他内容

输出格式：
[{"name": "实体名称", "type": "Method"}, ...]

论文摘要：
{text}"""

# 全局 httpx 客户端（惰性初始化）
_llm_http: httpx.Client | None = None


def _get_llm_client() -> httpx.Client | None:
    """惰性初始化 LLM httpx 同步客户端"""
    global _llm_http
    if _llm_http is not None:
        return _llm_http

    enabled = os.environ.get("NOVARE_KG_LLM_EXTRACT", "").lower() in {"1", "true", "yes", "on"}
    if not enabled:
        logger.debug("NOVARE_KG_LLM_EXTRACT 未启用，跳过 LLM 实体抽取")
        return None

    api_key = os.environ.get("NOVARE_API_KEY", "")
    base_url = os.environ.get("NOVARE_BASE_URL", "https://api.minimax.chat/v1")
    if not api_key:
        logger.warning("NOVARE_API_KEY 未配置，LLM 实体抽取不可用")
        return None

    _llm_http = httpx.Client(
        base_url=base_url.rstrip("/"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0),
    )
    return _llm_http


def _llm_extract_entities(text: str) -> list[dict]:
    """使用 LLM 从论文摘要中抽取研究实体，返回 [{name, type}]。

    抽取失败时返回空列表，调用方应 fallback 到字典匹配。
    """
    client = _get_llm_client()
    if client is None:
        return []

    model = os.environ.get("NOVARE_MODEL", "MiniMax-Text-01")
    body = {
        "model": model,
        "messages": [
            {"role": "user", "content": _EXTRACTION_PROMPT.format(text=text)},
        ],
        "max_tokens": 2048,
        "temperature": 0.1,
        "stream": False,
    }

    try:
        resp = client.post("/chat/completions", json=body)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"].strip()

        # 尝试从响应中提取 JSON 数组（可能被 ```json ``` 包裹）
        if "```" in content:
            match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", content, re.DOTALL)
            if match:
                content = match.group(1)
        elif not content.startswith("["):
            # 尝试找到第一个 [ 到最后一个 ]
            start = content.find("[")
            end = content.rfind("]")
            if start != -1 and end != -1:
                content = content[start:end + 1]

        entities = json.loads(content)
        if not isinstance(entities, list):
            logger.warning("LLM 返回非数组: %s", content[:200])
            return []

        # 校验实体结构
        valid_types = {"Method", "Task", "Dataset", "Metric", "Contribution", "Limitation"}
        result = []
        for e in entities:
            if not isinstance(e, dict) or "name" not in e or "type" not in e:
                continue
            if e["type"] not in valid_types:
                logger.debug("跳过未知实体类型: %s", e["type"])
                continue
            result.append({"name": str(e["name"]).strip(), "type": e["type"]})

        logger.info("LLM 实体抽取成功: %d 个实体", len(result))
        return result

    except httpx.HTTPError as e:
        logger.warning("LLM 实体抽取 HTTP 错误: %s", e)
        return []
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        logger.warning("LLM 实体抽取解析错误: %s", e)
        return []
    except Exception as e:
        logger.warning("LLM 实体抽取未知错误: %s", e)
        return []


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


# Actions that mutate the graph (need PG sync)
_MUTATING_ACTIONS = {"add_paper", "add_concept", "add_relation", "extract_from_abstract"}


def save_to_pg(user_id: str, graph: nx.DiGraph):
    """Save NetworkX graph nodes/edges to PostgreSQL (secondary storage)."""
    if not user_id:
        return
    try:
        from web.backend.db.base import SessionLocal
        from web.backend.db.models import KnowledgeNode, KnowledgeEdge
        from uuid import UUID
        db = SessionLocal()
        try:
            uid = UUID(user_id)

            # Build a label->pg_id mapping for edge resolution
            label_to_id = {}

            # Upsert nodes
            for node_id, data in graph.nodes(data=True):
                label = data.get("label") or data.get("name") or data.get("title") or node_id
                node_type = data.get("type", "concept")
                existing = db.query(KnowledgeNode).filter(
                    KnowledgeNode.user_id == uid,
                    KnowledgeNode.label == label,
                ).first()
                if not existing:
                    pg_node = KnowledgeNode(
                        user_id=uid,
                        label=label,
                        type=node_type,
                        properties={k: v for k, v in data.items() if k not in ("label", "type", "name", "title")},
                    )
                    db.add(pg_node)
                    db.flush()  # get the generated id
                    label_to_id[node_id] = pg_node.id
                else:
                    existing.type = node_type
                    existing.properties = {k: v for k, v in data.items() if k not in ("label", "type", "name", "title")}
                    label_to_id[node_id] = existing.id

            db.commit()

            # Upsert edges
            for u, v, edata in graph.edges(data=True):
                source_label = graph.nodes.get(u, {}).get("label") or graph.nodes.get(u, {}).get("name") or graph.nodes.get(u, {}).get("title") or u
                target_label = graph.nodes.get(v, {}).get("label") or graph.nodes.get(v, {}).get("name") or graph.nodes.get(v, {}).get("title") or v
                rel_type = edata.get("type", "related_to")

                # Resolve PG node IDs (may need a query if not freshly created)
                src_id = label_to_id.get(u)
                tgt_id = label_to_id.get(v)
                if not src_id:
                    src_node = db.query(KnowledgeNode).filter(KnowledgeNode.user_id == uid, KnowledgeNode.label == source_label).first()
                    src_id = src_node.id if src_node else None
                if not tgt_id:
                    tgt_node = db.query(KnowledgeNode).filter(KnowledgeNode.user_id == uid, KnowledgeNode.label == target_label).first()
                    tgt_id = tgt_node.id if tgt_node else None

                if not src_id or not tgt_id:
                    continue

                existing_edge = db.query(KnowledgeEdge).filter(
                    KnowledgeEdge.user_id == uid,
                    KnowledgeEdge.source_node_id == src_id,
                    KnowledgeEdge.target_node_id == tgt_id,
                    KnowledgeEdge.relation_type == rel_type,
                ).first()
                if not existing_edge:
                    db.add(KnowledgeEdge(
                        user_id=uid,
                        source_node_id=src_id,
                        target_node_id=tgt_id,
                        relation_type=rel_type,
                        properties={k: v for k, v in edata.items() if k != "type"},
                    ))

            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.warning("Failed to save KG to PostgreSQL: %s", e)


def _action_add_paper(G: nx.DiGraph, args: dict) -> str:
    """添加论文节点及其关系"""
    paper_id = args.get("paper_id")
    if not paper_id:
        return fail("knowledge_graph", "请提供 paper_id。")

    with get_connection() as conn:
        paper = get_paper(conn, paper_id)
        if not paper:
            return fail("knowledge_graph", f"论文 {paper_id} 不存在于数据库中。请先使用 paper_search 搜索。")

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
    return ok("knowledge_graph", {
        "action": "add_paper",
        "title": paper["title"],
        "author_count": len(authors),
        "citation_count": len(citations.get("citing", [])),
        "graph_stats": {"nodes": node_count, "edges": edge_count},
    }, summary=f"已添加论文到知识图谱: {paper['title']}")


def _action_add_concept(G: nx.DiGraph, args: dict) -> str:
    """添加概念节点"""
    name = args.get("name")
    if not name:
        return fail("knowledge_graph", "请提供概念名称 (name)。")

    concept_id = f"concept:{name.strip().lower().replace(' ', '_')}"
    G.add_node(concept_id, type="Concept", name=name.strip(),
               description=args.get("description", ""))
    _save_graph(G)
    return ok("knowledge_graph", {"action": "add_concept", "name": name}, summary=f"已添加概念: {name}")


def _action_add_relation(G: nx.DiGraph, args: dict) -> str:
    """添加关系边"""
    subject = args.get("subject")
    predicate = args.get("predicate")
    obj = args.get("object")

    if not all([subject, predicate, obj]):
        return fail("knowledge_graph", "请提供 subject、predicate、object。")

    # 确保节点存在
    if not G.has_node(subject):
        G.add_node(subject, type="Unknown")
    if not G.has_node(obj):
        G.add_node(obj, type="Unknown")

    G.add_edge(subject, obj, type=predicate)
    _save_graph(G)
    return ok("knowledge_graph", {
        "action": "add_relation",
        "subject": subject,
        "predicate": predicate,
        "object": obj,
    }, summary=f"已添加关系: {subject} --[{predicate}]--> {obj}")


def _action_query(G: nx.DiGraph, args: dict) -> str:
    """查询子图"""
    subject = args.get("subject")
    predicate = args.get("predicate")
    obj = args.get("object")

    if not any([subject, predicate, obj]):
        # 返回图谱概况 — 委托给 _action_stats 格式
        return _action_stats(G, args)

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
        return fail("knowledge_graph", "未找到匹配的关系。")

    relations = []
    for u, v, data in matching_edges[:20]:
        u_label = G.nodes.get(u, {}).get("name", G.nodes.get(u, {}).get("title", u))
        v_label = G.nodes.get(v, {}).get("name", G.nodes.get(v, {}).get("title", v))
        relations.append({"subject": u_label, "predicate": data.get("type", "?"), "object": v_label})

    return ok("knowledge_graph", {
        "action": "query",
        "relations": relations,
        "graph_stats": {"nodes": G.number_of_nodes(), "edges": G.number_of_edges()},
    }, summary=f"查询到 {len(matching_edges)} 条关系")


def _action_find_path(G: nx.DiGraph, args: dict) -> str:
    """查找两个实体之间的路径"""
    source = args.get("subject")
    target = args.get("target")

    if not source or not target:
        return fail("knowledge_graph", "请提供 subject 和 target。")

    # 尝试直接匹配或按名称匹配
    source_id = _find_node(G, source)
    target_id = _find_node(G, target)

    if not source_id:
        return fail("knowledge_graph", f"未找到节点 {source}。")
    if not target_id:
        return fail("knowledge_graph", f"未找到节点 {target}。")

    try:
        path = nx.shortest_path(G, source_id, target_id)
        path_labels = [_node_label(G, n) for n in path]
        edges_list = []
        for i in range(len(path) - 1):
            edge_data = G.edges[path[i], path[i + 1]]
            edges_list.append({
                "subject": _node_label(G, path[i]),
                "predicate": edge_data.get("type", "?"),
                "object": _node_label(G, path[i + 1]),
            })
        return ok("knowledge_graph", {
            "action": "find_path",
            "path": path_labels,
            "edges": edges_list,
        }, summary=f"找到路径: {' -> '.join(path_labels)}")
    except nx.NetworkXNoPath:
        return fail("knowledge_graph", f"未找到从 {source} 到 {target} 的路径。")
    except Exception as e:
        return fail("knowledge_graph", str(e))


def _action_stats(G: nx.DiGraph, args: dict) -> str:
    """图谱统计"""
    if G.number_of_nodes() == 0:
        return ok("knowledge_graph", {
            "action": "stats",
            "node_count": 0,
            "edge_count": 0,
            "node_types": {},
            "edge_types": {},
        }, summary="知识图谱为空")

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

    node_count = G.number_of_nodes()
    edge_count = G.number_of_edges()
    return ok("knowledge_graph", {
        "action": "stats",
        "node_count": node_count,
        "edge_count": edge_count,
        "node_types": type_counts,
        "edge_types": edge_counts,
    }, summary=f"知识图谱: {node_count} 节点, {edge_count} 边")


def _action_extract_from_abstract(G: nx.DiGraph, args: dict) -> str:
    """从论文摘要中自动提取实体并构建知识图谱

    支持两种模式：
    1. 自动模式：提供 paper_id，优先用 LLM 从摘要抽取，失败则回退词典匹配
    2. 手动模式：提供 paper_id + entities 列表，由调用方提供实体
    """
    paper_id = args.get("paper_id")
    if not paper_id:
        return fail("knowledge_graph", "请提供 paper_id。")

    # 确保论文节点存在
    with get_connection() as conn:
        paper = get_paper(conn, paper_id)

    if not paper:
        return fail("knowledge_graph", f"论文 {paper_id} 不存在于数据库中。请先使用 paper_search 搜索。")

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
                from web.backend.db.models import Chunk
                rows = (
                    conn.query(Chunk.text)
                    .filter(Chunk.paper_id == paper_id, Chunk.section.ilike("%abstract%"))
                    .order_by(Chunk.ordinal)
                    .limit(1)
                    .all()
                )
            if rows:
                abstract = rows[0][0]
        if not abstract:
            _save_graph(G)
            return fail("knowledge_graph", f"论文 {paper_id} 没有摘要，无法自动提取实体。请手动提供 entities 参数。")

        # 优先使用 LLM 抽取，失败则回退词典匹配
        entities = _llm_extract_entities(abstract)
        if entities:
            source = "LLM 抽取"
        else:
            entities = _extract_entities_from_text(abstract)
            source = "词典匹配（LLM 未启用或不可用）"

    if not entities:
        _save_graph(G)
        return fail("knowledge_graph", f"未从论文 {paper_id} 中提取到已知实体。",
                     data={"action": "extract_from_abstract", "paper_id": paper_id,
                           "entities_added": 0, "relations_added": 0,
                           "graph_stats": {"nodes": G.number_of_nodes(), "edges": G.number_of_edges()}})

    # 关系映射：实体类型 → 论文到实体的关系名
    RELATION_MAP = {
        "Method": "uses_method",
        "Dataset": "evaluated_on",
        "Task": "addresses_task",
        "Metric": "evaluated_with",
        "Contribution": "contributes",
        "Limitation": "limited_by",
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
    return ok("knowledge_graph", {
        "action": "extract_from_abstract",
        "paper_id": paper_id,
        "source": source,
        "entities_added": len(entities),
        "relations_added": len(added),
        "graph_stats": {"nodes": node_count, "edges": edge_count},
    }, summary=f"实体提取完成({source}): {paper.get('title', paper_id)}")


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


async def handle_knowledge_graph(args: dict, user_id: str = None) -> str:
    """知识图谱工具入口"""
    action = args.get("action")
    if not action or action not in ACTION_MAP:
        return fail("knowledge_graph", f"未知的 action '{action}'。支持: {', '.join(ACTION_MAP.keys())}")

    G = _load_graph()
    handler = ACTION_MAP[action]
    result = handler(G, args)

    # Persist to PostgreSQL after mutating actions
    if action in _MUTATING_ACTIONS:
        save_to_pg(user_id, G)

    return result


def extract_from_abstract_sync(paper_id: str, entities: list[dict] | None = None, user_id: str = None) -> str:
    """同步版本，供 paper_parse 直接调用。

    返回统一 JSON 字符串 (ok/fail)。调用方可以 json.loads() 获取结构化数据。
    """
    G = _load_graph()
    args = {"paper_id": paper_id}
    if entities:
        args["entities"] = entities
    result = _action_extract_from_abstract(G, args)
    save_to_pg(user_id, G)
    return result
