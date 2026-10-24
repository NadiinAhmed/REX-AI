"""Where tenders come from. Every source returns RawTender objects; the pipeline never cares how.

MVP source: `JsonFileSource` reads a JSON feed of listings (a curated export, or the labelled
synthetic sample used for tests and the demo). Live portals (Egypt's e-tenders portal, Saudi
Etimad, ministry and university sites) are added later as new `TenderSource` classes, with no
change to the screening, storage or agent code.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ValidationError

from src.rfp.models import RawTender


class SourceError(Exception):
    """A source could not be read. The scan reports it and continues with the other sources."""


class FeedInfo(BaseModel):
    name: str
    synthetic: bool = False
    notice: str = ""


class TenderSource(Protocol):
    name: str

    def fetch(self) -> list[RawTender]: ...


class JsonFileSource:
    """{"feed": {name, synthetic, notice}, "tenders": [RawTender, ...]}"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.name = path.name
        self.info = FeedInfo(name=path.name)
        self.skipped = 0

    def fetch(self) -> list[RawTender]:
        if not self.path.exists():
            raise SourceError(f"Tender feed not found: {self.path.name}. Check RFP_FEED_PATH.")
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            self.info = FeedInfo.model_validate(payload.get("feed", {"name": self.path.name}))
            items = payload["tenders"]
        except (json.JSONDecodeError, KeyError, TypeError, ValidationError) as exc:
            raise SourceError(f"Tender feed {self.path.name} is not valid ({type(exc).__name__}).") from exc
        tenders: list[RawTender] = []
        self.skipped = 0
        for item in items:
            try:
                tender = RawTender.model_validate(item)
            except ValidationError:
                self.skipped += 1      # one broken listing must not stop the scan (reported)
                continue
            if tender.source == "unknown":
                tender.source = self.info.name
            tenders.append(tender)
        return tenders