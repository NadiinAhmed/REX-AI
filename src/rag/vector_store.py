"""Vector storage (Chroma, persistent on disk).

The vector store holds ONLY embeddings + the minimum metadata needed to filter. SQLite remains
the source of truth for documents, chunk text and approval status. Every vector id equals a
`document_chunks.id`, so the two stores can always be reconciled.

Each embedding model gets its own collection: vectors from different models are not comparable,
so changing EMBEDDING_PROVIDER/EMBEDDING_MODEL means re-indexing (`python -m src.cli reindex`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


class VectorStoreError(Exception):
    pass


@dataclass(frozen=True)
class VectorHit:
    id: str
    similarity: float     # cosine similarity in [-1, 1]; higher is closer
    metadata: dict


class ChromaVectorStore:
    def __init__(self, path: Path, model_id: str) -> None:
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        path.mkdir(parents=True, exist_ok=True)
        name = "rex_chunks_" + re.sub(r"[^a-zA-Z0-9]+", "_", model_id).strip("_").lower()
        try:
            self._client = chromadb.PersistentClient(path=str(path),
                                                     settings=ChromaSettings(anonymized_telemetry=False))
            self._col = self._client.get_or_create_collection(
                name[:60], configuration={"hnsw": {"space": "cosine"}}, embedding_function=None)
        except Exception as exc:
            raise VectorStoreError(f"Cannot open vector store at {path} ({type(exc).__name__}).") from exc

    def upsert(self, ids: list[str], embeddings: list[list[float]], metadatas: list[dict]) -> None:
        if ids:
            self._col.upsert(ids=ids, embeddings=embeddings, metadatas=metadatas)

    def delete_document(self, document_id: str) -> None:
        self._col.delete(where={"document_id": document_id})

    def count(self) -> int:
        return self._col.count()

    def ids_for_document(self, document_id: str) -> list[str]:
        return self._col.get(where={"document_id": document_id}, include=[])["ids"]

    def query(self, embedding: list[float], top_k: int, document_ids: list[str]) -> list[VectorHit]:
        """Nearest chunks, restricted to `document_ids` (the approved documents)."""
        if not document_ids or self._col.count() == 0:
            return []
        where = {"document_id": document_ids[0]} if len(document_ids) == 1 else {"document_id": {"$in": document_ids}}
        try:
            result = self._col.query(query_embeddings=[embedding], n_results=top_k, where=where,
                                     include=["distances", "metadatas"])
        except Exception as exc:
            raise VectorStoreError(f"Vector search failed ({type(exc).__name__}).") from exc
        return [VectorHit(id=i, similarity=round(1.0 - d, 4), metadata=m or {})
                for i, d, m in zip(result["ids"][0], result["distances"][0], result["metadatas"][0])]
