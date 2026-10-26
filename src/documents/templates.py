"""The ten document templates (brief: Course Description ... Learning Outcomes and Assessment Plan).

Every template is a function  `(ctx, t, extras) -> list[Section]`  that only REARRANGES approved data
(the curriculum / course record). Rules:
  * a value that is not published is printed as "Not published", never invented;
  * generic wording (teaching approach, rubric descriptors, milestones) is marked as a template proposal;
  * `t(en, ar)` picks the language of the fixed text. Course data stays as stored (the catalog is English).
Bump TEMPLATE_VERSION when a template changes in a way readers should notice.
"""
from __future__ import annotations

from collections.abc import Callable

from src.curriculum.export import curriculum_document
from src.curriculum.generator import short
from src.curriculum.models import Curriculum, CurriculumModule, Origin
from src.documents.context import DocContext
from src.documents.models import DocType
from src.pdf.content import DocumentContent, Section, bullets, note, numbered, para, table

TEMPLATE_VERSION = "1.0"
Translator = Callable[[str, str], str]


def make_translator(language: str) -> Translator:
    return (lambda en, ar: ar) if language == "ar" else (lambda en, ar: en)


def _h(value: float | None) -> str:
    return "—" if value is None else f"{value:g}"


def _pub(value, t: Translator) -> str:
    """Printable value, or 'Not published' when the academy has not published it."""
    return str(value) if value not in (None, "", [], "unknown") else t("Not published", "غير منشور")


def _audience(c: DocContext, t: Translator) -> str:
    if c.audience:
        return c.audience
    return ", ".join(c.curriculum.target_audience) or t("Not defined", "غير محدد")


def _level(cur: Curriculum, t: Translator) -> str:
    return t("Not specified", "غير محدد") if cur.skill_level == "unknown" else cur.skill_level.replace("_", " ")


def _fee(c: DocContext, t: Translator) -> str:
    fee = c.course.fee if c.course else None
    return f"{fee.amount:g} {fee.currency}" if fee and fee.is_known else t("Not published", "غير منشور")


def _modules_table(cur: Curriculum, t: Translator, topics: bool = True) -> list:
    head = [t("#", "#"), t("Module", "الوحدة"), t("Hours", "الساعات")] + ([t("Topics", "المواضيع")] if topics else [])
    rows = [[str(m.number), m.title, _h(m.hours)] + ([str(len(m.topics))] if topics else []) for m in cur.modules]
    if cur.capstone:
        rows.append(["", t("Capstone project", "مشروع التخرج"), _h(cur.capstone.hours)] + ([""] if topics else []))
    rows.append(["", t("Total", "الإجمالي"), _h(cur.allocated_hours)] + ([""] if topics else []))
    return [table(head, rows)]


MILESTONE_SHARES = (0.10, 0.20, 0.40, 0.30)     # template proposal: proposal, design review, checkpoint, final demo


def _milestone_hours(total: float) -> list[float]:
    """Split capstone hours by MILESTONE_SHARES in half hours; the last milestone takes the remainder so the sum is exact."""
    hours = [round(total * share * 2) / 2 for share in MILESTONE_SHARES[:-1]]
    return [*hours, round(total - sum(hours), 2)]


def _assessment_table(cur: Curriculum, t: Translator):
    return table([t("Component", "المكوّن"), t("Type", "النوع"), t("Weight", "الوزن"), t("Description", "الوصف")],
                 [[a.name, a.kind, f"{a.weight_percent:g}%", a.description] for a in cur.assessments])


# ====================================================================== 1. course description
def course_description(c: DocContext, t: Translator, extras: dict) -> list[Section]:
    cur, course = c.curriculum, c.course
    return [
        Section(heading=t("Overview", "نظرة عامة"), blocks=[para(cur.summary or t("A description has not been published for this course.", "لم يُنشر وصف لهذا المقرر بعد."))]),
        Section(heading=t("At a glance", "لمحة سريعة"), blocks=[table([t("Item", "البند"), t("Detail", "التفاصيل")], [
            [t("Level", "المستوى"), _level(cur, t)], [t("Duration", "المدة"), f"{_h(cur.total_hours)} " + t("hours", "ساعة")],
            [t("Delivery format", "نمط التقديم"), _pub(course.delivery_format.value if course else None, t)],
            [t("Fee", "الرسوم"), _fee(c, t)], [t("Schedule", "الجدول"), _pub(course.schedule if course else None, t)],
            [t("Instructor", "المدرب"), _pub(course.instructor if course else None, t)],
            [t("Department", "القسم"), _pub(cur.department, t)], [t("Industry", "المجال"), _pub(cur.industry, t)]])]),
        Section(heading=t("Who is it for?", "لمن هذا المقرر؟"), blocks=[
            para(t("Intended audience: ", "الفئة المستهدفة: ") + _audience(c, t)),
            para(t("Prerequisites: ", "المتطلبات المسبقة: ") + ("; ".join(p.text for p in cur.prerequisites) or t("none published", "لا توجد متطلبات منشورة")))]),
        Section(heading=t("What you will learn", "ماذا ستتعلم"), blocks=[
            numbered([f"{m.title} ({_h(m.hours)} " + t("h", "س") + ")" for m in cur.modules])]),
        *([Section(heading=t("Tools and technologies", "الأدوات والتقنيات"), blocks=[bullets(cur.tools)])] if cur.tools else []),
        *([Section(heading=t("Capstone project", "مشروع التخرج"), blocks=[para(cur.capstone.description.text),
                                                                      bullets([d.text for d in cur.capstone.deliverables])])] if cur.capstone else []),
        *([Section(heading=t("Career opportunities", "الفرص الوظيفية"), blocks=[bullets(cur.career_paths)])] if cur.career_paths else []),
    ]


