"""Role 3 — headless UI test of app.py using Streamlit's AppTest (no browser needed)."""
import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from src.config import PROJECT_ROOT  # noqa: E402


@pytest.fixture
def app(monkeypatch, test_env):
    from src.ui_context import get_context
    get_context.clear()                       # a fresh cached context per test
    for key, value in test_env().items():     # synthetic catalog + temporary DB/vector store
        monkeypatch.setenv(key, value)
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


def test_student_chat_never_reaches_staff_agents(app):
    """Without the staff sign-in, a tender question is answered by REX, not by the RFP agent."""
    app.chat_input[0].set_value("Any education tenders in Egypt?").run()
    assert not app.exception
    captions = " ".join(c.value for c in app.caption)
    assert "RFP" not in captions and "→" not in captions


def test_staff_mode_routes_to_the_right_agent(monkeypatch, test_env):
    from src.ui_context import get_context
    get_context.clear()
    for key, value in test_env(ADMIN_PASSWORD="s3cret").items():
        monkeypatch.setenv(key, value)
    at = AppTest.from_file(str(PROJECT_ROOT / "app.py"), default_timeout=30)
    at.run()
    at.sidebar.text_input(key="staff_pw").set_value("wrong").run()
    assert any("Wrong password" in e.value for e in at.sidebar.error)
    at.sidebar.text_input(key="staff_pw").set_value("s3cret").run()
    assert any("Staff mode" in s.value for s in at.sidebar.success)
    at.chat_input[0].set_value("Any education tenders in Egypt?").run()
    assert not at.exception
    assert "→ RFP & Tender Research (keywords)" in " ".join(c.value for c in at.caption)
