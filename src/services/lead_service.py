"""Lead capture with explicit consent.

Contact details are stored ONLY after the learner agreed to a specific consent text. The
consent version is saved with the lead so the academy can prove what the person agreed to.
Personal data is never written to logs (only the lead id).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from src.db.repositories import LeadRepository
from src.utils.logging_config import get_logger

logger = get_logger("leads")

CONSENT_VERSION = "2026-10-v1"
CONSENT_TEXT = {
    "en": ("To have the academy contact you, I need to save your name, your phone number or email, "
           "the courses you are interested in and what you told me about your goals. The academy will "
           "use them only to follow up with you about its courses. Do you agree? (yes / no)"),
    "ar": ("حتى تتواصل معك الأكاديمية، أحتاج إلى حفظ اسمك ورقم هاتفك أو بريدك الإلكتروني، والدورات التي "
           "تهتم بها وما ذكرته عن أهدافك. ستستخدمها الأكاديمية فقط للتواصل معك بخصوص دوراتها. "
           "هل توافق؟ (نعم / لا)"),
}

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_RE = re.compile(r"\+?\d[\d\s-]{6,16}\d")
_NAME_RE = re.compile(r"(?:my name is|i am|i'm|name\s*[:=])\s*([A-Za-z][A-Za-z .'-]{1,58})"
                      r"|(?:اسمي|انا)\s+([\u0621-\u064A ]{2,40})", re.IGNORECASE)
_FILLER = re.compile(r"\b(and|my|email|e-mail|phone|number|mobile|is|it's|here|contact)\b"
                     r"|ايميلي|رقمي|تليفوني|موبايلي|و\s", re.IGNORECASE)


class LeadValidationError(ValueError):
    pass


@dataclass
class ContactDetails:
    name: str | None = None
    email: str | None = None
    phone: str | None = None

    def missing(self) -> list[str]:
        out = [] if self.name else ["name"]
        if not (self.email or self.phone):
            out.append("phone_or_email")
        return out


def parse_contact_details(text: str) -> ContactDetails:
    details = ContactDetails()
    if m := EMAIL_RE.search(text):
        details.email = m.group(0).lower()
    for m in PHONE_RE.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        if 8 <= len(digits) <= 15:
            details.phone = ("+" if m.group(0).startswith("+") else "") + digits
            break
    if m := _NAME_RE.search(text):
        details.name = (m.group(1) or m.group(2)).strip(" .,-")
    else:  # "Sara Ahmed, sara@x.com" -> what is left after removing contact data
        rest = PHONE_RE.sub(" ", EMAIL_RE.sub(" ", text))
        rest = _FILLER.sub(" ", rest)
        rest = re.sub(r"[^\w\s'-]", " ", rest)
        rest = re.sub(r"\d", " ", rest)
        rest = " ".join(rest.split())
        if 2 <= len(rest) <= 60 and len(rest.split()) <= 5:
            details.name = rest
    return details


class LeadService:
    def __init__(self, repository: LeadRepository) -> None:
        self._repo = repository

    def save(self, details: ContactDetails, *, language: str, profile: dict,
             interested_course_ids: list[str], consent_given: bool) -> str:
        if not consent_given:
            raise LeadValidationError("Consent is required before saving a lead.")
        if details.missing():
            raise LeadValidationError(f"Missing: {', '.join(details.missing())}")
        lead_id = self._repo.create(
            name=details.name, email=details.email, phone=details.phone, language=language,
            profile=profile, interested_course_ids=interested_course_ids,
            consent_given_at=datetime.now(timezone.utc), consent_version=CONSENT_VERSION)
        logger.info("Lead saved id=%s courses=%s", lead_id, interested_course_ids)   # no PII in logs
        return lead_id
