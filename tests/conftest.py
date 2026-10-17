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
