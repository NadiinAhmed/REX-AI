"""Role 2 — retrieval behaviour."""
from src.knowledge_base.retriever import CourseFilters
from src.knowledge_base.text_utils import tokenize
from src.models.course import DeliveryFormat, SkillLevel


def test_english_query_ranks_relevant_course_first(retriever):
    results = retriever.search("I want to become a data analyst")
    assert results[0].course.course_id == "DS-101"
    assert "data" in results[0].matched_terms


def test_arabic_query_matches_same_course_as_english(retriever):
    assert retriever.search("تسويق رقمي")[0].course.course_id == "MKT-120"
    assert retriever.search("digital marketing")[0].course.course_id == "MKT-120"


def test_unrelated_query_returns_nothing(retriever):
    assert retriever.search("cooking and baking") == []


def test_empty_or_stopword_query_returns_nothing(retriever):
    assert retriever.search("") == []
    assert retriever.search("I want to learn a course") == []


def test_top_k_is_respected(retriever):
    assert len(retriever.search("programming data web", top_k=2)) <= 2


def test_level_filter_excludes_mismatches(retriever):
    results = retriever.search("machine learning python", filters=CourseFilters(skill_level=SkillLevel.BEGINNER))
    assert all(r.course.course_id != "AI-201" for r in results)


def test_budget_filter_keeps_unknown_fees(retriever):
    results = retriever.search("project management", filters=CourseFilters(max_fee=100, currency="EGP"))
    assert [r.course.course_id for r in results] == ["PM-150"]  # fee unknown -> cannot rule out


def test_format_filter_accepts_hybrid(retriever):
    results = retriever.search("machine learning", filters=CourseFilters(delivery_format=DeliveryFormat.ONLINE))
    assert results and results[0].course.course_id == "AI-201"  # hybrid includes online


def test_lookup_by_id_and_title(retriever):
    assert retriever.get_by_id("ds-101").course_id == "DS-101"
    assert retriever.get_by_id("XX-000") is None
    assert retriever.find_by_title("كم سعر أساسيات التسويق الرقمي؟").course_id == "MKT-120"
    assert retriever.find_by_title("nothing here") is None


def test_results_preserve_source_metadata(retriever):
    src = retriever.search("cybersecurity")[0].course.source
    assert src.reference and src.last_updated is not None


def test_arabic_normalisation_and_conjunction_prefix():
    assert tokenize("البرمجة") == ["programming"]
    assert tokenize("وبرمجة") == ["programming"]
    assert "وظيفه" in tokenize("وظيفة")  # real word starting with و is kept