# ====================================================================== 2. course proposal
def course_proposal(c: DocContext, t: Translator, extras: dict) -> list[Section]:
    cur = c.curriculum
    align = [f"{r.framework_name}: {r.coverage_percent:g}% " + t("(alignment proposal)", "(مقترح مواءمة)") for r in c.alignment]
    return [
        Section(heading=t("Executive summary", "الملخص التنفيذي"), blocks=[para(t(
            f"This proposal presents {cur.title}, a {_h(cur.total_hours)}-hour programme for {_audience(c, t)}. "
            f"It has {len(cur.modules)} modules and ends with a capstone project.",
            f"يعرض هذا المقترح مقرر {cur.title} بمدة {_h(cur.total_hours)} ساعة للفئة: {_audience(c, t)}. "
            f"يتكون من {len(cur.modules)} وحدات وينتهي بمشروع تخرج."))]),
        Section(heading=t("Rationale and demand", "المبررات والطلب"), blocks=[
            para(t("Career paths: ", "المسارات الوظيفية: ") + (", ".join(cur.career_paths) or t("not published", "غير منشورة"))),
            bullets(cur.market_signals) if cur.market_signals else note(t("No market-demand data is attached to this curriculum.", "لا توجد بيانات عن الطلب في السوق مرفقة بهذا المنهج."))]),
        Section(heading=t("Objectives", "الأهداف"), blocks=[bullets([o.text for o in cur.objectives])]),
        Section(heading=t("Target audience and entry requirements", "الفئة المستهدفة وشروط الالتحاق"), blocks=[
            para(_audience(c, t)), para(t("Prerequisites: ", "المتطلبات المسبقة: ") + ("; ".join(p.text for p in cur.prerequisites) or t("none published", "لا توجد متطلبات منشورة")))]),
        Section(heading=t("Programme structure", "هيكل البرنامج"), blocks=_modules_table(cur, t)),
        Section(heading=t("Learning outcomes", "مخرجات التعلم"), blocks=[bullets([f"{o.id}: {o.text}" for o in cur.learning_outcomes])]),
        Section(heading=t("Assessment and certification", "التقييم والشهادة"), blocks=[
            _assessment_table(cur, t),
            *([bullets(align)] if align else []),
            note(t("No accreditation is claimed. Any certificate or accreditation must be confirmed with the issuing body before it is promised to learners.",
                   "لا يُدّعى أي اعتماد. يجب تأكيد أي شهادة أو اعتماد مع الجهة المانحة قبل وعد المتعلمين به."))]),
        Section(heading=t("Resources and budget", "الموارد والميزانية"), blocks=[
            para(t("Tools and platforms: ", "الأدوات والمنصات: ") + (", ".join(cur.tools) or t("to be defined", "تُحدد لاحقاً"))),
            table([t("Item", "البند"), t("Status", "الحالة")], [
                [t("Course fee", "رسوم المقرر"), _fee(c, t)],
                [t("Instructor", "المدرب"), _pub(c.course.instructor if c.course else None, t)],
                [t("Schedule", "الجدول"), _pub(c.course.schedule if c.course else None, t)],
                [t("Delivery format", "نمط التقديم"), _pub(c.course.delivery_format.value if c.course else None, t)]]),
            note(t("Items marked 'Not published' must be completed by the academy before approval.", "البنود غير المنشورة يجب أن تستكملها الأكاديمية قبل الاعتماد."))]),
        Section(heading=t("Risks and assumptions", "المخاطر والافتراضات"), blocks=[bullets(cur.notices) if cur.notices else para(t("None recorded.", "لا شيء مسجل."))]),
        Section(heading=t("Approvals", "الموافقات"), blocks=[table([t("Role", "الدور"), t("Name", "الاسم"), t("Signature", "التوقيع"), t("Date", "التاريخ")], [
            [t("Prepared by", "أعدّه"), "", "", ""], [t("Academic review", "المراجعة الأكاديمية"), "", "", ""], [t("Approved by", "اعتمده"), "", "", ""]])]),
    ]


# ====================================================================== 3. curriculum PDF (Agent 3 export)
def curriculum_pdf(c: DocContext, t: Translator, extras: dict) -> list[Section]:
    return curriculum_document(c.curriculum, c.alignment, curriculum_id=c.curriculum_id or "", status=c.curriculum_status,
                               revision=c.curriculum_revision or 1).sections


