"""Regression tests for bug B7 (found 2026-10-24): attribute follow-ups lost the course.

After a recommendation, "how many hours?" or "بكام الكورس ده؟" was answered with
"not found in the documents". Question words such as "many", "كم", "كام" or "ده" were kept as
a new topic, which replaced the course under discussion. Runs on the official catalog + PDF,
exactly like the app.
"""
import pytest

from src.agents.base import AgentRequest
from src.bootstrap import build_app
from src.config import load_settings
from tests.conftest import OFFICIAL_DATASET


@pytest.fixture(scope="module")
def ctx(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("b7")
    env = {
        "LLM_PROVIDER": "none", "DATABASE_URL": f"sqlite:///{tmp / 'rex.db'}",
        "VECTOR_DIR": str(tmp / "vectors"), "UPLOAD_DIR": str(tmp / "uploads"),
        "REX_COURSES_PATH": str(OFFICIAL_DATASET),
    }
    return build_app(load_settings(env))


def ask(ctx, *messages):
    state, r = {}, None
    for m in messages:
        r = ctx.registry.dispatch("rex_assistant", AgentRequest.from_text(m, state))
        state = r.state
    return r


# One course was recommended -> the follow-up is about that course.
@pytest.mark.parametrize("first, follow_up, expected_text", [
    ("I want to learn Flutter", "how many hours?", "120"),
    ("I want to learn Flutter", "how much is it?", "not available"),
    ("عايز أتعلم فلاتر", "كام ساعة؟", "120"),
    ("عايز أتعلم فلاتر", "كم مدة الكورس؟", "120"),
    ("عايز أتعلم فلاتر", "بكام الكورس ده؟", "غير متوفرة"),
])
def test_follow_up_attribute_question_keeps_the_course(ctx, first, follow_up, expected_text):
    first_reply = ask(ctx, first)
    assert [s.course_id for s in first_reply.sources] == ["TRK-002"]   # precondition: one course shown
    r = ask(ctx, first, follow_up)
    assert r.data["intent"] == "course_question", r.text
    assert r.sources[0].course_id == "TRK-002"
    assert expected_text in r.text


def test_follow_up_with_several_courses_asks_which_one(ctx):
    # Two courses were recommended, so "the course" is ambiguous: ask, don't say "not found".
    first = ask(ctx, "أنا خريج هندسة وعايز أشتغل في تطوير تطبيقات الموبايل")
    assert len(first.sources) >= 2   # precondition
    r = ask(ctx, "أنا خريج هندسة وعايز أشتغل في تطوير تطبيقات الموبايل", "كم مدة الكورس؟")
    assert r.data["intent"] == "clarify_course", r.text


def test_new_topic_with_attribute_still_switches_topic(ctx):
    # Guard against over-correcting: a real new topic must still win.
    r = ask(ctx, "I want to learn Flutter", "how long is the Software Testing track?")
    assert r.data["intent"] == "course_question" and r.sources[0].course_id == "TRK-001"
    assert "100" in r.text


def test_unrelated_topic_is_still_reported_as_unmatched(ctx):
    # No attribute asked: "cooking" is a genuine (unmatched) topic, not noise.
    r = ask(ctx, "I want to learn Flutter", "I want to learn cooking")
    assert r.sources == [] and "couldn't find" in r.text
