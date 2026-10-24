"""Agent 2 — Educational RFP & Tender Research (Egypt and Saudi Arabia only).

Chat commands (English or Arabic), all answered from stored, screened opportunities:
    "scan" / "ابحث"               run a new scan of the tender feeds
    "egypt" / "مصر"               Egypt opportunities (flagged for immediate review)
    "saudi" / "السعودية"          Saudi Arabia opportunities (monitored pipeline)
    "RFP-1A2B3C"                  full details of one opportunity
    anything else                 overview + help
The agent never invents tenders: if nothing is stored it says so and suggests a scan.
"""
from __future__ import annotations

import re
from datetime import date
from collections.abc import Callable

from src.agents.base import Agent, AgentRequest, AgentResponse, ResponseMode, SourceReference
from src.db.models import RfpOpportunityRecord
from src.db.rfp_repository import ACTIVE_STATUSES, RfpRepository
from src.knowledge_base.text_utils import contains_arabic, normalize_arabic
from src.rfp.models import Category, Country, ExclusionReason, ScanReport
from src.rfp.pipeline import RfpScanPipeline

_ID_RE = re.compile(r"\brfp-([0-9a-f]{6})\b", re.IGNORECASE)
_SCAN_RE = re.compile(r"\b(scan|search|find|refresh|update|run|check)\b|ابحث|بحث|حدث|تحديث|دور")
_EGYPT_RE = re.compile(r"\b(egypt|egyptian)\b|مصر")
_SAUDI_RE = re.compile(r"\b(saudi|ksa)\b|السعوديه|السعودية")
_MAX_LISTED = 8