# ====================================================================== 4. participant guide
def participant_guide(c: DocContext, t: Translator, extras: dict) -> list[Section]:
    cur = c.curriculum
    sections = [
        Section(heading=t("Welcome", "مرحباً بك"), blocks=[
            para(t(f"Welcome to {cur.title}. This guide helps you follow the course, prepare for each module and check your own progress.",
                   f"مرحباً بك في مقرر {cur.title}. يساعدك هذا الدليل على متابعة المقرر والاستعداد لكل وحدة وقياس تقدمك بنفسك.")),
            para(t("How to use this guide: read the module overview before each session, do the practice tasks, then use the self-check questions.",
                   "طريقة الاستخدام: اقرأ نظرة الوحدة قبل كل جلسة، ونفّذ المهام العملية، ثم استخدم أسئلة التقييم الذاتي."))]),
        Section(heading=t("Course at a glance", "المقرر في لمحة"), blocks=[table([t("Item", "البند"), t("Detail", "التفاصيل")], [
            [t("Level", "المستوى"), _level(cur, t)], [t("Total hours", "إجمالي الساعات"), _h(cur.total_hours)],
            [t("Modules", "الوحدات"), str(len(cur.modules))], [t("Capstone hours", "ساعات مشروع التخرج"), _h(cur.capstone_hours)]])]),
        Section(heading=t("Before you start", "قبل أن تبدأ"), blocks=[
            para(t("Prerequisites: ", "المتطلبات المسبقة: ") + ("; ".join(p.text for p in cur.prerequisites) or t("none published", "لا توجد متطلبات منشورة"))),
            *([para(t("Tools you will use (your instructor will confirm the setup):", "الأدوات التي ستستخدمها (سيؤكد المدرب خطوات التجهيز):")), bullets(cur.tools)] if cur.tools else [])]),
        Section(heading=t("What you will achieve", "ما الذي ستحققه"), blocks=[bullets([f"{o.id}: {o.text}" for o in cur.learning_outcomes])]),
    ]
    for m in cur.modules:
        sections.append(Section(heading=t(f"Module {m.number}: {m.title} ({_h(m.hours)} h)", f"الوحدة {m.number}: {m.title} ({_h(m.hours)} س)"), blocks=[
            para(t("What you will learn", "ما ستتعلمه")), bullets([x.text for x in m.topics]) if m.topics else para(t("Topic detail will be provided by your instructor.", "سيزوّدك المدرب بتفاصيل المواضيع.")),
            para(t("Practice", "التطبيق العملي")), bullets([e.text for e in m.exercises]),
            para(t("Check yourself", "قيّم نفسك")),
            bullets([t(f"Can you explain {short(x.text)} in your own words?", f"هل تستطيع شرح {short(x.text)} بأسلوبك؟") for x in m.topics[:4]]
                    or [t(f"Can you explain the main ideas of {m.title}?", f"هل تستطيع شرح الأفكار الرئيسية في {m.title}؟")]),
            para(t("My notes: ______________________________", "ملاحظاتي: ______________________________"))]))
    if cur.capstone:
        sections.append(Section(heading=t("Your capstone project", "مشروع التخرج"), blocks=[
            para(cur.capstone.description.text), bullets([d.text for d in cur.capstone.deliverables]),
            para(t(f"Time allocated: {_h(cur.capstone.hours)} hours.", f"الوقت المخصص: {_h(cur.capstone.hours)} ساعة."))]))
    sections += [
        Section(heading=t("How you will be assessed", "كيف سيتم تقييمك"), blocks=[_assessment_table(cur, t)]),
        Section(heading=t("Tips for success", "نصائح للنجاح"), blocks=[bullets([
            t("Attend every session and start labs early.", "احضر كل الجلسات وابدأ التطبيقات العملية مبكراً."),
            t("Ask questions as soon as something is unclear.", "اطرح أسئلتك فور غموض أي نقطة."),
            t("Keep your work in a repository so you can show it to employers.", "احفظ أعمالك في مستودع لتعرضها على جهات التوظيف."),
            t("Start the capstone early and review it with your instructor.", "ابدأ مشروع التخرج مبكراً وراجعه مع مدربك.")])])]
    return sections


# ====================================================================== 5. instructor guide
SPLIT = (0.3, 0.5, 0.2)      # concepts / guided lab / review: a TEMPLATE proposal the instructor may change


def _split(hours: float) -> list[float]:
    parts = [round(hours * s * 2) / 2 for s in SPLIT]
    parts[1] = round(hours - parts[0] - parts[2], 2)
    return parts


