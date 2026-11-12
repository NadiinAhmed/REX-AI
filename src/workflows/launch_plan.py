"""Course Launch workflow, step 1: turn ONE staff sentence into a structured, validated plan.

    "Launch a new course 'Cloud Computing' for beginners, 60 hours, fee 4000 EGP, 20% early-bird
     until Dec 15, email it to our leads and ali@example.com"
        -> LaunchPlan(title="Cloud Computing", skill_level="beginner", duration_hours=60,
                      fee_amount=4000, fee_currency="EGP", offer_percent=20,
                      offer_valid_until=2026-12-15, email_leads=True, recipients=["ali@example.com"])

Why deterministic (regex) parsing and not an LLM:
- free and instant, works without an API key;
- every field can be explained and tested ("why did it read 60 as the hours?");
- a wrong guess here would flow into the catalog and an e-mail, so anything unclear is ASKED
  (see `missing`), never guessed.

This module only PARSES. It creates nothing: no curriculum, course, offer or e-mail.
"""
from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel, Field, field_validator

from src.knowledge_base.text_utils import contains_arabic, normalize_arabic
from src.services.email_service import EmailError, parse_recipients

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٫", "0123456789.")

# Title: in quotes, or after "course called/named/titled", or "كورس اسمه".
_QUOTED = re.compile(r"[\"“”'‘’«»]([^\"“”'‘’«»]{3,80})[\"“”'‘’«»]")
_NAMED = re.compile(r"\b(?:course|program|track)\s+(?:called|named|titled)\s+([A-Za-z0-9 &+#.\-]{3,60}?)"
                    r"(?=\s*(?:,|\.|$|\bfor\b|\bwith\b|\bat\b))", re.I)
_NAMED_AR = re.compile(r"(?:كورس|دور[ةه]|برنامج|مسار)\s+(?:جديد[ةه]?\s+)?(?:اسمه|اسمها|بعنوان|باسم)\s+([^،,.\n]{3,60})")

_HOURS = re.compile(r"(\d{1,4}(?:\.\d)?)\s*(?:h\b|hrs?\b|hours?\b|ساعه|ساعات)", re.I)
_LEVELS = {"beginner": "beginner", "beginners": "beginner", "intermediate": "intermediate",
           "advanced": "advanced", "مبتدي": "beginner", "مبتدئين": "beginner", "مبتديين": "beginner",
           "متوسط": "intermediate", "متقدم": "advanced"}

# Fee: "4000 EGP", "fee 4,000 EGP", "4000 جنيه". A number next to "%" is never a fee.
_CURRENCY_WORDS = {"egp": "EGP", "le": "EGP", "جنيه": "EGP", "ج.م": "EGP", "sar": "SAR", "ريال": "SAR",
                   "usd": "USD", "$": "USD", "دولار": "USD", "aed": "AED", "درهم": "AED"}
_FEE = re.compile(r"(\d[\d,]{0,8}(?:\.\d{1,2})?)\s*(egp|le|sar|usd|aed|جنيه|ريال|دولار|درهم)\b"
                  r"|\$\s*(\d[\d,]{0,8})", re.I)

_PERCENT = re.compile(r"(\d{1,2}(?:\.\d)?)\s*(?:%|٪|percent|بالمئه|في المئه)")
_UNTIL = re.compile(normalize_arabic(r"(?:until|till|through|valid to|ends?|حتى|لغاية|لحد)") + r"\s+([^,.\n،]{3,30})", re.I)
_ISO_DATE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_MONTHS = {m: i for i, names in enumerate([
    (), ("jan", "january", "يناير"), ("feb", "february", "فبراير"), ("mar", "march", "مارس"),
    ("apr", "april", "ابريل"), ("may", "مايو"), ("jun", "june", "يونيو"), ("jul", "july", "يوليو"),
    ("aug", "august", "اغسطس"), ("sep", "sept", "september", "سبتمبر"), ("oct", "october", "اكتوبر"),
    ("nov", "november", "نوفمبر"), ("dec", "december", "ديسمبر")]) for m in map(normalize_arabic, names)}
_DAY_MONTH = re.compile(r"(\d{1,2})(?:st|nd|rd|th)?\s+([a-z\u0621-\u064A]+)|([a-z\u0621-\u064A]+)\s+(\d{1,2})(?:st|nd|rd|th)?\b")

# Topics: "covering Linux, Docker and AWS" / "topics: ..." / "المحاور: ...". The list ends at the next field
# (fee, %, e-mail, until, hours) or at ; . or a new line, so "fee 4000 EGP" is never read as a topic.
_TOPICS = re.compile(r"(?:\btopics?|\bcovering|\bcovers|\bmodules?|المحاور|المواضيع|يغطي|تغطي)\s*:?\s*(.+?)"
                     r"(?=[;.\n]|,?\s*\b(?:fee|price|cost|email|e-mail|send|until|for beginners|for intermediate|"
                     r"for advanced)\b|,?\s*\d|\s*[،,]?\s*(?:الرسوم|السعر|خصم|ابعت|ارسل|حتي|ايميل)|$)", re.I)
# Arabic "and" only as a separate word (" و "): an attached و starts many real words (وسائط).
_TOPIC_SPLIT = re.compile(r"\s*(?:,|،|\band\b|(?<=\s)و(?=\s))\s*", re.I)
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_LEADS = re.compile(r"\b(our |the |all )?leads\b|\binterested students\b|"
                    + normalize_arabic(r"(?:ال|لل|ل)(?:عملاء المحتملين|مهتمين|ليدز)"))


