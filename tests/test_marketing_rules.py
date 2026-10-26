"""Agent 5 deterministic rules: fact-checker (EN + AR), brief parser, strategy, facts provider."""
from datetime import date, timedelta

import pytest

from src.knowledge_base.loader import load_catalog
from src.knowledge_base.retriever import KeywordRetriever
from src.marketing.brief_parser import BriefParser
from src.marketing.claim_validator import ClaimValidator, has_blocking
from src.marketing.facts import CourseFactsProvider, CourseNotFoundError, CurriculumFacts
from src.marketing.models import CampaignBrief, CampaignObjective, Platform, Tone
from src.marketing.strategy import StrategyPlanner
from src.models.offer import Offer
from tests.conftest import DATASET, OFFICIAL_DATASET

TODAY = date.today()


class FakeOffers:
    def __init__(self, offers=None, fail=False):
        self.offers, self.fail = offers or [], fail

    def active_offers(self, course_id, on=None):
        if self.fail:
            raise ConnectionError("server down")
        return [o for o in self.offers if o.course_id == course_id]


class FakeCurriculum:
    def approved_curriculum(self, course_id):
        return CurriculumFacts(course_id=course_id, learning_outcomes=["Design automated test suites"],
                               modules=["Test strategy"], reference="CUR-1 v2")


def provider(path=OFFICIAL_DATASET, **kw):
    return CourseFactsProvider(KeywordRetriever(load_catalog(path).courses), **kw)


OFFER = Offer(id="o", course_id="AI-201", title_en="Early bird", discount_percent=10, valid_from=TODAY,
              valid_until=TODAY + timedelta(days=7), approval_status="approved")


@pytest.fixture(scope="module")
def official():
    return provider().facts_for("TRK-001")         # no fee, format, schedule, instructor, level


@pytest.fixture(scope="module")
def synthetic():
    return provider(DATASET, offers=FakeOffers([OFFER])).facts_for("AI-201")   # 9000 EGP, hybrid, 60 h


def kinds(text, facts):
    return {i.kind for i in ClaimValidator().validate(text, facts) if i.severity == "block"}


# ---------------------------------------------------------------- fact-checker: blocks
@pytest.mark.parametrize("text,kind", [
    ("Only 2,000 EGP!", "price"), ("فقط ٢٠٠٠ جنيه", "price"), ("500 LE only", "price"),
    ("Get 20% off", "discount"), ("خصم ٢٠٪", "discount"),
    ("Special offer this week", "offer"), ("عرض خاص لفترة محدودة", "offer"),
    ("Starts on 5 November", "schedule"), ("يبدأ في 5 نوفمبر", "schedule"),
    ("A 40-hour program", "duration"), ("برنامج ٤٠ ساعة", "duration"), ("in just 6 weeks", "duration"),
    ("Study online from home", "delivery_format"), ("الدراسة أونلاين", "delivery_format"),
    ("Earn an accredited diploma", "certification"), ("شهادة معتمدة", "certification"),
    ("Taught by industry experts who will mentor you", "instructor"), ("مع نخبة من المدربين", "instructor"),
    ("No prior experience needed", "prerequisites"), ("ابدأ من الصفر", "prerequisites"),
    ("Guaranteed job after the course", "forbidden_claim"), ("#1 academy", "forbidden_claim"),
    ("ضمان وظيفة بعد التخرج", "forbidden_claim"),
])
def test_unsupported_claims_are_blocked(official, text, kind):
    assert kind in kinds(text, official)


# ---------------------------------------------------------------- fact-checker: allows supported claims
@pytest.mark.parametrize("text", [
    "Join 12 lessons on Tuesday",                    # "12 le" is not a currency
    "A 100-hour program", "برنامج ١٠٠ ساعة",          # matches duration_hours=100
    "Earn an ITI completion certificate",            # the data lists a completion certificate
    "احصل على شهادة إتمام",
    "Become a Junior QA Engineer. Request the details today.",
    "May the testers among you apply",               # "May" without a day is not a date
])
def test_supported_or_neutral_text_passes(official, text):
    assert kinds(text, official) == set()


def test_supported_values_pass_and_wrong_values_fail(synthetic):
    assert kinds("Only 9,000 EGP, hybrid format, 60 hours", synthetic) == set()
    assert kinds("10% early-bird discount: 8100 EGP", synthetic) == set()      # discounted price is derived
    assert kinds("Only 9500 EGP", synthetic) == {"price"}
    assert kinds("25% off", synthetic) == {"discount"}
    assert kinds("Fully online", synthetic) == set()                            # hybrid includes online
    assert "delivery_format" in kinds("Only in-person classes", provider(DATASET).facts_for("DS-101"))


def test_certification_word_allowed_only_when_the_data_uses_it():
    p = provider()
    assert kinds("Includes certification preparation", p.facts_for("TRK-003")) == set()   # in TRK-003 modules
    assert kinds("Includes certification preparation", p.facts_for("TRK-001")) == {"certification"}