def instructor_guide(c: DocContext, t: Translator, extras: dict) -> list[Section]:
    cur = c.curriculum
    sections = [
        Section(heading=t("Purpose of this guide", "الغرض من الدليل"), blocks=[para(t(
            f"This guide helps you deliver {cur.title}: what to cover in each module, suggested time use, practice tasks and how to assess.",
            f"يساعدك هذا الدليل على تقديم مقرر {cur.title}: ما يجب تغطيته في كل وحدة والتوزيع الزمني المقترح والتطبيقات وطريقة التقييم."))]),
        Section(heading=t("Course overview", "نظرة على المقرر"), blocks=[table([t("Item", "البند"), t("Detail", "التفاصيل")], [
            [t("Audience", "الفئة"), _audience(c, t)], [t("Level", "المستوى"), _level(cur, t)], [t("Total hours", "إجمالي الساعات"), _h(cur.total_hours)],
            [t("Modules", "الوحدات"), str(len(cur.modules))]])]),
        Section(heading=t("Suggested time use", "التوزيع الزمني المقترح"), blocks=[
            note(t("Template proposal: about 30% concepts and demonstration, 50% guided lab, 20% review and quiz. Adjust to your class.",
                   "مقترح قياسي: نحو 30% للمفاهيم والعرض، و50% للتطبيق الموجّه، و20% للمراجعة والاختبار. عدّله حسب مجموعتك.")),
            table([t("Module", "الوحدة"), t("Hours", "الساعات"), t("Concepts", "المفاهيم"), t("Guided lab", "التطبيق"), t("Review", "المراجعة")],
                  [[f"{m.number}. {m.title}", _h(m.hours), *map(_h, _split(m.hours))] for m in cur.modules])]),
        Section(heading=t("Preparation checklist", "قائمة التجهيز"), blocks=[bullets([
            t("Tools and accounts ready for every learner: ", "الأدوات والحسابات جاهزة لكل متعلم: ") + (", ".join(cur.tools) or t("to be defined", "تُحدد لاحقاً")),
            t("Lab datasets, sample applications and repositories prepared (not supplied by REX AI).", "تجهيز مجموعات البيانات والتطبيقات التجريبية والمستودعات (لا توفرها REX AI)."),
            t("Attendance and assessment sheets printed or shared.", "تجهيز كشوف الحضور والتقييم.")])]),
    ]
    for m in cur.modules:
        sections.append(Section(heading=t(f"Module {m.number}: {m.title} ({_h(m.hours)} h)", f"الوحدة {m.number}: {m.title} ({_h(m.hours)} س)"), blocks=[
            para(t("Topics to cover", "المواضيع المطلوب تغطيتها")), bullets([x.text for x in m.topics]) if m.topics else para(t("No topic detail published: define the topics before teaching.", "لا توجد تفاصيل منشورة: حدّد المواضيع قبل التدريس.")),
            para(t("Practical exercises", "التمارين العملية")), bullets([e.text for e in m.exercises]),
            para(t(f"Assessment: {m.assessment}.", f"التقييم: {m.assessment}.")),
            para(t("Instructor notes: ______________________________", "ملاحظات المدرب: ______________________________"))]))
    if cur.capstone:
        mh = _milestone_hours(cur.capstone.hours)
        sections.append(Section(heading=t("Supervising the capstone", "الإشراف على مشروع التخرج"), blocks=[
            para(cur.capstone.description.text),
            table([t("Milestone", "المرحلة"), t("Suggested hours", "الساعات المقترحة"), t("Evidence", "الدليل")], [
                [t("Proposal approved", "اعتماد الفكرة"), _h(mh[0]), t("Short written proposal", "مقترح مكتوب موجز")],
                [t("Design review", "مراجعة التصميم"), _h(mh[1]), t("Architecture or test-plan diagram", "مخطط البنية أو خطة الاختبار")],
                [t("Implementation checkpoint", "نقطة متابعة التنفيذ"), _h(mh[2]), t("Working increment in the repository", "نسخة تعمل في المستودع")],
                [t("Final demo", "العرض النهائي"), _h(mh[3]), t("Demo and deliverables", "عرض وتسليمات")]])]))
    sections += [
        Section(heading=t("Assessment administration", "إدارة التقييم"), blocks=[_assessment_table(cur, t)]),
        *([Section(heading=t("Open items for the academy", "نقاط مفتوحة للأكاديمية"), blocks=[bullets(cur.notices)])] if cur.notices else []),
    ]
    return sections