class RfpResearchAgent(Agent):
    name = "rfp_research"
    description = "RFP Research: Egypt & Saudi Arabia education and training tenders"

    def __init__(self, pipeline: RfpScanPipeline, repository: RfpRepository,
                 clock: Callable[[], date] = date.today) -> None:
        self._pipeline = pipeline
        self._repo = repository
        self._clock = clock

    def handle(self, request: AgentRequest) -> AgentResponse:
        message = request.message
        lang = "ar" if contains_arabic(message) else "en"
        norm = normalize_arabic(message.lower())

        if m := _ID_RE.search(message):
            return self._detail(f"RFP-{m.group(1).upper()}", lang)
        if _SCAN_RE.search(norm):
            return self._scan(lang)
        if _EGYPT_RE.search(norm):
            return self._list(Country.EGYPT, lang)
        if _SAUDI_RE.search(norm):
            return self._list(Country.SAUDI_ARABIA, lang)
        return self._overview(lang)

    # ------------------------------------------------------------------ intents
    def run_scan(self) -> ScanReport:
        """Used by the dashboard button and by the "scan" command."""
        return self._pipeline.run()

    def _scan(self, lang: str) -> AgentResponse:
        report = self.run_scan()
        lines = [self._t(lang, "**Scan finished.**", "**اكتمل البحث.**"),
                 self._t(lang,
                         f"- Listings read: {report.fetched}\n- New opportunities: {len(report.new)}\n"
                         f"- Already known (refreshed): {len(report.updated)}\n- Excluded: {len(report.excluded)}",
                         f"- عدد الإعلانات: {report.fetched}\n- فرص جديدة: {len(report.new)}\n"
                         f"- فرص معروفة (تم تحديثها): {len(report.updated)}\n- مستبعدة: {len(report.excluded)}")]
        reasons = {r: report.excluded_count(r) for r in ExclusionReason if report.excluded_count(r)}
        if reasons:
            lines.append(self._t(lang, "Excluded because: ", "أسباب الاستبعاد: ") +
                         ", ".join(f"{self._reason(r, lang)} ({n})" for r, n in reasons.items()))
        if report.expired_marked:
            lines.append(self._t(lang, f"{report.expired_marked} stored opportunity(ies) have now expired.",
                                 f"{report.expired_marked} فرصة محفوظة انتهى موعدها."))
        records = [r for r in (self._repo.get(i) for i in report.alerts) if r]
        if records:
            lines.append(self._t(lang, "\n🔔 **New Egypt opportunities: review now**",
                                 "\n🔔 **فرص جديدة في مصر: تحتاج مراجعة الآن**"))
            lines += [self._line(r) for r in records]
        lines += [f"_{n}_" for n in report.source_notices]
        return self._response("\n".join(lines), lang, records, intent="scan",
                              data={"report": report.model_dump(mode="json")})

    def _list(self, country: Country, lang: str) -> AgentResponse:
        records = self._repo.list(country=country.value, statuses=ACTIVE_STATUSES)
        if not records:
            text = self._t(lang, f"No open {country.label} opportunities are stored. Type \"scan\" to search the feeds.",
                           f"لا توجد فرص مفتوحة محفوظة في {self._country_ar(country)}. اكتب \"ابحث\" لتحديث البحث.")
            return self._response(text, lang, [], intent=f"list_{country.value.lower()}")
        head = (self._t(lang, f"**{len(records)} Egypt opportunity(ies), flagged for immediate review:**",
                        f"**{len(records)} فرصة في مصر تحتاج مراجعة فورية:**")
                if country == Country.EGYPT else
                self._t(lang, f"**{len(records)} Saudi Arabia opportunity(ies) in the monitored pipeline:**",
                        f"**{len(records)} فرصة في السعودية قيد المتابعة:**"))
        shown = records[:_MAX_LISTED]
        lines = [head, *(self._line(r) for r in shown)]
        if len(records) > _MAX_LISTED:
            lines.append(self._t(lang, f"…and {len(records) - _MAX_LISTED} more on the RFP dashboard page.",
                                 f"…و{len(records) - _MAX_LISTED} أخرى في صفحة لوحة المناقصات."))
        lines.append(self._t(lang, "Type an ID (e.g. RFP-1A2B3C) for details.", "اكتب رقم الفرصة لعرض التفاصيل."))
        return self._response("\n".join(lines), lang, shown, intent=f"list_{country.value.lower()}")

    def _detail(self, opportunity_id: str, lang: str) -> AgentResponse:
        r = self._repo.get(opportunity_id)
        if r is None:
            return self._response(self._t(lang, f"No opportunity with ID {opportunity_id}.",
                                          f"لا توجد فرصة بالرقم {opportunity_id}."), lang, [], intent="detail")
        na = self._t(lang, "not stated in the listing", "غير مذكور في الإعلان")
        cats = ", ".join(Category(c).label for c in r.categories) or na
        rows = [
            f"**{r.title}** ({r.id})",
            f"- {self._t(lang, 'Issuing organization', 'الجهة')}: {r.issuing_organization or na}",
            f"- {self._t(lang, 'Country', 'الدولة')}: {Country(r.country).label}",
            f"- {self._t(lang, 'Deadline', 'آخر موعد')}: {r.deadline.isoformat() if r.deadline else na}",
            f"- {self._t(lang, 'Published', 'تاريخ النشر')}: {r.publication_date.isoformat() if r.publication_date else na}",
            f"- {self._t(lang, 'Category', 'المجال')}: {cats}",
            f"- {self._t(lang, 'Eligibility', 'شروط التأهل')}: {r.eligibility or na}",
            f"- {self._t(lang, 'Documents', 'المستندات')}: {', '.join(r.documents) or na}",
            f"- {self._t(lang, 'Submission link', 'رابط التقديم')}: {r.submission_link or na}",
            f"- {self._t(lang, 'Status', 'الحالة')}: {r.status.replace('_', ' ')}",
            "",
            f"**{self._t(lang, 'Summary', 'ملخص')}:** {r.summary}",
            "",
            f"**{self._t(lang, 'May the academy qualify?', 'هل يمكن أن تتأهل الأكاديمية؟')}** "
            f"{(r.fit or 'unknown').upper()} — "
            + self._t(lang, "a first assessment from the listing, not a guarantee.",
                      "تقييم مبدئي من نص الإعلان وليس ضماناً."),
            *(f"- {reason}" for reason in r.fit_reasons),
        ]
        if r.synthetic:
            rows.append(self._t(lang, "\n_Demo data: this listing is synthetic, not a real tender._",
                                "\n_بيانات تجريبية: هذا الإعلان ليس مناقصة حقيقية._"))
        return self._response("\n".join(rows), lang, [r], intent="detail", data={"opportunity_id": r.id})

    def _overview(self, lang: str) -> AgentResponse:
        counts = self._repo.counts()
        if not counts:
            text = self._t(lang,
                           "I find education and training tenders in **Egypt** and **Saudi Arabia** only. "
                           "Nothing is stored yet: type **scan** to search the tender feeds.",
                           "أبحث عن مناقصات التعليم والتدريب في **مصر** و**السعودية** فقط. "
                           "لا توجد فرص محفوظة بعد: اكتب **ابحث** لبدء البحث.")
            return self._response(text, lang, [], intent="overview")
        summary = ", ".join(f"{status.replace('_', ' ')}: {n}" for status, n in sorted(counts.items()))
        text = self._t(lang,
                       f"Stored opportunities ({summary}).\nCommands: **scan** · **egypt** · **saudi** · an ID like **RFP-1A2B3C**.",
                       f"الفرص المحفوظة ({summary}).\nالأوامر: **ابحث** · **مصر** · **السعودية** · رقم فرصة مثل **RFP-1A2B3C**.")
        return self._response(text, lang, [], intent="overview", data={"counts": counts})

    # ------------------------------------------------------------------ helpers
    def _line(self, r: RfpOpportunityRecord) -> str:
        today = self._clock()
        due = f"{r.deadline.isoformat()} ({(r.deadline - today).days}d)" if r.deadline else "deadline n/a"
        return f"- **{r.id}** · {r.title} · {r.issuing_organization or '—'} · {due} · fit: {r.fit or '—'}"

    def _response(self, text: str, lang: str, records: list[RfpOpportunityRecord], *, intent: str,
                  data: dict | None = None) -> AgentResponse:
        sources = [SourceReference(source_type="tender", reference=r.submission_link or r.source_name,
                                   last_updated=r.last_seen_at.date().isoformat() if r.last_seen_at else None,
                                   synthetic=r.synthetic) for r in records]
        return AgentResponse(agent_name=self.name, text=text, mode=ResponseMode.DETERMINISTIC, language=lang,
                             sources=sources, data={"intent": intent, **(data or {})})

    @staticmethod
    def _t(lang: str, en: str, ar: str) -> str:
        return ar if lang == "ar" else en

    @staticmethod
    def _reason(reason: ExclusionReason, lang: str) -> str:
        ar = {"outside_markets": "خارج مصر والسعودية", "country_unknown": "الدولة غير مؤكدة",
              "not_training_related": "ليست في مجال التدريب", "expired": "انتهى موعدها",
              "duplicate": "مكررة", "invalid_record": "بيانات ناقصة"}
        return ar[reason.value] if lang == "ar" else reason.value.replace("_", " ")

    @staticmethod
    def _country_ar(country: Country) -> str:
        return {"EG": "مصر", "SA": "السعودية"}.get(country.value, country.label)