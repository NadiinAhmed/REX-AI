"""REX Assistant: bilingual course advisor built as a LangGraph conversation graph.

Flow per turn:
  detect language -> refuse contact details -> merge learner profile ->
  (a) question about a specific course  -> answer from the record
  (b) enough info to recommend          -> retrieve + explain
  (c) otherwise                         -> ask follow-up questions
Academy facts always come from the retriever. The LLM, when configured, only rewrites
grounded facts into natural language; on any LLM failure we answer deterministically.
"""
from __future__ import annotations

import json
import re
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from src.agents import prompts
from src.agents.base import Agent, AgentRequest, AgentResponse, ResponseMode, SourceReference
from src.knowledge_base.retriever import CourseRetriever
from src.knowledge_base.text_utils import contains_arabic, normalize_arabic
from src.models.course import Course
from src.rag.retriever import PassageRetriever
from src.services.lead_service import CONSENT_TEXT, LeadService, parse_contact_details
from src.services.llm_service import ChatMessage, LLMError, LLMService
from src.utils.logging_config import get_logger
from src.workflows.learner_profile import LearnerProfile, contains_contact_details, extract_profile
from src.workflows.recommendation import Reason, Recommendation, recommend_courses

logger = get_logger("rex_assistant")

# Course question topics: field name -> regex over normalized lower text
_ATTRIBUTE_PATTERNS: dict[str, str] = {
    "fee": r"\b(price|prices|cost|fees?|how much)\b|سعر|اسعار|تكلفه|بكام|رسوم|كم سعر|بكم",
    "duration": r"\b(duration|how long|hours|weeks|length)\b|مده|كام ساعه|كم ساعه|كم اسبوع",
    "prerequisites": r"\b(prerequisites?|requirements?|need to know)\b|متطلبات|شروط",
    "instructor": r"\b(instructors?|trainers?|teachers?|lecturers?)\b|مدرب|مدربين|محاضر|مدرس|مدرسين",
    "schedule": r"\b(schedule|when does|when is|start date|starts?\s+on|dates?|timing)\b|موعد|مواعيد|ميعاد|امتي|متي|يبدا",
    "modules": r"\b(content|modules?|syllabus|topics|curriculum)\b|محتوي|محاور|منهج",
    "learning_outcomes": r"\b(outcomes?|will i learn|skills)\b|مخرجات|ماذا سأتعلم|هتعلم ايه",
    "delivery_format": r"\b(format|delivered|delivery)\b|طريقه الدراسه|طريقه التدريس",
    "career_paths": r"\b(career|jobs?|opportunit\w*)\b|وظائف|فرص|شغل",
    "promotions": r"\b(discount|offer|promotion)\b|خصم|عرض",
}
_ORDINALS = {
    0: r"\b(first|1st)\b|الاول|اول واحد", 1: r"\b(second|2nd)\b|الثاني|التاني", 2: r"\b(third|3rd)\b|الثالث|التالت",
}

_LABELS = {
    "en": {
        "fee": "Fee", "duration": "Duration", "prerequisites": "Prerequisites", "instructor": "Instructor",
        "schedule": "Schedule", "modules": "Content", "learning_outcomes": "Learning outcomes",
        "delivery_format": "Format", "career_paths": "Career paths", "promotions": "Promotions",
        "skill_level": "Level", "unavailable": "not available in our current data",
        "none_required": "none listed",
    },
    "ar": {
        "fee": "الرسوم", "duration": "المدة", "prerequisites": "المتطلبات المسبقة", "instructor": "المدرب",
        "schedule": "المواعيد", "modules": "المحتوى", "learning_outcomes": "مخرجات التعلم",
        "delivery_format": "طريقة الدراسة", "career_paths": "المسارات المهنية", "promotions": "العروض",
        "skill_level": "المستوى", "unavailable": "غير متوفرة في بياناتنا الحالية",
        "none_required": "لا يوجد",
    },
}
_VALUE_AR = {
    "online": "أونلاين", "in_person": "حضوري", "hybrid": "مدمج", "unknown": "غير معروف",
    "beginner": "مبتدئ", "intermediate": "متوسط", "advanced": "متقدم", "all_levels": "كل المستويات",
}


