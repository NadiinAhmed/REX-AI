"""Shared fixtures (owned by Role 3). Role-specific fakes live in each role's test files.

Imports of other roles' modules happen inside fixtures, so this file works on `main`
before the knowledge-base branch is merged (only tests that use these fixtures need it).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.config import PROJECT_ROOT

DATASET = PROJECT_ROOT / "data" / "courses.json"


@pytest.fixture(scope="session")
def catalog():
    from src.knowledge_base.loader import load_catalog
    return load_catalog(DATASET)


@pytest.fixture(scope="session")
def retriever(catalog):
    from src.knowledge_base.retriever import KeywordRetriever
    return KeywordRetriever(catalog.courses)


@pytest.fixture
def raw_dataset() -> dict:
    return json.loads(DATASET.read_text(encoding="utf-8"))


@pytest.fixture
def write_json(tmp_path: Path):
    def _write(payload, name: str = "courses.json") -> Path:
        path = tmp_path / name
        text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        path.write_text(text, encoding="utf-8")
        return path
    return _write


# ---------------------------------------------------------------- shared helpers (Sprint 2)
OFFICIAL_DATASET = PROJECT_ROOT / "data" / "courses_official.json"
PORTFOLIO_PDF = PROJECT_ROOT / "data" / "documents" / "andalusia_academy_portfolio_2026_2027.pdf"


@pytest.fixture
def test_env(tmp_path: Path):
    """Environment for an isolated app: temp DB, vector store and uploads; no LLM."""
    def _env(**overrides) -> dict[str, str]:
        env = {
            "LLM_PROVIDER": "none",
            "DATABASE_URL": f"sqlite:///{tmp_path / 'rex.db'}",
            "VECTOR_DIR": str(tmp_path / "vectors"),
            "UPLOAD_DIR": str(tmp_path / "uploads"),
            "REX_COURSES_PATH": str(DATASET),
        }
        env.update(overrides)
        return env
    return _env


@pytest.fixture
def make_pdf(tmp_path: Path):
    """Create a real PDF with one text block per page (reportlab)."""
    def _make(name: str, pages: list[str]) -> Path:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
        path = tmp_path / name
        c = canvas.Canvas(str(path), pagesize=A4)
        for text in pages:
            y = 800
            for line in text.split("\n"):
                c.drawString(50, y, line)
                y -= 16
            c.showPage()
        c.save()
        return path
    return _make
