"""Shared database: migrations, course catalog, cascade deletes, leads."""
from datetime import datetime, timezone

import pytest

from src.db.database import Database
from src.db.models import ApprovalStatus, ChunkRecord
from src.db.repositories import CourseRepository, DocumentRepository, LeadRepository


@pytest.fixture
def db():
    d = Database("sqlite://")
    d.migrate()               # runs the real Alembic migration
    return d


def test_migration_creates_all_shared_tables(db):
    from sqlalchemy import inspect
    tables = set(inspect(db.engine).get_table_names())
    assert {"courses", "catalog_meta", "documents", "document_chunks", "leads", "evaluation_runs"} <= tables


def test_course_catalog_round_trip_and_replace(db, catalog):
    repo = CourseRepository(db)
    assert repo.count() == 0 and repo.dataset_info() is None
    repo.replace_catalog(catalog, "courses.json")
    assert repo.count() == len(catalog.courses)
    assert repo.get("ds-101").title == "Python for Data Analysis"       # id lookup is case-insensitive
    info, source = repo.dataset_info()
    assert info.is_synthetic and source == "courses.json"
    repo.replace_catalog(catalog, "again.json")                          # replace, never duplicate
    assert repo.count() == len(catalog.courses)


def test_official_catalog_has_no_invented_values():
    from src.knowledge_base.loader import load_catalog
    from tests.conftest import OFFICIAL_DATASET
    cat = load_catalog(OFFICIAL_DATASET)
    assert len(cat.courses) == 5 and not cat.dataset.is_synthetic
    for c in cat.courses:   # the brochure publishes none of these
        assert not c.fee.is_known and c.instructor is None and c.schedule is None
        assert c.source.source_type.value == "official" and "p." in c.source.reference
    assert {c.course_id: c.duration_hours for c in cat.courses} == {
        "TRK-001": 100, "TRK-002": 120, "TRK-003": None, "TRK-004": 140, "TRK-005": 100}


def test_deleting_a_document_cascades_to_chunks(db):
    docs = DocumentRepository(db)
    doc_id = docs.create(filename="a.pdf", sha256="x" * 64, size_bytes=1, page_count=1, doc_type="faq",
                         approval_status=ApprovalStatus.PENDING, embedding_model="m")
    docs.add_chunks(doc_id, [ChunkRecord(id=f"{doc_id}:0", document_id=doc_id, page=1, chunk_index=0,
                                         text="hello world", text_hash="h")])
    assert docs.chunk_ids(doc_id) == [f"{doc_id}:0"]
    docs.delete(doc_id)
    assert docs.chunk_ids(doc_id) == []


def test_lead_is_stored_with_consent_metadata(db):
    repo = LeadRepository(db)
    repo.create(name="Sara", email="s@x.com", phone=None, language="en", profile={}, interested_course_ids=["TRK-005"],
                consent_given_at=datetime.now(timezone.utc), consent_version="v1")
    lead = repo.list()[0]
    assert lead.name == "Sara" and lead.consent_version == "v1" and lead.interested_course_ids == ["TRK-005"]
