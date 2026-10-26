"""Repositories: the only classes that read or write the shared tables.

Agents and services receive a repository (dependency injection) instead of writing SQL, so
each table has exactly one place that defines how it is used.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timezone

from sqlalchemy import delete, func, select

from src.db.database import Database
from src.db.models import (
    ApprovalStatus, CatalogMeta, ChunkRecord, CourseRecord, DocumentRecord, DraftStatus, EvaluationRunRecord,
    IndexStatus, LeadRecord, MarketingCampaignRecord, MarketingDraftRecord, OfferRecord,
)
from src.models.course import Course, CourseCatalog, DatasetInfo
from src.models.offer import Offer


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


def _as_datetime(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=timezone.utc)


def _as_date(dt: datetime) -> date:
    return dt.date()


class OfferRepository:
    """`course_offers`: the shared source of approved promotions."""

    def __init__(self, db: Database) -> None:
        self._db = db

    @staticmethod
    def _to_offer(r: OfferRecord) -> Offer:
        return Offer(id=r.id, course_id=r.course_id, title_en=r.title_en, title_ar=r.title_ar,
                     discount_percent=r.discount_percent, price_amount=r.price_amount, currency=r.currency,
                     valid_from=_as_date(r.valid_from), valid_until=_as_date(r.valid_until),
                     approval_status=r.approval_status, proposed_by=r.proposed_by, approved_by=r.approved_by)

    def create(self, offer: Offer) -> str:
        with self._db.session() as s:
            s.add(OfferRecord(id=offer.id, course_id=offer.course_id, title_en=offer.title_en, title_ar=offer.title_ar,
                              discount_percent=offer.discount_percent, price_amount=offer.price_amount,
                              currency=offer.currency, valid_from=_as_datetime(offer.valid_from),
                              valid_until=_as_datetime(offer.valid_until), approval_status=offer.approval_status,
                              proposed_by=offer.proposed_by))
        return offer.id

    def get(self, offer_id: str) -> Offer | None:
        with self._db.session() as s:
            row = s.get(OfferRecord, offer_id)
            return self._to_offer(row) if row else None

    def list(self, course_id: str | None = None, status: str | None = None) -> list[Offer]:
        q = select(OfferRecord).order_by(OfferRecord.created_at)
        if course_id:
            q = q.where(OfferRecord.course_id == course_id.strip().upper())
        if status:
            q = q.where(OfferRecord.approval_status == status)
        with self._db.session() as s:
            return [self._to_offer(r) for r in s.scalars(q).all()]

    def set_status(self, offer_id: str, status: ApprovalStatus, by: str) -> bool:
        with self._db.session() as s:
            row = s.get(OfferRecord, offer_id)
            if row is None:
                return False
            row.approval_status, row.approved_by = status.value, by
            row.approved_at = datetime.now(timezone.utc)
            return True


class MarketingRepository:
    """`marketing_campaigns` and `marketing_drafts`."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def create_campaign(self, *, course_id: str, brief: dict, strategy: dict, facts_snapshot: dict,
                        generator: str, drafts: list[dict]) -> tuple[str, list[str]]:
        """Campaign + its drafts in ONE transaction: either all are saved or none."""
        campaign_id = str(uuid.uuid4())
        draft_ids: list[str] = []
        with self._db.session() as s:
            s.add(MarketingCampaignRecord(id=campaign_id, course_id=course_id, brief=brief, strategy=strategy,
                                          facts_snapshot=facts_snapshot, generator=generator))
            s.flush()
            for i, d in enumerate(drafts):
                draft_id = str(uuid.uuid4())
                draft_ids.append(draft_id)
                s.add(MarketingDraftRecord(
                    id=draft_id, campaign_id=campaign_id, variation_index=i, platform=d["content"]["platform"],
                    language=d["content"]["language"], content=d["content"], claim_issues=d.get("claim_issues", []),
                    evaluation=d.get("evaluation", {}), approval_status=DraftStatus.PENDING_REVIEW.value))
        return campaign_id, draft_ids

    def get_campaign(self, campaign_id: str) -> MarketingCampaignRecord | None:
        with self._db.session() as s:
            return s.get(MarketingCampaignRecord, campaign_id)

    def list_campaigns(self) -> list[MarketingCampaignRecord]:
        with self._db.session() as s:
            return list(s.scalars(select(MarketingCampaignRecord)
                                  .order_by(MarketingCampaignRecord.created_at.desc())).all())

    def get_draft(self, draft_id: str) -> MarketingDraftRecord | None:
        with self._db.session() as s:
            return s.get(MarketingDraftRecord, draft_id)

    def list_drafts(self, status: DraftStatus | None = None, campaign_id: str | None = None) -> list[MarketingDraftRecord]:
        q = select(MarketingDraftRecord).order_by(MarketingDraftRecord.created_at.desc(),
                                                  MarketingDraftRecord.variation_index)
        if status:
            q = q.where(MarketingDraftRecord.approval_status == status.value)
        if campaign_id:
            q = q.where(MarketingDraftRecord.campaign_id == campaign_id)
        with self._db.session() as s:
            return list(s.scalars(q).all())

    def update_content(self, draft_id: str, *, content: dict, claim_issues: list, evaluation: dict) -> bool:
        """An edit sends the draft back to review: previous review fields are cleared."""
        with self._db.session() as s:
            row = s.get(MarketingDraftRecord, draft_id)
            if row is None:
                return False
            row.content, row.claim_issues, row.evaluation, row.edited = content, claim_issues, evaluation, True
            row.approval_status = DraftStatus.PENDING_REVIEW.value
            row.reviewed_by = row.reviewed_at = row.review_note = None
            return True

    def set_status(self, draft_id: str, status: DraftStatus, reviewed_by: str, note: str | None = None) -> bool:
        with self._db.session() as s:
            row = s.get(MarketingDraftRecord, draft_id)
            if row is None:
                return False
            row.approval_status, row.reviewed_by, row.review_note = status.value, reviewed_by, note
            row.reviewed_at = datetime.now(timezone.utc)
            return True
