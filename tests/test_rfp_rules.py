"""Agent 2 — screening, duplicates, qualification and summaries."""
from datetime import date

import pytest

from src.models.course import Course
from src.rfp.extraction import extract
from src.rfp.models import Country, ExclusionReason, Fit, OpportunityStatus, RawTender
from src.rfp.qualification import assess
from src.rfp.screening import DuplicateDetector, initial_status, screen
from src.rfp.summary import deterministic_summary, summarize
from src.services.llm_service import LLMError

TODAY = date(2026, 10, 24)


def opp_of(**kw):
    defaults = dict(title="AI training programme", organization="Org", country="Egypt",
                    deadline="2026-12-01", description="Training in AI for staff.")
    return extract(RawTender(**(defaults | kw)))


@pytest.mark.parametrize("overrides, reason", [
    ({"country": "United Arab Emirates"}, ExclusionReason.OUTSIDE_MARKETS),
    ({"country": "Germany"}, ExclusionReason.OUTSIDE_MARKETS),
    ({"country": "", "description": "Training online."}, ExclusionReason.COUNTRY_UNKNOWN),
    ({"title": "Supply of laptops", "description": "Hardware supply."}, ExclusionReason.NOT_TRAINING),
    ({"deadline": "2026-10-23"}, ExclusionReason.EXPIRED),
    ({"title": ""}, ExclusionReason.INVALID),
])
def test_screen_excludes_with_one_clear_reason(overrides, reason):
    opp, training = opp_of(**overrides)
    assert screen(opp, training, TODAY).reason == reason


def test_screen_keeps_egypt_and_saudi_training_and_unknown_deadlines():
    for country in ("Egypt", "Saudi Arabia"):
        assert screen(*opp_of(country=country), TODAY) is None
    assert screen(*opp_of(deadline="2026-10-24"), TODAY) is None         # due today: still open
    assert screen(*opp_of(deadline="1448/06/20 هـ"), TODAY) is None      # unknown date: kept, shown as n/a


def test_business_rule_egypt_flagged_saudi_monitored():
    assert initial_status(opp_of(country="Egypt")[0]) == OpportunityStatus.FLAGGED
    assert initial_status(opp_of(country="KSA")[0]) == OpportunityStatus.MONITORING


def test_duplicates_by_link_by_similar_title_and_not_across_organizations():
    original, _ = opp_of(title="Capacity building programme in data analysis", link="https://x.org/1")
    detector = DuplicateDetector()
    assert detector.match(original) is None
    detector.remember(original)
    reworded, _ = opp_of(title="Capacity building program in data analysis", link="https://y.org/2")
    same_link, _ = opp_of(title="Totally different words", link="https://x.org/1")
    other_org, _ = opp_of(title="Capacity building programme in data analysis", organization="Another Org")
    assert detector.match(reworded) == original.dedup_key
    assert detector.match(same_link) == original.dedup_key
    assert detector.match(other_org) is None                  # same words, different buyer: separate tender


@pytest.fixture
def courses():
    from src.knowledge_base.loader import load_catalog
    from tests.conftest import OFFICIAL_DATASET
    return load_catalog(OFFICIAL_DATASET).courses


def test_fit_strong_possible_weak(courses: list[Course]):
    strong, _ = opp_of(title="Software testing bootcamp", description="Training in test automation.",
                       eligibility="Training provider with a testing curriculum.")
    fit, reasons, ids = assess(strong, courses)
    assert fit == Fit.STRONG and ids == ["TRK-001"]

    to_verify, _ = opp_of(title="Flutter mobile training", description="Training in Flutter.",
                          eligibility="Commercial registration and 5 years of experience required.")
    fit, reasons, ids = assess(to_verify, courses)
    assert fit == Fit.POSSIBLE and "TRK-002" in ids
    assert any("5+ years" in r for r in reasons) and any("registration" in r for r in reasons)

    unpublished, _ = opp_of(title="Software testing bootcamp", eligibility=None)
    assert assess(unpublished, courses)[0] == Fit.POSSIBLE      # cannot be strong without requirements

    weak, _ = opp_of(title="Leadership workshops for principals", description="Training for school principals.",
                     eligibility="ISO certified provider with a bank guarantee.")
    assert assess(weak, courses)[0] == Fit.WEAK


def test_fit_never_claims_qualification(courses):
    opp, _ = opp_of(title="Data engineering training", eligibility="Any provider.")
    _, reasons, _ = assess(opp, courses)
    assert not any("qualifies" in r.lower() for r in reasons)


class FakeLLM:
    name = "fake"

    def __init__(self, reply: str | Exception):
        self.reply = reply

    def generate(self, system_prompt, messages):
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def test_summary_deterministic_and_llm_fallbacks():
    opp, _ = opp_of()
    base = deterministic_summary(opp, TODAY)
    assert "Org (Egypt)" in base and "2026-12-01 (38 days left)" in base
    assert summarize(opp, TODAY, None) == (base, "deterministic")
    assert summarize(opp, TODAY, FakeLLM("Org wants AI training by 2026-12-01.")) == \
        ("Org wants AI training by 2026-12-01.", "llm")
    assert summarize(opp, TODAY, FakeLLM("Org wants AI training soon."))[1] == "deterministic"   # lost deadline
    assert summarize(opp, TODAY, FakeLLM(LLMError("down")))[1] == "deterministic"


def test_summary_without_deadline_says_so():
    opp, _ = opp_of(deadline=None)
    assert "Deadline not stated" in deterministic_summary(opp, TODAY)
    assert opp.country == Country.EGYPT