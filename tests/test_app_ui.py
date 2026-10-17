"""Role 3 — headless UI test of app.py using Streamlit's AppTest (no browser needed)."""
import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from src.config import PROJECT_ROOT  # noqa: E402


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "none")
    at = AppTest.from_file(str(PROJECT_ROOT / "app.py"), default_timeout=30)
    at.run()
    return at


def test_app_starts_and_shows_synthetic_warning(app):
    assert not app.exception
    assert any("SYNTHETIC" in w.value for w in app.sidebar.warning)


def test_chat_round_trip(app):
    app.chat_input[0].set_value("I'm a beginner and want to become a data analyst").run()
    assert not app.exception
    texts = " ".join(m.value for m in app.markdown)
    assert "Python for Data Analysis" in texts
    captions = " ".join(c.value for c in app.caption)
    assert "Deterministic" in captions and "DS-101" in captions
