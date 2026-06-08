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

    def get_graph_data(self) -> dict:
        nodes = self.get_nodes()
        edges = self.get_edges()
        return {
            "nodes": [{"id": str(n.id), "label": n.label, "type": n.type, **(n.properties or {})} for n in nodes],
            "links": [{"source": str(e.source_node_id), "target": str(e.target_node_id), "relation": e.relation_type} for e in edges],
        }

    def get_stats(self) -> dict:
        nodes = self.get_nodes()
        edges = self.get_edges()
        node_types = {}
        for n in nodes:
            node_types[n.type] = node_types.get(n.type, 0) + 1
        edge_types = {}
        for e in edges:
            edge_types[e.relation_type] = edge_types.get(e.relation_type, 0) + 1
        return {"total_nodes": len(nodes), "total_edges": len(edges), "node_types": node_types, "edge_types": edge_types}
