"""Simple campaign strategy (brief requirement 39). Rule-based on purpose: planning which platform
comes first and which angle to use needs no creativity, and rules can never invent facts.
The LLM is used only for writing the copy.
"""
from __future__ import annotations

from src.marketing.models import CampaignBrief, CampaignObjective, CourseFacts, Platform, StrategyPlan
from src.marketing.platforms import spec_for

# For each objective: which platforms usually do the most work first.
_PRIORITY: dict[CampaignObjective, list[Platform]] = {
    CampaignObjective.AWARENESS: [Platform.INSTAGRAM, Platform.FACEBOOK, Platform.SOCIAL_CAPTION, Platform.LINKEDIN,
                                  Platform.WHATSAPP, Platform.EMAIL],
    CampaignObjective.LEAD_GENERATION: [Platform.FACEBOOK, Platform.LINKEDIN, Platform.INSTAGRAM, Platform.SOCIAL_CAPTION,
                                        Platform.EMAIL, Platform.WHATSAPP],
    CampaignObjective.ENROLLMENT: [Platform.EMAIL, Platform.WHATSAPP, Platform.FACEBOOK, Platform.LINKEDIN,
                                   Platform.INSTAGRAM, Platform.SOCIAL_CAPTION],
    CampaignObjective.RE_ENGAGEMENT: [Platform.EMAIL, Platform.WHATSAPP, Platform.LINKEDIN, Platform.FACEBOOK,
                                      Platform.INSTAGRAM, Platform.SOCIAL_CAPTION],
}

# Angle -> the fact it needs. An angle is only used when its fact exists.
_ANGLES: list[tuple[str, str, str, str]] = [   # (key, needed fact, English, Arabic)
    ("career", "career_paths", "Career outcome: the roles this course prepares for",
     "النتيجة المهنية: الوظائف التي يؤهل لها البرنامج"),
    ("project", "capstone", "Hands-on project: what the learner builds", "مشروع عملي: ما سيبنيه المتدرب"),
    ("skills", "tools", "Skills and tools the learner will use", "المهارات والأدوات التي سيستخدمها المتدرب"),
    ("deliverables", "deliverables", "Portfolio: what the learner leaves with", "ملف أعمال: ما يخرج به المتدرب"),
    ("outcomes", "learning_outcomes", "Learning outcomes", "مخرجات التعلم"),
    ("overview", "description", "What the course is about", "فكرة البرنامج"),
]
_PLATFORM_ANGLE: dict[Platform, list[str]] = {
    Platform.LINKEDIN: ["career", "deliverables", "skills"],
    Platform.INSTAGRAM: ["project", "skills", "career"],
    Platform.FACEBOOK: ["career", "project", "overview"],
    Platform.EMAIL: ["outcomes", "skills", "career", "overview"],
    Platform.WHATSAPP: ["career", "overview"],
    Platform.SOCIAL_CAPTION: ["project", "career", "overview"],
}
_SEQUENCE = {
    "en": {CampaignObjective.AWARENESS: ["Phase 1 — introduce the course and the problem it solves",
                                         "Phase 2 — show the hands-on project and skills",
                                         "Phase 3 — invite people to follow and learn more"],
           CampaignObjective.LEAD_GENERATION: ["Phase 1 — hook with the career outcome",
                                               "Phase 2 — course details from the approved data",
                                               "Phase 3 — ask people to request details"],
           CampaignObjective.ENROLLMENT: ["Phase 1 — remind interested people of the career outcome",
                                          "Phase 2 — answer common questions with approved facts",
                                          "Phase 3 — clear call to register"],
           CampaignObjective.RE_ENGAGEMENT: ["Phase 1 — personal reminder of what they were interested in",
                                             "Phase 2 — what they would build and learn",
                                             "Phase 3 — one clear next step"]},
    "ar": {CampaignObjective.AWARENESS: ["المرحلة 1 — تعريف بالبرنامج والمشكلة التي يحلها",
                                         "المرحلة 2 — عرض المشروع العملي والمهارات",
                                         "المرحلة 3 — دعوة للمتابعة ومعرفة المزيد"],
           CampaignObjective.LEAD_GENERATION: ["المرحلة 1 — جذب الانتباه بالنتيجة المهنية",
                                               "المرحلة 2 — تفاصيل البرنامج من البيانات المعتمدة",
                                               "المرحلة 3 — دعوة لطلب التفاصيل"],
           CampaignObjective.ENROLLMENT: ["المرحلة 1 — تذكير المهتمين بالنتيجة المهنية",
                                          "المرحلة 2 — إجابة الأسئلة الشائعة بحقائق معتمدة",
                                          "المرحلة 3 — دعوة واضحة للتسجيل"],
           CampaignObjective.RE_ENGAGEMENT: ["المرحلة 1 — تذكير شخصي بما كانوا مهتمين به",
                                             "المرحلة 2 — ما سيبنونه ويتعلمونه",
                                             "المرحلة 3 — خطوة تالية واحدة وواضحة"]},
}


class StrategyPlanner:
    def plan(self, brief: CampaignBrief, facts: CourseFacts, lang: str) -> StrategyPlan:
        objective = brief.objective or CampaignObjective.AWARENESS
        order = [p for p in _PRIORITY[objective] if p in brief.platforms]
        available = {key: (en, ar) for key, needed, en, ar in _ANGLES if facts.value(needed)}

        def angle_text(key: str) -> str:
            en, ar = available[key]
            return ar if lang == "ar" else en

        angles: dict[str, str] = {}
        for p in order:
            key = next((k for k in _PLATFORM_ANGLE[p] if k in available), None)
            if key:
                angles[p.value] = angle_text(key)
        keys = list(available) or ["overview"]
        variation_angles = [angle_text(keys[i % len(keys)]) if keys[i % len(keys)] in available else ""
                            for i in range(brief.variations)]

        notes: list[str] = []
        t = (lambda en, ar: ar if lang == "ar" else en)
        if order:
            first = spec_for(order[0]).label(lang)
            notes.append(t(f"Lead with {first}: it fits the '{objective.value}' objective best.",
                           f"ابدأ بـ {first}: الأنسب لهدف الحملة."))
        if brief.budget_note:
            notes.append(t(f"Budget: {brief.budget_note}. Put paid spend on {spec_for(order[0]).label_en if order else 'the first platform'}"
                           " and post organically elsewhere.",
                           f"الميزانية: {brief.budget_note}. ركّز الإنفاق المدفوع على المنصة الأولى والنشر المجاني على الباقي."))
        else:
            notes.append(t("No budget given: the plan assumes organic posting.",
                           "لم تُحدَّد ميزانية: الخطة تفترض النشر المجاني."))
        if "fee" in facts.missing_fields:
            notes.append(t("Fee is not in the approved data: CTAs ask people to request details instead of quoting a price.",
                           "الرسوم غير متوفرة في البيانات المعتمدة: الدعوة تطلب التواصل لمعرفة التفاصيل بدلًا من ذكر سعر."))
        if not facts.offers:
            notes.append(t("No approved offer: copy sells the value, not a discount.",
                           "لا يوجد عرض معتمد: المحتوى يركز على القيمة وليس الخصم."))
        return StrategyPlan(platform_order=order, angles=angles, variation_angles=variation_angles,
                            sequence=_SEQUENCE["ar" if lang == "ar" else "en"][objective], notes=notes)
