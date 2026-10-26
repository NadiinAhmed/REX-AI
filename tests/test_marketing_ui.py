"""Headless UI tests of the Marketing and Offers admin pages (Streamlit AppTest)."""
import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from src.config import PROJECT_ROOT  # noqa: E402
from tests.conftest import OFFICIAL_DATASET  # noqa: E402


@pytest.fixture
def page(monkeypatch, test_env):
    from src.ui_context import get_context
    get_context.clear()
    for key, value in test_env(REX_COURSES_PATH=str(OFFICIAL_DATASET)).items():
        monkeypatch.setenv(key, value)

    def _open(name):
        at = AppTest.from_file(str(PROJECT_ROOT / "pages" / name), default_timeout=60)
        at.run()
        assert not at.exception
        return at
    return _open


def test_generate_review_and_approve_from_the_page(page):
    at = page("4_Marketing.py")
    at.button[0].click().run()                                   # "Generate drafts" with the form defaults
    assert not at.exception
    assert any("pending review" in m.value for m in at.markdown)
    at.text_input(key="reviewer").set_value("Manager").run()
    assert "3 draft(s) waiting for review." in [m.value for m in at.markdown]
    approve = next(b for b in at.button if b.label == "Approve")
    approve.click().run()
    assert not at.exception
    assert "2 draft(s) waiting for review." in [m.value for m in at.markdown]
    assert any("approved by Manager" in m.value for m in at.markdown)


def test_approve_without_name_shows_error(page):
    at = page("4_Marketing.py")
    at.button[0].click().run()
    next(b for b in at.button if b.label == "Approve").click().run()
    assert any("reviewer name is required" in e.value for e in at.error)


def test_offer_page_requires_approval(page):
    at = page("5_Offers.py")
    at.text_input[0].set_value("Early-bird discount")
    at.number_input[0].set_value(15)
    at.text_input[3].set_value("Nadin")
    at.button[0].click().run()
    assert any("pending" in s.value for s in at.success)
    assert any("PENDING" in m.value for m in at.markdown)
    at.text_input(key="approver").set_value("Manager").run()
    next(b for b in at.button if b.label == "Approve").click().run()
    assert any("ACTIVE" in m.value for m in at.markdown)
