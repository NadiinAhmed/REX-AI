"""Read a campaign request (English or Arabic) into a CampaignBrief. Deterministic: no LLM.

Returns the brief AND the set of fields the message actually mentioned, so the agent can merge
this turn with earlier turns without overwriting what the user said before.
"""
from __future__ import annotations

import re
from collections.abc import Callable

from src.knowledge_base.text_utils import normalize_arabic
from src.marketing.models import CampaignBrief, CampaignObjective, Platform, Tone

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

_PLATFORMS: dict[Platform, str] = {
    Platform.FACEBOOK: r"\b(facebook|fb)\b|فيسبوك|فيس بوك|الفيس",
    Platform.INSTAGRAM: r"\b(instagram|insta|ig)\b|انستجرام|انستغرام|انستا",
    Platform.LINKEDIN: r"\blinked ?in\b|لينكد|لينكدان",
    Platform.EMAIL: r"\b(e-?mails?|newsletter|mailing)\b|بريد|ايميل|رساله بريديه",
    Platform.WHATSAPP: r"\b(whats ?app|wa)\b|واتساب|واتس",
    Platform.SOCIAL_CAPTION: r"\b(captions?|social media posts?|social post)\b|كابشن|بوست|منشور",
}
_ALL_PLATFORMS = r"\b(all (?:platforms|channels)|every platform|multi-?channel)\b|كل المنصات|جميع المنصات"
_OBJECTIVES: dict[CampaignObjective, str] = {
    CampaignObjective.RE_ENGAGEMENT: r"\b(re-?engage\w*|win back|past students|remind\w*)\b|اعاده|تذكير|الطلاب السابقين",
    CampaignObjective.ENROLLMENT: r"\b(enrol\w*|regist\w*|sign ?ups?|sales|sell)\b|تسجيل|التحاق|اشتراك|حجز",
    CampaignObjective.LEAD_GENERATION: r"\b(leads?|lead generation|inquir\w*|enquir\w*|contacts)\b|عملاء (?:ال)?محتملين|استفسار|بيانات التواصل",
    CampaignObjective.AWARENESS: r"\b(awareness|brand|visibility|introduce|announce\w*|launch)\b|توعيه|تعريف|انتشار|اعلان عن|اطلاق",
}
_TONES: dict[Tone, str] = {
    Tone.PROFESSIONAL: r"\bprofessional\b|احترافي|مهني",
    Tone.FORMAL: r"\bformal\b|رسمي",
    Tone.FRIENDLY: r"\b(friendly|casual|warm)\b|ودود|ودي|بسيط",
    Tone.ENTHUSIASTIC: r"\b(enthusiastic|exciting|energetic|fun)\b|حماسي|حماس",
}
_LANG_AR = r"\b(in arabic|arabic)\b|بالعربي|بالعربيه|باللغه العربيه|عربي"
_LANG_EN = r"\b(in english|english)\b|بالانجليزي|بالانجليزيه|باللغه الانجليزيه|انجليزي"
_VARIATIONS = re.compile(r"\b([1-5])\s*(variations?|versions?|options?|drafts?)\b|([1-5])\s*(نسخ|نسخه|صيغ|اختيارات)")
_AUDIENCE = re.compile(r"(?:target(?:ing)? audience|audience|targeting|aimed at)\s*(?:is|:|-)?\s*([^.\n،,]{3,120})"
                       r"|(?:الجمهور المستهدف|الجمهور|موجه(?:ه)? (?:الي|ل))\s*(?::|-)?\s*([^.\n،,]{3,120})")
_BUDGET = re.compile(r"\bbudget\s*(?:is|of|:)?\s*([^.\n،]{1,80})|ميزانيه\s*(?::)?\s*([^.\n،]{1,80})")
_OFFER = re.compile(r"[^.\n]*(?:\d+\s?%|٪|discount|special offer|promo|early.bird|خصم|تخفيض|عرض خاص)[^.\n]*", re.I)
_COURSE_ID = re.compile(r"\b([A-Za-z]{2,5}-\d{3})\b")

CourseResolver = Callable[[str], str | None]


def _norm(text: str) -> str:
    return normalize_arabic(text.lower().translate(_DIGITS))


class BriefParser:
    def __init__(self, resolve_course: CourseResolver) -> None:
        self._resolve_course = resolve_course

    def parse(self, message: str) -> tuple[CampaignBrief, set[str]]:
        t = _norm(message)
        data: dict = {}

        if (m := _COURSE_ID.search(message)) and (cid := self._resolve_course(m.group(1).upper())):
            data["course_id"] = cid
        elif cid := self._resolve_course(message):
            data["course_id"] = cid

        if re.search(_ALL_PLATFORMS, t):
            data["platforms"] = [p for p in Platform if p != Platform.SOCIAL_CAPTION]
        else:
            found = [p for p, rx in _PLATFORMS.items() if re.search(rx, t)]
            if len(found) > 1 and Platform.SOCIAL_CAPTION in found:   # "Instagram caption" = Instagram
                found.remove(Platform.SOCIAL_CAPTION)
            if found:
                data["platforms"] = found

        for objective, rx in _OBJECTIVES.items():            # dict order = priority
            if re.search(rx, t):
                data["objective"] = objective
                break
        for tone, rx in _TONES.items():
            if re.search(rx, t):
                data["tone"] = tone
                break
        if re.search(_LANG_AR, t):
            data["language"] = "ar"
        elif re.search(_LANG_EN, t):
            data["language"] = "en"
        if m := _VARIATIONS.search(t):
            data["variations"] = int(m.group(1) or m.group(3))
        if m := _AUDIENCE.search(message.translate(_DIGITS)):
            data["audience"] = (m.group(1) or m.group(2)).strip()[:300]
        if m := _BUDGET.search(message.translate(_DIGITS)):
            data["budget_note"] = (m.group(1) or m.group(2)).strip()[:200]
        if m := _OFFER.search(message):
            data["requested_offer"] = m.group(0).strip()[:300]

        return CampaignBrief.model_validate(data), set(data)
