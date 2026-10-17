"""REX Assistant: bilingual course advisor (Sprint 1 prototype).

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

from src.agents import prompts
from src.agents.base import Agent, AgentRequest, AgentResponse, ResponseMode, SourceReference
from src.knowledge_base.retriever import CourseRetriever
from src.knowledge_base.text_utils import contains_arabic, normalize_arabic
from src.models.course import Course
from src.services.llm_service import ChatMessage, LLMError, LLMService
from src.utils.logging_config import get_logger
from src.workflows.learner_profile import LearnerProfile, contains_contact_details, extract_profile
from src.workflows.recommendation import Reason, Recommendation, recommend_courses

logger = get_logger("rex_assistant")

# Course question topics: field name -> regex over normalized lower text
_ATTRIBUTE_PATTERNS: dict[str, str] = {
    "fee": r"\b(price|cost|fees?|how much)\b|سعر|تكلفه|بكام|رسوم|كم سعر|بكم",
    "duration": r"\b(duration|how long|hours|weeks|length)\b|مده|كام ساعه|كم ساعه|كم اسبوع",
    "prerequisites": r"\b(prerequisites?|requirements?|need to know)\b|متطلبات|شروط",
    "instructor": r"\b(instructor|trainer|teacher|lecturer)\b|مدرب|محاضر|مدرس",
    "schedule": r"\b(schedule|when|start|dates?|timing)\b|موعد|ميعاد|امتي|متي|يبدا",
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


class RexAssistant(Agent):
    name = "rex_assistant"
    description = "REX Assistant: bilingual course recommendations (Sprint 1 prototype)"

    def __init__(
        self, retriever: CourseRetriever, llm: LLMService | None = None,
        dataset_notice: str = "", top_k: int = 3,
    ) -> None:
        self._retriever = retriever
        self._llm = llm
        self._dataset_notice = dataset_notice
        self._top_k = top_k

    # ------------------------------------------------------------------ entry point
    def handle(self, request: AgentRequest) -> AgentResponse:
        message = request.message
        lang = "ar" if contains_arabic(message) else "en"
        prior = LearnerProfile.model_validate(request.state.get("profile", {}))
        last_ids: list[str] = request.state.get("last_course_ids", [])

        notes: list[str] = []
        if contains_contact_details(message):
            notes.append(self._t(lang,
                "Note: I can't save contact details in this prototype, so I've ignored them.",
                "ملاحظة: لا يمكنني حفظ بيانات التواصل في هذا النموذج الأولي، لذلك تم تجاهلها."))

        turn = extract_profile(message)
        attributes = self._asked_attributes(message)
        course, explicit = self._referenced_course(message, last_ids)
        # A course named in the message is always a question about it. A course referenced
        # implicitly ("the second one", or a follow-up about the last course) only counts
        # when the learner isn't introducing a new topic.
        is_course_question = course is not None and (explicit or not turn.has_topic())

        if is_course_question:
            profile = prior  # course titles are not learner interests
            intent, response = "course_question", self._answer_course_question(course, attributes, lang, message)
        elif attributes and not turn.has_topic():
            profile = prior
            intent, response = "clarify_course", self._ask_which_course(last_ids, lang)
        elif (profile := prior.merge(turn)).has_topic():
            intent, response = "recommendation", self._recommend(profile, lang, message)
        else:
            intent, response = "follow_up", self._ask_follow_up(profile, lang)

        logger.info("intent=%s lang=%s mode=%s sources=%s", intent, lang, response.mode.value,
                    [s.course_id for s in response.sources])
        if notes:
            response.text = "\n\n".join(notes + [response.text])
        response.language = lang
        response.data["intent"] = intent
        response.state = {
            "profile": profile.model_dump(mode="json", exclude_none=True),
            "last_course_ids": [s.course_id for s in response.sources] or last_ids,
        }
        return response

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
