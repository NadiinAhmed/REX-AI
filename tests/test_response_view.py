"""ResponseView: what students and staff see for the same agent answer."""
from src.agents.base import AgentResponse, ResponseMode, SourceReference
from src.response_view import ResponseView, source_label

DOC = SourceReference(source_type="document", reference="kb", filename="portfolio.pdf", page=9, score=0.4321)
COURSE = SourceReference(source_type="course", reference="data/courses_official.json", course_id="TRK-001")


def _response(mode=ResponseMode.DETERMINISTIC, lang="en", sources=(DOC, COURSE, DOC), routing=None):
    return AgentResponse(agent_name="rex_assistant", text="Answer", mode=mode, language=lang,
                         sources=list(sources), data={"routing": routing or {}})


def test_student_sees_readable_sources_without_diagnostics():
    view = ResponseView.from_response(_response(), "student")
    assert view.sources == ["portfolio.pdf, page 9", "Course record TRK-001"]   # duplicates removed
    assert view.note == "Answer from academy records"
    assert view.diagnostics is None


def test_student_is_told_when_an_llm_wrote_the_answer():
    assert ResponseView.from_response(_response(ResponseMode.LLM), "student").note.startswith("Written by AI")


def test_arabic_answers_get_arabic_labels():
    view = ResponseView.from_response(_response(lang="ar"), "student")
    assert view.note == "إجابة من بيانات الأكاديمية"
    assert view.sources[0] == "portfolio.pdf، صفحة 9"


def test_staff_see_agent_routing_mode_and_scores():
    view = ResponseView.from_response(
        _response(routing={"action": "dispatch", "agent": "rfp_research", "reason": "keywords"}),
        "staff", agent_label="RFP & Tender Research")
    assert view.diagnostics == ("Agent: RFP & Tender Research (routed by keywords)  |  "
                                "Deterministic rule-based answer (no LLM)")
    assert view.sources[0] == "portfolio.pdf, p.9 (score 0.43)"


def test_synthetic_course_is_marked_as_demo_data():
    demo = COURSE.model_copy(update={"synthetic": True})
    assert source_label(demo, "student") == "Course record TRK-001 (demo data)"


def test_round_trip_through_session_state_dict():
    view = ResponseView.from_response(_response(), "student")
    assert ResponseView.from_dict(view.to_dict()) == view


def test_error_view_hides_exception_type_from_students():
    assert ResponseView.error(ValueError("x"), "student").diagnostics is None
    assert ResponseView.error(ValueError("x"), "staff").diagnostics == "Error: ValueError"


def test_sentence_after_a_list_is_not_glued_onto_the_last_bullet():
    from src.response_view import tidy_markdown
    text = "We offer:\n- **A** (TRK-001)\n- **B** (TRK-002)\nTell me your goal."
    assert tidy_markdown(text) == "We offer:\n- **A** (TRK-001)\n- **B** (TRK-002)\n\nTell me your goal."
    assert tidy_markdown("- a\n  continued\n- b") == "- a\n  continued\n- b"     # nested text kept
    assert tidy_markdown("```\n- a\nb\n```") == "```\n- a\nb\n```"              # code untouched
