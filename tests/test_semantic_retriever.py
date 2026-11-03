"""Semantic course search (COURSE_RETRIEVER=semantic).

The real model (multilingual-e5-small) needs a download, so these tests use `ConceptEmbeddings`: a
tiny, deterministic stand-in that maps words to topic "concepts". It behaves like a semantic model
in the one way that matters here: different words with the same meaning get similar vectors. The
real model's quality is measured separately (evaluation/), not asserted in unit tests.
"""
from __future__ import annotations

import math

import pytest
from langchain_core.embeddings import Embeddings

from src.agents.base import AgentRequest
from src.agents.rex_assistant import RexAssistant
from src.config import ConfigError, load_settings
from src.knowledge_base.retriever import CourseFilters, KeywordRetriever
from src.knowledge_base.semantic_retriever import SemanticCourseRetriever, course_card
from src.models.course import DeliveryFormat
from src.workflows.learner_profile import extract_profile
from src.workflows.recommendation import recommend_courses

CONCEPTS: dict[str, tuple[str, ...]] = {
    "data": ("data", "analysis", "analyst", "numbers", "patterns", "statistics", "الارقام", "تحليل"),
    "ai": ("machine learning", "artificial intelligence", "ai "),
    "web": ("web", "website", "frontend", "html"),
    "design": ("design", "ui/ux", "look nice", "beautiful"),
    "security": ("security", "cyber", "hackers", "attacks"),
    "marketing": ("marketing", "advertis", "social media"),
    "management": ("project management", "managing projects", "manage"),
    "accounting": ("accounting", "financial", "excel"),
}


