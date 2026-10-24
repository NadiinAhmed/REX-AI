"""Shared database tables (SQLAlchemy 2.x ORM). SHARED INTERFACE for all five agents.

Design rules
- One table per business entity, shared by every agent. No agent keeps its own course copy.
- `courses.data` stores the full validated `Course` record (the Pydantic model stays the single
  definition of a course); the columns next to it are the fields other agents filter on.
- SQLite is the source of truth for documents and chunks. The vector store only holds
  embeddings keyed by `document_chunks.id` (see docs/rag_workflow.md, "Consistency").
- Schema changes go through Alembic migrations in `migrations/`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class ApprovalStatus(str, Enum):
    PENDING = "pending"      # uploaded, not yet reviewed: NEVER used in answers
    APPROVED = "approved"    # reviewed by staff: searchable by the assistant
    REJECTED = "rejected"


class IndexStatus(str, Enum):
    PENDING = "pending"      # row created, vectors not written yet
    INDEXED = "indexed"      # chunks + vectors written
    FAILED = "failed"


class CatalogMeta(Base):
    """One row describing which course dataset is loaded (synthetic demo or official)."""
    __tablename__ = "catalog_meta"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[str] = mapped_column(String(50))
    is_synthetic: Mapped[bool] = mapped_column(Boolean)
    notice: Mapped[str] = mapped_column(Text)
    source_file: Mapped[str | None] = mapped_column(String(500))
    loaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CourseRecord(Base):
    __tablename__ = "courses"
    course_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    title_ar: Mapped[str | None] = mapped_column(String(300))
    department: Mapped[str | None] = mapped_column(String(100), index=True)
    skill_level: Mapped[str] = mapped_column(String(20), index=True)
    delivery_format: Mapped[str] = mapped_column(String(20), index=True)
    source_type: Mapped[str] = mapped_column(String(20))
    approved: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    data: Mapped[dict] = mapped_column(JSON)   # full Course.model_dump(mode="json")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class DocumentRecord(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    filename: Mapped[str] = mapped_column(String(300))
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    page_count: Mapped[int] = mapped_column(Integer)
    doc_type: Mapped[str] = mapped_column(String(40), default="general")
    approval_status: Mapped[str] = mapped_column(String(20), default=ApprovalStatus.PENDING.value, index=True)
    index_status: Mapped[str] = mapped_column(String(20), default=IndexStatus.PENDING.value)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    embedding_model: Mapped[str | None] = mapped_column(String(200))
    error: Mapped[str | None] = mapped_column(Text)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    chunks: Mapped[list["ChunkRecord"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True)


class ChunkRecord(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (UniqueConstraint("document_id", "chunk_index", name="uq_chunk_position"),)
    id: Mapped[str] = mapped_column(String(80), primary_key=True)   # also the vector-store id
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    page: Mapped[int] = mapped_column(Integer)                      # 1-based page number
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    text_hash: Mapped[str] = mapped_column(String(64))
    document: Mapped[DocumentRecord] = relationship(back_populates="chunks")


class LeadRecord(Base):
    """A prospective student who explicitly agreed to be contacted."""
    __tablename__ = "leads"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(40))
    language: Mapped[str] = mapped_column(String(5))
    profile: Mapped[dict] = mapped_column(JSON, default=dict)          # learner profile at capture
    interested_course_ids: Mapped[list] = mapped_column(JSON, default=list)
    consent_given_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consent_version: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="new")    # new / contacted / closed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EvaluationRunRecord(Base):
    __tablename__ = "evaluation_runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))          # e.g. "ragas"
    config: Mapped[dict] = mapped_column(JSON)
    summary: Mapped[dict] = mapped_column(JSON)
    results_path: Mapped[str] = mapped_column(String(500))
    dataset_size: Mapped[int] = mapped_column(Integer)
    mean_score: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
