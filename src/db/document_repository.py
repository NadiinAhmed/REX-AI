"""Repository for the `generated_documents` table (Agent 4). The only code that reads or writes it.

(Named GeneratedDocumentRepository: `DocumentRepository` already exists for the RAG knowledge-base PDFs.)
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from sqlalchemy import func, select

from src.db.database import Database
from src.db.document_models import GeneratedDocumentRecord


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_document_id() -> str:
    return f"DOC-{secrets.token_hex(3).upper()}"


class GeneratedDocumentRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def get(self, document_id: str) -> GeneratedDocumentRecord | None:
        with self._db.session() as s:
            return s.get(GeneratedDocumentRecord, document_id.strip().upper())

    def list(self, *, doc_type: str | None = None, status: str | None = None, course_id: str | None = None,
             language: str | None = None) -> list[GeneratedDocumentRecord]:
        q = select(GeneratedDocumentRecord)
        for column, value in ((GeneratedDocumentRecord.doc_type, doc_type), (GeneratedDocumentRecord.status, status),
                              (GeneratedDocumentRecord.course_id, course_id), (GeneratedDocumentRecord.language, language)):
            if value:
                q = q.where(column == value)
        with self._db.session() as s:
            return list(s.scalars(q.order_by(GeneratedDocumentRecord.updated_at.desc())).all())

    def counts(self) -> dict[str, int]:
        with self._db.session() as s:
            rows = s.execute(select(GeneratedDocumentRecord.status, func.count())
                             .group_by(GeneratedDocumentRecord.status)).all()
        return {status: n for status, n in rows}

    def create(self, *, actor: str, **fields) -> GeneratedDocumentRecord:
        record = GeneratedDocumentRecord(id=new_document_id(), status="draft", revision=1,
                                         log=[self._entry(actor, "generated", fields.pop("note", ""))], **fields)
        with self._db.session() as s:
            s.add(record)
        return record

    def save_content(self, document_id: str, *, title: str, content: dict, actor: str, note: str = "") -> GeneratedDocumentRecord | None:
        """A human edit: new revision, back to draft (it must be approved again)."""
        with self._db.session() as s:
            row = s.get(GeneratedDocumentRecord, document_id.strip().upper())
            if row is None:
                return None
            row.title, row.content = title, content
            row.revision += 1
            row.status, row.approved_at, row.approved_by = "draft", None, None
            row.log = [*row.log, self._entry(actor, "edited", note)]
            return row

    def approve(self, document_id: str, reviewer: str, note: str = "") -> GeneratedDocumentRecord | None:
        with self._db.session() as s:
            row = s.get(GeneratedDocumentRecord, document_id.strip().upper())
            if row is None:
                return None
            row.status, row.approved_at, row.approved_by = "approved", _now(), reviewer
            row.log = [*row.log, self._entry(reviewer, "approved", note)]
            return row

    def add_log(self, document_id: str, actor: str, action: str, note: str = "") -> None:
        with self._db.session() as s:
            row = s.get(GeneratedDocumentRecord, document_id.strip().upper())
            if row is not None:
                row.log = [*row.log, self._entry(actor, action, note)]

    @staticmethod
    def _entry(actor: str, action: str, note: str) -> dict:
        return {"at": _now().isoformat(timespec="seconds"), "by": actor, "action": action, "note": note}
