"""Deterministic extraction of learner needs from Arabic or English messages.

Only learning-related preferences are kept. Contact details are detected so they can
be refused, but are never stored (lead capture is deferred until consent flow exists).
"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field

from src.knowledge_base.text_utils import STOPWORDS, normalize_arabic, tokenize
from src.models.course import DeliveryFormat, SkillLevel


class Budget(BaseModel):
    amount: float = Field(ge=0)
    currency: str


class LearnerProfile(BaseModel):
    interests: list[str] = Field(default_factory=list)
    career_goal: str | None = None
    background: str | None = None
    skill_level: SkillLevel | None = None
    preferred_format: DeliveryFormat | None = None
    budget: Budget | None = None
    hours_per_week: int | None = None

    def has_topic(self) -> bool:
        return bool(self.interests or self.career_goal)

    def search_query(self) -> str:
        return " ".join(self.interests + ([self.career_goal] if self.career_goal else []))

    def merge(self, other: "LearnerProfile") -> "LearnerProfile":
        """Combine a new turn into the stored profile.

        Preferences (level, format, budget, ...) are updated when the new turn states them.
        The topic (interests + career goal) follows the learner's LATEST topic statement:
        if `other` mentions a topic, it replaces the old one entirely, so "I want cooking"
        after "I want data analysis" searches cooking only.
        """
        data = self.model_dump()
        for key, value in other.model_dump().items():
            if key in ("interests", "career_goal"):
                continue
            if value not in (None, [], ""):
                data[key] = value
        if other.has_topic():
            data["interests"] = other.interests
            data["career_goal"] = other.career_goal
        return LearnerProfile.model_validate(data)


_LEVEL_PATTERNS = [
    (SkillLevel.BEGINNER, r"\b(beginner|new to|no experience|from scratch|basics)\b|مبتدي|بدون خبره|من الصفر|ليس لدي خبره|معنديش خبره"),
    (SkillLevel.INTERMEDIATE, r"\b(intermediate|some experience)\b|متوسط|عندي خبره بسيطه|لدي بعض الخبره"),
    (SkillLevel.ADVANCED, r"\b(advanced|expert|senior)\b|متقدم|خبير"),
]
_FORMAT_PATTERNS = [
    (DeliveryFormat.ONLINE, r"\b(online|remote|virtual)\b|اونلاين|اون لاين|عن بعد"),
    (DeliveryFormat.IN_PERSON, r"\b(in[- ]person|onsite|on-site|classroom|offline)\b|حضوري"),
    (DeliveryFormat.HYBRID, r"\bhybrid\b|مدمج|هجين"),
]
_CURRENCY = r"(egp|le|pounds?|جنيه|sar|riyals?|ريال)"
_BUDGET_RE = re.compile(rf"(\d[\d,\.]*)\s*{_CURRENCY}|{_CURRENCY}\s*(\d[\d,\.]*)")
_HOURS_RE = re.compile(r"(\d{1,2})\s*(hours?|hrs?|ساعات|ساعه)\s*(a|per|every|في|ف|كل)?\s*(week|الاسبوع|اسبوع)")
_GOAL_RE = re.compile(
    r"\b(?:become|be|work as|career as|job as)\s+(?:an?\s+)?([a-z][a-z /-]{2,40})"
    r"|(?:اصبح|اكون|اشتغل|اعمل|وظيفه)\s+(?:ك)?([\u0621-\u064A ]{3,40})"
)
_BACKGROUND_RE = re.compile(
    r"\b(?:i(?:'m| am) an? |graduate of |graduated (?:in|from) |i studied |studying |degree in )([a-z ]{3,40})"
    r"|(?:خريج|خريجه|ادرس|درست|بدرس|طالب|طالبه)\s+([\u0621-\u064A ]{3,40})"
)
_CONTACT_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+|(?:\+?\d[\s-]?){9,}")

# Words that describe preferences rather than topics; removed from interests.
_NON_TOPIC = {
    "beginner", "intermediate", "advanced", "online", "remote", "hybrid", "person", "onsite",
    "budget", "hours", "week", "egp", "sar", "pounds", "riyal", "le", "experience", "expert",
    "basics", "scratch", "new", "no", "some", "per", "senior", "hi", "hello", "thanks",
    "first", "second", "third", "one", "it", "price", "cost", "fee", "fees", "duration",
    "prerequisites", "instructor", "schedule", "content", "modules", "outcomes", "long",
    "الاول", "الثاني", "التاني", "الثالث", "التالت", "سعر", "بكام", "رسوم", "مده", "متطلبات",
    "graduate", "student", "studied", "study", "studying", "email", "phone", "number", "whatsapp", "contact",
    "مبتدي", "متوسط", "متقدم", "اونلاين", "حضوري", "ميزانيتي", "ميزانيه", "جنيه", "ريال",
    "ساعات", "اسبوع", "خبره", "مرحبا", "اهلا", "شكرا", "السلام", "عليكم", "بعد", "عن",
}


def _norm(text: str) -> str:
    return normalize_arabic(text.lower())


def contains_contact_details(text: str) -> bool:
    return bool(_CONTACT_RE.search(text))


def _clean_phrase(phrase: str) -> str:
    """Keep the core of a captured phrase: stop at punctuation, conjunctions,
    or preference words (e.g. 'data analyst online' -> 'data analyst')."""
    phrase = re.split(r"[.,!?؟،]| and | but | with | in | at | و ", phrase)[0]
    words: list[str] = []
    for w in phrase.split():
        if w in _NON_TOPIC or w in STOPWORDS or (w.startswith("و") and len(w) > 3 and words):
            break
        words.append(w)
        if len(words) == 4:
            break
    return " ".join(words)


def extract_profile(message: str) -> LearnerProfile:
    text = _norm(_CONTACT_RE.sub(" ", message))
    profile = LearnerProfile()

    for level, pattern in _LEVEL_PATTERNS:
        if re.search(pattern, text):
            profile.skill_level = level
            break
    for fmt, pattern in _FORMAT_PATTERNS:
        if re.search(pattern, text):
            profile.preferred_format = fmt
            break

    if m := _BUDGET_RE.search(text):
        amount_raw = m.group(1) or m.group(4)
        currency_raw = m.group(2) or m.group(3)
        currency = "SAR" if re.match(r"sar|riyal|ريال", currency_raw) else "EGP"
        try:
            profile.budget = Budget(amount=float(amount_raw.replace(",", "")), currency=currency)
        except ValueError:
            pass
    if m := _HOURS_RE.search(text):
        profile.hours_per_week = int(m.group(1))
    topic_text = _HOURS_RE.sub(" ", _BUDGET_RE.sub(" ", text))
    if m := _BACKGROUND_RE.search(text):
        profile.background = _clean_phrase(m.group(1) or m.group(2)) or None
        if profile.background:  # background describes the past, not what to search for
            topic_text = topic_text.replace(profile.background, " ")
    if m := _GOAL_RE.search(text):
        profile.career_goal = _clean_phrase(m.group(1) or m.group(2)) or None
        if profile.career_goal:  # the goal is added to the query separately
            topic_text = topic_text.replace(profile.career_goal, " ")

    profile.interests = [
        t for t in dict.fromkeys(tokenize(topic_text)) if t not in _NON_TOPIC and not t.isdigit()
    ]
    return profile
