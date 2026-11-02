"""The RFP scan as a LangGraph state graph (same pattern as Agent 1).

    fetch → screen → ai_review → deduplicate → assess → ai_analyze → summarize → store → alert → END

Each node does one job and returns only the keys it changes, so every step can be tested and
explained on its own. `graph_diagram()` prints the Mermaid diagram used in docs/rfp_agent.md.

The two `ai_*` nodes use the LLM (see ai.py) and do nothing when no LLM is configured, so the scan
gives the same rule-based result with or without an API key. The rules always decide the country
and the fit level; the LLM can rescue near-misses with verified evidence and add verified details.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from src.db.rfp_repository import RfpRepository
from src.models.course import Course
from src.rfp.ai import AiResult, TenderAI
from src.rfp.extraction import extract, normalize_text
from src.rfp.models import Country, Excluded, ExclusionReason, Fit, Opportunity, RawTender, ScanReport
from src.rfp.qualification import assess
from src.rfp.screening import DuplicateDetector, initial_status, screen
from src.rfp.sources import SourceError, TenderSource
from src.rfp.summary import summarize
from src.services.llm_service import LLMService
from src.utils.logging_config import get_logger

logger = get_logger("rfp.pipeline")


class ScanState(TypedDict, total=False):
    today: date
    raw: list[tuple[RawTender, bool]]                  # (listing, from a synthetic feed?)
    texts: dict[str, str]                              # dedup_key → full notice text (for the AI steps)
    candidates: list[Opportunity]                      # passed the screen
    near_misses: list[tuple[Opportunity, Excluded]]    # in-market, open, rejected only as "not training"
    ai_results: dict[str, AiResult]                    # dedup_key → verified AI reading (reused, not re-asked)
    to_store: list[tuple[Opportunity, bool]]           # (opportunity, is_new)
    report: ScanReport


class RfpScanPipeline:
    STEPS = ("fetch", "screen", "ai_review", "deduplicate", "assess", "ai_analyze", "summarize", "store", "alert")

    def __init__(self, sources: list[TenderSource], repository: RfpRepository,
                 courses: Callable[[], list[Course]], llm: LLMService | None = None,
                 clock: Callable[[], date] = date.today, ai: TenderAI | None = None,
                 ai_max_calls: int = 20) -> None:
        self._sources = sources
        self._repo = repository
        self._courses = courses
        self._llm = llm                  # summaries
        self._ai = ai                    # near-miss review + notice analysis
        self._ai_max_calls = ai_max_calls
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
        report = ScanReport(ai_mode=f"on ({self._ai.name})" if self._ai else "off")
        result = self._graph.invoke({"today": self._clock(), "report": report, "ai_results": {}})
        report = result["report"]
        logger.info("RFP scan: fetched=%d new=%d updated=%d excluded=%d alerts=%d ai_calls=%d",
                    report.fetched, len(report.new), len(report.updated), len(report.excluded),
                    len(report.alerts), report.ai_calls)
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
            if info and info.notice:          # e.g. "SYNTHETIC demo feed" or "World Bank API: 3 read"
                report.source_notices.append(info.notice)
            if getattr(source, "skipped", 0):
                report.source_notices.append(f"{source.skipped} listing(s) in {source.name} were not readable and were skipped.")
            raw.extend((item, synthetic) for item in items)
        report.fetched = len(raw)
        return {"raw": raw, "report": report}

    def _screen(self, s: ScanState) -> dict:
        report, keep, near, texts = s["report"], [], [], {}
        for item, synthetic in s["raw"]:
            opp, training = extract(item, synthetic=synthetic)
            texts[opp.dedup_key] = item.full_text
            excluded = screen(opp, training, s["today"])
            if excluded is None:
                keep.append(opp)
                continue
            report.excluded.append(excluded)
            if excluded.reason == ExclusionReason.NOT_TRAINING:   # in market and open: worth a second look
                near.append((opp, excluded))
        return {"candidates": keep, "near_misses": near, "texts": texts, "report": report}

    def _budget_left(self, report: ScanReport) -> bool:
        if report.ai_calls < self._ai_max_calls:
            return True
        report.ai_not_run += 1
        return False

    def _ai_review(self, s: ScanState) -> dict:
        """LLM second opinion on near-misses. A rescue needs a quote that really is in the notice."""
        report, candidates, results = s["report"], list(s["candidates"]), s["ai_results"]
        if not self._ai:
            return {}
        courses = self._courses()
        for opp, excluded in s["near_misses"]:
            if not self._budget_left(report):
                continue
            report.ai_calls += 1
            result = self._ai.read_notice(opp, s["texts"].get(opp.dedup_key, ""), courses)
            if result is None:
                report.ai_failed += 1
                continue
            if result.is_training and result.evidence:
                report.excluded.remove(excluded)
                candidates.append(opp)
                results[opp.dedup_key] = result
                report.ai_rescued.append(opp.title)
        return {"candidates": candidates, "ai_results": results, "report": report}

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

    def _ai_analyze(self, s: ScanState) -> dict:
        """LLM reads the notice of each NEW tender; only verified parts are used (rules stay in charge)."""
        report, results = s["report"], s["ai_results"]
        if not self._ai:
            return {}
        courses, keep = self._courses(), []
        for opp, is_new in s["to_store"]:
            result = results.get(opp.dedup_key)
            if result is None and is_new and self._budget_left(report):
                report.ai_calls += 1
                result = self._ai.read_notice(opp, s["texts"].get(opp.dedup_key, ""), courses)
                if result is None:
                    report.ai_failed += 1
            if result is not None and not self._apply(opp, result, s["today"], courses, report):
                continue                                       # AI found a deadline that has passed
            keep.append((opp, is_new))
        return {"to_store": keep, "report": report}

    def _apply(self, opp: Opportunity, r: AiResult, today: date, courses: list[Course], report: ScanReport) -> bool:
        """Merge a verified AI reading into a rule-assessed opportunity. False = now excluded as expired."""
        changed = False
        if r.deadline and opp.deadline is None:
            opp.deadline = r.deadline
            changed = True
            if r.deadline < today:
                report.excluded.append(Excluded(title=opp.title, reason=ExclusionReason.EXPIRED,
                                                detail=f"deadline {r.deadline.isoformat()} read by AI from the notice"))
                return False
        if r.requirements and not opp.eligibility:
            opp.eligibility = " ".join(f"{q.rstrip('.')}." for q in r.requirements)
            changed = True
        if changed:                                            # re-run the rules with the new facts
            opp.fit, opp.fit_reasons, opp.matched_course_ids = assess(opp, courses)
        notes: list[str] = []
        if r.evidence and opp.title in report.ai_rescued:
            notes.append(f'AI review: kept although no training keyword matched. Evidence from the notice: "{r.evidence}"')
        new_tracks = [t for t in r.track_ids if t not in opp.matched_course_ids]
        if new_tracks:
            opp.matched_course_ids = opp.matched_course_ids + new_tracks
            notes.append("AI: topic also relates to academy tracks " + ", ".join(new_tracks))
            if opp.fit == Fit.WEAK:
                opp.fit = Fit.POSSIBLE                         # AI can lift WEAK to POSSIBLE, never to STRONG
        if r.fit_explanation:
            notes.append(f"AI: {r.fit_explanation}")
        if r.requirements:
            notes.append(f"AI: {len(r.requirements)} requirement(s) read from the notice and checked against its text")
        if r.rejected:
            notes.append(f"AI: {len(r.rejected)} unverified item(s) ignored")
        opp.fit_reasons = opp.fit_reasons + notes
        report.ai_enriched += 1
        return True

    def _summarize(self, s: ScanState) -> dict:
        """LLM summaries for new tenders only (refreshed ones keep their stored summary)."""
        modes = set()
        for opp, is_new in s["to_store"]:
            opp.summary, opp.summary_mode = summarize(opp, s["today"], self._llm if is_new else None)
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