# Extra attributes answered from the new course fields (tools, capstone, deliverables).
_ATTRIBUTE_PATTERNS.update({
    "tools": r"\b(tools?|tech stack|stack|technolog\w*|frameworks?|libraries)\b|ادوات|تقنيات|تكنولوجيا",
    "capstone": r"\b(capstone|graduation project|final project)\b|مشروع التخرج|المشروع النهائي",
    "deliverables": r"\b(deliverables?|certificates?|certification)\b|شهاده|شهادات",
})
for _lang, _extra in {"en": {"tools": "Tools", "capstone": "Capstone project", "deliverables": "Deliverables"},
                      "ar": {"tools": "الأدوات", "capstone": "مشروع التخرج", "deliverables": "المخرجات"}}.items():
    _LABELS[_lang].update(_extra)
_LABELS["en"]["none_required"] = "none listed in our current data"
_LABELS["ar"]["none_required"] = "لا توجد متطلبات مذكورة في بياناتنا"

_QUESTION_RE = re.compile(
    r"[?؟]|^\s*(what|how|is|are|does|do|can|who|where|which|why|will|tell me)\b|^\s*(هل|ما|ماذا|كيف|ازاي|مين|فين|ايه|اي)\b")
_CATALOG_RE = re.compile(
    r"\b(what|which|list|show)\b.*\b(courses|programs|programmes|tracks)\b|\ball (courses|programs|tracks)\b"
    r"|(كل|ايه|ما هي|اعرض)\s*(ال)?(كورسات|دورات|برامج|مسارات)|(الكورسات|الدورات|البرامج|المسارات)\s*(اللي|التي)?\s*(عندكم|المتاحه)")
_ENROLL_RE = re.compile(
    r"\b(register|enroll|enrol|sign me up|sign up|apply|contact me|call me|reach me|book a seat)\b"
    r"|سجلني|عايز اسجل|اريد التسجيل|اسجل|اشترك|احجز|تواصلوا معايا|كلموني|تواصل معي|اتواصل")
_YES_RE = re.compile(r"^\s*(yes|y|yeah|yep|sure|ok|okay|i agree|agree|نعم|ايوه|اه|موافق|اوافق|تمام|اكيد)\b")
_NO_RE = re.compile(r"^\s*(no|nope|don'?t|do not|not now|لا|مش موافق|لا اوافق)\b")
_CANCEL_RE = re.compile(r"\b(cancel|stop|never ?mind|forget it)\b|الغاء|الغي|خلاص|مش عايز")


class RexState(TypedDict, total=False):
    """Everything one conversation turn needs. LangGraph passes it from node to node."""
    message: str
    lang: str
    prior: LearnerProfile
    turn: LearnerProfile
    last_ids: list[str]
    lead: dict
    notes: list[str]
    attributes: list[str]
    course: Course | None
    explicit_course: bool
    is_question: bool
    route: str
    profile: LearnerProfile
    response: AgentResponse


