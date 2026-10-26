"""Curriculum workflow: research -> generate -> align -> human review -> approval.

This is the one place that knows the rules of the review workflow. The agent (chat), the Streamlit
page and the other agents all go through it, so they behave the same.

    DRAFT ──submit──▶ IN_REVIEW ──approve──▶ APPROVED
      ▲                  │
      └──edit────  CHANGES_REQUESTED ◀──request_changes──┘          (any edit returns to DRAFT)

Only APPROVED curricula are handed to other agents (`approved_for_course`).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.curriculum.alignment import check_framework
from src.curriculum.frameworks import FrameworkError, FrameworkLibrary
from src.curriculum.generator import (
    CurriculumError, DraftInputs, allocate_hours, build_draft, enrich_with_llm,
)
from src.curriculum.models import (
    AlignmentReport, Curriculum, CurriculumRequest, CurriculumStatus, Origin, Outcome, SourceNote, TracedText,
)
from src.curriculum.topics import TrackTopics
from src.db.curriculum_models import CurriculumRecord
from src.db.curriculum_repository import CurriculumRepository
from src.services.llm_service import LLMService
from src.utils.logging_config import get_logger

logger = get_logger("curriculum.service")
ACTIVE_RFP_STATUSES = ("flagged_for_review", "monitoring", "reviewed")


class CurriculumWorkflowError(CurriculumError):
    """An action is not allowed in the curriculum's current state (message is safe to show)."""


@dataclass
class StoredCurriculum:
    id: str
    status: CurriculumStatus
    revision: int
    curriculum: Curriculum
    alignment: list[AlignmentReport]
    review_log: list[dict]
    approved_by: str | None = None
    approved_at: Any = None

    @classmethod
    def from_record(cls, r: CurriculumRecord) -> "StoredCurriculum":
        return cls(id=r.id, status=CurriculumStatus(r.status), revision=r.revision,
                   curriculum=Curriculum.model_validate(r.content),
                   alignment=[AlignmentReport.model_validate(a) for a in r.alignment],
                   review_log=list(r.review_log), approved_by=r.approved_by, approved_at=r.approved_at)


