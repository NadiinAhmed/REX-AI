"""Repository for the `curricula` table (Agent 3). The only code that reads or writes it."""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from sqlalchemy import func, select

from src.curriculum.models import AlignmentReport, Curriculum, CurriculumStatus
from src.db.curriculum_models import CurriculumRecord
from src.db.database import Database


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_curriculum_id() -> str:
    return f"CUR-{secrets.token_hex(3).upper()}"


class CurriculumRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------ reads
    def get(self, curriculum_id: str) -> CurriculumRecord | None:
        with self._db.session() as s:
            return s.get(CurriculumRecord, curriculum_id.strip().upper())

    def list(self, *, department: str | None = None, skill_level: str | None = None, industry: str | None = None,
             country: str | None = None, status: str | None = None, course_id: str | None = None) -> list[CurriculumRecord]:
        """Metadata filtering (brief: department, level, industry, country). Newest first."""
        q = select(CurriculumRecord)
        for column, value in ((CurriculumRecord.department, department), (CurriculumRecord.skill_level, skill_level),
                              (CurriculumRecord.industry, industry), (CurriculumRecord.country, country),
                              (CurriculumRecord.status, status), (CurriculumRecord.course_id, course_id)):
            if value:
                q = q.where(column == value)
        with self._db.session() as s:
            return list(s.scalars(q.order_by(CurriculumRecord.updated_at.desc())).all())

    def latest_approved_for_course(self, course_id: str) -> CurriculumRecord | None:
        """What other agents (Document Generation, Marketing) read: the newest APPROVED curriculum."""
        q = (select(CurriculumRecord).where(CurriculumRecord.course_id == course_id.strip().upper(),
                                            CurriculumRecord.status == CurriculumStatus.APPROVED.value)
             .order_by(CurriculumRecord.approved_at.desc()))
        with self._db.session() as s:
            return s.scalars(q).first()

    def counts(self) -> dict[str, int]:
        with self._db.session() as s:
            rows = s.execute(select(CurriculumRecord.status, func.count()).group_by(CurriculumRecord.status)).all()
        return {status: n for status, n in rows}

    # ------------------------------------------------------------------ writes
    def create(self, curriculum: Curriculum, alignment: list[AlignmentReport], actor: str,
               note: str = "Draft created") -> CurriculumRecord:
        record = CurriculumRecord(
            id=new_curriculum_id(), status=CurriculumStatus.DRAFT.value, revision=1,
            review_log=[self._entry(actor, "created", note)], alignment=[a.model_dump(mode="json") for a in alignment],
            **self._columns(curriculum))
        with self._db.session() as s:
            s.add(record)
        return record

    def save_content(self, curriculum_id: str, curriculum: Curriculum, alignment: list[AlignmentReport],
                     actor: str, action: str, note: str = "") -> CurriculumRecord | None:
        """Store an edited curriculum: a new revision that must be reviewed again."""
        with self._db.session() as s:
            row = s.get(CurriculumRecord, curriculum_id.strip().upper())
            if row is None:
                return None
            for key, value in self._columns(curriculum).items():
                setattr(row, key, value)
            row.alignment = [a.model_dump(mode="json") for a in alignment]
            row.revision += 1
            row.status = CurriculumStatus.DRAFT.value
            row.approved_at = None
            row.approved_by = None
            row.review_log = [*row.review_log, self._entry(actor, action, note)]
            return row

    def set_status(self, curriculum_id: str, status: CurriculumStatus, actor: str, action: str,
                   note: str = "") -> CurriculumRecord | None:
        with self._db.session() as s:
            row = s.get(CurriculumRecord, curriculum_id.strip().upper())
            if row is None:
                return None
            row.status = status.value
            if status == CurriculumStatus.APPROVED:
                row.approved_at, row.approved_by = _now(), actor
            else:
                row.approved_at, row.approved_by = None, None
            row.review_log = [*row.review_log, self._entry(actor, action, note)]
            return row

    def save_alignment(self, curriculum_id: str, alignment: list[AlignmentReport]) -> None:
        with self._db.session() as s:
            row = s.get(CurriculumRecord, curriculum_id.strip().upper())
            if row is not None:
                row.alignment = [a.model_dump(mode="json") for a in alignment]

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _columns(c: Curriculum) -> dict:
        return dict(course_id=c.course_id, title=c.title, department=c.department, skill_level=c.skill_level,
                    industry=c.industry, country=c.country, language=c.language, total_hours=c.total_hours,
                    framework_ids=list(c.framework_ids), generation_mode=c.generation_mode,
                    content=c.model_dump(mode="json"))

    @staticmethod
    def _entry(actor: str, action: str, note: str) -> dict:
        return {"at": _now().isoformat(timespec="seconds"), "by": actor, "action": action, "note": note}