class LaunchPlan(BaseModel):
    """Everything one launch sentence asked for. Nothing here is approved or saved yet."""
    title: str | None = Field(default=None, max_length=80)
    topics: list[str] = Field(default_factory=list)          # needed: a NEW course has no catalog content
    skill_level: str | None = None
    duration_hours: float | None = Field(default=None, gt=0, le=2000)
    fee_amount: float | None = Field(default=None, ge=0)
    fee_currency: str | None = None
    offer_percent: float | None = Field(default=None, gt=0, lt=100)
    offer_valid_until: date | None = None
    email_leads: bool = False
    recipients: list[str] = Field(default_factory=list)    # typed addresses (validated)
    language: str = "en"                                     # language of the e-mail and the replies
    problems: list[str] = Field(default_factory=list)        # things that must be fixed before preparing

    @field_validator("title")
    @classmethod
    def _clean_title(cls, v: str | None) -> str | None:
        v = (v or "").strip(" .,'\"")
        return v or None

    @property
    def wants_email(self) -> bool:
        return self.email_leads or bool(self.recipients)

    @property
    def missing(self) -> list[str]:
        """What must be asked before anything is created (empty = ready to prepare)."""
        out = list(self.problems)
        if not self.title:
            out.append("course title (put it in quotes, e.g. \"Cloud Computing\")")
        if not self.topics:
            out.append("main topics (e.g. \"covering Linux, Docker and AWS\")")
        if self.offer_percent is not None and self.offer_valid_until is None:
            out.append("offer end date (e.g. \"until Dec 15\")")
        if (self.fee_amount is None) != (self.fee_currency is None):
            out.append("fee with its currency (e.g. \"4000 EGP\")")
        if not self.wants_email:
            out.append("who receives the e-mail (\"our leads\" and/or e-mail addresses)")
        return out


def parse_launch_request(text: str, today: date | None = None) -> LaunchPlan:
    """Read one staff sentence. Never raises for user mistakes: they are listed in `plan.missing`."""
    today = today or date.today()
    raw = text.translate(_DIGITS)
    norm = normalize_arabic(raw.lower())
    problems: list[str] = []

    fee_amount = fee_currency = None
    fee = _FEE.search(norm)
    if fee:
        number = fee.group(1) or fee.group(3)
        fee_amount = float(number.replace(",", ""))
        fee_currency = "USD" if fee.group(3) else _CURRENCY_WORDS[normalize_arabic(fee.group(2).lower())]

    offer_percent = offer_until = None
    pct = _PERCENT.search(norm)
    if pct:
        offer_percent = float(pct.group(1))
        until = _UNTIL.search(norm)
        if until:
            offer_until = _parse_date(until.group(1), today)
            if offer_until is None:
                problems.append(f"I couldn't read the offer end date \"{until.group(1).strip()}\"; use e.g. 2026-12-15")
            elif offer_until < today:
                problems.append(f"the offer end date {offer_until} is in the past")
        if not 0 < offer_percent < 100:
            problems.append("the discount must be between 1% and 99%")
            offer_percent = None

    recipients: list[str] = []
    typed = _EMAIL.findall(raw)
    if typed:
        try:
            recipients = parse_recipients(typed)
        except EmailError as exc:
            problems.append(str(exc))

    hours = _HOURS.search(norm)
    return LaunchPlan(
        title=_title(raw),
        topics=_topics(_EMAIL.sub(" ", raw)),
        skill_level=_level(norm),
        duration_hours=float(hours.group(1)) if hours else None,
        fee_amount=fee_amount, fee_currency=fee_currency,
        offer_percent=offer_percent, offer_valid_until=offer_until,
        email_leads=bool(_LEADS.search(norm)),
        recipients=recipients,
        language="ar" if contains_arabic(text) else "en",
        problems=problems,
    )


def _topics(raw: str) -> list[str]:
    m = _TOPICS.search(raw.translate(_DIGITS))
    if not m:
        return []
    items = [t.strip(" '\"") for t in _TOPIC_SPLIT.split(m.group(1))]
    return list(dict.fromkeys(t for t in items if 2 <= len(t) <= 80))[:12]


def _level(norm: str) -> str | None:
    """English as a whole word; Arabic also with a prefix (للمبتدئين, والمتقدم)."""
    for word, level in _LEVELS.items():
        prefix = r"(?:ال|لل|ل|و|وال)?" if contains_arabic(word) else ""
        if re.search(rf"(?<![\w\u0621-\u064A]){prefix}{normalize_arabic(word)}(?![\w\u0621-\u064A])", norm):
            return level
    return None


def _title(raw: str) -> str | None:
    without_emails = _EMAIL.sub(" ", raw)
    for pattern in (_QUOTED, _NAMED, _NAMED_AR):
        m = pattern.search(without_emails)
        if m:
            words = m.group(1).split()
            # "الحوسبة السحابية للمبتدئين": the level is a separate field, not part of the name
            while len(words) > 1 and _level(normalize_arabic(words[-1].lower())):
                words.pop()
            return " ".join(words)
    return None


def _parse_date(text: str, today: date) -> date | None:
    """'2026-12-15', 'Dec 15', '15 December', '15 ديسمبر'. A day+month without a year means the
    next time that date comes (Dec 15 asked in November -> this year; Jan 10 -> next year)."""
    text = normalize_arabic(text.lower().strip())
    iso = _ISO_DATE.search(text)
    try:
        if iso:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        m = _DAY_MONTH.search(text)
        if not m:
            return None
        day, month_word = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
        month = _MONTHS.get(normalize_arabic(month_word))
        if month is None:
            return None
        candidate = date(today.year, month, int(day))
        return candidate if candidate >= today else date(today.year + 1, month, int(day))
    except ValueError:                      # e.g. Feb 30
        return None
