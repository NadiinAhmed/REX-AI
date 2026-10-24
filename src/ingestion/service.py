"""Document lifecycle: add, replace, remove, approve and re-index PDFs.

Consistency between SQLite (source of truth) and the vector store:
  ingest : write document row (index_status=pending) -> write chunk rows -> embed -> write
           vectors -> mark indexed. If ANY step fails, vectors and rows for that document are
           deleted, so a half-indexed document can never be searched.
  remove : delete vectors first, then the row (chunks cascade), then the stored file.
  replace: ingest the new file first; only if that succeeds is the old document removed.
  approve: only changes the row. Search reads approved ids from SQLite on every query, so no
           vector metadata has to be kept in sync.
Only one document changes at a time: adding a PDF never rebuilds the whole knowledge base.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from langchain_core.embeddings import Embeddings

from src.db.models import ApprovalStatus, ChunkRecord
from src.db.repositories import DocumentRepository
from src.ingestion.chunker import TextChunker
from src.ingestion.pdf import DocumentError, PdfTextExtractor, PdfValidator, sha256_of
from src.rag.embeddings import embedding_model_id
from src.rag.vector_store import ChromaVectorStore
from src.utils.logging_config import get_logger

logger = get_logger("ingestion")


class DuplicateDocumentError(DocumentError):
    def __init__(self, filename: str, existing_id: str, existing_name: str) -> None:
        super().__init__(f"'{filename}' is identical to already-uploaded '{existing_name}' "
                         f"(document {existing_id}). Nothing was added.")
        self.existing_id = existing_id


class DocumentNotFoundError(DocumentError):
    pass


class IngestionError(Exception):
    """Unexpected failure while indexing (embedding/vector store). Nothing was kept."""


@dataclass
class IngestionResult:
    document_id: str
    filename: str
    page_count: int
    chunk_count: int
    approval_status: str
    warnings: list[str] = field(default_factory=list)


class DocumentIngestionService:
    def __init__(self, documents: DocumentRepository, vectors: ChromaVectorStore, embeddings: Embeddings,
                 chunker: TextChunker, validator: PdfValidator, extractor: PdfTextExtractor,
                 storage_dir: Path) -> None:
        self._docs = documents
        self._vectors = vectors
        self._embeddings = embeddings
        self._chunker = chunker
        self._validator = validator
        self._extractor = extractor
        self._storage = storage_dir

    # ------------------------------------------------------------------ public API
    def ingest(self, path: Path, filename: str | None = None, doc_type: str = "general",
               approve: bool = False) -> IngestionResult:
        path = Path(path)
        filename = Path(filename or path.name).name   # never trust directory parts of a name
        self._validator.validate(path, filename)
        digest = sha256_of(path)
        if (existing := self._docs.find_by_hash(digest)) is not None:
            raise DuplicateDocumentError(filename, existing.id, existing.filename)
        extracted = self._extractor.extract(path, filename)
        chunks = self._chunker.split(extracted.pages)
        status = ApprovalStatus.APPROVED if approve else ApprovalStatus.PENDING

        doc_id = self._docs.create(filename=filename, sha256=digest, size_bytes=path.stat().st_size,
                                   page_count=extracted.page_count, doc_type=doc_type,
                                   approval_status=status, embedding_model=embedding_model_id(self._embeddings))
        try:
            self._store_file(path, doc_id)
            self._docs.add_chunks(doc_id, [self._record(doc_id, c.page, c.index, c.text, c.text_hash)
                                           for c in chunks])
            self._index(doc_id, filename)
        except Exception as exc:
            self._rollback(doc_id)
            logger.exception("Indexing failed for %s", filename)
            raise IngestionError(f"Indexing '{filename}' failed ({type(exc).__name__}); nothing was kept.") from exc
        logger.info("Ingested %s: %d pages, %d chunks, %s", filename, extracted.page_count, len(chunks), status.value)
        return IngestionResult(doc_id, filename, extracted.page_count, len(chunks), status.value, extracted.warnings)

    def replace(self, document_id: str, path: Path, filename: str | None = None) -> IngestionResult:
        old = self._require(document_id)
        result = self.ingest(path, filename or Path(path).name, doc_type=old.doc_type,
                             approve=old.approval_status == ApprovalStatus.APPROVED.value)
        self.remove(document_id)
        return result

    def remove(self, document_id: str) -> None:
        self._require(document_id)
        self._vectors.delete_document(document_id)
        self._docs.delete(document_id)
        stored = self._stored_path(document_id)
        if stored.exists():
            stored.unlink()
        logger.info("Removed document %s", document_id)

    def set_approval(self, document_id: str, status: ApprovalStatus) -> None:
        if not self._docs.set_approval(document_id, status):
            raise DocumentNotFoundError(f"No document with id {document_id}.")

    def reindex(self, document_id: str) -> int:
        """Re-embed one document from its stored chunks (e.g. after changing the embedding model)."""
        doc = self._require(document_id)
        self._vectors.delete_document(document_id)
        return self._index(document_id, doc.filename)

    def check_consistency(self) -> list[str]:
        """Report documents whose vectors do not match their chunk rows."""
        problems = []
        for doc in self._docs.list():
            vector_ids = set(self._vectors.ids_for_document(doc.id))
            chunk_ids = set(self._docs.chunk_ids(doc.id))
            if vector_ids != chunk_ids:
                problems.append(f"{doc.filename} ({doc.id}): {len(chunk_ids)} chunks, {len(vector_ids)} vectors")
        return problems

    # ------------------------------------------------------------------ internals
    def _index(self, doc_id: str, filename: str) -> int:
        rows = self._docs.chunks(doc_id)
        if rows:
            vectors = self._embeddings.embed_documents([r.text for r in rows])
            self._vectors.upsert(
                ids=[r.id for r in rows], embeddings=vectors,
                metadatas=[{"document_id": doc_id, "page": r.page, "chunk_index": r.chunk_index,
                            "filename": filename} for r in rows])
        self._docs.mark_indexed(doc_id, len(rows))
        return len(rows)

    def _rollback(self, doc_id: str) -> None:
        try:
            self._vectors.delete_document(doc_id)
        finally:
            self._docs.delete(doc_id)
            stored = self._stored_path(doc_id)
            if stored.exists():
                stored.unlink()

    def _require(self, document_id: str):
        doc = self._docs.get(document_id)
        if doc is None:
            raise DocumentNotFoundError(f"No document with id {document_id}.")
        return doc

    def _stored_path(self, doc_id: str) -> Path:
        return self._storage / f"{doc_id}.pdf"

    def _store_file(self, path: Path, doc_id: str) -> None:
        self._storage.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, self._stored_path(doc_id))

    @staticmethod
    def _record(doc_id: str, page: int, index: int, text: str, text_hash: str) -> ChunkRecord:
        return ChunkRecord(id=f"{doc_id}:{index}", document_id=doc_id, page=page, chunk_index=index,
                           text=text, text_hash=text_hash)