# ====================================================================== 6. presentation outline
def presentation_outline(c: DocContext, t: Translator, extras: dict) -> list[Section]:
    cur = c.curriculum
    slides: list[tuple[str, list[str], str]] = [
        (cur.title, [t("Andalusia Academy", "أكاديمية الأندلس"), _audience(c, t)], t("Welcome and introductions.", "الترحيب والتعارف.")),
        (t("Agenda", "الأجندة"), [f"{m.number}. {m.title}" for m in cur.modules][:12], t("Walk through the modules.", "استعراض الوحدات.")),
        (t("Course objectives", "أهداف المقرر"), [o.text for o in cur.objectives], t("Explain what learners will be able to do.", "وضّح ما سيتمكن المتعلمون من فعله.")),
        (t("Learning outcomes", "مخرجات التعلم"), [o.text for o in cur.learning_outcomes][:8], t("Link outcomes to the capstone.", "اربط المخرجات بمشروع التخرج.")),
    ]
    for m in cur.modules:
        points = [short(x.text) for x in m.topics[:6]] or [m.title]
        points.append(t("Lab: ", "تطبيق: ") + short(m.exercises[0].text.removeprefix("Hands-on lab: ")) if m.exercises else t("Lab: to be defined", "التطبيق: يُحدد لاحقاً"))
        slides.append((f"{m.number}. {m.title}", points, t(f"Allow about {_h(m.hours)} hours for this module.", f"خصص نحو {_h(m.hours)} ساعة لهذه الوحدة.")))
    if cur.capstone:
        slides.append((t("Capstone project", "مشروع التخرج"), [cur.capstone.description.text, *[d.text for d in cur.capstone.deliverables][:4]],
                       t(f"Time allocated: {_h(cur.capstone.hours)} hours.", f"الوقت المخصص: {_h(cur.capstone.hours)} ساعة.")))
    slides += [(t("How you will be assessed", "كيف سيتم التقييم"), [f"{a.name}: {a.weight_percent:g}%" for a in cur.assessments], t("State the pass criteria set by the academy.", "اذكر معايير النجاح التي تحددها الأكاديمية.")),
               (t("Questions and next steps", "الأسئلة والخطوات التالية"), [t("Questions?", "أي أسئلة؟")], t("Share contact and schedule details.", "شارك بيانات التواصل والجدول."))]
    return [Section(heading=t("About this outline", "عن هذا المخطط"), blocks=[para(t(
        f"{len(slides)} slides. This is an outline: build the slides from it in your presentation tool.",
        f"{len(slides)} شريحة. هذا مخطط عام: ابنِ الشرائح منه في برنامج العرض."))])] + [
        Section(heading=t(f"Slide {i}: {title}", f"الشريحة {i}: {title}"), blocks=[bullets(points), para(t("Speaker notes: ", "ملاحظات المحاضر: ") + notes)])
        for i, (title, points, notes) in enumerate(slides, 1)]


# ====================================================================== 7. question bank
_Q_EN = ("Explain {x} and why it matters in practice.", "Describe a realistic scenario where you would apply {x}. Which steps would you follow?",
         "Compare two approaches to {x} and justify your choice.", "What are the main risks or limitations when using {x}?")
_Q_AR = ("اشرح {x} ولماذا يهم في التطبيق العملي.", "صف موقفاً واقعياً تطبق فيه {x}. ما الخطوات التي ستتبعها؟",
         "قارن بين أسلوبين في {x} وبرر اختيارك.", "ما أبرز المخاطر أو القيود عند استخدام {x}؟")


def question_bank(c: DocContext, t: Translator, extras: dict) -> list[Section]:
    cur = c.curriculum
    head = [t("No.", "الرقم"), t("Question", "السؤال"), t("Type", "النوع"), t("Marks", "الدرجة"), t("Answer guidance", "إرشادات الإجابة")]
    sections = [Section(heading=t("How to use this bank", "طريقة استخدام البنك"), blocks=[
        para(t("Questions are generated from the course topics. Answer guidance quotes the academy's own topic wording; the instructor must complete model answers before use.",
               "الأسئلة مولّدة من مواضيع المقرر. تقتبس الإرشادات صياغة الأكاديمية نفسها، ويجب على المدرب استكمال الإجابات النموذجية قبل الاستخدام."))])]
    total = 0
    for m in cur.modules:
        rows = []
        for i, topic in enumerate(m.topics[:4]):
            name, _, detail = topic.text.partition(" — ")
            q = (t(_Q_EN[i % 4], _Q_AR[i % 4])).format(x=short(name))
            rows.append([f"M{m.number}-Q{len(rows) + 1}", q, t("Short answer", "إجابة قصيرة"), "5",
                         t("Look for: ", "ابحث عن: ") + detail if detail else t("Instructor to define the marking points.", "يحدد المدرب نقاط التصحيح.")])
        if m.exercises:
            task = m.exercises[0].text.removeprefix("Hands-on lab: ")
            rows.append([f"M{m.number}-Q{len(rows) + 1}", t(f"Practical task: {task}. Submit evidence (output, screenshots or repository link).",
                                                           f"مهمة عملية: {task}. قدّم دليلاً (المخرجات أو لقطات الشاشة أو رابط المستودع)."),
                         t("Practical", "عملي"), "10", t("Mark with the practical-lab rubric.", "يُقيَّم بمعيار التطبيق العملي.")])
        if not rows:
            rows.append([f"M{m.number}-Q1", t(f"Explain the main ideas of {m.title}.", f"اشرح الأفكار الرئيسية في {m.title}."), t("Short answer", "إجابة قصيرة"), "5", t("Instructor to define the marking points.", "يحدد المدرب نقاط التصحيح.")])
        total += sum(int(r[3]) for r in rows)
        sections.append(Section(heading=t(f"Module {m.number}: {m.title}", f"الوحدة {m.number}: {m.title}"), blocks=[table(head, rows)]))
    mcq = extras.get("mcq") or []
    if mcq:
        rows = [[f"MCQ-{i}", f"{q['question']}\n" + "\n".join(f"{'ABCD'[j]}. {o}" for j, o in enumerate(q["options"])), t("Multiple choice", "اختيار من متعدد"), "1", "ABCD"[q["answer"]]]
                for i, q in enumerate(mcq, 1)]
        total += len(rows)
        sections.append(Section(heading=t("Multiple-choice questions (AI-generated)", "أسئلة الاختيار من متعدد (مولّدة بالذكاء الاصطناعي)"), blocks=[
            note(t("These questions and answer keys were written by an AI model. An instructor must verify every answer before use.",
                   "كُتبت هذه الأسئلة ومفاتيح الإجابة بواسطة نموذج ذكاء اصطناعي. يجب على المدرب التحقق من كل إجابة قبل الاستخدام.")),
            table([t("No.", "الرقم"), t("Question and options", "السؤال والخيارات"), t("Type", "النوع"), t("Marks", "الدرجة"), t("Key", "المفتاح")], rows)]))
    sections.insert(1, Section(heading=t("Summary", "ملخص"), blocks=[para(t(f"Total marks in the bank: {total}.", f"إجمالي الدرجات في البنك: {total}."))]))
    return sections


