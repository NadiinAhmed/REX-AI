"""Headless UI tests of app.py using Streamlit's AppTest (no browser needed).

app.py is a router: an entry page asks "student or staff", then shows only that role's pages."""
import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from src.config import PROJECT_ROOT  # noqa: E402


def _start(monkeypatch, test_env, **overrides) -> AppTest:
    from src.ui_context import get_context
    get_context.clear()                       # a fresh cached context per test
    for key, value in test_env(**overrides).items():   # synthetic catalog + temporary DB/vector store
        monkeypatch.setenv(key, value)
    at = AppTest.from_file(str(PROJECT_ROOT / "app.py"), default_timeout=30)
    at.run()
    return at


def _captions(at: AppTest) -> str:
    return " ".join(c.value for c in at.caption)


@pytest.fixture
def entry(monkeypatch, test_env):
    return _start(monkeypatch, test_env)


@pytest.fixture
def student(entry):
    entry.button(key="enter_student").click().run()
    return entry


def test_entry_page_asks_for_a_role_and_shows_synthetic_warning(entry):
    assert not entry.exception
    assert any("SYNTHETIC" in w.value for w in entry.warning)
    assert entry.button(key="enter_student") and entry.button(key="enter_staff")
    assert not entry.chat_input                # no chat before a role is chosen


def test_chat_round_trip_as_student(student):
    assert not student.exception
    student.chat_input[0].set_value("I'm a beginner and want to become a data analyst").run()
    assert not student.exception
    assert "Python for Data Analysis" in " ".join(m.value for m in student.markdown)
    captions = _captions(student)
    assert "Answer from academy records" in captions and "Course record DS-101" in captions
    assert "Deterministic" not in captions     # engineering diagnostics are staff-only


def test_starter_button_sends_a_message(student):
    student.button(key="starter_1").click().run()     # "What programs do you offer?"
    assert not student.exception
    assert len(student.session_state["messages"]) == 2
    assert not [b for b in student.button if b.key and b.key.startswith("starter_")]


def test_student_chat_never_reaches_staff_agents(student):
    """A student's tender question is answered by REX, not by the RFP agent."""
    student.chat_input[0].set_value("Any education tenders in Egypt?").run()
    assert not student.exception
    assert "RFP" not in _captions(student)


def test_change_role_returns_to_entry_and_clears_chat(student):
    student.chat_input[0].set_value("What programs do you offer?").run()
    student.sidebar.button(key="leave_role").click().run()
    assert not student.exception
    assert student.button(key="enter_student")
    assert "messages" not in student.session_state


def test_staff_sign_in_routes_to_the_right_agent(monkeypatch, test_env):
    at = _start(monkeypatch, test_env, ADMIN_PASSWORD="s3cret")
    at.text_input(key="staff_pw").set_value("wrong")
    at.button(key="enter_staff").click().run()
    assert any("Wrong password" in e.value for e in at.error)
    assert not at.chat_input
    at.text_input(key="staff_pw").set_value("s3cret")
    at.button(key="enter_staff").click().run()
    assert not at.exception
    assert "Signed in as academy staff" in " ".join(m.value for m in at.sidebar.markdown)
    at.chat_input[0].set_value("Any education tenders in Egypt?").run()
    assert not at.exception
    captions = _captions(at)
    assert "Agent: RFP & Tender Research" in captions and "Deterministic" in captions


def test_student_cannot_sign_in_as_staff_with_a_wrong_password(monkeypatch, test_env):
    at = _start(monkeypatch, test_env, ADMIN_PASSWORD="s3cret")
    at.text_input(key="staff_pw").set_value("guess")
    at.button(key="enter_staff").click().run()
    assert "role" not in at.session_state and "admin_ok" not in at.session_state
