"""Topic detail from the approved academy portfolio (data/curriculum/track_topics.json).

The portfolio lists the topics of each track but not which module teaches which; that assignment is
a team proposal, flagged in the file's `_notice` and repeated in every generated curriculum.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from src.config import PROJECT_ROOT
from src.utils.logging_config import get_logger

logger = get_logger("curriculum.topics")
TOPICS_PATH = PROJECT_ROOT / "data" / "curriculum" / "track_topics.json"


@dataclass(frozen=True)
class TrackDetail:
    source: str                                   # e.g. "Andalusia Academy Portfolio 2026-2027, p.3"
    modules: dict[str, list[str]]                 # module title -> topics
    capstone_topics: list[str] = field(default_factory=list)
    notice: str = ""


class TrackTopics:
    def __init__(self, tracks: dict[str, TrackDetail] | None = None) -> None:
        self._tracks = tracks or {}

    @classmethod
    def load(cls, path: Path = TOPICS_PATH) -> "TrackTopics":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            name, notice = raw.get("_source", "Approved academy document"), raw.get("_notice", "")
            tracks = {cid: TrackDetail(source=f"{name}, p.{t['page']}", modules=t["modules"],
                                       capstone_topics=t.get("capstone_topics", []), notice=notice)
                      for cid, t in raw["tracks"].items()}
        except (OSError, KeyError, ValueError, TypeError) as exc:   # the agent still works without topic detail
            logger.warning("Topic detail unavailable (%s): curricula will have module titles only.", type(exc).__name__)
            tracks = {}
        return cls(tracks)

    def for_course(self, course_id: str | None) -> TrackDetail | None:
        return self._tracks.get((course_id or "").upper())
