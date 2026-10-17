"""Role 1 — recommendation workflow."""
from src.workflows.learner_profile import extract_profile
from src.workflows.recommendation import recommend_courses


def _codes(rec):
    return {r.code for r in rec.reasons}


def test_recommendation_with_explanations(retriever):
    profile = extract_profile("beginner, want to become a data analyst, online, budget 5000 EGP")
    result = recommend_courses(profile, retriever)
    top = result.recommendations[0]
    assert top.course.course_id == "DS-101"
    assert {"topic_match", "career_match", "level_match", "format_match", "budget_ok"} <= _codes(top)


def test_weak_incidental_matches_are_dropped(retriever):
    result = recommend_courses(extract_profile("I want to become a data analyst"), retriever)
    assert "MKT-120" not in [r.course.course_id for r in result.recommendations]


def test_filters_relaxed_when_nothing_fits_and_mismatch_explained(retriever):
    result = recommend_courses(extract_profile("advanced web development"), retriever)
    assert result.filters_relaxed is True
    assert "level_mismatch" in _codes(result.recommendations[0])


def test_unknown_fee_is_flagged_not_invented(retriever):
    result = recommend_courses(extract_profile("project management, budget 3000 EGP"), retriever)
    pm = next(r for r in result.recommendations if r.course.course_id == "PM-150")
    assert "fee_unknown" in _codes(pm)


def test_no_topic_or_no_match_returns_empty(retriever):
    assert recommend_courses(extract_profile("hello"), retriever).recommendations == []
    assert recommend_courses(extract_profile("I want to learn cooking"), retriever).recommendations == []
