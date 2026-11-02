"""Real tenders from the World Bank Procurement Notices API (free, no API key).

    GET https://search.worldbank.org/api/v2/procnotices?format=json&fl=*&project_ctry_name_exact=<country>

Every notice is an official procurement notice for a World Bank-financed project, published by the
borrowing government (e.g. an Egyptian ministry). Each one includes the full notice document as
HTML (`notice_text`), which we read for requirements and deadlines (see documents.py).

Coverage, honestly: the World Bank finances projects in Egypt, so Egyptian notices exist; it does
not lend to Saudi Arabia, so the Saudi query normally returns 0. Saudi government tenders are on
Etimad, whose API needs a Saudi commercial registration and a paid subscription.

Contract-award notices announce who already WON a contract: they are not opportunities and are
skipped. The data is published under CC BY 4.0 by the World Bank.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from src.rfp.documents import html_to_text
from src.rfp.models import RawTender
from src.rfp.sources import FeedInfo, SourceError
from src.utils.logging_config import get_logger

logger = get_logger("rfp.worldbank")

API_URL = "https://search.worldbank.org/api/v2/procnotices"
NOTICE_URL = "https://projects.worldbank.org/en/projects-operations/procurement-detail/{id}"
# The API's own country names (the World Bank writes Egypt as "Egypt, Arab Republic of").
COUNTRIES = ("Egypt, Arab Republic of", "Saudi Arabia")
SOURCE_NAME = "World Bank Procurement API"


def _first(value) -> str:
    """API fields are sometimes a string, sometimes a list of strings: return clean text."""
    if isinstance(value, list):
        value = next((v for v in value if v), "")
    return str(value).strip() if value is not None else ""


def _pick(notice: dict, *keys: str) -> str:
    return next((v for v in (_first(notice.get(k)) for k in keys) if v), "")


def _http_get_json(url: str, timeout: float) -> dict:
    request = Request(url, headers={"Accept": "application/json",
                                    "User-Agent": "REX-AI student project (RFP research agent)"})
    with urlopen(request, timeout=timeout) as response:          # noqa: S310 (fixed https URL)
        return json.loads(response.read().decode("utf-8"))


def _notices(payload: dict) -> list[dict]:
    """The API returns `procnotices` as a list (v2) or as an id → notice mapping."""
    items = payload.get("procnotices") or []
    if isinstance(items, dict):
        items = list(items.values())
    return [n for n in items if isinstance(n, dict)]


class WorldBankSource:
    """A `TenderSource` (see sources.py): `fetch()` returns RawTender objects."""

    def __init__(self, countries: tuple[str, ...] = COUNTRIES, rows: int = 100, timeout: float = 20.0,
                 get_json: Callable[[str, float], dict] | None = None) -> None:
        self.name = SOURCE_NAME
        self.countries = countries
        self.rows = rows
        self.timeout = timeout
        self._get_json = get_json or _http_get_json          # tests inject a fake: no network needed
        self.info = FeedInfo(name=SOURCE_NAME, synthetic=False)
        self.skipped = 0
        self.awards_skipped = 0

    def url_for(self, country: str) -> str:
        params = {"format": "json", "fl": "*", "rows": self.rows, "os": 0,
                  "srt": "submission_date", "order": "desc", "project_ctry_name_exact": country}
        return f"{API_URL}?{urlencode(params)}"

    def fetch(self) -> list[RawTender]:
        tenders: list[RawTender] = []
        self.skipped = self.awards_skipped = 0
        per_country: list[str] = []
        failures: list[str] = []
        for country in self.countries:
            try:
                payload = self._get_json(self.url_for(country), self.timeout)
            except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
                logger.warning("World Bank API failed for %s: %s", country, type(exc).__name__)
                failures.append(f"{country.split(',')[0]} ({type(exc).__name__})")
                continue
            count = 0
            for notice in _notices(payload):
                tender = self._to_raw(notice)
                if tender is not None:
                    tenders.append(tender)
                    count += 1
            per_country.append(f"{country.split(',')[0]}: {count}")
        if failures and len(failures) == len(self.countries):
            raise SourceError("World Bank API could not be reached (" + ", ".join(failures) +
                              "). Check the internet connection; other sources were still scanned.")
        notice = (f"{SOURCE_NAME}: open-call notices read ({', '.join(per_country)}); "
                  f"{self.awards_skipped} contract-award notice(s) skipped.")
        if failures:
            notice += " Not reached: " + ", ".join(failures) + "."
        self.info = FeedInfo(name=SOURCE_NAME, synthetic=False, notice=notice)
        return tenders

    def _to_raw(self, n: dict) -> RawTender | None:
        notice_type = _pick(n, "notice_type")
        if "award" in notice_type.lower():
            self.awards_skipped += 1
            return None
        status = _pick(n, "notice_status").lower()
        if "cancel" in status:
            self.skipped += 1
            return None
        notice_id = _pick(n, "id")
        full_text = html_to_text(_pick(n, "notice_text"))
        short = html_to_text(_pick(n, "bid_description"))
        title = _pick(n, "noticetitle", "bid_description", "project_name")
        if not title:
            self.skipped += 1
            return None
        method = _pick(n, "procurement_method_name", "procurement_method")
        return RawTender(
            title=html_to_text(title)[:500],
            organization=_pick(n, "contact_organization", "agency_name", "borrower", "implementing_agency") or None,
            country=_pick(n, "project_ctry_name", "countryshortname") or None,
            deadline=_pick(n, "submission_deadline_date") or None,
            published=_pick(n, "noticedate", "submission_date") or None,
            # Short description is used for classification; the full document is read separately,
            # so a goods notice that merely mentions "training of operators" is not mistaken for a training tender.
            description=(short or full_text[:600]),
            full_text=full_text,
            category="; ".join(filter(None, [notice_type, method, _pick(n, "procurement_group")])) or None,
            link=NOTICE_URL.format(id=notice_id) if notice_id else None,
            documents=[f"{notice_type or 'Procurement'} notice: full text on the World Bank site"],
            source=SOURCE_NAME,
        )