class CurriculumService:
    def __init__(self, repository: CurriculumRepository, courses, frameworks: FrameworkLibrary,
                 topics: TrackTopics | None = None, passages=None, llm: LLMService | None = None,
                 rfp_repository=None) -> None:
        self._repo = repository
        self._courses = courses                  # CourseRepository (or anything with .get(course_id))
        self.frameworks = frameworks
        self._topics = topics or TrackTopics()
        self._passages = passages                # PassageRetriever or None
        self._llm = llm
        self._rfp = rfp_repository

    # ------------------------------------------------------------------ create
    def create(self, request: CurriculumRequest, actor: str = "system", use_llm: bool = True) -> StoredCurriculum:
        course = None
        if request.course_id:
            course = self._courses.get(request.course_id)
            if course is None:
                raise CurriculumError(f"Course {request.course_id} is not in the approved catalog.")
        framework_ids = list(dict.fromkeys(request.framework_ids))
        if not framework_ids and course:
            framework_ids = [f.framework_id for f in self.frameworks.suggest_for(course.department)]
        try:
            selected = [self.frameworks.get(i) for i in framework_ids]
        except FrameworkError as exc:
            raise CurriculumError(str(exc)) from exc
        request = request.model_copy(update={"framework_ids": framework_ids})

        evidence = self._document_evidence(course) if course else []
        signals, signal_sources = self._market_signals(course.course_id) if course else ([], [])
        draft = build_draft(DraftInputs(request=request, course=course, evidence=[*evidence, *signal_sources],
                                        detail=self._topics.for_course(course.course_id) if course else None,
                                        market_signals=signals),
                            frameworks_for_new_course=selected)
        for fw in selected:
            draft.sources.append(SourceNote(kind="framework", reference=fw.name,
                                            detail="Reference checklist, not the issuer's official text" if fw.status != "official_text" else "Official text",
                                            last_updated=None))
        if not selected:
            draft.notices.append("No competency framework was selected, so no alignment report was produced.")
        if use_llm and self._llm is not None:
            draft = enrich_with_llm(draft, self._llm)
        reports = [check_framework(draft, fw) for fw in selected]
        record = self._repo.create(draft, reports, actor)
        logger.info("Curriculum %s created (course=%s, mode=%s)", record.id, draft.course_id, draft.generation_mode)
        return StoredCurriculum.from_record(record)

    # ------------------------------------------------------------------ read
    def get(self, curriculum_id: str) -> StoredCurriculum | None:
        row = self._repo.get(curriculum_id)
        return StoredCurriculum.from_record(row) if row else None

    def list(self, **filters) -> list[StoredCurriculum]:
        return [StoredCurriculum.from_record(r) for r in self._repo.list(**filters)]

    def approved_for_course(self, course_id: str) -> StoredCurriculum | None:
        """The hook for Document Generation and Marketing: only an APPROVED curriculum is returned."""
        row = self._repo.latest_approved_for_course(course_id)
        return StoredCurriculum.from_record(row) if row else None

    def draft_for_course(self, course_id: str) -> Curriculum:
        """An UNSAVED draft built from the catalog (used by Document Generation when no approved curriculum exists)."""
        course = self._courses.get(course_id)
        if course is None:
            raise CurriculumError(f"Course {course_id} is not in the approved catalog.")
        return build_draft(DraftInputs(request=CurriculumRequest(course_id=course.course_id), course=course,
                                       detail=self._topics.for_course(course.course_id)))

    # ------------------------------------------------------------------ alignment
    def align(self, curriculum_id: str, framework_ids: list[str] | None = None) -> StoredCurriculum:
        stored = self._require(curriculum_id)
        ids = list(dict.fromkeys(framework_ids or stored.curriculum.framework_ids))
        if not ids:
            raise CurriculumError("Choose at least one framework to compare with.")
        try:
            reports = [check_framework(stored.curriculum, self.frameworks.get(i)) for i in ids]
        except FrameworkError as exc:
            raise CurriculumError(str(exc)) from exc
        stored.curriculum.framework_ids = ids
        row = self._repo.save_content(curriculum_id, stored.curriculum, reports, "system", "alignment_rechecked",
                                      "Compared with " + ", ".join(ids))
        return StoredCurriculum.from_record(row)

    # ------------------------------------------------------------------ review workflow
    def submit(self, curriculum_id: str, actor: str, note: str = "") -> StoredCurriculum:
        stored = self._require(curriculum_id)
        if stored.status not in (CurriculumStatus.DRAFT, CurriculumStatus.CHANGES_REQUESTED):
            raise CurriculumWorkflowError(f"{stored.id} is {stored.status.label}; only a draft can be submitted for review.")
        problems = stored.curriculum.validation_problems()
        if problems:
            raise CurriculumWorkflowError("Fix before submitting: " + " ".join(problems))
        return self._set(curriculum_id, CurriculumStatus.IN_REVIEW, actor, "submitted", note)

    def approve(self, curriculum_id: str, reviewer: str, note: str = "") -> StoredCurriculum:
        reviewer = (reviewer or "").strip()
        if not reviewer:
            raise CurriculumWorkflowError("Approval needs the reviewer's name.")
        stored = self._require(curriculum_id)
        if stored.status != CurriculumStatus.IN_REVIEW:
            raise CurriculumWorkflowError(f"{stored.id} is {stored.status.label}; submit it for review before approving.")
        problems = stored.curriculum.validation_problems()
        if problems:
            raise CurriculumWorkflowError("Cannot approve: " + " ".join(problems))
        return self._set(curriculum_id, CurriculumStatus.APPROVED, reviewer, "approved", note)

    def request_changes(self, curriculum_id: str, reviewer: str, note: str) -> StoredCurriculum:
        if not (note or "").strip():
            raise CurriculumWorkflowError("Say what should change.")
        if not (reviewer or "").strip():
            raise CurriculumWorkflowError("A change request needs the reviewer's name.")
        stored = self._require(curriculum_id)
        if stored.status != CurriculumStatus.IN_REVIEW:
            raise CurriculumWorkflowError(f"{stored.id} is {stored.status.label}; changes can be requested while it is in review.")
        return self._set(curriculum_id, CurriculumStatus.CHANGES_REQUESTED, reviewer.strip(), "changes_requested", note.strip())

    # ------------------------------------------------------------------ edits
    def apply_edits(self, curriculum_id: str, actor: str, edits: list[dict], note: str = "") -> StoredCurriculum:
        """Apply human edits in one revision. The curriculum returns to DRAFT and must be re-reviewed."""
        stored = self._require(curriculum_id)
        cur = stored.curriculum.model_copy(deep=True)
        for edit in edits:
            self._apply_one(cur, edit)
        for i, m in enumerate(cur.modules, 1):
            m.number = i
        reports = []
        for fid in cur.framework_ids:
            try:
                reports.append(check_framework(cur, self.frameworks.get(fid)))
            except FrameworkError:
                logger.warning("Framework %s no longer available", fid)
        summary = note or "; ".join(str(e.get("op")) for e in edits)
        row = self._repo.save_content(curriculum_id, cur, reports, actor, "edited", summary)
        return StoredCurriculum.from_record(row)

    @staticmethod
    def _module(cur: Curriculum, number: int):
        for m in cur.modules:
            if m.number == int(number):
                return m
        raise CurriculumError(f"There is no module {number}.")

    def _apply_one(self, cur: Curriculum, e: dict) -> None:
        op = e.get("op")
        if op == "add_topic":
            self._module(cur, e["module"]).topics.append(TracedText(text=e["text"].strip(), origin=Origin.USER, source="Reviewer edit"))
            self._rebalance(cur)
        elif op == "remove_topic":
            m = self._module(cur, e["module"])
            if not 0 <= int(e["index"]) < len(m.topics):
                raise CurriculumError("That topic does not exist.")
            m.topics.pop(int(e["index"]))
            self._rebalance(cur)
        elif op == "rename_module":
            self._module(cur, e["module"]).title = e["title"].strip()
        elif op == "add_module":
            from src.curriculum.models import CurriculumModule
            topics = [TracedText(text=t.strip(), origin=Origin.USER, source="Reviewer edit") for t in e.get("topics", []) if t.strip()]
            cur.modules.append(CurriculumModule(number=len(cur.modules) + 1, title=e["title"].strip(), hours=0, topics=topics,
                                                origin=Origin.USER, source="Reviewer edit",
                                                exercises=[TracedText(text=f"Guided practical lab applying '{e['title'].strip()}'.", origin=Origin.TEMPLATE)],
                                                assessment="Short quiz and a graded practical lab"))
            self._rebalance(cur)
        elif op == "remove_module":
            m = self._module(cur, e["module"])
            if len(cur.modules) == 1:
                raise CurriculumError("A curriculum needs at least one module.")
            cur.modules.remove(m)
            for o in cur.learning_outcomes:
                o.module_numbers = [n for n in o.module_numbers if n != m.number]
            self._rebalance(cur)
        elif op == "set_module_hours":
            self._module(cur, e["module"]).hours = float(e["hours"])
            cur.total_hours, cur.hours_origin = cur.allocated_hours, Origin.USER
        elif op == "set_total_hours":
            hours = float(e["hours"])
            if hours <= 0:
                raise CurriculumError("Total hours must be above zero.")
            cur.total_hours, cur.hours_origin = hours, Origin.USER
            self._rebalance(cur)
        elif op == "add_outcome":
            cur.learning_outcomes.append(Outcome(id="", text=e["text"].strip(), origin=Origin.USER, source="Reviewer edit",
                                                 module_numbers=[int(n) for n in e.get("modules", [])]))
            for i, o in enumerate(cur.learning_outcomes, 1):
                o.id = f"LO{i}"
        elif op == "remove_outcome":
            cur.learning_outcomes = [o for o in cur.learning_outcomes if o.id != e["id"]]
            for i, o in enumerate(cur.learning_outcomes, 1):
                o.id = f"LO{i}"
        elif op == "set_prerequisites":
            cur.prerequisites = [TracedText(text=p.strip(), origin=Origin.USER, source="Reviewer edit") for p in e["items"] if p.strip()]
        elif op == "set_audience":
            cur.target_audience = [a.strip() for a in e["items"] if a.strip()]
        elif op == "set_skill_level":
            cur.skill_level = e["level"].strip().lower() or "unknown"
        elif op == "set_frameworks":
            for fid in e["ids"]:
                self.frameworks.get(fid)          # raises FrameworkError for unknown ids
            cur.framework_ids = list(dict.fromkeys(e["ids"]))
        else:
            raise CurriculumError(f"Unknown edit '{op}'.")

    @staticmethod
    def _rebalance(cur: Curriculum) -> None:
        if cur.total_hours and cur.modules:
            hours, capstone = allocate_hours(cur.total_hours, [len(m.topics) for m in cur.modules])
            for m, h in zip(cur.modules, hours):
                m.hours = h
            if cur.capstone:
                cur.capstone.hours = capstone

    # ------------------------------------------------------------------ helpers
    def _require(self, curriculum_id: str) -> StoredCurriculum:
        stored = self.get(curriculum_id)
        if stored is None:
            raise CurriculumError(f"No curriculum with ID {curriculum_id}.")
        return stored

    def _set(self, curriculum_id: str, status: CurriculumStatus, actor: str, action: str, note: str) -> StoredCurriculum:
        return StoredCurriculum.from_record(self._repo.set_status(curriculum_id, status, actor, action, note))

    def _document_evidence(self, course) -> list[SourceNote]:
        """Approved-document passages that support this course (research step). Never raises."""
        if self._passages is None:
            return []
        query = f"{course.title} {' '.join(course.modules[:4])}"
        try:
            result = self._passages.retrieve(query, top_k=3)
        except Exception as exc:        # research is best effort: the curriculum is still built from the catalog
            logger.warning("Passage research failed: %s", type(exc).__name__)
            return []
        return [SourceNote(kind="document", reference=p.citation, detail=" ".join(p.text.split())[:200])
                for p in result.passages]

    def _market_signals(self, course_id: str) -> tuple[list[str], list[SourceNote]]:
        """Cross-agent link: open tenders found by the RFP agent that match this course."""
        if self._rfp is None:
            return [], []
        try:
            rows = [r for r in self._rfp.list(statuses=ACTIVE_RFP_STATUSES) if course_id in (r.matched_course_ids or [])]
        except Exception as exc:
            logger.warning("RFP signals unavailable: %s", type(exc).__name__)
            return [], []
        if not rows:
            return [], []
        by_country: dict[str, int] = {}
        for r in rows:
            by_country[r.country] = by_country.get(r.country, 0) + 1
        label = {"EG": "Egypt", "SA": "Saudi Arabia"}
        text = (f"{len(rows)} open tender(s) found by the RFP agent match this course ("
                + ", ".join(f"{label.get(c, c)}: {n}" for c, n in sorted(by_country.items())) + ")")
        if any(r.synthetic for r in rows):
            text += " — based on SYNTHETIC demo listings, not real demand"
        return [text], [SourceNote(kind="rfp_signal", reference="RFP opportunities table", detail=text)]