class ConceptEmbeddings(Embeddings):
    """Counts concept trigger words, then L2-normalises (like normalize_embeddings=True)."""

    def __init__(self) -> None:
        self.document_calls = 0
        self.queries: list[str] = []

    def _vector(self, text: str, neutral: bool) -> list[float]:
        t = f" {text.lower()} "
        v = [float(sum(t.count(w) for w in words)) for words in CONCEPTS.values()]
        v.append(1.0 if neutral and not any(v) else 0.0)     # off-topic queries point "nowhere"
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.document_calls += 1
        return [self._vector(t, neutral=False) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        self.queries.append(text)
        return self._vector(text, neutral=True)


@pytest.fixture
def embeddings():
    return ConceptEmbeddings()


@pytest.fixture
def semantic(catalog, embeddings):
    return SemanticCourseRetriever(catalog.courses, embeddings, min_score=0.5)


def ids(results):
    return [r.course.course_id for r in results]


# ------------------------------------------------------------------ retriever
def test_course_card_holds_the_facts_that_carry_meaning(catalog):
    card = course_card(catalog.courses[0])
    assert catalog.courses[0].title in card and "Keywords:" in card
    assert "EGP" not in card and "SAR" not in card        # fees are filters, not topics


def test_paraphrase_without_shared_words_is_found_by_meaning(semantic, retriever):
    query = "I enjoy finding hidden patterns in numbers"
    assert retriever.search(query) == []                  # the keyword retriever finds nothing
    results = semantic.search(query)
    assert "DS-101" in ids(results)
    assert all(r.matched_terms == [] for r in results)    # no exact word matched: pure meaning


def test_unrelated_request_returns_nothing(semantic):
    assert semantic.search("I want to learn cooking") == []


def test_exact_title_is_kept_even_below_the_similarity_bar(catalog, embeddings):
    strict = SemanticCourseRetriever(catalog.courses, embeddings, min_score=0.999)
    results = strict.search("Cybersecurity")
    assert ids(results)[0] == "SEC-210" and results[0].matched_terms


def test_filters_are_applied(semantic):
    online_only = semantic.search("data analysis", top_k=8, filters=CourseFilters(delivery_format=DeliveryFormat.IN_PERSON))
    assert "DS-101" not in ids(online_only)              # DS-101 is online-only


def test_courses_are_embedded_once_and_each_search_embeds_one_query(catalog, embeddings):
    r = SemanticCourseRetriever(catalog.courses, embeddings, min_score=0.5)
    r.search("data")
    r.search("web")
    assert embeddings.document_calls == 1 and len(embeddings.queries) == 2


def test_contract_lookups_delegate_to_the_catalog(semantic, catalog):
    assert len(semantic.all_courses()) == len(catalog.courses)
    assert semantic.get_by_id("ds-101").course_id == "DS-101"
    assert semantic.find_by_title("tell me about Web Development Bootcamp").course_id == "WEB-110"


def test_empty_query_and_empty_catalog(semantic, embeddings):
    assert semantic.search("   ") == []
    assert SemanticCourseRetriever([], embeddings, 0.5).search("data") == []


# ------------------------------------------------------------------ recommendation workflow
def test_recommendation_embeds_the_learners_own_sentence(semantic, embeddings):
    profile = extract_profile("I enjoy finding hidden patterns in numbers, budget 5000 EGP")
    result = recommend_courses(profile, semantic)
    assert "DS-101" in [r.course.course_id for r in result.recommendations]
    assert "patterns in numbers" in embeddings.queries[-1]   # full phrase, not the keyword bag
    assert "5000" not in embeddings.queries[-1]              # budget is a filter, not a topic
    top = next(r for r in result.recommendations if r.course.course_id == "DS-101")
    assert top.reasons[0].code == "semantic_match"


def test_keyword_retriever_recommendations_are_unchanged(retriever):
    result = recommend_courses(extract_profile("I want to become a data analyst"), retriever)
    assert result.recommendations[0].course.course_id == "DS-101"
    assert result.recommendations[0].reasons[0].code == "topic_match"


def test_profile_topic_text_follows_the_latest_topic():
    first = extract_profile("I like web design")
    merged = first.merge(extract_profile("online please"))
    assert merged.topic_text == first.topic_text              # a preference-only turn keeps the topic
    merged = merged.merge(extract_profile("actually I prefer cyber security"))
    assert "cyber security" in merged.semantic_query()


@pytest.mark.parametrize("message, expected", [
    ("I enjoy finding hidden patterns in numbers", "Closely related to what you described"),
    ("أحب تحليل الارقام", "قريب جداً مما وصفته"),
])
def test_rex_explains_a_meaning_based_match_in_both_languages(semantic, catalog, message, expected):
    agent = RexAssistant(semantic, llm=None, dataset_notice=catalog.dataset.notice)
    response = agent.handle(AgentRequest.from_text(message, {}))
    assert expected in response.text


# ------------------------------------------------------------------ configuration and wiring
def test_settings_default_to_keyword_and_validate(test_env):
    assert load_settings(test_env()).course_retriever == "keyword"
    with pytest.raises(ConfigError):
        load_settings(test_env(COURSE_RETRIEVER="magic"))
    with pytest.raises(ConfigError):
        load_settings(test_env(COURSE_SEMANTIC_MIN_SCORE="1.5"))


def test_semantic_with_hashing_embeddings_falls_back_to_keyword_with_a_warning(test_env):
    from src.bootstrap import build_app
    ctx = build_app(load_settings(test_env(COURSE_RETRIEVER="semantic")))
    assert isinstance(ctx.registry.get("rex_assistant")._retriever.inner, KeywordRetriever)
    assert any("Semantic course search is not active" in w for w in ctx.warnings)


def test_semantic_is_built_with_sentence_transformer_settings(test_env, embeddings):
    from src.bootstrap import build_app, build_course_retriever
    ctx = build_app(load_settings(test_env()))
    settings = load_settings(test_env(COURSE_RETRIEVER="semantic", EMBEDDING_PROVIDER="sentence_transformers",
                                      COURSE_SEMANTIC_MIN_SCORE="0.6"))
    retriever, status = build_course_retriever(settings, ctx.courses, embeddings)
    assert isinstance(retriever.inner, SemanticCourseRetriever) and retriever.min_score == 0.6
    assert "semantic search" in status
