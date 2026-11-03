"""Regression: English "quality"/"security" synonyms caused false course matches (found 2026-11-03).

"I want to become a QA engineer" ranked Data Engineering 2nd because its module
"Data Quality, Governance & Security" was indexed as "testing". "I want to learn cybersecurity"
quoted unrelated PDF passages about ".NET Testing & Security".
"""
from src.knowledge_base.text_utils import tokenize
from tests.test_course_resolver import ask, ctx  # noqa: F401  (shared app fixture, official catalog)


def test_quality_and_security_are_not_rewritten():
    assert "testing" not in tokenize("Data Quality, Governance & Security")
    assert "cybersecurity" not in tokenize("Testing & Security")


def test_arabic_and_explicit_terms_still_map():
    assert tokenize("qa") == ["testing"]
    assert "testing" in tokenize("جودة البرمجيات")
    assert "cybersecurity" in tokenize("أمن سيبراني")


def test_qa_engineer_does_not_get_data_engineering_for_testing(ctx):  # noqa: F811
    r = ask(ctx, "rex_assistant", "I want to become a QA engineer")
    recs = r.data["recommendations"]
    assert recs[0]["course_id"] == "TRK-001"
    for rec in recs[1:]:
        terms = [t for reason in rec["reasons"] for t in reason["facts"].get("terms", [])]
        assert "testing" not in terms, rec


def test_cybersecurity_does_not_quote_unrelated_security_passages(ctx):  # noqa: F811
    r = ask(ctx, "rex_assistant", "I want to learn cybersecurity")
    assert not r.sources            # the academy has no cybersecurity track: honest "no match"
