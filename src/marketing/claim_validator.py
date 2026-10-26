"""Deterministic fact-checker for marketing copy (English and Arabic). No LLM involved.

It finds CLAIMS in the text (a price, a percent, a date, "certified", "online", ...) and checks
each one against the approved `CourseFacts`. A claim the data does not support is an issue:
  block -> the draft cannot be approved until the text or the data is fixed
  warn  -> shown to the reviewer (e.g. vague superlatives)

Known limit: rules match wording, so a cleverly paraphrased claim can slip through. The offline
evaluation's LLM judge covers part of that gap (docs/marketing_agent.md).
"""
from __future__ import annotations

import re

from src.knowledge_base.text_utils import normalize_arabic
from src.marketing.models import ClaimIssue, CourseFacts

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫", "01234567890123456789.")
_NUM = r"(\d{1,3}(?:[,\s]\d{3})+|\d+(?:\.\d+)?)"

_CURRENCY = r"(?<![a-z])(egp|sar|usd|aed|kwd|eur|l\.e\.?|le|\$|£|€|جنيه|جنيها|ريال|ريالا|دولار|درهم|ج\.م|ر\.س)(?![a-z])"
_MONEY = re.compile(rf"(?:{_CURRENCY}\s?{_NUM})|(?:{_NUM}\s?{_CURRENCY})")
_PERCENT = re.compile(rf"{_NUM}\s?(%|٪|percent\b|بالمائه|بالميه|في الميه)")
_DISCOUNT_WORDS = re.compile(
    r"\b(discount(?:ed)?|special offer|limited[- ]time offer|limited offer|promo ?code|early[- ]bird|"
    r"on sale|half price|free of charge|for free)\b|خصم|تخفيض|عرض خاص|عرض محدود|مجانا|مجاني|ببلاش")
_MONTHS = (r"january|february|march|april|may|june|july|august|september|october|november|december|"
           r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec|"
           r"يناير|فبراير|مارس|ابريل|مايو|يونيو|يوليو|اغسطس|سبتمبر|اكتوبر|نوفمبر|ديسمبر")
_DATE = re.compile(rf"\b\d{{1,2}}\s?(?:{_MONTHS})\b|(?<!\w)(?:{_MONTHS})\s?\d{{1,2}}\b|\b\d{{1,2}}[/-]\d{{1,2}}(?:[/-]\d{{2,4}})?\b")
_START = re.compile(r"\b(starts? on|starting on|start date|begins? on|classes start|enrollment closes|"
                    r"deadline|last day to register|next cohort)\b|يبدا في|تبدا في|موعد البدء|اخر موعد|اخر يوم")
_HOURS = re.compile(rf"{_NUM}\s?(?:-?\s?)(hours?|hrs?|h\b|ساعه|ساعات|ساعة)")
_WEEKS = re.compile(rf"{_NUM}\s?(weeks?|months?|days?|اسابيع|اسبوع|اسبوعا|شهور|اشهر|شهر|ايام|يوم)")
_ONLINE = re.compile(r"\b(online|virtual(?:ly)?|remote(?:ly)?|from home|live sessions? online)\b|اونلاين|عن بعد|اون لاين")
_IN_PERSON = re.compile(r"\b(in[- ]person|on[- ]site|onsite|classroom|on campus|face[- ]to[- ]face)\b|حضوري|في المقر|وجها لوجه")
_HYBRID = re.compile(r"\bhybrid\b|هجين|مدمج")
_CERTIFICATE = re.compile(r"\b(certificates?|certificate of completion)\b|شهاده|شهادات")
_ACCREDITED = re.compile(r"\b(certified|certification|accredit(?:ed|ation)|diplomas?|licensed|internationally recognized)\b|معتمد|اعتماد|دبلوم")
_INSTRUCTOR = re.compile(r"\b(instructors?|trainers?|taught by|teachers?|mentors?|experts? (?:will|who))\b|مدرب|مدربين|محاضر|يقدمه|خبراء")
_NO_PREREQ = re.compile(r"\b(no (?:prior )?experience (?:needed|required)|no prerequisites?|beginners? welcome|"
                        r"anyone can join|from zero|from scratch)\b|بدون خبره|لا يشترط|لا يتطلب|من الصفر|بدون اي خبره")
_FORBIDDEN = re.compile(r"\b(guaranteed? (?:a )?(?:job|employment|placement|income)|job guarantee|100% (?:job|placement|success)|"
                        r"best (?:academy|course|training) in|number one|money[- ]back)\b|#1\b|(?<!\w)no\. ?1\b|"
                        r"ضمان وظيفه|وظيفه مضمونه|نضمن لك|مضمون 100|الافضل في مصر|الافضل في السعوديه|رقم 1|رقم واحد")
_SUPERLATIVE = re.compile(r"\b(best|leading|top-rated|world[- ]class|unmatched)\b|الافضل|الاقوي|الرائد")

_CERTIFICATE_EVIDENCE = re.compile(r"certificate|شهاد")
_ACCREDITED_EVIDENCE = re.compile(r"accredit|diploma|معتمد|اعتماد")


def _norm(text: str) -> str:
    return normalize_arabic(text.lower().translate(_DIGITS))


def _number(raw: str) -> float:
    return float(re.sub(r"[,\s]", "", raw))


