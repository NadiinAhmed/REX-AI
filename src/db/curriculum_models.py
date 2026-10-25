"""Table for Agent 3 (Curriculum Research & Accreditation Alignment). Added in migration 0003_curriculum.

Kept in its own module (like `rfp_repository.py`) so Agent 3 work never conflicts with edits to
`models.py` made for the other agents.

Design
- `content` stores the whole validated `Curriculum` (the Pydantic model stays the single definition);
  the columns next to it are the metadata other code filters on (department, level, industry, country).
- `alignment` stores the latest accreditation-alignment report, `review_log` the human review trail.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Float
from sqlalchemy.orm import Mapped, mapped_column

from src.db.models import Base, utcnow


class CurriculumRecord(Base):
    __tablename__ = "curricula"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)                    # "CUR-1A2B3C"
    course_id: Mapped[str | None] = mapped_column(String(20), index=True)           # catalog course it is based on
    title: Mapped[str] = mapped_column(String(300))
    department: Mapped[str | None] = mapped_column(String(100), index=True)
    skill_level: Mapped[str] = mapped_column(String(20), index=True)
    industry: Mapped[str | None] = mapped_column(String(100), index=True)
    country: Mapped[str | None] = mapped_column(String(100), index=True)
    language: Mapped[str] = mapped_column(String(5), default="en")
    status: Mapped[str] = mapped_column(String(30), index=True)                      # see CurriculumStatus
    revision: Mapped[int] = mapped_column(Integer, default=1)
    total_hours: Mapped[float | None] = mapped_column(Float)
    framework_ids: Mapped[list] = mapped_column(JSON, default=list)
    generation_mode: Mapped[str] = mapped_column(String(20), default="deterministic")
    content: Mapped[dict] = mapped_column(JSON)                                      # full Curriculum dump
    alignment: Mapped[list] = mapped_column(JSON, default=list)                      # list of AlignmentReport dumps
    review_log: Mapped[list] = mapped_column(JSON, default=list)                     # [{at, by, action, note}]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(String(200))
