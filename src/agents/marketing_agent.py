"""Agent 5 — AI Marketing Agent, built as a LangGraph workflow.

    START → parse_brief ──missing info──► ask_followup → END
                 │
                 ▼
            load_facts ──course not found──► report_failure → END
                 │
                 ▼
          recommend_strategy → generate → validate_claims ──blocked & attempts < max──► generate
                                                  │                    └──nothing usable──► report_failure
                                                  ▼
                                              evaluate → save_drafts → respond → END

Facts come ONLY from CourseFactsProvider. The LLM (if configured) writes the copy; the
deterministic ClaimValidator checks it; failing variations are retried with feedback, then
dropped. Every result is saved as pending_review: nothing is published or sent by this agent.
"""
from __future__ import annotations

import re
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from src.agents.base import Agent, AgentRequest, AgentResponse, ResponseMode, SourceReference
from src.db.repositories import MarketingRepository
from src.knowledge_base.text_utils import contains_arabic, normalize_arabic
from src.marketing.brief_parser import BriefParser
from src.marketing.claim_validator import ClaimValidator, has_blocking
from src.marketing.evaluator import MarketingEvaluator
from src.marketing.facts import CourseFactsProvider, CourseNotFoundError
from src.marketing.generator import ContentGenerator, GenerationError, LLMContentGenerator, TemplateContentGenerator
from src.marketing.models import (
    CampaignBrief, CampaignObjective, ClaimIssue, CourseFacts, DraftEvaluation, DraftVariation, Platform, StrategyPlan,
)
from src.marketing.platforms import spec_for
from src.marketing.strategy import StrategyPlanner
from src.services.llm_service import LLMService
from src.utils.logging_config import get_logger

logger = get_logger("marketing_agent")
_RESET = re.compile(r"\b(new campaign|start over|reset)\b|حمله جديده|من البدايه|ابدا من جديد")
_OBJECTIVE_LABELS = {
    CampaignObjective.AWARENESS: ("awareness", "التوعية"),
    CampaignObjective.LEAD_GENERATION: ("lead generation", "جمع العملاء المحتملين"),
    CampaignObjective.ENROLLMENT: ("enrollment", "التسجيل"),
    CampaignObjective.RE_ENGAGEMENT: ("re-engagement", "إعادة التفاعل"),
}


class MarketingState(TypedDict, total=False):
    message: str
    lang: str
    brief: CampaignBrief
    missing: list[str]
    facts: CourseFacts
    strategy: StrategyPlan
    drafts: dict[str, list[DraftVariation]]              # platform -> variations
    issues: dict[str, list[list[ClaimIssue]]]            # platform -> issues per variation
    generator_used: dict[str, str]                       # platform -> generator name
    attempts: int
    notes: list[str]
    kept: list[tuple[DraftVariation, list[ClaimIssue], DraftEvaluation]]
    dropped: int
    campaign_id: str | None
    draft_ids: list[str]
    error: str
    response: AgentResponse


