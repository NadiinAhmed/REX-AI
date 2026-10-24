"""Turn a RawTender into a clean Opportunity: dates, country, categories, language.

All rules are deterministic (no LLM), bilingual (Arabic + English) and unit-tested, so the
Egypt/Saudi filter can never be talked out of its decision.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime

from src.knowledge_base.text_utils import contains_arabic, normalize_arabic
from src.rfp.models import Category, Country, Opportunity, RawTender

# ----------------------------------------------------------------------------- text
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def normalize_text(text: str | None) -> str:
    """Lowercase, Arabic-normalized, digits unified, punctuation collapsed to single spaces."""
    text = normalize_arabic((text or "").lower()).translate(_ARABIC_DIGITS)
    return re.sub(r"[^\w؀-ۿ]+", " ", text).strip()


def _has_term(norm_text: str, terms: tuple[str, ...]) -> bool:
    """Does the (already normalized) text contain any of the terms?

    English: whole words / phrases only, so "it" never matches inside "with".
    Arabic: substring match, because Arabic glues prefixes onto words (ال / و / ب / لل).
    """
    padded = f" {norm_text} "
    for term in terms:
        t = normalize_text(term)
        if contains_arabic(term):
            if t in norm_text:
                return True
        elif f" {t} " in padded:
            return True
    return False


# ----------------------------------------------------------------------------- dates
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_AR_MONTHS = {"يناير": 1, "فبراير": 2, "مارس": 3, "ابريل": 4, "مايو": 5, "يونيو": 6, "يوليو": 7,
              "اغسطس": 8, "سبتمبر": 9, "اكتوبر": 10, "نوفمبر": 11, "ديسمبر": 12}


def parse_date(value: str | None) -> date | None:
    """Parse the date formats tender portals use. Day-first for dd/mm/yyyy (Egypt/Saudi usage).

    Returns None when the date is missing or not understood (e.g. a Hijri date): we never guess.
    """
    if not value:
        return None
    text = normalize_arabic(value.strip().lower()).translate(_ARABIC_DIGITS)
    if re.search(r"هـ|\bah\b|hijri|هجري", text):    # Hijri calendar: not converted in the MVP
        return None
    try:
        return datetime.fromisoformat(text[:10]).date()                     # 2026-11-15
    except ValueError:
        pass
    m = re.search(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})\b", text)        # 15/11/2026
    if m:
        d, mo, y = map(int, m.groups())
        return _safe_date(y, mo, d)
    m = re.search(r"\b(\d{4})[/.](\d{1,2})[/.](\d{1,2})\b", text)            # 2026/11/15
    if m:
        y, mo, d = map(int, m.groups())
        return _safe_date(y, mo, d)
    month = next((n for name, n in _AR_MONTHS.items() if normalize_arabic(name) in text), None)
    if month is None:
        month = next((n for name, n in _MONTHS.items() if re.search(rf"\b{name}[a-z]*\b", text)), None)
    nums = [int(n) for n in re.findall(r"\d+", text)]
    year = next((n for n in nums if 2000 <= n <= 2100), None)
    day = next((n for n in nums if 1 <= n <= 31), None)
    if month and year and day:                                               # 15 Nov 2026 / Nov 15, 2026
        return _safe_date(year, month, day)
    return None


def _safe_date(y: int, mo: int, d: int) -> date | None:
    if not 2000 <= y <= 2100:      # e.g. a Hijri year (1448): not converted, so unknown
        return None
    try:
        return date(y, mo, d)
    except ValueError:
        return None


# ----------------------------------------------------------------------------- country
_COUNTRY_NAMES = {
    Country.EGYPT: ("egypt", "eg", "arab republic of egypt", "مصر", "جمهورية مصر العربية", "egyptian"),
    Country.SAUDI_ARABIA: ("saudi arabia", "ksa", "sa", "kingdom of saudi arabia", "saudi",
                           "السعودية", "المملكة العربية السعودية", "المملكه"),
}
# Words that point to a market inside free text (country names + main cities).
_TEXT_HINTS = {
    Country.EGYPT: ("egypt", "egyptian", "cairo", "giza", "alexandria", "new administrative capital",
                    "mansoura", "assiut", "مصر", "المصرية", "القاهرة", "الجيزة", "الاسكندرية",
                    "العاصمة الادارية", "المنصورة", "اسيوط"),
    Country.SAUDI_ARABIA: ("saudi", "ksa", "riyadh", "jeddah", "dammam", "makkah", "mecca", "madinah",
                           "medina", "khobar", "السعودية", "الرياض", "جدة", "الدمام", "مكة",
                           "المدينة المنورة", "الخبر"),
}
_DOMAIN_HINTS = {Country.EGYPT: (".gov.eg", ".edu.eg", ".org.eg", ".eg/"),
                 Country.SAUDI_ARABIA: (".gov.sa", ".edu.sa", ".org.sa", ".sa/")}


def detect_country(raw: RawTender) -> Country:
    """Strict market rule. Explicit country field wins; otherwise infer from cities and web domains.

    A listing that mentions BOTH markets, or neither, is UNKNOWN (excluded until a person checks).
    """
    if raw.country and raw.country.strip():
        norm = normalize_text(raw.country)
        for country, names in _COUNTRY_NAMES.items():
            if norm in {normalize_text(n) for n in names}:
                return country
        return Country.OTHER
    text = normalize_text(" ".join(filter(None, [raw.title, raw.organization, raw.description])))
    link = (raw.link or "").lower() + "/"
    found = {c for c in (Country.EGYPT, Country.SAUDI_ARABIA)
             if _has_term(text, _TEXT_HINTS[c]) or any(h in link for h in _DOMAIN_HINTS[c])}
    return found.pop() if len(found) == 1 else Country.UNKNOWN


# ----------------------------------------------------------------------------- categories
CATEGORY_TERMS: dict[Category, tuple[str, ...]] = {
    Category.EDUCATION_TRAINING: ("training", "training program", "training programme", "education",
                                  "capacity building", "courses", "workshops", "bootcamp",
                                  "تدريب", "التدريب", "تعليم", "بناء القدرات", "دورات", "برنامج تدريبي"),
    Category.TECHNICAL_TRAINING: ("technical training", "vocational", "tvet", "technical skills",
                                  "hands on", "تدريب فني", "تدريب مهني", "المهارات الفنية"),
    Category.PROFESSIONAL_DEVELOPMENT: ("professional development", "upskilling", "reskilling",
                                        "leadership", "certification", "تطوير مهني", "تأهيل", "رفع كفاءة",
                                        "تطوير المهارات"),
    Category.DIGITAL_TRANSFORMATION: ("digital transformation", "digitalization", "digitization",
                                      "e government", "التحول الرقمي", "رقمنة"),
    Category.SOFTWARE_IT_TRAINING: ("software", "programming", "it", "information technology", "data",
                                    "artificial intelligence", "ai", "machine learning", "cybersecurity",
                                    "cloud", "web development", "mobile", "software testing",
                                    "برمجة", "البرمجيات", "تقنية المعلومات", "تكنولوجيا المعلومات",
                                    "الذكاء الاصطناعي", "البيانات", "الامن السيبراني"),
    Category.CORPORATE_LEARNING: ("corporate training", "employee training", "staff training", "e learning",
                                  "lms", "learning management", "تدريب الموظفين", "تدريب العاملين",
                                  "التعلم الالكتروني"),
}

# A listing must ask for TRAINING. "Supply of IT hardware" mentions IT but is not our business.
TRAINING_SIGNALS: tuple[str, ...] = (
    "training", "train", "trainees", "courses", "course", "education", "educational", "learning",
    "capacity building", "upskilling", "reskilling", "bootcamp", "workshops", "curriculum",
    "professional development", "تدريب", "التدريب", "تدريبية", "تعليم", "تعليمية", "دورات", "تأهيل",
    "بناء القدرات", "تعلم", "رفع كفاءة")


def classify(raw: RawTender) -> tuple[list[Category], bool]:
    """Return (categories, is_training_related)."""
    text = normalize_text(" ".join(filter(None, [raw.title, raw.category, raw.description])))
    cats = [c for c, terms in CATEGORY_TERMS.items() if _has_term(text, terms)]
    return cats, _has_term(text, TRAINING_SIGNALS)


# ----------------------------------------------------------------------------- assemble
def dedup_key(title: str, organization: str | None, country: Country) -> str:
    basis = "|".join([normalize_text(title), normalize_text(organization), country.value])
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def extract(raw: RawTender, *, synthetic: bool = False) -> tuple[Opportunity, bool]:
    """RawTender -> (Opportunity, is_training_related). Pure function: no I/O."""
    country = detect_country(raw)
    categories, training = classify(raw)
    title = re.sub(r"\s+", " ", raw.title).strip()
    opp = Opportunity(
        title=title,
        issuing_organization=(raw.organization or "").strip() or None,
        country=country,
        deadline=parse_date(raw.deadline),
        publication_date=parse_date(raw.published),
        description=raw.description.strip(),
        eligibility=(raw.eligibility or "").strip() or None,
        categories=categories,
        submission_link=(raw.link or "").strip() or None,
        documents=[d.strip() for d in raw.documents if d.strip()],
        source_name=raw.source,
        language="ar" if contains_arabic(title + raw.description) else "en",
        synthetic=synthetic,
        dedup_key=dedup_key(title, raw.organization, country),
    )
    return opp, training