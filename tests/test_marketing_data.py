"""Agent 5 data layer: models, migration 0003, repositories, shared OfferService."""
from datetime import date, timedelta

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import inspect

from src.db.database import MIGRATIONS_DIR, Database
from src.db.models import DraftStatus
from src.db.repositories import CourseRepository, MarketingRepository, OfferRepository
from src.knowledge_base.loader import load_catalog
from src.marketing.models import CampaignBrief, CampaignObjective, Platform
from src.models.offer import Offer
from src.services.offer_service import OfferError, OfferService
from tests.conftest import OFFICIAL_DATASET

TODAY = date.today()
NEW_TABLES = {"course_offers", "marketing_campaigns", "marketing_drafts"}


@pytest.fixture
def db(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'm.db'}")
    database.migrate()
    CourseRepository(database).replace_catalog(load_catalog(OFFICIAL_DATASET))
    return database


@pytest.fixture
def offers(db):
    return OfferService(OfferRepository(db), CourseRepository(db))


def _offer(**kw) -> Offer:
    base = dict(id="o1", course_id="TRK-001", title_en="Early bird", discount_percent=15,
                valid_from=TODAY, valid_until=TODAY + timedelta(days=10))
    return Offer(**(base | kw))


# ---------------------------------------------------------------- models
def test_brief_reports_missing_required_fields_and_merges_turns():
    brief = CampaignBrief(course_id="TRK-001")
    assert brief.missing_required() == ["platforms", "objective"]
    turn = CampaignBrief(platforms=[Platform.EMAIL], objective=CampaignObjective.ENROLLMENT)
    merged = brief.merged_with(turn, {"platforms", "objective"})
    assert merged.course_id == "TRK-001" and merged.missing_required() == []
    assert brief.merged_with(CampaignBrief(), set()).course_id == "TRK-001"   # unmentioned fields keep old values


def test_brief_rejects_out_of_range_variations():
    with pytest.raises(ValidationError):
        CampaignBrief(variations=9)


@pytest.mark.parametrize("bad", [
    dict(discount_percent=None),                                    # neither percent nor price
    dict(price_amount=100),                                         # both
    dict(discount_percent=None, price_amount=100),                  # price without currency
    dict(valid_until=TODAY - timedelta(days=1)),                    # ends before it starts
    dict(discount_percent=150),
])
def test_offer_validation(bad):
    with pytest.raises(ValidationError):
        _offer(**bad)


def test_offer_active_only_when_approved_and_in_window():
    offer = _offer(approval_status="approved")
    assert offer.is_active(TODAY) and not offer.is_active(TODAY + timedelta(days=11))
    assert not _offer().is_active(TODAY)                          # pending
    assert "15%" in offer.describe("en")


# ---------------------------------------------------------------- migration
def test_migration_0003_adds_tables_and_downgrades_cleanly(tmp_path):
    url = f"sqlite:///{tmp_path / 'mig.db'}"
    database = Database(url)
    database.migrate()
    assert NEW_TABLES <= set(inspect(database.engine).get_table_names())
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", url)
    with database.engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "0002")
    tables = set(inspect(database.engine).get_table_names())
    assert not (NEW_TABLES & tables) and {"courses", "leads", "documents", "rfp_opportunities"} <= tables  # others untouched
    database.migrate()                                                                # and back up again
    assert NEW_TABLES <= set(inspect(database.engine).get_table_names())


def test_offers_survive_a_catalog_reseed(db, offers):
    """No hard FK on course_id: replacing the catalog must not delete or block offers."""
    oid = offers.propose(course_id="TRK-001", title_en="Early bird", discount_percent=10, valid_from=TODAY,
                         valid_until=TODAY, proposed_by="nadin")
    CourseRepository(db).replace_catalog(load_catalog(OFFICIAL_DATASET))
    assert OfferRepository(db).get(oid) is not None


# ---------------------------------------------------------------- OfferService
def test_only_approved_current_offers_are_active(offers):
    kw = dict(course_id="trk-001", title_en="Offer", discount_percent=10, proposed_by="nadin")
    pending = offers.propose(valid_from=TODAY, valid_until=TODAY, **kw)
    approved = offers.propose(valid_from=TODAY, valid_until=TODAY + timedelta(days=5), **kw)
    expired = offers.propose(valid_from=TODAY - timedelta(days=9), valid_until=TODAY - timedelta(days=1), **kw)
    future = offers.propose(valid_from=TODAY + timedelta(days=3), valid_until=TODAY + timedelta(days=9), **kw)
    rejected = offers.propose(valid_from=TODAY, valid_until=TODAY, **kw)
    for oid in (approved, expired, future):
        offers.approve(oid, "manager")
    offers.reject(rejected, "manager")
    assert [o.id for o in offers.active_offers("TRK-001")] == [approved]
    assert pending and len(offers.list_offers("TRK-001")) == 5


def test_offer_service_rejects_bad_input(offers):
    with pytest.raises(OfferError, match="No approved course"):
        offers.propose(course_id="ZZZ-999", title_en="x offer", discount_percent=5, valid_from=TODAY,
                       valid_until=TODAY, proposed_by="nadin")
    with pytest.raises(OfferError):
        offers.propose(course_id="TRK-001", title_en="x offer", valid_from=TODAY, valid_until=TODAY, proposed_by="n")
    oid = offers.propose(course_id="TRK-001", title_en="x offer", discount_percent=5, valid_from=TODAY,
                         valid_until=TODAY, proposed_by="nadin")
    with pytest.raises(OfferError, match="reviewer name"):
        offers.approve(oid, "  ")
    with pytest.raises(OfferError, match="No offer"):
        offers.approve("missing", "manager")


# ---------------------------------------------------------------- MarketingRepository
def test_campaign_and_drafts_saved_together_and_edits_reset_review(db):
    repo = MarketingRepository(db)
    content = {"platform": "email", "language": "en", "headline": "h", "body": "b", "cta": "c"}
    cid, ids = repo.create_campaign(course_id="TRK-001", brief={}, strategy={}, facts_snapshot={"x": 1},
                                    generator="deterministic", drafts=[{"content": content}, {"content": content}])
    assert len(ids) == 2 and repo.get_campaign(cid).facts_snapshot == {"x": 1}
    assert all(d.approval_status == "pending_review" for d in repo.list_drafts(campaign_id=cid))
    repo.set_status(ids[0], DraftStatus.APPROVED, "manager")
    repo.update_content(ids[0], content=content, claim_issues=[], evaluation={})
    draft = repo.get_draft(ids[0])
    assert draft.approval_status == "pending_review" and draft.reviewed_by is None and draft.edited


def test_campaign_save_is_atomic(db):
    repo = MarketingRepository(db)
    with pytest.raises(Exception):     # second draft is malformed: nothing may be saved
        repo.create_campaign(course_id="TRK-001", brief={}, strategy={}, facts_snapshot={}, generator="x",
                             drafts=[{"content": {"platform": "email", "language": "en"}}, {"content": {}}])
    assert repo.list_campaigns() == []