class MarketingAgent(Agent):
    name = "marketing"
    description = "AI Marketing Agent: campaigns from approved course data (human approval required)"

    def __init__(self, facts: CourseFactsProvider, llm: LLMService | None = None,
                 repository: MarketingRepository | None = None, validator: ClaimValidator | None = None,
                 evaluator: MarketingEvaluator | None = None, planner: StrategyPlanner | None = None,
                 generator: ContentGenerator | None = None, max_attempts: int = 2) -> None:
        self._facts = facts
        self._repo = repository
        self._validator = validator or ClaimValidator()
        self._evaluator = evaluator or MarketingEvaluator(self._validator)
        self._planner = planner or StrategyPlanner()
        self._primary: ContentGenerator | None = generator or (LLMContentGenerator(llm) if llm else None)
        self._template = TemplateContentGenerator()
        self._max_attempts = max(1, max_attempts)
        self._parser = BriefParser(self._resolve_course)
        self._graph = self._build_graph()

    # ------------------------------------------------------------------ graph
    def _build_graph(self):
        g = StateGraph(MarketingState)
        for node in ("parse_brief", "ask_followup", "load_facts", "recommend_strategy", "generate",
                     "validate_claims", "evaluate", "save_drafts", "respond", "report_failure"):
            g.add_node(node, getattr(self, f"_node_{node}"))
        g.add_edge(START, "parse_brief")
        g.add_conditional_edges("parse_brief", self._route_after_parse, ["ask_followup", "load_facts"])
        g.add_edge("ask_followup", END)
        g.add_conditional_edges("load_facts", self._route_after_facts, ["recommend_strategy", "report_failure"])
        g.add_edge("recommend_strategy", "generate")
        g.add_edge("generate", "validate_claims")
        g.add_conditional_edges("validate_claims", self._route_after_validation,
                                ["generate", "evaluate", "report_failure"])
        g.add_edge("evaluate", "save_drafts")
        g.add_edge("save_drafts", "respond")
        g.add_edge("respond", END)
        g.add_edge("report_failure", END)
        return g.compile()

    @property
    def facts_provider(self) -> CourseFactsProvider:
        return self._facts

    def graph_diagram(self) -> str:
        return self._graph.get_graph().draw_mermaid()

    # ------------------------------------------------------------------ public API
    def handle(self, request: AgentRequest) -> AgentResponse:
        prior = request.state.get("brief", {})
        if _RESET.search(normalize_arabic(request.message.lower())):
            prior = {}
        result = self._graph.invoke({"message": request.message, "brief": CampaignBrief.model_validate(prior),
                                     "notes": [], "attempts": 0, "drafts": {}, "issues": {}, "generator_used": {}})
        return result["response"]

    def create_campaign(self, brief: CampaignBrief) -> AgentResponse:
        """Structured entry point (admin UI, evaluation): the brief is already complete."""
        return self.handle(AgentRequest(message="generate", state={"brief": brief.model_dump(mode="json")}))

    # ------------------------------------------------------------------ routes (pure functions of state)
    @staticmethod
    def _route_after_parse(s: MarketingState) -> str:
        return "ask_followup" if s["missing"] else "load_facts"

    @staticmethod
    def _route_after_facts(s: MarketingState) -> str:
        return "report_failure" if s.get("error") else "recommend_strategy"

    def _route_after_validation(self, s: MarketingState) -> str:
        retryable = [p for p, per in s["issues"].items()
                     if any(has_blocking(i) for i in per) and s["generator_used"].get(p, "").startswith("llm")]
        if retryable and s["attempts"] < self._max_attempts:
            return "generate"
        usable = sum(not has_blocking(i) for per in s["issues"].values() for i in per)
        return "evaluate" if usable else "report_failure"

    # ------------------------------------------------------------------ nodes
    def _node_parse_brief(self, s: MarketingState) -> dict:
        turn, fields = self._parser.parse(s["message"])
        brief = s["brief"].merged_with(turn, fields)
        lang = brief.language or ("ar" if contains_arabic(s["message"]) else "en")
        return {"brief": brief, "lang": lang, "missing": brief.missing_required()}

    def _node_ask_followup(self, s: MarketingState) -> dict:
        lang, missing = s["lang"], s["missing"]
        t = (lambda en, ar: ar if lang == "ar" else en)
        lines = [t("To prepare the campaign I need a little more information:",
                   "لتجهيز الحملة أحتاج بعض المعلومات:")]
        if "course_id" in missing:
            courses = ", ".join(f"{c.course_id} ({(c.title_ar or c.title) if lang == 'ar' else c.title})"
                                for c in self._facts.list_courses())
            lines.append(t(f"- Which course should we promote? Available: {courses}",
                           f"- ما البرنامج الذي نروّج له؟ المتاح: {courses}"))
        if "platforms" in missing:
            labels = ", ".join(spec_for(p).label(lang) for p in Platform)
            lines.append(t(f"- Which platforms? Options: {labels}", f"- ما المنصات؟ الخيارات: {labels}"))
        if "objective" in missing:
            labels = ", ".join(v[1] if lang == "ar" else v[0] for v in _OBJECTIVE_LABELS.values())
            lines.append(t(f"- What is the campaign objective? Options: {labels}",
                           f"- ما هدف الحملة؟ الخيارات: {labels}"))
        return {"response": AgentResponse(
            agent_name=self.name, text="\n".join(lines), mode=ResponseMode.DETERMINISTIC, language=lang,
            data={"intent": "follow_up", "missing": missing},
            state={"brief": s["brief"].model_dump(mode="json")})}

    def _node_load_facts(self, s: MarketingState) -> dict:
        brief, lang = s["brief"], s["lang"]
        try:
            facts = self._facts.facts_for(brief.course_id)
        except CourseNotFoundError as exc:
            return {"error": str(exc)}
        notes = list(s["notes"]) + facts.notes
        if brief.requested_offer:
            if facts.offers:
                notes.append("Only approved offers are used: " + "; ".join(o.describe(lang) for o in facts.offers))
            else:
                notes.append("The offer in your request is not an approved, active offer, so it was NOT used. "
                             "Add it on the Offers page and have it approved to include it.")
        return {"facts": facts, "notes": notes}

    def _node_recommend_strategy(self, s: MarketingState) -> dict:
        return {"strategy": self._planner.plan(s["brief"], s["facts"], s["lang"])}

    def _node_generate(self, s: MarketingState) -> dict:
        drafts, used = dict(s["drafts"]), dict(s["generator_used"])
        notes = list(s["notes"])
        for platform in s["strategy"].platform_order:
            key = platform.value
            previous = s["issues"].get(key)
            if key in drafts and previous is not None and not any(has_blocking(i) for i in previous):
                continue                                    # this platform already passed
            feedback = [f"Remove or fix: '{i.span}' ({i.explanation})"
                        for per in (previous or []) for i in per if i.severity == "block"]
            drafts[key], used[key] = self._generate_one(s, platform, feedback, notes)
        return {"drafts": drafts, "generator_used": used, "attempts": s["attempts"] + 1, "notes": notes}

    def _generate_one(self, s: MarketingState, platform: Platform, feedback: list[str],
                      notes: list[str]) -> tuple[list[DraftVariation], str]:
        args = (s["brief"], s["facts"], s["strategy"], platform, s["lang"], feedback)
        if self._primary is not None:
            try:
                return self._primary.generate(*args), self._primary.name
            except GenerationError as exc:
                logger.warning("Generation failed for %s: %s", platform.value, exc)
                notes.append(f"{spec_for(platform).label_en}: the AI model failed ({exc}); "
                             "deterministic copy from approved data was used instead.")
        return self._template.generate(*args), self._template.name

    def _node_validate_claims(self, s: MarketingState) -> dict:
        facts = s["facts"]
        return {"issues": {p: [self._validator.validate(v.full_text(), facts) for v in vs]
                           for p, vs in s["drafts"].items()}}

    def _node_evaluate(self, s: MarketingState) -> dict:
        kept, dropped = [], 0
        for platform in s["strategy"].platform_order:
            for v, issues in zip(s["drafts"].get(platform.value, []), s["issues"].get(platform.value, [])):
                if has_blocking(issues):
                    dropped += 1
                    continue
                kept.append((v, issues, self._evaluator.evaluate(v, s["brief"], s["facts"], issues)))
        notes = list(s["notes"])
        if dropped:
            notes.append(f"{dropped} variation(s) were removed because they still made unsupported claims "
                         f"after {s['attempts']} attempt(s).")
        return {"kept": kept, "dropped": dropped, "notes": notes}

    def _node_save_drafts(self, s: MarketingState) -> dict:
        if self._repo is None:
            return {"campaign_id": None, "draft_ids": []}
        try:
            generators = sorted(set(s["generator_used"].values()))
            campaign_id, draft_ids = self._repo.create_campaign(
                course_id=s["facts"].course_id, brief=s["brief"].model_dump(mode="json"),
                strategy=s["strategy"].model_dump(mode="json"), facts_snapshot=s["facts"].model_dump(mode="json"),
                generator=",".join(generators),
                drafts=[{"content": v.model_dump(mode="json"), "claim_issues": [i.model_dump() for i in issues],
                         "evaluation": e.model_dump(mode="json")} for v, issues, e in s["kept"]])
            return {"campaign_id": campaign_id, "draft_ids": draft_ids}
        except Exception as exc:                 # the drafts are still returned to the user
            logger.exception("Saving campaign failed")
            return {"campaign_id": None, "draft_ids": [],
                    "notes": list(s["notes"]) + [f"Drafts could not be saved ({type(exc).__name__})."]}

    def _node_respond(self, s: MarketingState) -> dict:
        lang, facts, strategy = s["lang"], s["facts"], s["strategy"]
        t = (lambda en, ar: ar if lang == "ar" else en)
        title = (facts.title_ar or facts.title) if lang == "ar" else facts.title
        objective = _OBJECTIVE_LABELS[s["brief"].objective][1 if lang == "ar" else 0]
        out = [t(f"## Campaign: {title} — {objective}", f"## حملة: {title} — {objective}"),
               t("### Strategy", "### الاستراتيجية")]
        out += [f"- {line}" for line in strategy.sequence + strategy.notes]
        for i, (v, issues, e) in enumerate(s["kept"], start=1):
            out.append(f"\n### {spec_for(v.platform).label(lang)} — {t('variation', 'نسخة')} {i}")
            if v.email_subject:
                out.append(t(f"**Subject:** {v.email_subject}", f"**العنوان:** {v.email_subject}"))
            out += [f"**{v.headline}**", v.body, f"👉 {v.cta}"]
            if v.hashtags:
                out.append(" ".join(v.hashtags))
            warn = [i.span for i in issues if i.severity == "warn"]
            status = t("checks passed" if e.passed else "needs review", "اجتاز الفحص" if e.passed else "يحتاج مراجعة")
            out.append(f"_{status} · {t('score', 'الدرجة')} {e.mean}" + (f" · ⚠ {', '.join(warn)}" if warn else "") + "_")
        if s["notes"]:
            out.append(t("\n### Notes", "\n### ملاحظات"))
            out += [f"- {n}" for n in s["notes"]]
        out.append(t("\n**Status: pending review.** Nothing has been published or sent. A reviewer must approve "
                     "each draft on the Marketing page.",
                     "\n**الحالة: بانتظار المراجعة.** لم يُنشر أو يُرسل أي شيء. يجب أن يعتمد مراجع كل نسخة من صفحة التسويق."))
        llm_used = any(g.startswith("llm") for g in s["generator_used"].values())
        course = self._facts.get_course(facts.course_id)
        sources = [SourceReference(source_type="course", reference=course.source.reference, course_id=course.course_id,
                                   synthetic=facts.is_synthetic)] if course else []
        brief_state = s["brief"].model_copy(update={"requested_offer": None}).model_dump(mode="json")
        return {"response": AgentResponse(
            agent_name=self.name, text="\n".join(out), mode=ResponseMode.LLM if llm_used else ResponseMode.DETERMINISTIC,
            language=lang, sources=sources, state={"brief": brief_state},
            data={"intent": "campaign", "campaign_id": s.get("campaign_id"), "draft_ids": s.get("draft_ids", []),
                  "variations": [v.model_dump(mode="json") for v, _, _ in s["kept"]],
                  "claim_issues": [[i.model_dump() for i in issues] for _, issues, _ in s["kept"]],
                  "evaluations": [e.model_dump(mode="json") for _, _, e in s["kept"]],
                  "strategy": strategy.model_dump(mode="json"), "notes": s["notes"], "dropped": s.get("dropped", 0),
                  "missing_fields": facts.missing_fields, "generators": s["generator_used"],
                  "attempts": s["attempts"], "status": "pending_review"})}

    def _node_report_failure(self, s: MarketingState) -> dict:
        lang = s.get("lang", "en")
        reason = s.get("error") or ("No usable variation: every draft made claims the approved data does not "
                                    "support. Check the course data or try again.")
        text = (f"لم أتمكن من إنشاء الحملة: {reason}" if lang == "ar" else f"I couldn't create the campaign: {reason}")
        return {"response": AgentResponse(agent_name=self.name, text=text, mode=ResponseMode.ERROR, language=lang,
                                          data={"intent": "error", "error": reason, "notes": s.get("notes", [])},
                                          state={"brief": s["brief"].model_dump(mode="json")})}

    # ------------------------------------------------------------------ helpers
    def _resolve_course(self, text: str) -> str | None:
        course = self._facts.get_course(text) if re.fullmatch(r"[A-Z]{2,5}-\d{3}", text) else None
        course = course or self._facts.find_course(text)
        return course.course_id if course else None
