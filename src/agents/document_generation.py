"""Agent 4 — Educational Content & Document Generation (chat interface).

    "generate a participant guide for TRK-001"        create a draft document (English)
    "اعمل دليل المشارك لـ TRK-001 بالعربية"            Arabic
    "question bank for CUR-1A2B3C"                    build from a stored curriculum
    "types"  ·  "list"  ·  "DOC-1A2B3C"  ·  "pdf DOC-1A2B3C"
Approval and e-mailing are NOT available in chat: they need a named reviewer and live on the Documents page.
"""
from __future__ import annotations

import re

from src.agents.base import Agent, AgentRequest, AgentResponse, ResponseMode, SourceReference
from src.documents.context import DocumentError
from src.documents.models import DocType, DocumentRequest
from src.documents.service import DocumentService
from src.knowledge_base.text_utils import contains_arabic, normalize_arabic


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(normalize_arabic(pattern))


# most specific first: "curriculum" is the generic fallback
_TYPE_PATTERNS: list[tuple[DocType, re.Pattern[str]]] = [(t, _rx(p)) for t, p in [
    (DocType.PARTICIPANT_GUIDE, r"participant guide|student guide|learner guide|دليل المشارك|دليل المتدرب|دليل الطالب"),
    (DocType.INSTRUCTOR_GUIDE, r"instructor guide|facilitator guide|trainer guide|teacher guide|دليل المدرب|دليل المحاضر|دليل الميسر"),
    (DocType.LEARNING_OUTCOMES_PLAN, r"learning outcomes|assessment plan|outcomes plan|مخرجات التعلم|خطه التقييم"),
    (DocType.QUESTION_BANK, r"question bank|exam questions|questions|بنك الاسئله|اسئله"),
    (DocType.ASSESSMENT_RUBRICS, r"rubrics?|assessment criteria|معايير التقييم|معيار التقييم"),
    (DocType.CAPSTONE_BRIEF, r"capstone|graduation project|مشروع التخرج"),
    (DocType.PRESENTATION_OUTLINE, r"presentation|slides?|slide deck|عرض تقديمي|شرائح"),
    (DocType.COURSE_PROPOSAL, r"proposal|مقترح"),
    (DocType.COURSE_DESCRIPTION, r"course description|description|brochure|وصف المقرر|وصف"),
    (DocType.CURRICULUM_PDF, r"curriculum|syllabus|المنهج|منهج"),
]]
_DOC_ID = re.compile(r"\bdoc-([0-9a-f]{6})\b", re.I)
_CUR_ID = re.compile(r"\bcur-([0-9a-f]{6})\b", re.I)
_COURSE_ID = re.compile(r"\b([a-z]{2,5}-\d{3})\b", re.I)
_LIST = _rx(r"\b(list|all documents|documents)\b|قائمه|كل المستندات")
_TYPES = _rx(r"\b(types|what documents|which documents|what can you)\b|انواع|ما المستندات")
_PDF = _rx(r"\bpdf\b|تحميل")
_ARABIC = _rx(r"\barabic\b|بالعربيه|بالعربي|عربي")
_ENGLISH = _rx(r"\benglish\b|بالانجليزيه|بالانجليزي|انجليزي")
_GENERATE = _rx(r"\b(generate|create|make|write|prepare|produce|draft|build)\b|اعمل|انشئ|جهز|اكتب|اعد|ولد")