class ClaimValidator:
    def __init__(self, tolerance: float = 0.01) -> None:
        self._tol = tolerance

    def validate(self, text: str, facts: CourseFacts) -> list[ClaimIssue]:
        t = _norm(text)
        issues: list[ClaimIssue] = []

        def flag(kind: str, span: str, explanation: str, severity: str = "block") -> None:
            if not any(i.kind == kind and i.span == span for i in issues):
                issues.append(ClaimIssue(kind=kind, span=span.strip(), severity=severity, explanation=explanation))

        self._money(t, facts, flag)
        self._discounts(t, facts, flag)
        self._dates(t, facts, flag)
        self._durations(t, facts, flag)
        self._delivery(t, facts, flag)
        evidence = _norm(facts.all_text())
        if (m := _CERTIFICATE.search(t)) and not _CERTIFICATE_EVIDENCE.search(evidence):
            flag("certification", m.group(0), "No certificate is mentioned in the approved data.")
        for m in _ACCREDITED.finditer(t):
            if m.group(0) in evidence or _ACCREDITED_EVIDENCE.search(evidence):
                continue
            flag("certification", m.group(0),
                 "Accreditation/certified/diploma claims are not supported: the data only supports what it states.")
            break
        if (m := _INSTRUCTOR.search(t)) and not facts.instructor:
            flag("instructor", m.group(0), "Instructor details are not in the approved data.")
        if (m := _NO_PREREQ.search(t)) and facts.skill_level not in ("beginner", "all_levels"):
            flag("prerequisites", m.group(0), "The data does not confirm the course is open to beginners.")
        for m in _FORBIDDEN.finditer(t):
            flag("forbidden_claim", m.group(0), "Guarantees and 'number one' claims are never allowed.")
        for m in _SUPERLATIVE.finditer(t):
            if not _FORBIDDEN.search(m.group(0)):
                flag("superlative", m.group(0), "Vague superlative: make sure the reviewer is comfortable with it.", "warn")
        return issues

    # ---------------------------------------------------------------- rules
    def _allowed_prices(self, facts: CourseFacts) -> set[float]:
        prices: set[float] = set()
        if facts.fee_amount is not None:
            prices.add(facts.fee_amount)
        for o in facts.offers:
            if o.price_amount is not None:
                prices.add(o.price_amount)
            if o.discount_percent is not None and facts.fee_amount is not None:
                prices.add(round(facts.fee_amount * (1 - o.discount_percent / 100), 2))
        return prices

    def _close(self, value: float, allowed: set[float]) -> bool:
        return any(abs(value - a) <= max(self._tol * a, 0.5) for a in allowed)

    def _money(self, t: str, facts: CourseFacts, flag) -> None:
        allowed = self._allowed_prices(facts)
        for m in _MONEY.finditer(t):
            raw = next(g for g in m.groups() if g and re.match(r"\d", g))
            if not allowed:
                flag("price", m.group(0), "No approved fee or price offer exists for this course.")
            elif not self._close(_number(raw), allowed):
                flag("price", m.group(0), "This amount does not match the approved fee or an active offer.")

    def _discounts(self, t: str, facts: CourseFacts, flag) -> None:
        percents = {o.discount_percent for o in facts.offers if o.discount_percent is not None}
        for m in _PERCENT.finditer(t):
            value = _number(m.group(1))
            if not any(abs(value - p) < 0.01 for p in percents):
                flag("discount", m.group(0), "This percentage does not match an approved, active offer.")
        if not facts.offers:
            for m in _DISCOUNT_WORDS.finditer(t):
                flag("offer", m.group(0), "No approved, active offer exists: do not mention discounts or promotions.")

    def _dates(self, t: str, facts: CourseFacts, flag) -> None:
        for o in facts.offers:                 # an approved offer's own dates are verified facts
            for d in (o.valid_from, o.valid_until):
                t = t.replace(d.isoformat(), " ")
        for regex in (_DATE, _START):
            for m in regex.finditer(t):
                if facts.schedule or facts.offers:
                    flag("schedule", m.group(0), "Check that this date matches the approved schedule/offer.", "warn")
                else:
                    flag("schedule", m.group(0), "No schedule or dates exist in the approved data.")

    def _durations(self, t: str, facts: CourseFacts, flag) -> None:
        for m in _HOURS.finditer(t):
            value = _number(m.group(1))
            if facts.duration_hours is None or abs(value - facts.duration_hours) > 0.01:
                flag("duration", m.group(0), "This number of hours does not match the approved duration.")
        duration_text = _norm(facts.duration_text or "")
        for m in _WEEKS.finditer(t):
            if m.group(1) not in duration_text:
                flag("duration", m.group(0), "This duration is not in the approved data.")

    def _delivery(self, t: str, facts: CourseFacts, flag) -> None:
        fmt = facts.delivery_format
        checks = ((_ONLINE, {"online", "hybrid"}), (_IN_PERSON, {"in_person", "hybrid"}), (_HYBRID, {"hybrid"}))
        for regex, ok in checks:
            if (m := regex.search(t)) and fmt not in ok:
                why = "The delivery format is unknown." if fmt is None else f"The approved format is '{fmt}'."
                flag("delivery_format", m.group(0), why)


BLOCKING = "block"


def has_blocking(issues: list[ClaimIssue]) -> bool:
    return any(i.severity == BLOCKING for i in issues)
