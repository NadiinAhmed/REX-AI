"""Agent 3 — Curriculum Research & Accreditation Alignment (chat interface).

Commands (English or Arabic). Everything is answered from stored curricula or created through
`CurriculumService`, so the chat, the Streamlit page and other agents behave the same:
    "create a curriculum for TRK-001 [80 hours] [beginner]"   design a curriculum from the approved catalog
    "list"                                                   stored curricula
    "CUR-1A2B3C"                                             one curriculum
    "align CUR-1A2B3C [swebok-3]"                            accreditation-alignment report
    "submit CUR-1A2B3C"                                      send for review
    "pdf CUR-1A2B3C"                                         how to download the PDF
    "frameworks"                                             frameworks the agent can compare with
Approving or requesting changes is NOT available in chat: it needs a named reviewer, so it lives on
the Curriculum page (behind the admin password).
"""
from __future__ import annotations

import re

from src.agents.base import Agent, AgentRequest, AgentResponse, ResponseMode, SourceReference
from src.curriculum.generator import CurriculumError
from src.curriculum.models import AreaStatus, CurriculumRequest
from src.curriculum.service import CurriculumService, StoredCurriculum
from src.knowledge_base.text_utils import contains_arabic, normalize_arabic

def _rx(pattern: str, flags: int = 0) -> re.Pattern[str]:
    """Patterns are normalised like the incoming message, so Arabic spellings (hamza, ta marbuta) match."""
    return re.compile(normalize_arabic(pattern), flags)


_CUR_ID = re.compile(r"\bcur-([0-9a-f]{6})\b", re.I)
_COURSE_ID = re.compile(r"\b([a-z]{2,5}-\d{3})\b", re.I)
_HOURS = re.compile(r"(\d{1,4}(?:\.\d)?)\s*(?:h\b|hrs?\b|hours?\b|ساع[ةه]|ساعات)", re.I)
_CREATE = _rx(r"\b(create|design|generate|build|make|draft|new)\b|انشئ|صمم|اعمل|ولد|جديد")
_LIST = _rx(r"\b(list|all|curricula|curriculums)\b|قائمه|كل المناهج|المناهج")
_ALIGN = _rx(r"\b(align|alignment|gap|gaps|compare|accreditation|standard)\b|مواءم|فجوات|قارن|اعتماد المعايير")
_SUBMIT = _rx(r"\b(submit|review)\b|ارسل للمراجعه|قدم للمراجعه")
_PDF = _rx(r"\bpdf\b|ملف")
_FRAMEWORKS = _rx(r"\b(frameworks?|standards?)\b|معايير|اطر")
_LEVELS = {"beginner": "beginner", "intermediate": "intermediate", "advanced": "advanced",
           "مبتدي": "beginner", "متوسط": "intermediate", "متقدم": "advanced"}


