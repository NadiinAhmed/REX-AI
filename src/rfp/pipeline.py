"""The RFP scan as a LangGraph state graph (same pattern as Agent 1).

    fetch → screen → deduplicate → assess → summarize → store → alert → END

Each node does one job and returns only the keys it changes, so every step can be tested and
explained on its own. `graph_diagram()` prints the Mermaid diagram used in docs/rfp_agent.md.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from src.db.rfp_repository import RfpRepository
from src.models.course import Course
from src.rfp.extraction import extract, normalize_text
from src.rfp.models import Country, Excluded, ExclusionReason, Opportunity, RawTender, ScanReport
from src.rfp.qualification import assess
from src.rfp.screening import DuplicateDetector, initial_status, screen
from src.rfp.sources import SourceError, TenderSource
from src.rfp.summary import summarize
from src.services.llm_service import LLMService
from src.utils.logging_config import get_logger

logger = get_logger("rfp.pipeline")


class ScanState(TypedDict, total=False):
    today: date
    raw: list[tuple[RawTender, bool]]          # (listing, from a synthetic feed?)
    candidates: list[Opportunity]               # passed the screen
    to_store: list[tuple[Opportunity, bool]]    # (opportunity, is_new)
    report: ScanReport


class RfpScanPipeline:
    STEPS = ("fetch", "screen", "deduplicate", "assess", "summarize", "store", "alert")

    def __init__(self, sources: list[TenderSource], repository: RfpRepository,
                 courses: Callable[[], list[Course]], llm: LLMService | None = None,
                 clock: Callable[[], date] = date.today) -> None:
        self._sources = sources
        self._repo = repository
        self._courses = courses
        self._llm = llm
        self._clock = clock
        self._graph = self._build_graph()

    def _build_graph(self):
        g = StateGraph(ScanState)
        for step in self.STEPS:
            g.add_node(step, getattr(self, f"_{step}"))
        g.add_edge(START, self.STEPS[0])
        for a, b in zip(self.STEPS, self.STEPS[1:]):
            g.add_edge(a, b)
        g.add_edge(self.STEPS[-1], END)
        return g.compile()

    def graph_diagram(self) -> str:
        return self._graph.get_graph().draw_mermaid()

    def run(self) -> ScanReport:
        result = self._graph.invoke({"today": self._clock(), "report": ScanReport()})
        report = result["report"]
        logger.info("RFP scan: fetched=%d new=%d updated=%d excluded=%d alerts=%d",
                    report.fetched, len(report.new), len(report.updated), len(report.excluded),
                    len(report.alerts))
        return report

    # ------------------------------------------------------------------ nodes
    def _fetch(self, s: ScanState) -> dict:
        report, raw = s["report"], []
        for source in self._sources:
            try:
                items = source.fetch()
            except SourceError as exc:
                report.source_notices.append(str(exc))
                continue
            info = getattr(source, "info", None)
            synthetic = bool(info and info.synthetic)
            if synthetic and info.notice:
                report.source_notices.append(info.notice)
            if getattr(source, "skipped", 0):
                report.source_notices.append(f"{source.skipped} listing(s) in {source.name} were not readable and were skipped.")
            raw.extend((item, synthetic) for item in items)
        report.fetched = len(raw)
        return {"raw": raw, "report": report}

    def _screen(self, s: ScanState) -> dict:
        report, keep = s["report"], []
        for item, synthetic in s["raw"]:
            opp, training = extract(item, synthetic=synthetic)
            excluded = screen(opp, training, s["today"])
            if excluded:
                report.excluded.append(excluded)
            else:
                keep.append(opp)
        return {"candidates": keep, "report": report}

    def _deduplicate(self, s: ScanState) -> dict:
        report, to_store = s["report"], []
        stored = {key for key, *_ in self._repo.duplicate_index()}
        detector = DuplicateDetector(self._repo.duplicate_index())
        seen: set[str] = set()
        for opp in s["candidates"]:
            match = detector.match(opp)
            if match is None:                                    # brand new tender
                detector.remember(opp)
                seen.add(opp.dedup_key)
                to_store.append((opp, True))
            elif match == opp.dedup_key and match in stored and match not in seen:
                seen.add(match)                                  # same tender as last scan: refresh
                to_store.append((opp, False))
            else:                                                # re-post / second portal / repeat
                report.excluded.append(Excluded(title=opp.title, reason=ExclusionReason.DUPLICATE,
                                                detail=f"same tender as RFP-{match[:6].upper()}"))
        return {"to_store": to_store, "report": report}

    def _assess(self, s: ScanState) -> dict:
        courses = self._courses()
        for opp, _ in s["to_store"]:
            opp.fit, opp.fit_reasons, opp.matched_course_ids = assess(opp, courses)
        return {}

    def _summarize(self, s: ScanState) -> dict:
        modes = set()
        for opp, _ in s["to_store"]:
            opp.summary, opp.summary_mode = summarize(opp, s["today"], self._llm)
            modes.add(opp.summary_mode)
        s["report"].summary_mode = "llm" if modes == {"llm"} else ("mixed" if len(modes) > 1 else "deterministic")
        return {"report": s["report"]}

    def _store(self, s: ScanState) -> dict:
        report = s["report"]
        for opp, _ in s["to_store"]:
            is_new = self._repo.upsert(opp, initial_status(opp), normalize_text(opp.title))
            (report.new if is_new else report.updated).append(opp.opportunity_id)
        report.expired_marked = self._repo.expire_past_deadlines(s["today"])
        return {"report": report}

    def _alert(self, s: ScanState) -> dict:
        """New Egypt opportunities are flagged immediately (business rule); Saudi ones are monitored."""
        report = s["report"]
        egypt_new = [opp.opportunity_id for opp, _ in s["to_store"]
                     if opp.country == Country.EGYPT and opp.opportunity_id in report.new]
        self._repo.mark_alerted(egypt_new)
        report.alerts = egypt_new
        return {"report": report}