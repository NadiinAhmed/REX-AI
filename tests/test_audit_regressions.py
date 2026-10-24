"""Regression tests for bugs found in the Agent 1 audit (B1-B4).

Each test reproduces the exact message that misbehaved before the fix.
"""
import pytest

from src.agents.base import AgentRequest
from src.agents.rex_assistant import RexAssistant


@pytest.fixture
def assistant(retriever):
    return RexAssistant(retriever=retriever)


def _chat(assistant, *messages):
    state, response = {}, None
    for m in messages:
        response = assistant.handle(AgentRequest.from_text(m, state))
        state = response.state
    return response


def test_b1_filler_words_do_not_produce_a_recommendation(assistant):
    # Before: "into" matched Project Management.
    r = _chat(assistant, "I want to get into tech but I don't know where to start")
    assert [s.course_id for s in r.sources] == []


def test_b2_single_weak_word_is_not_a_match(assistant):
    # Before: "build" (found in module lists) recommended three unrelated courses.
    r = _chat(assistant, "I want to build mobile apps")
    assert [s.course_id for s in r.sources] == []


def test_b3_preference_follow_up_keeps_the_topic(assistant):
    # Before: "make cheaper than" replaced the topic "data", giving "no matching course".
    r = _chat(assistant, "I want to learn data", "actually make it cheaper than 5000 EGP")
    assert r.data["intent"] == "recommendation"
    assert [s.course_id for s in r.sources] == ["DS-101"]   # DS-101 is 4500 EGP in the demo data


def test_b3_new_topic_without_preferences_still_replaces_topic(assistant):
    r = _chat(assistant, "I want to learn data", "I want cooking")
    assert r.sources == []


def test_b4_arabic_plural_schedule_is_a_course_question(assistant):
    # Before: "المواعيد" (plural) was treated as a search topic.
    r = _chat(assistant, "ما هي المواعيد؟")
    assert r.data["intent"] == "clarify_course"
    assert r.language == "ar"