class CurriculumAgent(Agent):
    name = "curriculum"
    description = "Curriculum Research & Accreditation Alignment Agent"

    def __init__(self, service: CurriculumService, courses) -> None:
        self._svc = service
        self._courses = courses          # CourseRepository: list_approved()

    def handle(self, request: AgentRequest) -> AgentResponse:
        message = request.message
        lang = "ar" if contains_arabic(message) else "en"
        norm = normalize_arabic(message.lower())
        try:
            cur_id = _CUR_ID.search(message)
            cid = f"CUR-{cur_id.group(1).upper()}" if cur_id else None
            if _FRAMEWORKS.search(norm) and not cid:
                return self._frameworks(lang)
            if cid and _ALIGN.search(norm):
                return self._align(cid, norm, lang)
            if cid and _SUBMIT.search(norm):
                return self._submit(cid, lang)
            if cid and _PDF.search(norm):
                return self._pdf_hint(cid, lang)
            if cid:
                return self._show(cid, lang)
            if _CREATE.search(norm) and not _LIST.search(norm.replace("new", "")):
                return self._create(message, norm, lang)
            if _LIST.search(norm):
                return self._list(lang)
        except CurriculumError as exc:
            return self._reply(str(exc), lang)
        return self._overview(lang)

    # ------------------------------------------------------------------ intents
    def _create(self, message: str, norm: str, lang: str) -> AgentResponse:
        course = self._find_course(message, norm)
        if course is None:
            titles = ", ".join(f"{c.course_id} ({c.title})" for c in self._courses.list_approved())
            return self._reply(self._t(lang,
                f"Which course should the curriculum be for? Name a catalog course, e.g. **create a curriculum for TRK-001**.\n"
                f"Catalog: {titles}.\nOptional: add hours (\"80 hours\"), a level (beginner / intermediate / advanced) or a framework ID "
                "(type **frameworks**). To design a brand-new course use the Curriculum page.",
                f"لأي مقرر تريد المنهج؟ اذكر مقرراً من الكتالوج مثل **أنشئ منهج TRK-001**.\nالكتالوج: {titles}.\n"
                "اختيارياً: عدد الساعات أو المستوى أو معيار المواءمة (اكتب **المعايير**). لتصميم مقرر جديد استخدم صفحة المناهج."), lang,
                intent="create_needs_course")
        hours = _HOURS.search(message)
        level = next((v for k, v in _LEVELS.items() if k in norm), None)
        fw_ids = [f.framework_id for f in self._svc.frameworks.all() if f.framework_id in norm]
        stored = self._svc.create(CurriculumRequest(
            course_id=course.course_id, duration_hours=float(hours.group(1)) if hours else None,
            skill_level=level, framework_ids=fw_ids), actor="chat")
        text = self._t(lang, "**Draft curriculum created.** It is not approved: an instructor must review it first.\n\n",
                       "**تم إنشاء مسودة منهج.** لم يتم اعتمادها: يجب أن يراجعها المدرب أولاً.\n\n") + self._summary(stored, lang)
        return self._reply(text, lang, intent="create", stored=stored)

    def _show(self, cid: str, lang: str) -> AgentResponse:
        stored = self._svc.get(cid)
        if stored is None:
            return self._reply(self._t(lang, f"No curriculum with ID {cid}.", f"لا يوجد منهج بالرقم {cid}."), lang, intent="show")
        return self._reply(self._summary(stored, lang), lang, intent="show", stored=stored)

    def _align(self, cid: str, norm: str, lang: str) -> AgentResponse:
        ids = [f.framework_id for f in self._svc.frameworks.all() if f.framework_id in norm]
        stored = self._svc.align(cid, ids or None)
        lines = [self._t(lang, f"**Alignment report for {stored.id}** (a proposal, not an accreditation):",
                         f"**تقرير المواءمة للمنهج {stored.id}** (اقتراح وليس اعتماداً):")]
        for rep in stored.alignment:
            lines.append(f"\n**{rep.framework_name}** — {rep.coverage_percent:g}% "
                         f"({rep.covered} {self._t(lang, 'covered', 'مغطى')}, {rep.partial} {self._t(lang, 'partial', 'جزئي')}, "
                         f"{rep.missing} {self._t(lang, 'missing', 'ناقص')})")
            lines += [f"- {a.title}: {a.recommendation}" for a in rep.gaps]
            lines.append(f"_{rep.disclaimer}_")
        return self._reply("\n".join(lines), lang, intent="align", stored=stored)

    def _submit(self, cid: str, lang: str) -> AgentResponse:
        stored = self._svc.submit(cid, actor="chat")
        return self._reply(self._t(lang, f"{stored.id} was sent for review. A reviewer approves it or requests changes on the Curriculum page.",
                                   f"تم إرسال {stored.id} للمراجعة. يعتمده المراجع أو يطلب تعديلات من صفحة المناهج."), lang,
                           intent="submit", stored=stored)

    def _pdf_hint(self, cid: str, lang: str) -> AgentResponse:
        stored = self._svc.get(cid)
        if stored is None:
            return self._show(cid, lang)
        return self._reply(self._t(lang, f"Open the **Curriculum** page, select {cid} and press *Download PDF*.",
                                   f"افتح صفحة **المناهج** واختر {cid} ثم اضغط *تحميل PDF*."), lang, intent="pdf", stored=stored)

    def _list(self, lang: str) -> AgentResponse:
        rows = self._svc.list()
        if not rows:
            return self._reply(self._t(lang, "No curricula yet. Try **create a curriculum for TRK-001**.",
                                       "لا توجد مناهج بعد. جرّب **أنشئ منهج TRK-001**."), lang, intent="list")
        lines = [self._t(lang, f"**{len(rows)} curriculum(s):**", f"**{len(rows)} منهج:**")]
        lines += [f"- **{s.id}** · {s.curriculum.title} · {s.status.label} · {s.curriculum.total_hours:g} h · rev {s.revision}" for s in rows[:10]]
        return self._reply("\n".join(lines), lang, intent="list", data={"count": len(rows)})

    def _frameworks(self, lang: str) -> AgentResponse:
        lines = [self._t(lang, "**Frameworks I can compare a curriculum with** (reference checklists, not official standard text):",
                         "**المعايير التي أقارن بها المنهج** (قوائم مرجعية وليست النص الرسمي للمعيار):")]
        lines += [f"- `{f.framework_id}` — {f.name}" for f in self._svc.frameworks.all()]
        lines.append(self._t(lang, "I propose alignment only: I never state that a course is accredited.",
                             "أقترح المواءمة فقط ولا أذكر أن مقرراً معتمد رسمياً."))
        return self._reply("\n".join(lines), lang, intent="frameworks")

    def _overview(self, lang: str) -> AgentResponse:
        counts = self._svc.list()
        text = self._t(lang,
            "I design **curricula** from the approved course catalog, compare them with competency frameworks and prepare a PDF for review.\n"
            "Commands: **create a curriculum for TRK-001** · **list** · **CUR-XXXXXX** · **align CUR-XXXXXX** · **submit CUR-XXXXXX** · **frameworks**.\n"
            "To start I need: the course, the audience, the level, the hours and the framework (defaults come from the catalog).",
            "أصمم **المناهج** من كتالوج المقررات المعتمد وأقارنها بأطر الكفاءة وأجهز ملف PDF للمراجعة.\n"
            "الأوامر: **أنشئ منهج TRK-001** · **قائمة** · **CUR-XXXXXX** · **مواءمة CUR-XXXXXX** · **المعايير**.")
        return self._reply(text, lang, intent="overview", data={"stored": len(counts)})

    # ------------------------------------------------------------------ helpers
    def _find_course(self, message: str, norm: str):
        approved = self._courses.list_approved()
        if m := _COURSE_ID.search(message):
            found = next((c for c in approved if c.course_id == m.group(1).upper()), None)
            if found:
                return found
        for c in approved:
            if c.title.lower() in norm or (c.title_ar and normalize_arabic(c.title_ar) in norm):
                return c
        return None

    def _summary(self, s: StoredCurriculum, lang: str) -> str:
        c = s.curriculum
        rows = [f"**{c.title}** ({s.id}) — {s.status.label}, revision {s.revision}",
                f"- {self._t(lang, 'Total hours', 'إجمالي الساعات')}: {c.total_hours:g}"
                + (self._t(lang, " (proposed: not published)", " (مقترحة: غير منشورة)") if c.hours_origin.value == "template" else ""),
                f"- {self._t(lang, 'Modules', 'الوحدات')} ({len(c.modules)}): " + "; ".join(f"{m.number}. {m.title} ({m.hours:g} h)" for m in c.modules[:6])
                + ("…" if len(c.modules) > 6 else ""),
                f"- {self._t(lang, 'Learning outcomes', 'مخرجات التعلم')}: {len(c.learning_outcomes)} · "
                f"{self._t(lang, 'Capstone', 'مشروع التخرج')}: {c.capstone.hours:g} h" if c.capstone else ""]
        for rep in s.alignment:
            gaps = [a.title for a in rep.areas if a.status != AreaStatus.COVERED]
            rows.append(f"- {self._t(lang, 'Alignment with', 'المواءمة مع')} {rep.framework_id}: {rep.coverage_percent:g}%"
                        + (self._t(lang, f" · gaps: {', '.join(gaps)}", f" · الفجوات: {', '.join(gaps)}") if gaps else ""))
        if c.notices:
            rows.append(self._t(lang, "\n**Review before approval:**", "\n**للمراجعة قبل الاعتماد:**"))
            rows += [f"- {n}" for n in c.notices[:4]]
        return "\n".join(r for r in rows if r)

    def _reply(self, text: str, lang: str, *, intent: str = "", stored: StoredCurriculum | None = None,
               data: dict | None = None) -> AgentResponse:
        sources: list[SourceReference] = []
        payload = {"intent": intent, **(data or {})}
        mode = ResponseMode.DETERMINISTIC
        if stored is not None:
            c = stored.curriculum
            payload.update(curriculum_id=stored.id, status=stored.status.value)
            if c.course_id:
                sources.append(SourceReference(source_type="course", reference=c.sources[0].reference if c.sources else c.course_id,
                                               course_id=c.course_id))
            sources += [SourceReference(source_type="framework", reference=r.framework_name) for r in stored.alignment]
            sources.append(SourceReference(source_type="curriculum", reference=stored.id))
            if c.generation_mode == "llm":
                mode = ResponseMode.LLM
        return AgentResponse(agent_name=self.name, text=text, mode=mode, language=lang, sources=sources, data=payload)

    @staticmethod
    def _t(lang: str, en: str, ar: str) -> str:
        return ar if lang == "ar" else en
