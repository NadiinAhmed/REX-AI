"""Agent 2 — pipeline + shared database + agent, end to end on the demo feed and on small feeds."""
import json
from datetime import date
from pathlib import Path

import pytest

from src.agents.base import AgentRequest, AgentStatus
from src.config import PROJECT_ROOT
from src.db.database import Database
from src.db.rfp_repository import RfpRepository
from src.rfp.models import ExclusionReason, OpportunityStatus
from src.rfp.pipeline import RfpScanPipeline
from src.rfp.sources import JsonFileSource

DEMO_FEED = PROJECT_ROOT / "data" / "rfp" / "sample_tenders.json"
TODAY = date(2026, 10, 24)


@pytest.fixture
def db():
    d = Database("sqlite://")
    d.migrate()
    return d


@pytest.fixture
def courses():
    from src.knowledge_base.loader import load_catalog
    from tests.conftest import OFFICIAL_DATASET
    return load_catalog(OFFICIAL_DATASET).courses


def make_pipeline(db, courses, feed: Path = DEMO_FEED, today: date = TODAY):
    repo = RfpRepository(db)
    return RfpScanPipeline([JsonFileSource(feed)], repo, courses=lambda: courses, clock=lambda: today), repo


def write_feed(tmp_path: Path, tenders: list[dict], name="feed.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps({"feed": {"name": name}, "tenders": tenders}, ensure_ascii=False), encoding="utf-8")
    return path


def test_migration_creates_rfp_table(db):
    from sqlalchemy import inspect
    assert "rfp_opportunities" in inspect(db.engine).get_table_names()


def test_demo_feed_scan_applies_every_rule(db, courses):
    pipeline, repo = make_pipeline(db, courses)
    report = pipeline.run()
    assert report.fetched == 13 and len(report.new) == 6
    assert {r: report.excluded_count(r) for r in ExclusionReason} == {
        ExclusionReason.OUTSIDE_MARKETS: 2, ExclusionReason.COUNTRY_UNKNOWN: 1, ExclusionReason.NOT_TRAINING: 1,
        ExclusionReason.EXPIRED: 1, ExclusionReason.DUPLICATE: 1, ExclusionReason.INVALID: 1}
    stored = repo.list()
    assert {r.country for r in stored} == {"EG", "SA"}                       # strict market rule
    assert all(r.status == "flagged_for_review" for r in stored if r.country == "EG")
    assert all(r.status == "monitoring" for r in stored if r.country == "SA")
    assert len(report.alerts) == 3 and all(repo.get(i).alerted_at for i in report.alerts)
    assert all(r.synthetic for r in stored) and any("SYNTHETIC" in n for n in report.source_notices)


def test_rescan_is_idempotent_and_does_not_realert(db, courses):
    pipeline, repo = make_pipeline(db, courses)
    pipeline.run()
    second = pipeline.run()
    assert second.new == [] and len(second.updated) == 6 and second.alerts == []
    assert len(repo.list()) == 6


def test_human_decision_survives_rescan(db, courses):
    pipeline, repo = make_pipeline(db, courses)
    first = pipeline.run()
    oid = first.alerts[0]
    repo.set_status(oid, OpportunityStatus.DISMISSED)
    pipeline.run()
    assert repo.get(oid).status == "dismissed"


def test_stored_opportunity_expires_on_later_scan(db, courses):
    pipeline, repo = make_pipeline(db, courses)
    pipeline.run()
    later, _ = make_pipeline(db, courses, today=date(2026, 11, 21))   # after the 2026-11-20 deadline
    report = later.run()
    assert report.expired_marked >= 1
    assert any(r.status == "expired" for r in repo.list(country="EG"))


def test_bad_feed_is_reported_not_crashing(db, courses, tmp_path):
    missing, _ = make_pipeline(db, courses, feed=tmp_path / "nope.json")
    report = missing.run()
    assert report.fetched == 0 and "not found" in report.source_notices[0]
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert "not valid" in make_pipeline(db, courses, feed=broken)[0].run().source_notices[0]
    partly = write_feed(tmp_path, [{"title": "Data training in Cairo", "documents": "not-a-list"},
                                   {"title": "AI training in Cairo", "deadline": "2026-12-01"}])
    report = make_pipeline(db, courses, feed=partly)[0].run()
    assert report.fetched == 1 and len(report.new) == 1 and "skipped" in report.source_notices[0]


def test_same_tender_twice_in_one_feed_is_stored_once(db, courses, tmp_path):
    item = {"title": "AI training", "organization": "Org", "country": "Egypt", "deadline": "2026-12-01"}
    report = make_pipeline(db, courses, feed=write_feed(tmp_path, [item, dict(item)]))[0].run()
    assert len(report.new) == 1 and report.excluded_count(ExclusionReason.DUPLICATE) == 1


# ------------------------------------------------------------------ agent through the registry
@pytest.fixture
def ctx(test_env):
    from src.bootstrap import build_app
    from src.config import load_settings
    context = build_app(load_settings(test_env(REX_COURSES_PATH=str(PROJECT_ROOT / "data" / "courses_official.json"))),
                        enable_rag=False)
    agent = context.registry.get("rfp_research")
    agent._clock = agent._pipeline._clock = lambda: TODAY
    return context


def ask(ctx, text):
    return ctx.registry.dispatch("rfp_research", AgentRequest.from_text(text))


def test_agent_is_registered_and_implemented(ctx):
    status = {a.name: a.status for a in ctx.registry.list_agents()}
    assert status["rfp_research"] == AgentStatus.IMPLEMENTED and ctx.rfp_repository is not None


def test_agent_conversation_english(ctx):
    assert "Nothing is stored yet" in ask(ctx, "hello").text
    scan = ask(ctx, "scan for new tenders")
    assert scan.data["intent"] == "scan" and "New Egypt opportunities" in scan.text
    assert len(scan.sources) == 3 and all(s.source_type == "tender" for s in scan.sources)
    egypt = ask(ctx, "show egypt")
    assert egypt.data["intent"] == "list_eg" and "flagged for immediate review" in egypt.text
    saudi = ask(ctx, "saudi please")
    assert saudi.data["intent"] == "list_sa" and "Riyadh Future Skills" in saudi.text
    oid = ctx.rfp_repository.list(country="SA")[0].id
    detail = ask(ctx, f"details for {oid.lower()}")
    assert oid in detail.text and "not a guarantee" in detail.text and "synthetic" in detail.text


def test_agent_conversation_arabic(ctx):
    scan = ask(ctx, "ابحث عن مناقصات جديدة")
    assert scan.language == "ar" and "اكتمل البحث" in scan.text and "خارج مصر والسعودية" in scan.text
    assert "قيد المتابعة" in ask(ctx, "السعودية").text
    assert "مراجعة فورية" in ask(ctx, "مصر").text


def test_agent_unknown_id_and_empty_lists(ctx):
    assert "No opportunity with ID RFP-ABCDEF" in ask(ctx, "RFP-abcdef").text
    assert "Type \"scan\"" in ask(ctx, "egypt").text