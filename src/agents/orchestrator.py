"""REX AI — Agent Orchestrator / Router (project brief, section 4).

    User / Admin interface  ->  OrchestratorAgent  ->  one of the five specialised agents

The orchestrator owns NO business logic. For every message it decides which agent should answer,
forwards the message unchanged through the AgentRegistry, and keeps each agent's conversation
state separate so switching topics never corrupts another agent's flow.

Routing order (first rule that applies wins):
    1. /help or /agents                       -> list the agents the user may use
    2. answer to a clarifying question        -> the agent the user picked ("1", "2", or a name)
    3. explicit command ("/marketing ...", "switch to curriculum", "حولني للتسويق")
    4. the active agent is waiting for this user's reply (Agent.expects_reply), e.g. REX lead capture
    5. strong keyword signal for exactly one agent      (several equally strong -> ask the user)
    6. otherwise stay with the active agent (follow-up)
    7. otherwise REX Assistant (the student-facing default)

Permissions: staff-only agents (RFP, Curriculum, Documents, Marketing) are only reachable when the
caller passes them in `allowed_agents` (the UI does this after the admin password). Students are
always answered by REX; an explicit request for a staff tool gets a polite refusal.

Routing is deterministic on purpose: it is free, instant, explainable in the discussion, and fully
testable. The router is injected, so an LLM classifier can replace or back it later.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from src.agents.base import Agent, AgentError, AgentRequest, AgentResponse, ResponseMode
from src.agents.registry import AgentRegistry
from src.knowledge_base.text_utils import contains_arabic, normalize_arabic
from src.utils.logging_config import get_logger

logger = get_logger("orchestrator")

REX = "rex_assistant"


def _rx(pattern: str) -> re.Pattern[str]:
    """Patterns are normalised like the incoming message, so Arabic spellings (hamza, ta marbuta) match."""
    return re.compile(normalize_arabic(pattern))


def _normalise(text: str) -> str:
    return normalize_arabic(text.lower()).strip()


# ---------------------------------------------------------------------------- agent profiles
@dataclass(frozen=True)
class AgentProfile:
    name: str
    label_en: str
    label_ar: str
    command: str                      # the slash command shown to users
    aliases: tuple[str, ...]          # words accepted in "/x", "switch to x" and clarification answers
    staff_only: bool
    # Sticky: a message with no keywords goes back to this agent when it was the last one used (a
    # conversation). Course Launch only understands launch sentences and LP-commands, so after it,
    # an unrelated question ("what offers do you have?") must go to REX instead.
    sticky: bool = True

    def label(self, lang: str) -> str:
        return self.label_ar if lang == "ar" else self.label_en


AGENT_PROFILES: tuple[AgentProfile, ...] = (
    AgentProfile(REX, "REX Assistant (course advice)", "مساعد REX (اختيار الكورسات)", "/rex",
                 ("rex", "assistant", "courses", "course", "كورسات", "الكورسات", "المساعد"), staff_only=False),
    AgentProfile("rfp_research", "RFP & Tender Research", "البحث عن المناقصات", "/rfp",
                 ("rfp", "rfps", "tender", "tenders", "مناقصات", "المناقصات"), staff_only=True),
    AgentProfile("curriculum", "Curriculum & Accreditation", "المناهج والمواءمة", "/curriculum",
                 ("curriculum", "curricula", "منهج", "مناهج", "المناهج"), staff_only=True),
    AgentProfile("document_generation", "Document Generation", "إعداد المستندات", "/documents",
                 ("documents", "document", "docs", "doc", "مستندات", "المستندات"), staff_only=True),
    AgentProfile("course_launch", "Course Launch (course + curriculum + offer + e-mail)", "إطلاق مقرر (مقرر + منهج + عرض + إيميل)",
                 "/launch", ("launch", "اطلاق", "إطلاق"), staff_only=True, sticky=False),
    AgentProfile("marketing", "Marketing (drafts need approval)", "التسويق (المسودات تحتاج اعتماد)", "/marketing",
                 ("marketing", "تسويق", "التسويق"), staff_only=True),
)
PROFILES: dict[str, AgentProfile] = {p.name: p for p in AGENT_PROFILES}
ALL_AGENTS: frozenset[str] = frozenset(PROFILES)
PUBLIC_AGENTS: frozenset[str] = frozenset(p.name for p in AGENT_PROFILES if not p.staff_only)
_ALIASES: dict[str, str] = {_normalise(a): p.name for p in AGENT_PROFILES for a in p.aliases}


# ---------------------------------------------------------------------------- keyword rules
_VERB = r"(create|design|generate|build|draft|develop|write|prepare|make|produce|انشئ|صمم|اعمل|ولد|جهز|اكتب|اعد)"

# (agent, weight, pattern). 3 = unambiguous (a record ID), 2 = strong, 1 = hint. A document name is 4:
# in "question bank for CUR-1A2B3C" the curriculum ID is only the input of the Document agent.
DEFAULT_RULES: tuple[tuple[str, int, re.Pattern[str]], ...] = tuple((a, w, _rx(p)) for a, w, p in [
    ("rfp_research", 3, r"\brfp-[0-9a-f]{6}\b"),
    ("rfp_research", 3, r"\b(rfps?|tenders?|procurement)\b|مناقص|عطاءات|كراسه الشروط"),
    ("rfp_research", 1, r"\b(egypt|saudi|ksa)\b|مصر|السعودية"),

    ("curriculum", 3, r"\bcur-[0-9a-f]{6}\b"),
    ("curriculum", 2, _VERB + r".*(\b(curriculum|curricula|syllabus)\b|منهج)"),
    ("curriculum", 2, r"\b(align|alignment|gap analysis|frameworks?|swebok|istqb|crisp-dm)\b|مواءمة|تحليل الفجوات"),
    ("curriculum", 2, r"\bcurricula\b|كل المناهج|قائمة المناهج"),
    ("curriculum", 1, r"\baccreditation\b|اعتماد"),

    ("document_generation", 3, r"\bdoc-[0-9a-f]{6}\b"),
    ("document_generation", 4, r"participant guide|student guide|learner guide|instructor guide|trainer guide|"
                               r"facilitator guide|question bank|exam questions|\brubrics?\b|capstone|"
                               r"presentation outline|learning outcomes plan|assessment plan|"
                               r"دليل المشارك|دليل المتدرب|دليل المدرب|بنك الأسئلة|معايير التقييم|مشروع التخرج|خطة التقييم"),
    ("document_generation", 2, _VERB + r".*(\b(course description|proposal|presentation|brochure|document|pdf)\b|"
                                       r"وصف المقرر|مقترح|عرض تقديمي|مستند)"),

    # Course Launch: its plan ID, or "launch" with a course. 4 beats the curriculum/marketing words the
    # same sentence contains ("launch a course with a curriculum and an e-mail campaign").
    ("course_launch", 4, r"\blp-[0-9a-f]{6}\b"),
    ("course_launch", 4, r"\blaunch\b.*\b(course|program|track)\b|(اطلق|اطلاق).*(كورس|دوره|برنامج|مسار|مقرر)"),

    # "marketing" alone is only a hint: the catalog can contain a marketing COURSE ("digital marketing").
    ("marketing", 2, r"\b(campaigns?|social media|captions?|ad copy|adverts?|advertis\w*)\b|حملة|حملات"),
    ("marketing", 2, _VERB + r".*(\b(posts?|ads?)\b|منشور|بوست)"),
    ("marketing", 1, r"\bmarketing\b|تسويق|إعلان"),
    ("marketing", 1, r"\b(linkedin|facebook|instagram|tiktok|twitter)\b|فيسبوك|انستجرام|لينكد"),

    # Student-style questions, so a staff member mid-campaign can still ask REX a quick course question.
    (REX, 2, r"\b(recommend|suggest)\b.*\bcourses?\b|which course|\bfees?\b|how much|\benroll|\bregister\b|"
             r"prerequisites?|رشح|انصح|بكام|رسوم|مواعيد|سجلني|اسجل"),
])

_COMMAND = re.compile(r"^\s*/(\S+)\s*(.*)$", re.S)
# Matched on the ORIGINAL message so the forwarded text keeps its exact spelling and casing.
_SWITCH = re.compile(r"^\s*(?:switch to|talk to|go to|open)\s+(?:the\s+)?([^\s:,]+)(?:\s+agent)?\s*[:,\-]?\s*(.*)$",
                     re.I | re.S)
_SWITCH_AR = re.compile(r"^\s*(?:حولني|حوّلني|انتقل|افتح)\s*(?:إلى|الى|ل)?\s*([^\s:،,]+)\s*[:،,\-]?\s*(.*)$", re.S)
_HELP = {"help", "agents", "مساعدة", "الوكلاء"}
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


Action = Literal["dispatch", "clarify", "switch", "deny", "help"]


@dataclass(frozen=True)
class RoutingDecision:
    """What the orchestrator decided, and why. `reason` is logged and shown in the UI caption."""
    action: Action
    agent: str | None = None
    message: str = ""                          # the text forwarded to the agent
    reason: str = ""
    candidates: tuple[str, ...] = ()
    scores: dict[str, int] = field(default_factory=dict)


class IntentRouter:
    """Deterministic, bilingual intent scoring. Pure functions: no I/O, no state, no LLM."""

    STRONG = 2

    def __init__(self, rules: Iterable[tuple[str, int, re.Pattern[str]]] = DEFAULT_RULES) -> None:
        self._rules = tuple(rules)

    def scores(self, message: str) -> dict[str, int]:
        norm = _normalise(message)
        out: dict[str, int] = {}
        for agent, weight, pattern in self._rules:
            if pattern.search(norm):
                out[agent] = out.get(agent, 0) + weight
        return out

    def strongest(self, message: str, allowed: frozenset[str]) -> tuple[list[str], dict[str, int]]:
        """Agents (among `allowed`) with the highest score, if that score is strong enough."""
        scores = {a: s for a, s in self.scores(message).items() if a in allowed}
        if not scores or max(scores.values()) < self.STRONG:
            return [], scores
        top = max(scores.values())
        return sorted(a for a, s in scores.items() if s == top), scores

    @staticmethod
    def explicit(message: str) -> tuple[str | None, str] | None:
        """("/marketing make a post", "switch to rfp", "حولني للتسويق") -> (agent or None, remaining text).
        Returns None when the message is not a command. Agent None means "help"."""
        m = _COMMAND.match(message)
        if m:
            word = _normalise(m.group(1))
            if word in _HELP:
                return None, ""
            agent = _ALIASES.get(word)
            return (agent, m.group(2).strip()) if agent else (None, "")
        for pattern in (_SWITCH, _SWITCH_AR):
            m = pattern.match(message)
            if m:
                word = _normalise(m.group(1))
                agent = next((_ALIASES[w] for w in (word, word.removeprefix("لل"), word.removeprefix("ل"))
                              if w in _ALIASES), None)
                if agent:
                    return agent, m.group(2).strip()
        return None

    @staticmethod
    def pick_option(message: str, options: list[str]) -> str | None:
        """Answer to a clarifying question: "1", "٢", or an agent name."""
        norm = _normalise(message).translate(_DIGITS).strip(" .)")
        if norm.isdigit() and 1 <= int(norm) <= len(options):
            return options[int(norm) - 1]
        agent = _ALIASES.get(norm.lstrip("/"))
        return agent if agent in options else None


# ---------------------------------------------------------------------------- orchestrator
class OrchState(TypedDict, total=False):
    message: str
    lang: str
    conv: dict[str, Any]            # {"active": str|None, "agents": {name: state}, "pending": {...}|None}
    allowed: frozenset[str]
    decision: RoutingDecision
    response: AgentResponse


class OrchestratorAgent(Agent):
    """Single entry point of the unified chat. Implements the shared Agent contract, so the UI talks
    to it exactly like it talks to any agent; `response.agent_name` says who really answered."""

    name = "orchestrator"
    description = "REX AI Agent Orchestrator / Router"

    def __init__(self, registry: AgentRegistry, router: IntentRouter | None = None) -> None:
        self._registry = registry
        self._router = router or IntentRouter()
        self._graph = self._build_graph()

    # ------------------------------------------------------------------ graph
    def _build_graph(self):
        g = StateGraph(OrchState)
        g.add_node("analyze", self._analyze)
        actions = ("dispatch", "clarify", "switch", "deny", "help")
        for action in actions:
            g.add_node(action, getattr(self, f"_node_{action}"))
            g.add_edge(action, END)
        g.add_edge(START, "analyze")
        g.add_conditional_edges("analyze", lambda s: s["decision"].action, {a: a for a in actions})
        return g.compile()

    def graph_diagram(self) -> str:
        return self._graph.get_graph().draw_mermaid()

    # ------------------------------------------------------------------ public API
    def handle(self, request: AgentRequest, *, allowed_agents: Iterable[str] | None = None) -> AgentResponse:
        """`allowed_agents` defaults to the public (student) agents: least privilege unless the UI
        has verified a staff member."""
        allowed = frozenset(allowed_agents) & ALL_AGENTS if allowed_agents is not None else PUBLIC_AGENTS
        conv = {"active": request.state.get("active"), "agents": dict(request.state.get("agents", {})),
                "pending": request.state.get("pending")}
        result = self._graph.invoke({"message": request.message, "conv": conv, "allowed": allowed,
                                     "lang": "ar" if contains_arabic(request.message) else "en"})
        decision, response = result["decision"], result["response"]
        logger.info("route action=%s agent=%s reason=%s scores=%s", decision.action, decision.agent,
                    decision.reason, decision.scores)       # never the message text: it may hold contact details
        response.data = {**response.data, "routing": {"action": decision.action, "agent": decision.agent,
                                                      "reason": decision.reason, "scores": decision.scores}}
        return response

    def decide(self, message: str, state: dict[str, Any] | None = None,
               allowed_agents: Iterable[str] | None = None) -> RoutingDecision:
        """The routing decision alone, without calling any agent (used by tests and diagnostics)."""
        state = state or {}
        allowed = frozenset(allowed_agents) & ALL_AGENTS if allowed_agents is not None else PUBLIC_AGENTS
        conv = {"active": state.get("active"), "agents": dict(state.get("agents", {})), "pending": state.get("pending")}
        return self._analyze({"message": message, "conv": conv, "allowed": allowed})["decision"]

    # ------------------------------------------------------------------ analyze
    def _analyze(self, s: OrchState) -> dict:
        message, conv, allowed = s["message"], s["conv"], s["allowed"]
        active = conv.get("active") if conv.get("active") in allowed else None

        # 1-2. a clarifying question is open: did the user pick one of the options?
        pending = conv.get("pending")
        if pending:
            choice = self._router.pick_option(message, pending.get("options", []))
            if choice:
                return {"decision": RoutingDecision("dispatch", choice, pending["message"], "clarified")}
            conv["pending"] = None                  # they asked something else: forget the question

        # 1-3. explicit command
        explicit = self._router.explicit(message)
        if explicit is not None:
            agent, rest = explicit
            if agent is None:
                return {"decision": RoutingDecision("help", reason="help")}
            if agent not in allowed:
                return {"decision": RoutingDecision("deny", agent, reason="not_permitted")}
            if not rest:
                return {"decision": RoutingDecision("switch", agent, reason="explicit")}
            return {"decision": RoutingDecision("dispatch", agent, rest, "explicit")}

        # 4. the active agent is in the middle of collecting an answer from this user
        if active and self._expects_reply(active, conv["agents"].get(active, {})):
            return {"decision": RoutingDecision("dispatch", active, message, "awaiting_reply")}

        # 5. keywords
        best, scores = self._router.strongest(message, allowed)
        if active in best and len(best) > 1:
            best = [active]                          # a tie that includes the current agent: stay
        if len(best) == 1:
            reason = "follow_up" if best[0] == active else "keywords"
            return {"decision": RoutingDecision("dispatch", best[0], message, reason, scores=scores)}
        if len(best) > 1:
            return {"decision": RoutingDecision("clarify", None, message, "ambiguous", tuple(best), scores)}

        # 6-7. follow-up or default
        if active and (PROFILES[active].sticky if active in PROFILES else True):
            return {"decision": RoutingDecision("dispatch", active, message, "follow_up", scores=scores)}
        return {"decision": RoutingDecision("dispatch", REX, message, "default", scores=scores)}

    def _expects_reply(self, name: str, agent_state: dict) -> bool:
        try:
            return self._registry.get(name).expects_reply(agent_state)
        except AgentError:
            return False

    # ------------------------------------------------------------------ action nodes
    def _node_dispatch(self, s: OrchState) -> dict:
        d, conv, lang = s["decision"], s["conv"], s["lang"]
        agents = dict(conv["agents"])
        try:
            response = self._registry.dispatch(d.agent, AgentRequest(message=d.message, state=agents.get(d.agent, {})))
        except AgentError:
            logger.warning("Agent '%s' is not available", d.agent)
            return {"response": self._reply(conv, self._t(lang,
                f"The {PROFILES[d.agent].label_en} agent is not available right now. Please try again later.",
                f"وكيل {PROFILES[d.agent].label_ar} غير متاح حالياً. حاول لاحقاً."), lang, ResponseMode.ERROR)}
        except Exception:                            # one agent failing must not break the whole chat
            logger.exception("Agent '%s' failed", d.agent)
            return {"response": self._reply(conv, self._t(lang,
                "Sorry, something went wrong while answering. Please try again.",
                "عذراً، حدث خطأ أثناء الإجابة. حاول مرة أخرى."), lang, ResponseMode.ERROR)}
        agents[d.agent] = response.state
        response.state = {"active": d.agent, "agents": agents, "pending": None}
        return {"response": response}

    def _node_clarify(self, s: OrchState) -> dict:
        d, conv, lang = s["decision"], s["conv"], s["lang"]
        options = list(d.candidates)
        lines = [self._t(lang, "Which of these should handle your request?", "أي من هؤلاء تريد أن يتولى طلبك؟")]
        lines += [f"{i}. {PROFILES[a].label(lang)} (`{PROFILES[a].command}`)" for i, a in enumerate(options, 1)]
        lines.append(self._t(lang, "Reply with the number.", "اكتب الرقم."))
        conv = {**conv, "pending": {"message": d.message, "options": options}}
        return {"response": self._reply(conv, "\n".join(lines), lang)}

    def _node_switch(self, s: OrchState) -> dict:
        d, conv, lang = s["decision"], s["conv"], s["lang"]
        conv = {**conv, "active": d.agent, "pending": None}
        p = PROFILES[d.agent]
        return {"response": self._reply(conv, self._t(lang,
            f"You are now talking to **{p.label_en}**. Type `/help` to see the other agents.",
            f"أنت الآن تتحدث مع **{p.label_ar}**. اكتب `/help` لعرض باقي الوكلاء."), lang)}

    def _node_deny(self, s: OrchState) -> dict:
        conv, lang = s["conv"], s["lang"]
        return {"response": self._reply(conv, self._t(lang,
            "That tool is for academy staff. I'm REX, and I can help you choose a course, "
            "compare options, or answer questions about fees, schedules and content.",
            "هذه الأداة مخصصة لموظفي الأكاديمية. أنا REX ويمكنني مساعدتك في اختيار كورس "
            "أو الإجابة عن الرسوم والمواعيد والمحتوى."), lang)}

    def _node_help(self, s: OrchState) -> dict:
        conv, lang, allowed = s["conv"], s["lang"], s["allowed"]
        lines = [self._t(lang, "**Agents you can use:**", "**الوكلاء المتاحون لك:**")]
        lines += [f"- `{p.command}` — {p.label(lang)}" for p in AGENT_PROFILES
                  if p.name in allowed and self._registry.is_available(p.name)]
        lines.append(self._t(lang, "Just type your request: I route it automatically. "
                             "Start with a command (e.g. `/marketing ...`) to choose the agent yourself.",
                             "اكتب طلبك مباشرة وسأوجهه تلقائياً، أو ابدأ بأمر مثل `/marketing` لاختيار الوكيل بنفسك."))
        return {"response": self._reply(conv, "\n".join(lines), lang)}

    # ------------------------------------------------------------------ helpers
    def _reply(self, conv: dict, text: str, lang: str, mode: ResponseMode = ResponseMode.DETERMINISTIC) -> AgentResponse:
        return AgentResponse(agent_name=self.name, text=text, mode=mode, language=lang,
                             state={"active": conv.get("active"), "agents": conv.get("agents", {}),
                                    "pending": conv.get("pending")})

    @staticmethod
    def _t(lang: str, en: str, ar: str) -> str:
        return ar if lang == "ar" else en
