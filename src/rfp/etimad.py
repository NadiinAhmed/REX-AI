"""Real Saudi government tenders from Etimad (tenders.etimad.sa), the official Saudi procurement portal.

Etimad's tender list is public for visitors (no account needed). Its visitor page loads the list
from a JSON endpoint, which this source reads:

    GET https://tenders.etimad.sa/Tender/AllSupplierTendersForVisitorAsync
        ?PageSize=24&PageNumber=1&TenderCategory=2&PublishDateId=5&MultipleSearch=<keyword>

- `TenderCategory=2`  = open for offers (no awarded or closed tenders)
- `PublishDateId=5`   = published in the last 3 months
- `MultipleSearch`    = keyword search; we search training words (Arabic, since Etimad is in Arabic)
- at most 24 rows per page; pages are read in order, one request at a time, with a pause

Each row gives the tender name, agency, type, activity and the Gregorian dates, and the link to its
public details page. This is not a documented API: Etimad can change it, so any failure is reported
on the dashboard and the other sources are still scanned. Tender names are published in Arabic.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from src.rfp.models import RawTender
from src.rfp.sources import FeedInfo, SourceError
from src.utils.logging_config import get_logger

logger = get_logger("rfp.etimad")

API_URL = "https://tenders.etimad.sa/Tender/AllSupplierTendersForVisitorAsync"
DETAIL_URL = "https://tenders.etimad.sa/Tender/DetailsForVisitor?STenderId={id}"
SOURCE_NAME = "Etimad (Saudi government tenders)"
PAGE_SIZE = 24
# Training words searched on Etimad (its search matches words in the tender name).
KEYWORDS = ("تدريب", "تدريبية", "تأهيل", "دورات", "بناء القدرات", "تطوير المهارات", "تعليم")
# Etimad answers the default Python user agent with HTTP 403, so a browser-like one is sent.
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/141.0 Safari/537.36 (REX-AI student project)",
           "Accept": "application/json, text/plain, */*"}


def _http_get_json(url: str, timeout: float) -> dict:
    with urlopen(Request(url, headers=HEADERS), timeout=timeout) as response:   # noqa: S310 (fixed https URL)
        return json.loads(response.read().decode("utf-8"))


def _text(value) -> str:
    return str(value).strip() if value not in (None, "") else ""


class EtimadSource:
    """A `TenderSource` (see sources.py): `fetch()` returns open Saudi tenders that match training words."""

    def __init__(self, keywords: tuple[str, ...] = KEYWORDS, max_pages: int = 3, timeout: float = 20.0,
                 pause_seconds: float = 1.0, get_json: Callable[[str, float], dict] | None = None) -> None:
        self.name = SOURCE_NAME
        self.keywords = keywords
        self.max_pages = max_pages
        self.timeout = timeout
        self.pause_seconds = pause_seconds
        self._get_json = get_json or _http_get_json          # tests inject a fake: no network needed
        self.info = FeedInfo(name=SOURCE_NAME, synthetic=False)
        self.skipped = 0

    def url_for(self, keyword: str, page: int) -> str:
        params = {"PageSize": PAGE_SIZE, "PageNumber": page, "TenderCategory": 2, "PublishDateId": 5,
                  "MultipleSearch": keyword}
        return f"{API_URL}?{urlencode(params)}"

    def _pause(self) -> None:
        if self.pause_seconds:
            time.sleep(self.pause_seconds)      # one request at a time: be polite to a public portal

    def fetch(self) -> list[RawTender]:
        seen: dict[str, RawTender] = {}
        self.skipped = 0
        failures: list[str] = []
        requests = 0
        for keyword in self.keywords:
            for page in range(1, self.max_pages + 1):
                if requests:
                    self._pause()
                requests += 1
                try:
                    payload = self._get_json(self.url_for(keyword, page), self.timeout)
                except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
                    logger.warning("Etimad request failed (%s, page %d): %s", keyword, page, type(exc).__name__)
                    failures.append(type(exc).__name__)
                    break                                   # Etimad pages must be read in order: next keyword
                rows = payload.get("data") if isinstance(payload, dict) else None
                rows = [r for r in (rows or []) if isinstance(r, dict)]
                for row in rows:
                    key = _text(row.get("tenderId")) or _text(row.get("tenderIdString"))
                    tender = self._to_raw(row)
                    if tender is None:
                        self.skipped += 1
                    elif key and key not in seen:          # the same tender matches several keywords
                        seen[key] = tender
                if len(rows) < PAGE_SIZE:
                    break                                   # last page for this keyword
        if failures and len(failures) == requests:
            raise SourceError("Etimad could not be reached (" + ", ".join(sorted(set(failures))) +
                              "). Check the internet connection; other sources were still scanned.")
        notice = (f"{SOURCE_NAME}: {len(seen)} open tender(s) found for training keywords "
                  f"({', '.join(self.keywords)}).")
        if failures:
            notice += f" {len(failures)} request(s) failed; results may be incomplete."
        self.info = FeedInfo(name=SOURCE_NAME, synthetic=False, notice=notice)
        return list(seen.values())

    def _to_raw(self, row: dict) -> RawTender | None:
        title = _text(row.get("tenderName"))
        if not title:
            return None
        agency = _text(row.get("agencyName"))
        branch = _text(row.get("branchName"))
        organization = agency + (f" — {branch}" if branch and branch != agency else "")
        kind = _text(row.get("tenderTypeName"))
        activity = _text(row.get("tenderActivityName"))
        id_string = _text(row.get("tenderIdString"))
        reference = _text(row.get("referenceNumber")) or _text(row.get("tenderNumber"))
        details = [f"{label}: {value}" for label, value in (
            ("Activity", activity), ("Type", kind), ("Reference", reference),
            ("Booklet price (SAR)", _text(row.get("condetionalBookletPrice"))),
            ("Last date for enquiries", _text(row.get("lastEnqueriesDate"))[:10])) if value]
        return RawTender(
            title=title[:500],
            organization=organization or None,
            country="Saudi Arabia",                       # Etimad only lists Saudi government tenders
            deadline=_text(row.get("lastOfferPresentationDate")) or None,
            published=_text(row.get("submitionDate")) or None,
            description=" · ".join([title, activity]) if activity else title,
            full_text="\n".join([title, *details]),
            category="; ".join(filter(None, [kind, activity])) or None,
            # tenderIdString must stay percent-encoded exactly (a "+" for a space sends you to the login page)
            link=DETAIL_URL.format(id=quote(id_string, safe="")) if id_string else None,
            documents=["Tender booklet: on the Etimad details page (buying it needs a supplier account)"],
            source=SOURCE_NAME,
        )
