"""Repositories: the only classes that read or write the shared tables.

Agents and services receive a repository (dependency injection) instead of writing SQL, so
each table has exactly one place that defines how it is used.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, func, select

from src.db.database import Database
from src.db.models import (
    ApprovalStatus, CatalogMeta, ChunkRecord, CourseRecord, DocumentRecord, EvaluationRunRecord,
    IndexStatus, LeadRecord,
)
from src.models.course import Course, CourseCatalog, DatasetInfo


class CourseRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def count(self) -> int:
        with self._db.session() as s:
            return s.scalar(select(func.count()).select_from(CourseRecord)) or 0

    def replace_catalog(self, catalog: CourseCatalog, source_file: str | None = None) -> int:
        """Replace ALL courses with `catalog` in one transaction (all or nothing)."""
        with self._db.session() as s:
            s.execute(delete(CourseRecord))
            s.execute(delete(CatalogMeta))
            s.add(CatalogMeta(id=1, name=catalog.dataset.name, version=catalog.dataset.version,
                              is_synthetic=catalog.dataset.is_synthetic, notice=catalog.dataset.notice,
                              source_file=source_file))
            for c in catalog.courses:
                s.add(self._to_record(c))
        return len(catalog.courses)

    def list_approved(self) -> list[Course]:
        with self._db.session() as s:
            rows = s.scalars(select(CourseRecord).where(CourseRecord.approved.is_(True))
                             .order_by(CourseRecord.course_id)).all()
            return [Course.model_validate(r.data) for r in rows]

    def get(self, course_id: str) -> Course | None:
        with self._db.session() as s:
            row = s.get(CourseRecord, course_id.strip().upper())
            return Course.model_validate(row.data) if row and row.approved else None

    def dataset_info(self) -> tuple[DatasetInfo, str | None] | None:
        with self._db.session() as s:
            meta = s.get(CatalogMeta, 1)
            if meta is None:
                return None
            return (DatasetInfo(name=meta.name, version=meta.version, is_synthetic=meta.is_synthetic,
                                notice=meta.notice), meta.source_file)

    @staticmethod
    def _to_record(c: Course) -> CourseRecord:
        return CourseRecord(
            course_id=c.course_id, title=c.title, title_ar=c.title_ar, department=c.department,
            skill_level=c.skill_level.value, delivery_format=c.delivery_format.value,
            source_type=c.source.source_type.value, approved=True, data=c.model_dump(mode="json"),
        )


class DocumentRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def find_by_hash(self, sha256: str) -> DocumentRecord | None:
        with self._db.session() as s:
            return s.scalar(select(DocumentRecord).where(DocumentRecord.sha256 == sha256))

    def get(self, document_id: str) -> DocumentRecord | None:
        with self._db.session() as s:
            return s.get(DocumentRecord, document_id)

    def list(self) -> list[DocumentRecord]:
        with self._db.session() as s:
            return list(s.scalars(select(DocumentRecord).order_by(DocumentRecord.uploaded_at)).all())

    def approved_indexed_ids(self) -> list[str]:
        with self._db.session() as s:
            return list(s.scalars(select(DocumentRecord.id).where(
                DocumentRecord.approval_status == ApprovalStatus.APPROVED.value,
                DocumentRecord.index_status == IndexStatus.INDEXED.value)).all())

    def create(self, *, filename: str, sha256: str, size_bytes: int, page_count: int, doc_type: str,
               approval_status: ApprovalStatus, embedding_model: str, document_id: str | None = None) -> str:
        doc_id = document_id or str(uuid.uuid4())
        with self._db.session() as s:
            s.add(DocumentRecord(id=doc_id, filename=filename, sha256=sha256, size_bytes=size_bytes,
                                 page_count=page_count, doc_type=doc_type,
                                 approval_status=approval_status.value, embedding_model=embedding_model,
                                 index_status=IndexStatus.PENDING.value))
        return doc_id

    def add_chunks(self, document_id: str, chunks: list[ChunkRecord]) -> None:
        with self._db.session() as s:
            s.add_all(chunks)

    def replace_chunks(self, document_id: str, chunks: list[ChunkRecord]) -> None:
        with self._db.session() as s:
            s.execute(delete(ChunkRecord).where(ChunkRecord.document_id == document_id))
            s.flush()
            s.add_all(chunks)

    def chunk_ids(self, document_id: str) -> list[str]:
        with self._db.session() as s:
            return list(s.scalars(select(ChunkRecord.id).where(ChunkRecord.document_id == document_id)).all())

    def chunks(self, document_id: str) -> list[ChunkRecord]:
        with self._db.session() as s:
            return list(s.scalars(select(ChunkRecord).where(ChunkRecord.document_id == document_id)
                                  .order_by(ChunkRecord.chunk_index)).all())

    def chunk_texts(self, chunk_ids: list[str]) -> dict[str, str]:
        if not chunk_ids:
            return {}
        with self._db.session() as s:
            rows = s.execute(select(ChunkRecord.id, ChunkRecord.text).where(ChunkRecord.id.in_(chunk_ids))).all()
        return {r.id: r.text for r in rows}

    def mark_indexed(self, document_id: str, chunk_count: int) -> None:
        with self._db.session() as s:
            doc = s.get(DocumentRecord, document_id)
            doc.index_status, doc.chunk_count, doc.error = IndexStatus.INDEXED.value, chunk_count, None
            doc.indexed_at = datetime.now(timezone.utc)

    def mark_failed(self, document_id: str, error: str) -> None:
        with self._db.session() as s:
            doc = s.get(DocumentRecord, document_id)
            if doc:
                doc.index_status, doc.error = IndexStatus.FAILED.value, error[:500]

    def set_embedding_model(self, document_id: str, model_id: str) -> None:
        with self._db.session() as s:
            doc = s.get(DocumentRecord, document_id)
            if doc:
                doc.embedding_model = model_id

    def set_approval(self, document_id: str, status: ApprovalStatus) -> bool:
        with self._db.session() as s:
            doc = s.get(DocumentRecord, document_id)
            if doc is None:
                return False
            doc.approval_status = status.value
            return True

    def delete(self, document_id: str) -> bool:
        with self._db.session() as s:
            doc = s.get(DocumentRecord, document_id)
            if doc is None:
                return False
            s.delete(doc)   # chunks are removed by ON DELETE CASCADE / ORM cascade
            return True

    def stats(self) -> dict[str, int]:
        docs = self.list()
        return {
            "documents": len(docs),
            "approved": sum(d.approval_status == ApprovalStatus.APPROVED.value for d in docs),
            "pending_review": sum(d.approval_status == ApprovalStatus.PENDING.value for d in docs),
            "chunks": sum(d.chunk_count for d in docs),
        }


class LeadRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def create(self, *, name: str, email: str | None, phone: str | None, language: str, profile: dict,
               interested_course_ids: list[str], consent_given_at: datetime, consent_version: str) -> str:
        lead_id = str(uuid.uuid4())
        with self._db.session() as s:
            s.add(LeadRecord(id=lead_id, name=name, email=email, phone=phone, language=language,
                             profile=profile, interested_course_ids=interested_course_ids,
                             consent_given_at=consent_given_at, consent_version=consent_version))
        return lead_id

    def list(self) -> list[LeadRecord]:
        with self._db.session() as s:
            return list(s.scalars(select(LeadRecord).order_by(LeadRecord.created_at.desc())).all())

    def count(self) -> int:
        with self._db.session() as s:
            return s.scalar(select(func.count()).select_from(LeadRecord)) or 0


class EvaluationRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def record(self, *, run_id: str, kind: str, config: dict, summary: dict, results_path: str,
               dataset_size: int, mean_score: float | None) -> None:
        with self._db.session() as s:
            s.merge(EvaluationRunRecord(id=run_id, kind=kind, config=config, summary=summary,
                                        results_path=results_path, dataset_size=dataset_size,
                                        mean_score=mean_score))

    def list(self) -> list[EvaluationRunRecord]:
        with self._db.session() as s:
            return list(s.scalars(select(EvaluationRunRecord).order_by(EvaluationRunRecord.created_at)).all())
