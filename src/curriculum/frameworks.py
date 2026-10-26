"""Competency / accreditation frameworks the curriculum can be compared with.

Frameworks are plain JSON files in `data/frameworks/` (one per framework), so staff can add the
real standard their accreditor uses without touching code. A framework file says what it IS:
`status = reference_checklist` means "assembled by the team, not the issuer's text".
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from src.config import PROJECT_ROOT
from src.utils.logging_config import get_logger

logger = get_logger("curriculum.frameworks")
FRAMEWORKS_DIR = PROJECT_ROOT / "data" / "frameworks"


class FrameworkError(Exception):
    """A framework file is missing or invalid."""


class FrameworkArea(BaseModel):
    id: str
    title: str
    keywords: list[str] = Field(min_length=1)
    expected_outcome: str = ""


class Framework(BaseModel):
    framework_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    name: str
    issuer: str
    kind: str = "competency_standard"
    status: str = "reference_checklist"          # reference_checklist | official_text
    verified: bool = False
    source_url: str | None = None
    notice: str = ""
    suggested_for_departments: list[str] = Field(default_factory=list)
    areas: list[FrameworkArea] = Field(min_length=1)


class FrameworkLibrary:
    def __init__(self, frameworks: list[Framework]) -> None:
        self._by_id = {f.framework_id: f for f in frameworks}

    @classmethod
    def load(cls, directory: Path = FRAMEWORKS_DIR) -> "FrameworkLibrary":
        """Load every *.json file. A bad file is skipped with a warning: it must not stop the app."""
        frameworks: list[Framework] = []
        for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
            try:
                frameworks.append(Framework.model_validate(json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, ValueError, ValidationError) as exc:
                logger.warning("Skipping framework file %s: %s", path.name, type(exc).__name__)
        return cls(frameworks)

    def all(self) -> list[Framework]:
        return list(self._by_id.values())

    def get(self, framework_id: str) -> Framework:
        try:
            return self._by_id[framework_id.strip().lower()]
        except KeyError:
            known = ", ".join(sorted(self._by_id)) or "none loaded"
            raise FrameworkError(f"Unknown framework '{framework_id}'. Available: {known}.") from None

    def suggest_for(self, department: str | None) -> list[Framework]:
        if not department:
            return []
        return [f for f in self._by_id.values() if department in f.suggested_for_departments]
