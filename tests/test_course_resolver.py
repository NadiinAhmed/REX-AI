"""Shared course resolver (REX Assistant + Marketing Agent), found 2026-11-02.

Before: both agents recognised a course only by its code or full title.
  - REX:       "Who is the instructor for Flutter?" -> a recommendation list, not the answer.
  - REX:       "Is the software testing course online?" -> a raw PDF passage, not "format not available".
  - Marketing: "campaign for the data science track" / "حملة لكورس الفلاتر" -> "which course?" again.
"""
import pytest

from src.agents.base import AgentRequest
from src.bootstrap import build_app
from src.config import load_settings
from src.knowledge_base.course_resolver import CourseResolver, tokens
from src.knowledge_base.loader import load_catalog
from src.knowledge_base.retriever import KeywordRetriever
from tests.conftest import OFFICIAL_DATASET


@pytest.fixture(scope="module")
def resolver():
    return CourseResolver(KeywordRetriever(load_catalog(OFFICIAL_DATASET).courses))


@pytest.fixture(scope="module")
def ctx(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("resolver")
    env = {
        "LLM_PROVIDER": "none", "DATABASE_URL": f"sqlite:///{tmp / 'rex.db'}",
        "VECTOR_DIR": str(tmp / "vectors"), "UPLOAD_DIR": str(tmp / "uploads"),
        "REX_COURSES_PATH": str(OFFICIAL_DATASET),
    }
    return build_app(load_settings(env))


def ask(ctx, agent, *messages):
    state, r = {}, None
    for m in messages:
        r = ctx.registry.dispatch(agent, AgentRequest.from_text(m, state))
        state = r.state
    return r


# ---------------------------------------------------------------- resolver unit tests
def test_arabic_prefixes_are_removed_consistently():
    assert tokens("لكورس الفلاتر") == ["كورس", "فلاتر"]
    assert tokens("وتعلم الآلة") == ["تعلم", "اله"]


@pytest.mark.parametrize("text, context, expected, level", [
    ("TRK-002", False, "TRK-002", "exact"),
    ("Flutter Development", False, "TRK-002", "exact"),
    ("tell me about the data science track", False, "TRK-005", "alias"),   # phrase + course noun
    ("Who is the instructor for Flutter?", True, "TRK-002", "alias"),     # distinctive name
    ("اعمل حملة لكورس الفلاتر", False, "TRK-002", "alias"),
    ("مسار تعلم الآلة", False, "TRK-005", "alias"),
    ("the testing track", False, "TRK-001", "noun"),                     # common word next to a noun
    ("the AI track", False, "TRK-005", "noun"),
])
def test_resolves_short_names(resolver, text, context, expected, level):
    match = resolver.resolve(text, course_context=context)
    assert match.course is not None and match.course.course_id == expected
    assert match.level == level


@pytest.mark.parametrize("text, context", [
    ("I want to work in machine learning", False),   # a learner goal: must stay a recommendation
    ("I want to learn flutter", False),
    ("the data track", False),                       # "data" is in two titles
    ("campaign for software engineers", True),       # "software" alone is too common
    ("", True),
])
def test_does_not_guess(resolver, text, context):
    assert resolver.resolve(text, course_context=context).course is None


def test_two_courses_are_reported_as_ambiguous(resolver):
    match = resolver.resolve("flutter or react course?")
    assert match.course is None and match.level == "ambiguous"
    assert match.candidates == ("TRK-002", "TRK-004")


def test_works_with_the_synthetic_catalog(retriever):
    match = CourseResolver(retriever).resolve("how much is the python course?", course_context=True)
    assert match.course is not None and match.course.course_id == "DS-101"


# ---------------------------------------------------------------- REX Assistant
@pytest.mark.parametrize("message, course_id, expected", [
    ("Who is the instructor for Flutter?", "TRK-002", "Instructor: not available"),
    ("Is the software testing course online?", "TRK-001", "Format: not available"),
    ("هل كورس اختبار البرمجيات أونلاين؟", "TRK-001", "غير متوفرة"),
    ("how many hours is the data science track?", "TRK-005", "100"),
])
def test_rex_answers_the_attribute_of_a_course_named_by_short_name(ctx, message, course_id, expected):
    r = ask(ctx, "rex_assistant", message)
    assert r.data.get("route", r.data.get("intent")) != "recommend" or len(r.sources) == 1
    assert [s.course_id for s in r.sources] == [course_id]
    assert expected in r.text


def test_rex_still_recommends_for_a_learner_goal(ctx):
    r = ask(ctx, "rex_assistant", "I want to work in machine learning")
    assert "TRK-005" in [s.course_id for s in r.sources]
    assert "fit best" in r.text                     # recommendation wording, not a single-course answer


def test_rex_stated_format_preference_is_not_a_course_question(ctx):
    r = ask(ctx, "rex_assistant", "I want to learn Flutter", "I prefer online")
    assert "fit best" in r.text                     # a preference: the recommender re-ranks
    assert "preferred format" in r.text


# ---------------------------------------------------------------- Marketing Agent
@pytest.mark.parametrize("message, course_id", [
    ("I need a campaign for the data science track on instagram to increase enrollment", "TRK-005"),
    ("اعمل حملة لكورس الفلاتر على فيسبوك للتسجيل", "TRK-002"),
    ("LinkedIn post for the testing track for awareness", "TRK-001"),
])
def test_marketing_accepts_short_course_names(ctx, message, course_id):
    r = ask(ctx, "marketing", message)
    assert "course_id" not in r.data.get("missing", [])
    assert r.state["brief"]["course_id"] == course_id


def test_marketing_audience_words_do_not_choose_the_course(ctx):
    r = ask(ctx, "marketing", "LinkedIn awareness post for TRK-001, audience: data science students")
    assert r.state["brief"]["course_id"] == "TRK-001"
    r = ask(ctx, "marketing", "LinkedIn awareness post, audience: data science students")
    assert "course_id" in r.data.get("missing", [])   # asks, instead of guessing TRK-005


def test_marketing_asks_when_two_courses_match(ctx):
    r = ask(ctx, "marketing", "facebook enrollment campaign for flutter or react")
    assert "course_id" in r.data.get("missing", [])