class DocumentGenerationAgent(Agent):
    name = "document_generation"
    description = "Educational Content & Document Generation Agent"

    def __init__(self, service: DocumentService, courses) -> None:
        self._svc = service
        self._courses = courses

    def handle(self, request: AgentRequest) -> AgentResponse:
        message = request.message
        norm = normalize_arabic(message.lower())
        lang = "ar" if contains_arabic(message) else "en"
        try:
            if m := _DOC_ID.search(message):
                doc_id = f"DOC-{m.group(1).upper()}"
                return self._pdf_hint(doc_id, lang) if _PDF.search(norm) else self._show(doc_id, lang)
            if _TYPES.search(norm):
                return self._types(lang)
            doc_type = next((t for t, rx in _TYPE_PATTERNS if rx.search(norm)), None)
            if doc_type is not None or _GENERATE.search(norm):
                return self._generate(message, norm, doc_type, lang)
            if _LIST.search(norm):
                return self._list(lang)
        except DocumentError as exc:
            return self._reply(str(exc), lang)
        return self._overview(lang)

    # ------------------------------------------------------------------ intents
    def _generate(self, message: str, norm: str, doc_type: DocType | None, lang: str) -> AgentResponse:
        course = self._find_course(message, norm)
        cur = _CUR_ID.search(message)
        if doc_type is None or (course is None and cur is None):
            return self._ask(doc_type, lang)
        out_lang = "en" if _ENGLISH.search(norm) else ("ar" if (_ARABIC.search(norm) or lang == "ar") else "en")
        stored = self._svc.generate(DocumentRequest(
            doc_type=doc_type, course_id=course.course_id if course else None,
            curriculum_id=f"CUR-{cur.group(1).upper()}" if cur else None, language=out_lang), actor="chat")
        text = self._t(lang,
            f"**Draft created:** {stored.title} ({stored.id}).\n- Built from: {stored.basis}\n- Language: {stored.language} · revision {stored.revision} · status draft\n"
            + "".join(f"- {n}\n" for n in stored.notices[:3])
            + f"\nOpen the **Documents** page to review, edit, approve and download the PDF. It is not approved yet.",
            f"**تم إنشاء مسودة:** {stored.title} ({stored.id}).\n- مبني على: {stored.basis}\n- اللغة: {stored.language} · المراجعة {stored.revision} · الحالة: مسودة\n"
            + "\nافتح صفحة **المستندات** للمراجعة والتعديل والاعتماد وتحميل PDF. المستند غير معتمد بعد.")
        return self._reply(text, lang, intent="generate", stored=stored)

    def _ask(self, doc_type: DocType | None, lang: str) -> AgentResponse:
        types = ", ".join(t.label_ar if lang == "ar" else t.label for t in DocType)
        courses = ", ".join(f"{c.course_id} ({c.title})" for c in self._courses.list_approved())
        text = self._t(lang,
            f"To prepare a document I need: **which document**{' (' + doc_type.label + ' ✓)' if doc_type else ''}, **which course** or curriculum, "
            f"the audience and the language.\nDocuments: {types}.\nCourses: {courses}.\nExample: **generate a participant guide for TRK-001**.",
            f"لإعداد مستند أحتاج: **نوع المستند** و**المقرر** أو المنهج والفئة المستهدفة واللغة.\nالمستندات: {types}.\nالمقررات: {courses}.\nمثال: **اعمل دليل المشارك لـ TRK-001 بالعربية**.")
        return self._reply(text, lang, intent="needs_input")

    def _types(self, lang: str) -> AgentResponse:
        lines = [self._t(lang, "**Documents I can prepare:**", "**المستندات التي أستطيع إعدادها:**")]
        lines += [f"- {t.label_ar if lang == 'ar' else t.label}" for t in DocType]
        lines.append(self._t(lang, "Each is a draft until a reviewer approves it. English and Arabic are supported.",
                             "كل مستند يبقى مسودة حتى يعتمده مراجع. تتوفر العربية والإنجليزية."))
        return self._reply("\n".join(lines), lang, intent="types")

    def _show(self, doc_id: str, lang: str) -> AgentResponse:
        stored = self._svc.get(doc_id)
        if stored is None:
            return self._reply(self._t(lang, f"No document with ID {doc_id}.", f"لا يوجد مستند بالرقم {doc_id}."), lang, intent="show")
        text = (f"**{stored.title}** ({stored.id}) — {stored.status.value}, revision {stored.revision}, {stored.language}\n"
                f"- {self._t(lang, 'Built from', 'مبني على')}: {stored.basis}\n- {self._t(lang, 'Sections', 'الأقسام')}: "
                + "; ".join(s.heading for s in stored.content.sections[:8]) + ("…" if len(stored.content.sections) > 8 else ""))
        return self._reply(text, lang, intent="show", stored=stored)

    def _pdf_hint(self, doc_id: str, lang: str) -> AgentResponse:
        stored = self._svc.get(doc_id)
        if stored is None:
            return self._show(doc_id, lang)
        return self._reply(self._t(lang, f"Open the **Documents** page, select {doc_id} and press *Download PDF*.",
                                   f"افتح صفحة **المستندات** واختر {doc_id} ثم اضغط *تحميل PDF*."), lang, intent="pdf", stored=stored)

    def _list(self, lang: str) -> AgentResponse:
        rows = self._svc.list()
        if not rows:
            return self._reply(self._t(lang, "No documents yet. Try **generate a participant guide for TRK-001**.",
                                       "لا توجد مستندات بعد. جرّب **اعمل دليل المشارك لـ TRK-001**."), lang, intent="list")
        lines = [self._t(lang, f"**{len(rows)} document(s):**", f"**{len(rows)} مستند:**")]
        lines += [f"- **{d.id}** · {d.doc_type.label} · {d.course_id or '—'} · {d.language} · {d.status.value} · rev {d.revision}" for d in rows[:10]]
        return self._reply("\n".join(lines), lang, intent="list", data={"count": len(rows)})

    def _overview(self, lang: str) -> AgentResponse:
        text = self._t(lang,
            "I prepare course documents from the approved curriculum or course record: descriptions, proposals, guides, presentation outlines, "
            "question banks, rubrics, capstone briefs and assessment plans, in English or Arabic.\n"
            "Try: **generate a participant guide for TRK-001** · **types** · **list** · **DOC-XXXXXX**.",
            "أعد مستندات المقررات من المنهج المعتمد أو بيانات المقرر: الأوصاف والمقترحات والأدلة والعروض وبنوك الأسئلة ومعايير التقييم ومشاريع التخرج وخطط التقييم بالعربية أو الإنجليزية.\n"
            "جرّب: **اعمل دليل المشارك لـ TRK-001** · **أنواع المستندات** · **قائمة**.")
        return self._reply(text, lang, intent="overview")

    # ------------------------------------------------------------------ helpers
    def _find_course(self, message: str, norm: str):
        approved = self._courses.list_approved()
        if m := _COURSE_ID.search(message):
            found = next((c for c in approved if c.course_id == m.group(1).upper()), None)
            if found:
                return found
        return next((c for c in approved if c.title.lower() in norm or (c.title_ar and normalize_arabic(c.title_ar) in norm)), None)

    def _reply(self, text: str, lang: str, *, intent: str = "", stored=None, data: dict | None = None) -> AgentResponse:
        sources: list[SourceReference] = []
        payload = {"intent": intent, **(data or {})}
        mode = ResponseMode.DETERMINISTIC
        if stored is not None:
            payload.update(document_id=stored.id, status=stored.status.value)
            if stored.course_id:
                sources.append(SourceReference(source_type="course", reference=stored.course_id, course_id=stored.course_id))
            if stored.curriculum_id:
                sources.append(SourceReference(source_type="curriculum", reference=stored.curriculum_id))
            if stored.generation_mode == "llm":
                mode = ResponseMode.LLM
        return AgentResponse(agent_name=self.name, text=text, mode=mode, language=lang, sources=sources, data=payload)

    @staticmethod
    def _t(lang: str, en: str, ar: str) -> str:
        return ar if lang == "ar" else en
