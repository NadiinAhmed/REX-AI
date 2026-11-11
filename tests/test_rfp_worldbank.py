"""Agent 2 — World Bank API source and reading real notice documents (offline: the API is faked)."""
from datetime import date
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse

import pytest

from src.config import ConfigError, load_settings
from src.db.database import Database
from src.db.rfp_repository import RfpRepository
from src.rfp.documents import deadline_from_text, html_to_text, requirement_sentences
from src.rfp.extraction import extract
from src.rfp.models import Country, ExclusionReason, RawTender
from src.rfp.pipeline import RfpScanPipeline
from src.rfp.sources import JsonFileSource, SourceError
from src.rfp.worldbank import WorldBankSource

TODAY = date(2026, 11, 1)

# Shaped like the API's notices (field names from the API); the content is invented for tests.
TRAINING_REOI = {
    "id": "OP00999001", "notice_type": "Request for Expression of Interest", "notice_status": "Published",
    "noticetitle": "Consultancy services for a capacity building and training programme in data analytics",
    "bid_description": "Consulting firm to design and deliver training in data analytics for ministry staff",
    "project_ctry_name": ["Egypt, Arab Republic of"], "contact_organization": "Test Ministry Project Unit",
    "noticedate": "15-Oct-2026", "submission_deadline_date": "2026-11-25T00:00:00Z",
    "procurement_method_name": "Quality And Cost-Based Selection", "procurement_group": "CS",
    "notice_text": "<p>The Ministry intends to apply part of the proceeds for consulting services.</p>"
                   "<p>The consultant must have at least 5 years of experience in delivering training "
                   "programmes in data analytics.</p><p>Firms shall provide evidence of similar assignments.</p>",
}
GOODS_IFB = {
    "id": "OP00999002", "notice_type": "Invitation for Bids", "noticetitle": "Supply of laboratory equipment",
    "bid_description": "Supply and installation of laboratory equipment",
    "project_ctry_name": "Egypt, Arab Republic of", "contact_organization": "Test Health Unit",
    "noticedate": "10-Oct-2026", "submission_deadline_date": "2026-11-30T00:00:00Z",
    "notice_text": "<p>Supply of equipment, including training of operators.</p>",
}
AWARD = {"id": "OP00999003", "notice_type": "Contract Award", "noticetitle": "Award: training services",
         "project_ctry_name": "Egypt, Arab Republic of"}
NO_FIELD_DEADLINE = {
    "id": "OP00999004", "notice_type": "Request for Expression of Interest",
    "noticetitle": "Technical training for vocational instructors", "project_ctry_name": "Egypt, Arab Republic of",
    "contact_organization": "Test TVET Agency", "noticedate": "20-Oct-2026",
    "notice_text": "<p>Expressions of interest must be delivered by December 3, 2026 at 12:00.</p>",
}


def fake_api(by_country: dict[str, dict] | None = None, fail: set[str] = frozenset()):
    calls = []

    def get_json(url: str, timeout: float) -> dict:
        country = parse_qs(urlparse(url).query)["project_ctry_name_exact"][0]
        calls.append(country)
        if country in fail:
            raise URLError("offline")
        return (by_country or {}).get(country, {"procnotices": [], "total": 0})
    get_json.calls = calls
    return get_json


EGYPT = "Egypt, Arab Republic of"


def test_documents_html_requirements_and_deadline():
    text = html_to_text("<p>Deadline: 20/11/2026.</p><script>bad()</script><p>The firm must have "
                        "at least 3 years of experience.</p><p>Good&nbsp;luck to all applicants today.</p>")
    assert "bad()" not in text and "Good luck" in text
    assert requirement_sentences(text) == "The firm must have at least 3 years of experience."
    assert deadline_from_text(text) == date(2026, 11, 20)
    assert deadline_from_text("Contact us on 12/12/2026 for questions about the project.") is None  # not a deadline sentence
    assert requirement_sentences("Short text.") is None and html_to_text(None) == ""


def test_source_maps_fields_skips_awards_and_queries_both_markets():
    api = fake_api({EGYPT: {"procnotices": [TRAINING_REOI, GOODS_IFB, AWARD]}})
    source = WorldBankSource(get_json=api)
    tenders = source.fetch()
    assert api.calls == [EGYPT, "Saudi Arabia"]
    assert len(tenders) == 2 and source.awards_skipped == 1
    t = tenders[0]
    assert t.title.startswith("Consultancy services") and t.organization == "Test Ministry Project Unit"
    assert t.country == EGYPT and t.deadline.startswith("2026-11-25") and t.published == "15-Oct-2026"
    assert t.link.endswith("/procurement-detail/OP00999001") and "5 years" in t.full_text
    assert not source.info.synthetic and "1 contract-award" in source.info.notice and "Saudi Arabia: 0" in source.info.notice


