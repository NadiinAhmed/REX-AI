"""Load and validate the course catalog from JSON."""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from src.models.course import CourseCatalog
from src.utils.logging_config import get_logger

logger = get_logger("kb.loader")


class KnowledgeBaseError(Exception):
    """Raised when the course catalog cannot be loaded or is invalid."""


def load_catalog(path: Path) -> CourseCatalog:
    """Read `path`, validate every record, and return a CourseCatalog.

    Raises KnowledgeBaseError with a human-readable message on any failure.
    """
    path = Path(path)
    if not path.exists():
        raise KnowledgeBaseError(f"Course file not found: {path}. Check REX_COURSES_PATH.")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise KnowledgeBaseError(
            f"Course file {path.name} is not valid JSON (line {exc.lineno}): {exc.msg}"
        ) from exc
    try:
        catalog = CourseCatalog.model_validate(raw)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()[:5]
        )
        raise KnowledgeBaseError(f"Course file {path.name} failed validation: {problems}") from exc

    logger.info(
        "Loaded %d courses from %s (synthetic=%s)",
        len(catalog.courses), path.name, catalog.dataset.is_synthetic,
    )
    return catalog