def test_superlatives_warn_but_do_not_block(official):
    issues = ClaimValidator().validate("The best way to learn testing", official)
    assert [(i.kind, i.severity) for i in issues] == [("superlative", "warn")] and not has_blocking(issues)


# ---------------------------------------------------------------- brief parser
@pytest.fixture(scope="module")
def parser():
    r = KeywordRetriever(load_catalog(OFFICIAL_DATASET).courses)

    def resolve(text):
        c = r.get_by_id(text) or r.find_by_title(text)
        return c.course_id if c else None
    return BriefParser(resolve)


def test_parser_english(parser):
    brief, fields = parser.parse("3 variations for Software Testing on LinkedIn and Facebook to get registrations, "
                                 "audience: fresh graduates, professional tone, budget: 5000 EGP")
    assert brief.course_id == "TRK-001" and set(brief.platforms) == {Platform.LINKEDIN, Platform.FACEBOOK}
    assert brief.objective == CampaignObjective.ENROLLMENT and brief.tone == Tone.PROFESSIONAL
    assert brief.audience == "fresh graduates" and brief.budget_note == "5000 EGP" and brief.variations == 3
    assert {"course_id", "platforms", "objective", "variations"} <= fields


def test_parser_arabic_and_offer_detection(parser):
    brief, _ = parser.parse("اعمل حملة على انستجرام وواتساب لبرنامج اختبار البرمجيات للتوعية بالعربي")
    assert brief.course_id == "TRK-001" and brief.language == "ar"
    assert set(brief.platforms) == {Platform.INSTAGRAM, Platform.WHATSAPP} and brief.objective == CampaignObjective.AWARENESS
    brief, _ = parser.parse("Email for TRK-005 with a 20% discount to past students")
    assert brief.course_id == "TRK-005" and brief.requested_offer and brief.objective == CampaignObjective.RE_ENGAGEMENT


def test_parser_avoids_false_matches(parser):
    brief, fields = parser.parse("this course offers a lot. Make an instagram caption")
    assert brief.platforms == [Platform.INSTAGRAM] and brief.requested_offer is None
    assert parser.parse("hello")[1] == set()
    assert parser.parse("unknown course ZZZ-999")[0].course_id is None


# ---------------------------------------------------------------- strategy
def test_strategy_orders_platforms_by_objective_and_states_data_gaps(official):
    brief = CampaignBrief(course_id="TRK-001", platforms=[Platform.LINKEDIN, Platform.EMAIL, Platform.WHATSAPP],
                          objective=CampaignObjective.ENROLLMENT, variations=2)
    plan = StrategyPlanner().plan(brief, official, "en")
    assert plan.platform_order == [Platform.EMAIL, Platform.WHATSAPP, Platform.LINKEDIN]
    assert len(plan.variation_angles) == 2 and len(plan.sequence) == 3
    assert any("Fee is not in the approved data" in n for n in plan.notes)
    assert any("No approved offer" in n for n in plan.notes)
    assert StrategyPlanner().plan(brief, official, "ar").sequence[0].startswith("المرحلة")


# ---------------------------------------------------------------- facts provider
def test_facts_provider_reports_missing_data_and_unknown_course(official):
    assert {"fee", "schedule", "instructor", "delivery_format"} <= set(official.missing_fields)
    assert official.value("duration") == "100 hours" and official.fee_amount is None
    with pytest.raises(CourseNotFoundError):
        provider().facts_for("ZZZ-999")


def test_free_text_promotion_is_not_used_as_an_offer():
    facts = provider(DATASET).facts_for("AI-201")        # has a free-text "10% early registration" promotion
    assert facts.offers == [] and any("free-text promotion" in n for n in facts.notes)
    assert "discount" in kinds("10% early registration discount", facts)


def test_curriculum_source_adds_traceable_facts():
    facts = provider(curriculum=FakeCurriculum()).facts_for("TRK-001")
    outcome = next(f for f in facts.facts if f.field == "learning_outcomes")
    assert outcome.value == "Design automated test suites" and "CUR-1 v2" in outcome.source
    assert "learning_outcomes" not in facts.missing_fields


def test_offer_source_failure_means_no_offers_not_a_crash():
    facts = provider(DATASET, offers=FakeOffers([OFFER], fail=True)).facts_for("AI-201")
    assert facts.offers == [] and any("could not be loaded" in n for n in facts.notes)


def test_regressions_found_by_evaluation(parser, official):
    """Found by the baseline run of evaluation/marketing (see docs/marketing_agent.md, 'Evaluation')."""
    brief, _ = parser.parse("بريد تسويقي لبرنامج TRK-005 لجمع العملاء المحتملين")   # 'ال' on both words
    assert brief.objective == CampaignObjective.LEAD_GENERATION
    assert kinds("Get 25% off when you register", official) == {"discount"}            # reported once, not twice