# ====================================================================== 8. assessment rubrics
def _levels(t: Translator):
    return [t("Excellent (4)", "ممتاز (4)"), t("Good (3)", "جيد (3)"), t("Satisfactory (2)", "مقبول (2)"), t("Needs improvement (1)", "يحتاج تحسيناً (1)")]


def assessment_rubrics(c: DocContext, t: Translator, extras: dict) -> list[Section]:
    cur = c.curriculum
    tools = ", ".join(cur.tools[:4]) or t("the course tools", "أدوات المقرر")
    deliverables = "; ".join(d.text for d in (cur.capstone.deliverables if cur.capstone else [])[:3]) or t("the required deliverables", "التسليمات المطلوبة")
    head = [t("Criterion", "المعيار"), t("Weight", "الوزن"), *_levels(t)]
    cap = [
        (t("Technical implementation", "التنفيذ الفني"), 30, t("Works fully and meets every requirement", "يعمل بالكامل ويحقق كل المتطلبات"), t("Works with minor gaps", "يعمل مع ثغرات بسيطة"), t("Works partly", "يعمل جزئياً"), t("Does not work", "لا يعمل")),
        (t(f"Use of tools ({tools})", f"استخدام الأدوات ({tools})"), 15, t("Tools used correctly and appropriately", "أدوات مستخدمة بدقة وبشكل مناسب"), t("Mostly correct use", "استخدام صحيح في الغالب"), t("Basic use only", "استخدام أساسي فقط"), t("Tools misused or missing", "أدوات مستخدمة بشكل خاطئ أو غائبة")),
        (t("Testing, quality and reliability", "الاختبار والجودة والموثوقية"), 15, t("Thorough tests; robust behaviour", "اختبارات شاملة وسلوك متين"), t("Good tests; few weaknesses", "اختبارات جيدة ونقاط ضعف قليلة"), t("Some tests", "اختبارات محدودة"), t("No meaningful tests", "لا اختبارات ذات قيمة")),
        (t("Documentation and organisation", "التوثيق والتنظيم"), 15, t("Clear, complete, well organised", "واضح وكامل ومنظم"), t("Clear with small gaps", "واضح مع نواقص بسيطة"), t("Incomplete or hard to follow", "ناقص أو يصعب تتبعه"), t("Missing", "غائب")),
        (t(f"Deliverables ({deliverables})", f"التسليمات ({deliverables})"), 15, t("All delivered and complete", "كلها مسلَّمة وكاملة"), t("Most delivered", "معظمها مسلَّم"), t("Some delivered", "بعضها مسلَّم"), t("Few or none", "قليلة أو لا شيء")),
        (t("Presentation and communication", "العرض والتواصل"), 10, t("Clear, confident, answers questions well", "واضح وواثق ويجيب بدقة"), t("Clear with minor issues", "واضح مع ملاحظات بسيطة"), t("Understandable but weak", "مفهوم لكنه ضعيف"), t("Unclear", "غير واضح"))]
    lab = [
        (t("Task completion", "إنجاز المهمة"), 40, t("Complete and correct", "كامل وصحيح"), t("Mostly complete", "شبه كامل"), t("Partly complete", "جزئي"), t("Not attempted or wrong", "لم يُنفّذ أو خاطئ")),
        (t("Correct use of tools", "الاستخدام الصحيح للأدوات"), 25, t("Fluent and correct", "متمكن وصحيح"), t("Minor mistakes", "أخطاء بسيطة"), t("Needs frequent help", "يحتاج مساعدة متكررة"), t("Cannot use the tools", "لا يستطيع استخدام الأدوات")),
        (t("Understanding and explanation", "الفهم والشرح"), 20, t("Explains choices clearly", "يشرح اختياراته بوضوح"), t("Explains most choices", "يشرح معظم اختياراته"), t("Limited explanation", "شرح محدود"), t("Cannot explain", "لا يستطيع الشرح")),
        (t("Timeliness and teamwork", "الالتزام بالوقت والتعاون"), 15, t("On time; helps others", "في الوقت ويساعد الآخرين"), t("On time", "في الوقت"), t("Slightly late", "تأخر قليلاً"), t("Late or absent", "متأخر أو غائب"))]
    return [
        Section(heading=t("How to use these rubrics", "طريقة استخدام معايير التقييم"), blocks=[
            para(t("Score each criterion from 1 to 4, multiply by the weight and divide by 4 to get the criterion's share of 100.",
                   "قيّم كل معيار من 1 إلى 4، واضرب في الوزن ثم اقسم على 4 لتحصل على نصيب المعيار من 100.")),
            note(t("The performance descriptors are a template proposal for the instructor to adapt. The pass mark is set by the academy.",
                   "أوصاف الأداء مقترح قياسي يعدّله المدرب. تحدد الأكاديمية درجة النجاح."))]),
        Section(heading=t("Rubric A: Capstone project", "المعيار أ: مشروع التخرج"), blocks=[table(head, [[n, f"{w}%", *d] for n, w, *d in cap])]),
        Section(heading=t("Rubric B: Practical lab", "المعيار ب: التطبيق العملي"), blocks=[table(head, [[n, f"{w}%", *d] for n, w, *d in lab])]),
        Section(heading=t("Where the rubrics apply", "أين تُطبق المعايير"), blocks=[_assessment_table(cur, t)]),
    ]


