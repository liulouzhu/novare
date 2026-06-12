from uuid import UUID
from sqlalchemy.orm import Session
from web.backend.db.models import KnowledgeNode, KnowledgeEdge
from .base import BaseRepository


class KnowledgeRepository(BaseRepository):
    def __init__(self, db: Session, user_id: UUID):
        super().__init__(db, user_id)

    def add_node(self, label: str, node_type: str, properties: dict | None = None) -> KnowledgeNode:
        node = KnowledgeNode(user_id=self.user_id, label=label, type=node_type, properties=properties or {})
        self.db.add(node)
        self.db.flush()
        return node

    def add_edge(self, source_id: UUID, target_id: UUID, relation_type: str,
                 properties: dict | None = None) -> KnowledgeEdge:
        edge = KnowledgeEdge(
            user_id=self.user_id, source_node_id=source_id, target_node_id=target_id,
            relation_type=relation_type, properties=properties or {},
        )
        self.db.add(edge)
        self.db.flush()
        return edge

    def get_nodes(self, node_type: str | None = None) -> list[KnowledgeNode]:
        query = self.db.query(KnowledgeNode).filter(KnowledgeNode.user_id == self.user_id)
        if node_type:
            query = query.filter(KnowledgeNode.type == node_type)
        return query.all()

    def get_edges(self) -> list[KnowledgeEdge]:
        return self.db.query(KnowledgeEdge).filter(KnowledgeEdge.user_id == self.user_id).all()

    def find_node_by_label(self, label: str) -> KnowledgeNode | None:
        return self.db.query(KnowledgeNode).filter(
            KnowledgeNode.user_id == self.user_id, KnowledgeNode.label == label,
        ).first()

    def get_neighbors(self, node_id: UUID) -> list[KnowledgeNode]:
        edge_ids = self.db.query(KnowledgeEdge.target_node_id).filter(
            KnowledgeEdge.user_id == self.user_id, KnowledgeEdge.source_node_id == node_id,
        ).union(
            self.db.query(KnowledgeEdge.source_node_id).filter(
                KnowledgeEdge.user_id == self.user_id, KnowledgeEdge.target_node_id == node_id,
            )
        ).all()
        ids = [r[0] for r in edge_ids]
        if not ids:
            return []
        return self.db.query(KnowledgeNode).filter(KnowledgeNode.id.in_(ids)).all()

    def find_path(self, source_id: UUID, target_id: UUID, max_depth: int = 5) -> list:
        from collections import deque
        visited = {source_id}
        queue = deque([(source_id, [source_id])])
        while queue:
            current, path = queue.popleft()
            if len(path) > max_depth:
                continue
            if current == target_id:
                return path
            edges = self.db.query(KnowledgeEdge).filter(
                KnowledgeEdge.user_id == self.user_id,
                (KnowledgeEdge.source_node_id == current) | (KnowledgeEdge.target_node_id == current),
            ).all()
            for edge in edges:
                next_id = edge.target_node_id if edge.source_node_id == current else edge.source_node_id
                if next_id not in visited:
                    visited.add(next_id)
                    queue.append((next_id, path + [next_id]))
        return []

    def get_graph_data(self, exclude_types: list[str] | None = None) -> dict:
        nodes = self.get_nodes()
        edges = self.get_edges()

        # 按类型过滤节点
        excluded = set(exclude_types) if exclude_types else set()
        excluded_ids = set()

        filtered_nodes = []
        for n in nodes:
            if n.type in excluded:
                excluded_ids.add(str(n.id))
                continue
            filtered_nodes.append(
                {"id": str(n.id), "label": n.label, "type": n.type, **(n.properties or {})}
            )

        # 过滤引用被排除节点的边
        filtered_links = []
        for e in edges:
            src, tgt = str(e.source_node_id), str(e.target_node_id)
            if src in excluded_ids or tgt in excluded_ids:
                continue
            filtered_links.append(
                {"source": src, "target": tgt, "relation": e.relation_type, **(e.properties or {})}
            )

        return {"nodes": filtered_nodes, "links": filtered_links}

    def get_stats(self, exclude_types: list[str] | None = None) -> dict:
        nodes = self.get_nodes()
        edges = self.get_edges()
        excluded = set(exclude_types) if exclude_types else set()
        excluded_ids = {str(n.id) for n in nodes if n.type in excluded}
        filtered_nodes = [n for n in nodes if str(n.id) not in excluded_ids]
        filtered_edges = [
            e for e in edges
            if str(e.source_node_id) not in excluded_ids and str(e.target_node_id) not in excluded_ids
        ]

        node_types = {}
        for n in filtered_nodes:
            node_types[n.type] = node_types.get(n.type, 0) + 1
        edge_types = {}
        for e in filtered_edges:
            edge_types[e.relation_type] = edge_types.get(e.relation_type, 0) + 1
        return {
            "total_nodes": len(filtered_nodes),
            "total_edges": len(filtered_edges),
            "node_types": node_types,
            "edge_types": edge_types,
        }