class RexAssistant(Agent):
    """Bilingual course advisor. The conversation flow is a LangGraph state graph:

        analyze ──route──► lead_capture | catalog_overview | course_question | clarify_course
                           | document_question | recommend | follow_up ──► finalize ──► END

    `analyze` makes a deterministic, testable routing decision. Each route node produces the
    answer from approved data only; the LLM (if configured) only rewrites grounded facts.
    """
    name = "rex_assistant"
    description = "REX Assistant: bilingual course recommendations and academy Q&A"

    ROUTES = ("lead_capture", "catalog_overview", "course_question", "clarify_course",
              "document_question", "recommend", "follow_up")

    def __init__(
        self, retriever: CourseRetriever, llm: LLMService | None = None,
        dataset_notice: str = "", top_k: int = 3,
        passages: PassageRetriever | None = None, lead_service: LeadService | None = None,
    ) -> None:
        self._retriever = retriever
        self._llm = llm
        self._dataset_notice = dataset_notice
        self._top_k = top_k
        self._passages = passages
        self._leads = lead_service
        self._graph = self._build_graph()

    # ------------------------------------------------------------------ graph
    def _build_graph(self):
        g = StateGraph(RexState)
        g.add_node("analyze", self._analyze)
        for route in self.ROUTES:
            g.add_node(route, getattr(self, f"_node_{route}"))
            g.add_edge(route, "finalize")
        g.add_node("finalize", self._finalize_turn)
        g.add_edge(START, "analyze")
        g.add_conditional_edges("analyze", lambda s: s["route"], {r: r for r in self.ROUTES})
        g.add_edge("finalize", END)
        return g.compile()

    def graph_diagram(self) -> str:
        """Mermaid diagram of the conversation graph (used in the docs)."""
        return self._graph.get_graph().draw_mermaid()

    def handle(self, request: AgentRequest) -> AgentResponse:
        result = self._graph.invoke({
            "message": request.message,
            "prior": LearnerProfile.model_validate(request.state.get("profile", {})),
            "last_ids": list(request.state.get("last_course_ids", [])),
            "lead": dict(request.state.get("lead", {})),
            "notes": [],
        })
        return result["response"]

    # ------------------------------------------------------------------ node: analyze
    def _analyze(self, s: RexState) -> dict:
        message, last_ids, lead = s["message"], s["last_ids"], s["lead"]
        lang = "ar" if contains_arabic(message) else "en"
        norm = normalize_arabic(message.lower())
        notes: list[str] = []
        out: dict = {"lang": lang, "notes": notes, "is_question": bool(_QUESTION_RE.search(norm))}

        # 1. An active lead flow owns the turn (consent answer or contact details).
        if lead.get("stage") in ("awaiting_consent", "awaiting_contact"):
            return out | {"route": "lead_capture"}
        if _ENROLL_RE.search(norm):
            return out | {"route": "lead_capture"}
        if contains_contact_details(message):
            notes.append(self._t(lang,
                "Note: I can't save contact details without your consent, so I've ignored them. "
                "If you'd like the academy to contact you, type \"register\".",
                "ملاحظة: لا يمكنني حفظ بيانات التواصل دون موافقتك، لذلك تم تجاهلها. "
                "إذا أردت أن تتواصل معك الأكاديمية اكتب \"سجلني\"."))

        turn = extract_profile(message)
        attributes = self._asked_attributes(message)
        # Bug B7: in "how many hours?" or "بكام الكورس ده؟" the leftover words ("many", "ده")
        # looked like a new topic and replaced the course being discussed. When the turn asks
        # about a course attribute, leftover words that match no course are noise, not a topic.
        if attributes and turn.has_topic() and not self._retriever.search(turn.search_query(), top_k=1):
            turn = turn.model_copy(update={"interests": [], "career_goal": None})
        course, explicit = self._referenced_course(message, last_ids)
        out |= {"turn": turn, "attributes": attributes, "course": course, "explicit_course": explicit}

        if _CATALOG_RE.search(norm) and not turn.has_topic():
            return out | {"route": "catalog_overview"}
        # A course named in the message is always about that course. A course referenced
        # implicitly ("the second one", "it") only counts when no new topic is introduced.
        if course is not None and (explicit or not turn.has_topic()):
            if explicit and not attributes and out["is_question"] and self._passages is not None:
                return out | {"route": "document_question"}     # e.g. "Is Flutter good for beginners?"
            return out | {"route": "course_question"}
        if attributes and not turn.has_topic():
            return out | {"route": "clarify_course"}
        if self._merge_profile(s["prior"], turn).has_topic():
            return out | {"route": "recommend"}
        if out["is_question"] and self._passages is not None:
            return out | {"route": "document_question"}
        return out | {"route": "follow_up"}

    # ------------------------------------------------------------------ route nodes
    def _node_follow_up(self, s: RexState) -> dict:
        profile = self._merge_profile(s["prior"], s["turn"])
        return {"profile": profile, "response": self._ask_follow_up(profile, s["lang"])}

    def _node_clarify_course(self, s: RexState) -> dict:
        return {"profile": s["prior"], "response": self._ask_which_course(s["last_ids"], s["lang"])}

    def _node_course_question(self, s: RexState) -> dict:
        return {"profile": s["prior"],
                "response": self._answer_course_question(s["course"], s["attributes"], s["lang"], s["message"])}

    def _node_recommend(self, s: RexState) -> dict:
        profile = self._merge_profile(s["prior"], s["turn"])
        response = self._recommend(profile, s["lang"], s["message"])
        # A question that matches no course ("Is it ITIDA accredited?") may still be answered
        # by the approved documents. The profile is then left unchanged.
        if not response.sources and s.get("is_question") and self._passages is not None:
            doc = self._answer_from_documents(s["message"], s["lang"], course=None)
            return {"profile": s["prior"], "route": "document_question", "response": doc}
        return {"profile": profile, "response": response}

    def _node_document_question(self, s: RexState) -> dict:
        return {"profile": s["prior"],
                "response": self._answer_from_documents(s["message"], s["lang"], course=s.get("course"))}

    def _node_catalog_overview(self, s: RexState) -> dict:
        lang = s["lang"]
        courses = self._retriever.all_courses()
        lines = [self._t(lang, f"We currently offer {len(courses)} programs:",
                         f"نقدم حالياً {len(courses)} برامج:")]
        for c in courses:
            paths = ", ".join(c.career_paths[:3])
            lines.append(f"- **{self._title(c, lang)}** ({c.course_id}) · "
                         f"{_LABELS[lang]['duration']}: {self._field_value(c, 'duration', lang)}"
                         + (f" · {paths}" if paths else ""))
        lines.append(self._t(lang, "Tell me your background and goal and I'll suggest the best fit.",
                             "أخبرني بخلفيتك وهدفك وسأقترح عليك الأنسب."))
        lines.append(self._demo_notice(courses[0], lang) if courses else "")
        return {"profile": s["prior"], "response": AgentResponse(
            agent_name=self.name, text="\n".join(l for l in lines if l), mode=ResponseMode.DETERMINISTIC,
            sources=[self._source(c) for c in courses])}

    def _node_lead_capture(self, s: RexState) -> dict:
        lang, lead, message = s["lang"], dict(s["lead"]), s["message"]
        norm = normalize_arabic(message.lower())
        profile = s["prior"]
        reply = lambda text: AgentResponse(agent_name=self.name, text=text, mode=ResponseMode.DETERMINISTIC)

        if self._leads is None:
            return {"profile": profile, "lead": {}, "response": reply(self._t(lang,
                "Registration is not available in this deployment. Please contact the academy directly.",
                "التسجيل غير متاح حالياً. يرجى التواصل مع الأكاديمية مباشرة."))}
        stage = lead.get("stage")
        if stage and _CANCEL_RE.search(norm):
            return {"profile": profile, "lead": {}, "response": reply(self._t(lang,
                "No problem, I've cancelled it. Nothing was saved.", "لا مشكلة، تم الإلغاء ولم يتم حفظ أي بيانات."))}
        if stage == "awaiting_consent":
            if _NO_RE.search(norm):
                return {"profile": profile, "lead": {}, "response": reply(self._t(lang,
                    "Understood. Nothing was saved. I'm still happy to help you choose a course.",
                    "مفهوم، لم يتم حفظ أي شيء. ما زلت سعيداً بمساعدتك في اختيار دورة."))}
            if _YES_RE.search(norm):
                lead = {"stage": "awaiting_contact", "consent": True}
                return {"profile": profile, "lead": lead, "response": reply(self._t(lang,
                    "Thank you. Please send your name and your phone number or email.",
                    "شكراً لك. من فضلك أرسل اسمك ورقم هاتفك أو بريدك الإلكتروني."))}
            return {"profile": profile, "lead": lead, "response": reply(CONSENT_TEXT[lang])}
        if stage == "awaiting_contact":
            details = parse_contact_details(message)
            if missing := details.missing():
                ask = {"name": self._t(lang, "your name", "اسمك"),
                       "phone_or_email": self._t(lang, "a phone number or email", "رقم هاتف أو بريد إلكتروني")}
                return {"profile": profile, "lead": lead, "response": reply(self._t(lang,
                    f"I still need {' and '.join(ask[m] for m in missing)}. (Type \"cancel\" to stop.)",
                    f"ما زلت أحتاج إلى {' و'.join(ask[m] for m in missing)}. (اكتب \"إلغاء\" للتوقف.)"))}
            lead_id = self._leads.save(
                details, language=lang, profile=profile.model_dump(mode="json", exclude_none=True),
                interested_course_ids=s["last_ids"], consent_given=bool(lead.get("consent")))
            response = reply(self._t(lang,
                f"Thank you, {details.name}! Your details are saved and the academy team will contact you.",
                f"شكراً لك يا {details.name}! تم حفظ بياناتك وسيتواصل معك فريق الأكاديمية."))
            response.data["lead_id"] = lead_id
            return {"profile": profile, "lead": {}, "response": response}
        # No flow yet: start it by asking for consent. Nothing is stored at this point.
        return {"profile": profile, "lead": {"stage": "awaiting_consent"}, "response": reply(CONSENT_TEXT[lang])}

    # ------------------------------------------------------------------ node: finalize
    def _finalize_turn(self, s: RexState) -> dict:
        response, lang = s["response"], s["lang"]
        intent = {"recommend": "recommendation"}.get(s["route"], s["route"])
        logger.info("intent=%s lang=%s mode=%s sources=%s", intent, lang, response.mode.value,
                    [x.course_id or x.label() for x in response.sources])
        if s["notes"]:
            response.text = "\n\n".join(s["notes"] + [response.text])
        response.language = lang
        response.data["intent"] = intent
        course_ids = [x.course_id for x in response.sources if x.source_type == "course" and x.course_id]
        response.state = {
            "profile": s.get("profile", s["prior"]).model_dump(mode="json", exclude_none=True),
            "last_course_ids": course_ids or s["last_ids"],
            "lead": s.get("lead", s["lead"]) if "lead" in s else {},
        }
        return {"response": response}

    # ------------------------------------------------------------------ document Q&A (RAG)
    def _answer_from_documents(self, message: str, lang: str, course: Course | None) -> AgentResponse:
        query = f"{course.title} {message}" if course else message
        result = self._passages.retrieve(query)
        data = {"retrieval": result.diagnostics.model_dump(),
                "contexts": [{"chunk_id": p.chunk_id, "page": p.page, "filename": p.filename, "text": p.text}
                             for p in result.passages]}
        if not result.found:
            text = self._t(lang,
                "I couldn't find this in the academy's approved documents, so I can't answer it reliably. "
                "You can ask me about our programs, their content, tools, duration or career paths.",
                "لم أجد هذه المعلومة في مستندات الأكاديمية المعتمدة، لذلك لا يمكنني الإجابة عليها بدقة. "
                "يمكنك سؤالي عن برامجنا ومحتواها وأدواتها ومدتها والمسارات المهنية.")
            data["llm_status"] = "not_needed"
            return AgentResponse(agent_name=self.name, text=text, mode=ResponseMode.DETERMINISTIC, data=data)

        sources = [SourceReference(source_type="document", reference=p.citation, document_id=p.document_id,
                                   filename=p.filename, page=p.page, score=p.score) for p in result.passages]
        excerpts = [f"> {self._excerpt(p.text)}\n> — _{p.citation}_" for p in result.passages[:2]]
        fallback = "\n\n".join([self._t(lang, "Here is what the academy's documents say:",
                                        "هذا ما تذكره مستندات الأكاديمية:")] + excerpts)
        if self._llm is None:
            data["llm_status"] = "disabled"
            return AgentResponse(agent_name=self.name, text=fallback, mode=ResponseMode.DETERMINISTIC,
                                 sources=sources, data=data)
        docs = "\n\n".join(f"[{i}] ({p.citation})\n{p.text}" for i, p in enumerate(result.passages, 1))
        system = prompts.DOC_QA_SYSTEM_PROMPT.format(language_name="Arabic" if lang == "ar" else "English")
        user = f"{prompts.DOC_QA_TASK}\n\n<documents>\n{docs}\n</documents>\n\nLearner question: {message}"
        try:
            text = self._llm.generate(system, [ChatMessage(role="user", content=user)])
            data["llm_status"] = "used"
            return AgentResponse(agent_name=self.name, text=text, mode=ResponseMode.LLM, sources=sources, data=data)
        except LLMError as exc:
            logger.warning("Falling back to excerpts: %s", exc)
            data["llm_status"] = "failed"
            prefix = self._t(lang, "(The AI service is unavailable, so here are the relevant excerpts.)",
                             "(خدمة الذكاء الاصطناعي غير متاحة، لذا هذه المقتطفات ذات الصلة.)")
            return AgentResponse(agent_name=self.name, text=f"{prefix}\n\n{fallback}",
                                 mode=ResponseMode.DETERMINISTIC, sources=sources, data=data)

    def answer_from_documents(self, question: str) -> AgentResponse:
        """Public RAG entry point (used by the evaluation scripts): retrieve + generate, no routing."""
        if self._passages is None:
            raise RuntimeError("No passage retriever configured.")
        return self._answer_from_documents(question, "ar" if contains_arabic(question) else "en", course=None)

    @staticmethod
    def _excerpt(text: str, limit: int = 350) -> str:
        flat = " ".join(text.split())
        return flat if len(flat) <= limit else flat[:limit].rsplit(" ", 1)[0] + " …"

    # ------------------------------------------------------------------ intents
    def _ask_follow_up(self, profile: LearnerProfile, lang: str) -> AgentResponse:
        en = ["Hi! I'm REX, I can help you find a suitable course. To recommend well, could you tell me:"]
        ar = ["أهلاً! أنا REX وأستطيع مساعدتك في اختيار الدورة المناسبة. لأرشح لك بدقة، أخبرني من فضلك:"]
        en.append("- What field or skill interests you, or what job do you want to reach?")
        ar.append("- ما المجال أو المهارة التي تهتم بها، أو ما الوظيفة التي تطمح إليها؟")
        if not profile.background:
            en.append("- Your background (what you studied or work in)?")
            ar.append("- خلفيتك (ماذا درست أو في أي مجال تعمل)؟")
        if not profile.skill_level:
            en.append("- Your level: beginner, intermediate or advanced?")
            ar.append("- مستواك: مبتدئ أم متوسط أم متقدم؟")
        if not profile.preferred_format:
            en.append("- Do you prefer online, in-person or hybrid study?")
            ar.append("- هل تفضل الدراسة أونلاين أم حضوري أم مدمج؟")
        return AgentResponse(agent_name=self.name, text="\n".join(ar if lang == "ar" else en),
                             mode=ResponseMode.DETERMINISTIC)

    def _ask_which_course(self, last_ids: list[str], lang: str) -> AgentResponse:
        courses = [c for c in (self._retriever.get_by_id(i) for i in last_ids) if c]
        if courses:
            names = "\n".join(f"- {self._title(c, lang)} ({c.course_id})" for c in courses)
            text = self._t(lang, f"Which course do you mean? Recently mentioned:\n{names}",
                           f"أي دورة تقصد؟ الدورات التي ذكرناها:\n{names}")
        else:
            text = self._t(lang, "Which course are you asking about? You can use its name or code (e.g. DS-101).",
                           "عن أي دورة تسأل؟ يمكنك كتابة اسمها أو رمزها (مثل DS-101).")
        return AgentResponse(agent_name=self.name, text=text, mode=ResponseMode.DETERMINISTIC)

    def _recommend(self, profile: LearnerProfile, lang: str, message: str) -> AgentResponse:
        result = recommend_courses(profile, self._retriever, self._top_k)
        recs = result.recommendations
        if not recs:
            text = self._t(lang,
                "I couldn't find a course in our current catalog that matches that. "
                "I won't suggest something unrelated. Could you describe another field or goal you're interested in?",
                "لم أجد دورة في الكتالوج الحالي تطابق طلبك، ولن أقترح شيئاً غير مرتبط. "
                "هل يمكنك ذكر مجال أو هدف آخر يهمك؟")
            return AgentResponse(agent_name=self.name, text=text, mode=ResponseMode.DETERMINISTIC,
                                 data={"recommendations": []})

        sources = [self._source(r.course) for r in recs]
        data = {
            "recommendations": [
                {"course_id": r.course.course_id, "score": r.score,
                 "reasons": [x.model_dump() for x in r.reasons]} for r in recs
            ],
            "filters_relaxed": result.filters_relaxed,
        }
        fallback_text = self._render_recommendations(recs, result.filters_relaxed, profile, lang)
        context = {
            "learner_profile": profile.model_dump(mode="json", exclude_none=True),
            "filters_relaxed": result.filters_relaxed,
            "matches": [{"course": self._course_payload(r.course),
                         "match_reasons": [x.model_dump() for x in r.reasons]} for r in recs],
        }
        return self._finalize(prompts.RECOMMEND_TASK, context, message, lang, fallback_text, sources, data)

    def _answer_course_question(self, course: Course, attributes: list[str], lang: str, message: str) -> AgentResponse:
        fields = attributes or ["description", "skill_level", "duration", "delivery_format", "fee", "prerequisites"]
        lines = [f"**{self._title(course, lang)}** ({course.course_id})"]
        for f in fields:
            if f == "description":
                if course.description:
                    lines.append(course.description)
                continue
            lines.append(f"- {_LABELS[lang][f]}: {self._field_value(course, f, lang)}")
        lines.append(self._demo_notice(course, lang))
        context = {"course": self._course_payload(course), "asked_about": fields}
        return self._finalize(prompts.COURSE_QUESTION_TASK, context, message, lang,
                              "\n".join(l for l in lines if l), [self._source(course)],
                              {"course_id": course.course_id, "asked_about": fields})

    # ------------------------------------------------------------------ LLM / fallback
    def _finalize(self, task: str, context: dict, message: str, lang: str,
                  fallback_text: str, sources: list[SourceReference], data: dict) -> AgentResponse:
        if self._llm is None:
            data["llm_status"] = "disabled"
            return AgentResponse(agent_name=self.name, text=fallback_text, mode=ResponseMode.DETERMINISTIC,
                                 sources=sources, data=data)
        system = prompts.SYSTEM_PROMPT.format(language_name="Arabic" if lang == "ar" else "English")
        user = (f"{task}\n\n<dataset_notice>{self._dataset_notice}</dataset_notice>\n"
                f"<course_data>\n{json.dumps(context, ensure_ascii=False, indent=1)}\n</course_data>\n\n"
                f"Learner message: {message}")
        try:
            text = self._llm.generate(system, [ChatMessage(role="user", content=user)])
            data["llm_status"] = "used"
            return AgentResponse(agent_name=self.name, text=text, mode=ResponseMode.LLM, sources=sources, data=data)
        except LLMError as exc:
            logger.warning("Falling back to deterministic answer: %s", exc)
            data["llm_status"] = "failed"
            prefix = self._t(lang, "(The AI service is unavailable, so this is a rule-based answer.)",
                             "(خدمة الذكاء الاصطناعي غير متاحة حالياً، لذا هذه إجابة آلية مبنية على القواعد.)")
            return AgentResponse(agent_name=self.name, text=f"{prefix}\n\n{fallback_text}",
                                 mode=ResponseMode.DETERMINISTIC, sources=sources, data=data)

    # ------------------------------------------------------------------ rendering
    def _render_recommendations(self, recs: list[Recommendation], relaxed: bool,
                                profile: LearnerProfile, lang: str) -> str:
        out = [self._t(lang, "Based on what you told me, these courses from our catalog fit best:",
                       "بناءً على ما ذكرته، هذه أنسب الدورات من الكتالوج:")]
        if relaxed:
            out.append(self._t(lang,
                "No course matched all your preferences, so these match your topic but differ in some details (see notes).",
                "لا توجد دورة تطابق كل تفضيلاتك، لذلك هذه دورات تطابق المجال لكنها تختلف في بعض التفاصيل (انظر الملاحظات)."))
        for i, r in enumerate(recs, 1):
            c = r.course
            out.append(f"\n**{i}. {self._title(c, lang)}** ({c.course_id})")
            out.append(f"- {_LABELS[lang]['skill_level']}: {self._field_value(c, 'skill_level', lang)} · "
                       f"{_LABELS[lang]['delivery_format']}: {self._field_value(c, 'delivery_format', lang)} · "
                       f"{_LABELS[lang]['duration']}: {self._field_value(c, 'duration', lang)} · "
                       f"{_LABELS[lang]['fee']}: {self._field_value(c, 'fee', lang)}")
            for reason in r.reasons:
                out.append(f"- {'✓' if reason.positive else '⚠'} {self._reason_text(reason, lang)}")
        if not profile.skill_level or not profile.preferred_format:
            out.append("\n" + self._t(lang,
                "To refine this, tell me your level and whether you prefer online or in-person study.",
                "لتحسين الترشيح، أخبرني بمستواك وهل تفضل الدراسة أونلاين أم حضوري."))
        out.append(self._t(lang, "Ask me about any course's fees, content or prerequisites.",
                           "يمكنك سؤالي عن رسوم أي دورة أو محتواها أو متطلباتها."))
        out.append(self._demo_notice(recs[0].course, lang))
        return "\n".join(l for l in out if l)

    def _reason_text(self, r: Reason, lang: str) -> str:
        f = r.facts
        def j(v): return ", ".join(v) if isinstance(v, list) else str(v)
        en = {
            "topic_match": f"Covers your interest: {j(f.get('terms', []))}",
            "career_match": f"Leads toward: {j(f.get('paths', []))}",
            "level_match": "Matches your level",
            "level_mismatch": f"Different level from yours (course is {f.get('course_level')})",
            "level_unknown": "Course level not specified",
            "format_match": "Available in your preferred format",
            "format_mismatch": f"Not in your preferred format (course is {f.get('course_format')})",
            "budget_ok": f"Within your budget ({f.get('fee')})",
            "over_budget": f"Above your budget ({f.get('fee')})",
            "fee_unknown": "Fee is not available in our data",
            "fee_other_currency": f"Priced in a different currency ({f.get('fee')})",
            "prerequisites": f"Check prerequisites: {j(f.get('items', []))}",
        }
        ar = {
            "topic_match": f"يغطي اهتمامك: {j(f.get('terms', []))}",
            "career_match": f"يؤهلك نحو: {j(f.get('paths', []))}",
            "level_match": "مناسب لمستواك",
            "level_mismatch": f"مستوى مختلف عن مستواك (مستوى الدورة: {_VALUE_AR.get(str(f.get('course_level')), f.get('course_level'))})",
            "level_unknown": "مستوى الدورة غير محدد",
            "format_match": "متاح بطريقة الدراسة التي تفضلها",
            "format_mismatch": f"ليس بطريقة الدراسة المفضلة (الدورة: {_VALUE_AR.get(str(f.get('course_format')), f.get('course_format'))})",
            "budget_ok": f"ضمن ميزانيتك ({f.get('fee')})",
            "over_budget": f"أعلى من ميزانيتك ({f.get('fee')})",
            "fee_unknown": "الرسوم غير متوفرة في بياناتنا",
            "fee_other_currency": f"الرسوم بعملة مختلفة ({f.get('fee')})",
            "prerequisites": f"راجع المتطلبات المسبقة: {j(f.get('items', []))}",
        }
        return (ar if lang == "ar" else en).get(r.code, r.code)

    def _field_value(self, c: Course, field: str, lang: str) -> str:
        unavailable = _LABELS[lang]["unavailable"]
        if field == "fee":
            return f"{c.fee.amount:g} {c.fee.currency}" if c.fee.is_known else unavailable
        if field == "duration":
            parts = [p for p in (f"{c.duration_hours:g} h" if c.duration_hours else None, c.duration_text) if p]
            return " / ".join(parts) or unavailable
        if field in ("skill_level", "delivery_format"):
            value = getattr(c, field).value
            if value == "unknown":
                return unavailable
            return _VALUE_AR.get(value, value) if lang == "ar" else value.replace("_", " ")
        value = getattr(c, field)
        if field == "prerequisites" and value == []:
            return _LABELS[lang]["none_required"]
        if isinstance(value, list):
            return ", ".join(value) if value else unavailable
        return value or unavailable

    # ------------------------------------------------------------------ helpers
    def _merge_profile(self, prior: LearnerProfile, turn: LearnerProfile) -> LearnerProfile:
        """Merge a turn into the stored profile without losing the topic (bug B3).

        "Actually make it cheaper than 3000 EGP" states a preference, not a new subject. If a
        turn carries a preference AND leftover words that match no course, those words are noise,
        so the previous topic is kept. A turn with no preference ("I want cooking") still
        replaces the topic, so the assistant can honestly say there is no such course.
        """
        if (turn.has_topic() and turn.has_preferences() and prior.has_topic()
                and not self._retriever.search(turn.search_query(), top_k=1)):
            turn = turn.model_copy(update={"interests": [], "career_goal": None})
        return prior.merge(turn)

    def _referenced_course(self, message: str, last_ids: list[str]) -> tuple[Course | None, bool]:
        """Return (course, explicit). Explicit = named by ID or title in this message."""
        course = self._retriever.find_by_title(message)
        if course:
            return course, True
        text = normalize_arabic(message.lower())
        for idx, pattern in _ORDINALS.items():
            if re.search(pattern, text):
                # Out-of-range ordinal ("the second one" when only one was shown): don't guess.
                if idx >= len(last_ids):
                    return None, False
                return self._retriever.get_by_id(last_ids[idx]), False
        if len(last_ids) == 1 and self._asked_attributes(message):
            return self._retriever.get_by_id(last_ids[0]), False
        return None, False

    @staticmethod
    def _asked_attributes(message: str) -> list[str]:
        text = normalize_arabic(message.lower())
        return [f for f, p in _ATTRIBUTE_PATTERNS.items() if re.search(p, text)]

    @staticmethod
    def _title(c: Course, lang: str) -> str:
        return c.title_ar if lang == "ar" and c.title_ar else c.title

    @staticmethod
    def _source(c: Course) -> SourceReference:
        return SourceReference(
            course_id=c.course_id, reference=c.source.reference,
            last_updated=c.source.last_updated.isoformat() if c.source.last_updated else None,
            synthetic=c.source.is_synthetic,
        )

    @staticmethod
    def _course_payload(c: Course) -> dict:
        return c.model_dump(mode="json", exclude={"keywords"})

    def _demo_notice(self, c: Course, lang: str) -> str:
        if not c.source.is_synthetic:
            return ""
        return self._t(lang, "_Demo data: details are illustrative and must be confirmed with the academy._",
                       "_بيانات تجريبية: التفاصيل للتوضيح فقط ويجب تأكيدها مع الأكاديمية._")

    @staticmethod
    def _t(lang: str, en: str, ar: str) -> str:
        return ar if lang == "ar" else en
