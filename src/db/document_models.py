"""Table for Agent 4 (Document Generation). Added in migration 0004_documents.

Own module (like curriculum_models.py) so Agent 4 never conflicts with edits to `models.py`.
`content` holds the editable `DocumentContent`; the PDF is rendered on demand from it, so the stored
text and the downloaded file can never disagree.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from src.db.models import Base, utcnow


class GeneratedDocumentRecord(Base):
    __tablename__ = "generated_documents"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)                    # "DOC-1A2B3C"
    doc_type: Mapped[str] = mapped_column(String(40), index=True)
    course_id: Mapped[str | None] = mapped_column(String(20), index=True)
    curriculum_id: Mapped[str | None] = mapped_column(String(20), index=True)       # the curriculum it was built from
    curriculum_revision: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str] = mapped_column(String(5))
    title: Mapped[str] = mapped_column(String(400))
    status: Mapped[str] = mapped_column(String(20), index=True)                      # draft | approved
    revision: Mapped[int] = mapped_column(Integer, default=1)
    basis: Mapped[str] = mapped_column(String(300))                                  # human-readable data basis
    template_version: Mapped[str] = mapped_column(String(20))
    generation_mode: Mapped[str] = mapped_column(String(20), default="deterministic")
    content: Mapped[dict] = mapped_column(JSON)                                      # DocumentContent dump
    notices: Mapped[list] = mapped_column(JSON, default=list)
    log: Mapped[list] = mapped_column(JSON, default=list)                            # [{at, by, action, note}]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(String(200))
