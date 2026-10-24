"""Agent 2 — the RFP dashboard page, run headless with Streamlit's AppTest."""
import json

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from src.config import PROJECT_ROOT  # noqa: E402


FEED = {"feed": {"name": "ui-test feed", "synthetic": True, "notice": "SYNTHETIC test feed"}, "tenders": [
    {"title": "AI training programme", "organization": "Org A", "country": "Egypt", "deadline": "2099-01-10"},
    {"title": "Software testing bootcamp", "organization": "Org B", "country": "Egypt", "deadline": "2099-02-10"},
    {"title": "Flutter training for graduates", "organization": "Org C", "country": "Saudi Arabia"},
    {"title": "Teacher training", "organization": "Org D", "country": "United Arab Emirates"},
]}


@pytest.fixture
def page(monkeypatch, test_env, tmp_path):
    # Its own feed with far-future deadlines, so the test does not depend on today's date.
    feed = tmp_path / "feed.json"
    feed.write_text(json.dumps(FEED), encoding="utf-8")
    env = test_env(REX_COURSES_PATH=str(PROJECT_ROOT / "data" / "courses_official.json"), RFP_FEED_PATH=str(feed))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    from src.ui_context import get_context
    get_context.clear()                        # fresh AppContext with the temp database
    at = AppTest.from_file(str(PROJECT_ROOT / "pages" / "3_RFP_Opportunities.py"), default_timeout=60)
    at.run()
    yield at
    get_context.clear()


def test_dashboard_scan_flags_egypt_and_lists_pipeline(page):
    assert not page.exception
    page.button[0].click().run()                                  # "Run scan"
    assert not page.exception
    assert any("3 new" in s.value and "1 excluded" in s.value for s in page.success)
    assert any("2 Egypt opportunity(ies) flagged" in e.value for e in page.error)
    assert any("not real tenders" in i.value for i in page.info)
    table = page.dataframe[-1].value
    assert set(table["country"]) == {"Egypt", "Saudi Arabia"} and len(table) == 3


def test_dashboard_review_buttons_change_status(page):
    page.button[0].click().run()
    before = len(page.dataframe[-1].value)
    dismiss = next(b for b in page.button if "Dismiss" in b.label)
    dismiss.click().run()
    assert not page.exception
    assert len(page.dataframe[-1].value) == before - 1             # dismissed rows leave the default view