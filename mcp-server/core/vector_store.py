"""Milvus vector store for paper chunk embeddings.

Provides insert/search operations with per-user partition key isolation.
Falls back gracefully when Milvus is unavailable.
"""

import os
import logging

from pymilvus import (
    connections,
    Collection,
    CollectionSchema,
    FieldSchema,
    DataType,
    utility,
)

logger = logging.getLogger(__name__)

MILVUS_HOST = os.getenv("MILVUS_HOST", "localhost")
MILVUS_PORT = int(os.getenv("MILVUS_PORT", "19530"))
COLLECTION_NAME = "paper_chunks"
EMBEDDING_DIM = 1024  # Match DashScope text-embedding-v4


def connect_milvus() -> None:
    """Establish connection to Milvus server."""
    connections.connect(host=MILVUS_HOST, port=MILVUS_PORT)
    logger.info("Connected to Milvus at %s:%s", MILVUS_HOST, MILVUS_PORT)


def ensure_collection() -> Collection:
    """Get or create the paper_chunks collection with proper schema and index."""
    if utility.has_collection(COLLECTION_NAME):
        return Collection(COLLECTION_NAME)

    fields = [
        FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=128),
        FieldSchema(
            name="user_id", dtype=DataType.VARCHAR, max_length=64,
            is_partition_key=True,
        ),
        FieldSchema(name="paper_id", dtype=DataType.VARCHAR, max_length=255),
        FieldSchema(name="chunk_id", dtype=DataType.INT64),
        FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=8192),
        FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=EMBEDDING_DIM),
    ]
    schema = CollectionSchema(
        fields, description="Paper chunks with user partition key"
    )
    collection = Collection(COLLECTION_NAME, schema)
    collection.create_index(
        "embedding",
        {
            "index_type": "IVF_FLAT",
            "metric_type": "COSINE",
            "params": {"nlist": 128},
        },
    )
    logger.info("Created Milvus collection: %s", COLLECTION_NAME)
    return collection


def insert_vectors(
    user_id: str,
    paper_id: str,
    chunk_ids: list[int],
    texts: list[str],
    embeddings: list[list[float]],
) -> None:
    """Insert chunk vectors into Milvus.

    Args:
        user_id: Partition key for user isolation.
        paper_id: Paper identifier.
        chunk_ids: Integer chunk IDs (matching SQLite chunks table).
        texts: Chunk text content.
        embeddings: Corresponding embedding vectors.
    """
    collection = ensure_collection()
    ids = [f"{user_id}_{paper_id}_{cid}" for cid in chunk_ids]
    data = [
        ids,
        [user_id] * len(ids),
        [paper_id] * len(ids),
        chunk_ids,
        texts,
        embeddings,
    ]
    collection.insert(data)
    collection.flush()
    logger.info(
        "Inserted %d vectors for user=%s paper=%s", len(ids), user_id, paper_id
    )


def search_vectors(
    user_id: str,
    query_embedding: list[float],
    top_k: int = 5,
) -> list[dict]:
    """Search for similar chunk vectors scoped to a user.

    Args:
        user_id: Partition key to scope search.
        query_embedding: Query vector.
        top_k: Number of results to return.

    Returns:
        List of hit dicts with keys: id, score, paper_id, chunk_id, text.
    """
    collection = ensure_collection()
    collection.load()
    results = collection.search(
        data=[query_embedding],
        anns_field="embedding",
        param={"metric_type": "COSINE", "params": {"nprobe": 16}},
        limit=top_k,
        expr=f'user_id == "{user_id}"',
        output_fields=["paper_id", "chunk_id", "text"],
    )
    hits = []
    for hit in results[0]:
        hits.append(
            {
                "id": hit.id,
                "score": hit.score,
                "paper_id": hit.get("paper_id"),
                "chunk_id": hit.get("chunk_id"),
                "text": hit.get("text"),
            }
        )
    return hits