def test_notices_as_id_mapping_are_accepted():
    source = WorldBankSource(get_json=fake_api({EGYPT: {"procnotices": {"OP00999001": TRAINING_REOI}}}))
    assert len(source.fetch()) == 1


def test_real_document_fills_eligibility_and_deadline():
    source = WorldBankSource(get_json=fake_api({EGYPT: {"procnotices": [TRAINING_REOI, NO_FIELD_DEADLINE]}}))
    reoi, no_deadline = source.fetch()
    opp, training = extract(reoi)
    assert training and opp.country == Country.EGYPT and opp.deadline == date(2026, 11, 25)
    assert "5 years of experience" in opp.eligibility and "similar assignments" in opp.eligibility
    assert opp.publication_date == date(2026, 10, 15)
    assert extract(no_deadline)[0].deadline == date(2026, 12, 3)          # read from the notice text


def test_goods_notice_mentioning_training_is_not_a_training_tender():
    source = WorldBankSource(get_json=fake_api({EGYPT: {"procnotices": [GOODS_IFB]}}))
    assert extract(source.fetch()[0])[1] is False


def test_unreachable_api_is_reported_partial_failure_noted():
    with pytest.raises(SourceError, match="could not be reached"):
        WorldBankSource(get_json=fake_api(fail={EGYPT, "Saudi Arabia"})).fetch()
    partial = WorldBankSource(get_json=fake_api({EGYPT: {"procnotices": [TRAINING_REOI]}}, fail={"Saudi Arabia"}))
    assert len(partial.fetch()) == 1 and "Not reached: Saudi Arabia" in partial.info.notice


@pytest.fixture
def courses():
    from src.knowledge_base.loader import load_catalog
    from tests.conftest import OFFICIAL_DATASET
    return load_catalog(OFFICIAL_DATASET).courses


def test_scan_with_world_bank_and_demo_feed(courses):
    from tests.test_rfp_agent import DEMO_FEED
    db = Database("sqlite://")
    db.migrate()
    repo = RfpRepository(db)
    wb = WorldBankSource(get_json=fake_api({EGYPT: {"procnotices": [TRAINING_REOI, GOODS_IFB, AWARD, NO_FIELD_DEADLINE]}}))
    report = RfpScanPipeline([wb, JsonFileSource(DEMO_FEED)], repo, courses=lambda: courses,
                             clock=lambda: TODAY).run()
    real = [r for r in repo.list() if r.source_name == "World Bank Procurement API"]
    assert {r.title[:20] for r in real} == {"Consultancy services", "Technical training f"}
    assert all(not r.synthetic and r.country == "EG" and r.status == "flagged_for_review" for r in real)
    assert all(repo.get(i).synthetic for i in report.new if repo.get(i).source_name != "World Bank Procurement API")
    assert any("World Bank Procurement API" in n for n in report.source_notices)
    assert report.excluded_count(ExclusionReason.NOT_TRAINING) >= 2      # goods notice + demo IT hardware
    data_tender = next(r for r in real if r.title.startswith("Consultancy"))
    assert "TRK-003" in data_tender.matched_course_ids or "TRK-005" in data_tender.matched_course_ids


def test_offline_api_does_not_stop_the_demo_feed(courses):
    from tests.test_rfp_agent import DEMO_FEED
    db = Database("sqlite://")
    db.migrate()
    report = RfpScanPipeline([WorldBankSource(get_json=fake_api(fail={EGYPT, "Saudi Arabia"})), JsonFileSource(DEMO_FEED)],
                             RfpRepository(db), courses=lambda: courses, clock=lambda: date(2026, 10, 24)).run()
    assert len(report.new) == 6 and any("could not be reached" in n for n in report.source_notices)


def test_rfp_sources_setting():
    assert load_settings({}).rfp_sources == ("worldbank", "etimad", "ungm")
    assert load_settings({"RFP_SOURCES": " file , worldbank,file"}).rfp_sources == ("file", "worldbank")
    with pytest.raises(ConfigError):
        load_settings({"RFP_SOURCES": "tendersinfo"})


def test_bootstrap_builds_sources_in_order():
    from src.bootstrap import build_rfp_sources
    built = build_rfp_sources(load_settings({"RFP_SOURCES": "worldbank,file"}))
    assert [type(s).__name__ for s in built] == ["WorldBankSource", "JsonFileSource"]


def test_raw_tender_without_full_text_unchanged():
    opp, _ = extract(RawTender(title="AI training", country="Egypt", deadline="2026-12-01"))
    assert opp.eligibility is None and opp.deadline == date(2026, 12, 1)