# ====================================================================== 9. capstone brief
def capstone_brief(c: DocContext, t: Translator, extras: dict) -> list[Section]:
    cur = c.curriculum
    if not cur.capstone:
        return [Section(heading=t("Capstone project", "مشروع التخرج"), blocks=[para(t("This curriculum defines no capstone project.", "لا يحدد هذا المنهج مشروع تخرج."))])]
    h = cur.capstone.hours
    shares = [(t("Proposal approved", "اعتماد الفكرة"), t("Short written proposal", "مقترح مكتوب موجز")),
              (t("Design review", "مراجعة التصميم"), t("Design or test-plan diagram", "مخطط التصميم أو خطة الاختبار")),
              (t("Implementation checkpoint", "نقطة متابعة التنفيذ"), t("Working increment in the repository", "نسخة تعمل في المستودع")),
              (t("Final demo and submission", "العرض النهائي والتسليم"), t("Demo, deliverables and documentation", "العرض والتسليمات والتوثيق"))]
    hours = _milestone_hours(h)
    weights = [a for a in cur.assessments if a.kind in ("project", "presentation")]
    return [
        Section(heading=t("Project overview", "نظرة على المشروع"), blocks=[para(cur.capstone.description.text), para(t(f"Course: {cur.title}.", f"المقرر: {cur.title}."))]),
        Section(heading=t("Learning goals", "أهداف التعلم"), blocks=[bullets([f"{o.id}: {o.text}" for o in cur.learning_outcomes][:8])]),
        Section(heading=t("Requirements and deliverables", "المتطلبات والتسليمات"), blocks=[
            bullets([d.text for d in cur.capstone.deliverables]) if cur.capstone.deliverables else para(t("Deliverables are to be defined by the instructor.", "يحدد المدرب التسليمات.")),
            para(t("Work individually or in teams as your instructor decides.", "اعمل فردياً أو ضمن فريق حسب قرار المدرب."))]),
        Section(heading=t("Technologies", "التقنيات"), blocks=[bullets(cur.tools) if cur.tools else para(t("Not specified.", "غير محددة."))]),
        Section(heading=t("Timeline and milestones", "الجدول الزمني والمراحل"), blocks=[
            para(t(f"Total time allocated: {_h(h)} hours (suggested split, adjustable).", f"إجمالي الوقت المخصص: {_h(h)} ساعة (توزيع مقترح قابل للتعديل).")),
            table([t("Milestone", "المرحلة"), t("Hours", "الساعات"), t("Evidence", "الدليل")], [[n, _h(x), e] for (n, e), x in zip(shares, hours)])]),
        Section(heading=t("How it is assessed", "كيف يتم التقييم"), blocks=[
            bullets([f"{a.name}: {a.weight_percent:g}%" for a in weights]) if weights else para(t("See the assessment plan.", "راجع خطة التقييم.")),
            para(t("Use the capstone rubric for detailed criteria.", "استخدم معيار مشروع التخرج للمعايير التفصيلية."))]),
        Section(heading=t("Submission checklist", "قائمة التسليم"), blocks=[bullets([
            t("All deliverables submitted by the deadline", "تسليم جميع المخرجات قبل الموعد"), t("Repository or files organised with a README", "تنظيم المستودع أو الملفات مع ملف تعريفي"),
            t("Work runs as described", "العمل يعمل كما هو موصوف"), t("Demo prepared", "تجهيز العرض")])]),
        Section(heading=t("Academic integrity", "النزاهة الأكاديمية"), blocks=[para(t(
            "Submit your own work, credit every source and tool you used, and disclose any AI assistance.",
            "قدّم عملك الخاص، واذكر كل مصدر وأداة استخدمتها، وصرّح بأي مساعدة من الذكاء الاصطناعي."))]),
    ]


# ====================================================================== 10. learning outcomes and assessment plan
def learning_outcomes_plan(c: DocContext, t: Translator, extras: dict) -> list[Section]:
    cur = c.curriculum
    yes = "✓"
    rows = []
    for o in cur.learning_outcomes:
        mapped = bool(o.module_numbers)
        mods = ", ".join(map(str, o.module_numbers)) if mapped else "—"
        rows.append([o.id, o.text, mods, yes if mapped else "—", yes if mapped else "—", yes])
    unmapped = [o.id for o in cur.learning_outcomes if not o.module_numbers]
    checks = [
        (t("Every outcome is linked to a module", "كل مخرج مرتبط بوحدة"), not unmapped),
        (t("Assessment weights add up to 100%", "أوزان التقييم تساوي 100%"), abs(sum(a.weight_percent for a in cur.assessments) - 100) < 0.01),
        (t("Module and capstone hours equal the total", "ساعات الوحدات والمشروع تساوي الإجمالي"), abs(cur.allocated_hours - (cur.total_hours or 0)) < 0.01),
        (t("The capstone assesses every outcome", "مشروع التخرج يقيس كل المخرجات"), bool(cur.capstone))]
    return [
        Section(heading=t("Learning outcomes and where they are taught and assessed", "مخرجات التعلم وأين تُدرَّس وتُقاس"), blocks=[
            table([t("ID", "الرمز"), t("Learning outcome", "مخرج التعلم"), t("Modules", "الوحدات"), t("Quiz", "اختبار"), t("Lab", "تطبيق"), t("Capstone", "المشروع")], rows),
            *([note(t(f"Not linked to a module yet: {', '.join(unmapped)}. A reviewer should link them.", f"غير مرتبطة بوحدة بعد: {', '.join(unmapped)}. على المراجع ربطها."))] if unmapped else [])]),
        Section(heading=t("Assessment plan", "خطة التقييم"), blocks=[_assessment_table(cur, t)]),
        Section(heading=t("Assessment by module", "التقييم حسب الوحدة"), blocks=[table(
            [t("Module", "الوحدة"), t("Hours", "الساعات"), t("Assessment", "التقييم"), t("Outcomes", "المخرجات")],
            [[f"{m.number}. {m.title}", _h(m.hours), m.assessment, ", ".join(o.id for o in cur.learning_outcomes if m.number in o.module_numbers) or "—"] for m in cur.modules])]),
        Section(heading=t("Quality checks", "فحوص الجودة"), blocks=[table([t("Check", "الفحص"), t("Result", "النتيجة")],
                                                                        [[name, t("Pass", "ناجح") if ok else t("Needs attention", "يحتاج مراجعة")] for name, ok in checks])]),
    ]


# ====================================================================== registry
BUILDERS: dict[DocType, Callable[[DocContext, Translator, dict], list[Section]]] = {
    DocType.COURSE_DESCRIPTION: course_description, DocType.COURSE_PROPOSAL: course_proposal,
    DocType.CURRICULUM_PDF: curriculum_pdf, DocType.PARTICIPANT_GUIDE: participant_guide,
    DocType.INSTRUCTOR_GUIDE: instructor_guide, DocType.PRESENTATION_OUTLINE: presentation_outline,
    DocType.QUESTION_BANK: question_bank, DocType.ASSESSMENT_RUBRICS: assessment_rubrics,
    DocType.CAPSTONE_BRIEF: capstone_brief, DocType.LEARNING_OUTCOMES_PLAN: learning_outcomes_plan,
}


def build_document(doc_type: DocType, ctx: DocContext, extras: dict | None = None) -> DocumentContent:
    t = make_translator(ctx.language)
    cur = ctx.curriculum
    title_name = (cur.title_ar if ctx.language == "ar" and cur.title_ar else cur.title)
    label = doc_type.label_ar if ctx.language == "ar" else doc_type.label
    meta = [(t("Document type", "نوع المستند"), label), (t("Course", "المقرر"), f"{title_name}" + (f" ({cur.course_id})" if cur.course_id else "")),
            (t("Built from", "مبني على"), ctx.basis), (t("Intended audience", "الفئة المستهدفة"), _audience(ctx, t)),
            (t("Template version", "إصدار القالب"), TEMPLATE_VERSION), (t("Generated on", "تاريخ الإنشاء"), ctx.today.isoformat())]
    return DocumentContent(
        title=f"{title_name}: {label}" if ctx.language == "en" else f"{title_name} — {label}",
        subtitle=t("Andalusia Academy · prepared by REX AI", "أكاديمية الأندلس · أعدّه REX AI"),
        language=ctx.language, meta=meta, sections=BUILDERS[doc_type](ctx, t, extras or {}),
        disclaimer=t("Generated by REX AI from the academy's approved data and reviewed by staff before use. Items described as a proposal or template "
                     "are not facts about the course. Fees, schedules and instructors appear only when the academy has published them. "
                     "This document does not state or imply that the course is accredited or certified by any body.",
                     "أُنشئ هذا المستند بواسطة REX AI من بيانات الأكاديمية المعتمدة، ويراجعه الموظفون قبل الاستخدام. البنود الموصوفة بأنها مقترح أو قالب ليست حقائق عن المقرر. "
                     "لا تظهر الرسوم والجداول والمدربون إلا إذا نشرتها الأكاديمية. لا يذكر هذا المستند ولا يوحي بأن المقرر معتمد أو مُصدَّق من أي جهة."))
