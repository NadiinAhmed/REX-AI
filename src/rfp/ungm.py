"""Real tenders from the UN Global Marketplace (www.ungm.org), for Egypt AND Saudi Arabia.

UNGM is where UN agencies (UNDP, UNICEF, UNIDO, UNESCO, WFP...) publish their procurement notices.
Their country offices in Egypt and Saudi Arabia regularly ask for training, capacity building and
consulting services. The notice list is public (no account needed). Its search page loads results
from this endpoint, which this source calls:

    POST https://www.ungm.org/Public/Notice/Search      (JSON body, answers with HTML rows)

The beneficiary-country filter needs UNGM's internal country IDs, so they are read from the
country list on https://www.ungm.org/Public/Notice the first time. If they cannot be found, the
source searches by training keywords instead and keeps only rows whose country is Egypt or Saudi
Arabia. Only active notices (deadline not passed) are requested.

This is not a documented API: UNGM can change it. Any failure is reported on the dashboard and the
other sources are still scanned. All parsing is kept in `parse_rows()` so it is easy to adjust.
"""
from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from datetime import date
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener

from src.rfp.documents import html_to_text
from src.rfp.models import RawTender
from src.rfp.sources import FeedInfo, SourceError
from src.utils.logging_config import get_logger

logger = get_logger("rfp.ungm")

BASE = "https://www.ungm.org"
SEARCH_URL = f"{BASE}/Public/Notice/Search"
NOTICE_PAGE = f"{BASE}/Public/Notice"
NOTICE_URL = f"{BASE}/Public/Notice/{{id}}"
SOURCE_NAME = "UN Global Marketplace (UNGM)"
COUNTRIES = ("Egypt", "Saudi Arabia")
FALLBACK_KEYWORDS = ("training", "capacity building", "workshop", "learning", "course")
PAGE_SIZE = 15                                   # UNGM returns up to 15 rows per page
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/141.0 Safari/537.36 (REX-AI student project)",
           "Accept": "*/*", "X-Requested-With": "XMLHttpRequest"}

_DATE = re.compile(r"\b\d{1,2}[-\s][A-Za-z]{3}[-\s]\d{4}\b")       # 25-Nov-2026


class _Session:
    """Like a browser tab: keeps UNGM's cookies from the notice page and sends its anti-forgery token back."""

    def __init__(self) -> None:
        self._opener = build_opener(HTTPCookieProcessor(CookieJar()))
        self.token: str | None = None

    def __call__(self, url: str, timeout: float, body: dict | None = None) -> str:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = dict(HEADERS, Referer=NOTICE_PAGE)
        if data is not None:
            headers.update({"Content-Type": "application/json", "Origin": BASE})
            if self.token:
                headers["RequestVerificationToken"] = self.token      # header name as the browser sends it
        with self._opener.open(Request(url, data=data, headers=headers), timeout=timeout) as response:  # noqa: S310
            text = response.read().decode("utf-8", errors="replace")
        if data is None:
            self.token = verification_token(text) or self.token
        return text


def _ungm_date(day: date) -> str:
    return f"{day.day:02d}-{day.strftime('%b')}-{day.year}"          # 11-Nov-2026, as the UNGM page sends it


def search_body(page: int, countries: list[str], title: str = "", today: date | None = None) -> dict:
    """The same JSON the UNGM search page sends (captured from the browser, 2026-11-11)."""
    today = today or date.today()
    return {"PageIndex": page, "PageSize": PAGE_SIZE, "Title": title, "Description": "", "Reference": "",
            "PublishedFrom": "", "PublishedTo": _ungm_date(today), "DeadlineFrom": _ungm_date(today),
            "DeadlineTo": "", "Countries": countries, "Agencies": [], "UNSPSCs": [], "NoticeTypes": [],
            "SortField": "Deadline", "SortAscending": True, "isPicker": False, "IsSustainable": False,
            "IsActive": True, "NoticeDisplayType": None, "NoticeSearchTotalLabelId": "noticeSearchTotal",
            "TypeOfCompetitions": []}


def verification_token(page_html: str) -> str | None:
    """The anti-forgery token the search page sends back (hidden input, either attribute order)."""
    for pattern in (r'name="__RequestVerificationToken"[^>]*value="([^"]+)"',
                    r'value="([^"]+)"[^>]*name="__RequestVerificationToken"',
                    r'RequestVerificationToken["\']?\s*[:=]\s*["\']([\w\-]{20,})'):
        match = re.search(pattern, page_html)
        if match:
            return match.group(1)
    return None


def country_ids(page_html: str, names: tuple[str, ...] = COUNTRIES) -> dict[str, str]:
    """UNGM's internal IDs for the countries, read from the <option> list of the search page."""
    found: dict[str, str] = {}
    for value, label in re.findall(r'<option[^>]*value="(\d+)"[^>]*>\s*([^<]+?)\s*</option>', page_html):
        for name in names:
            if label.strip().lower() == name.lower():
                found.setdefault(name, value)
    return found


def _clean(fragment: str) -> str:
    return re.sub(r"\s+", " ", html_to_text(fragment)).strip()


def parse_rows(html: str) -> list[dict]:
    """HTML result rows → dicts with id, title, deadline, published, agency, notice_type, reference, countries."""
    rows: list[dict] = []
    starts = [m for m in re.finditer(r'data-noticeid="(\d+)"', html)]
    for i, m in enumerate(starts):
        chunk = html[m.start(): starts[i + 1].start() if i + 1 < len(starts) else len(html)]
        notice_id = m.group(1)
        title_match = (re.search(r'class="[^"]*ungm-title[^"]*"[^>]*>(.*?)</', chunk, re.S)
                       or re.search(rf'href="/Public/Notice/{notice_id}"[^>]*>(.*?)</a>', chunk, re.S))
        title = _clean(title_match.group(1)) if title_match else ""
        lines = [ln.strip() for ln in html_to_text(chunk.split(">", 1)[-1]).splitlines() if ln.strip()]
        dates = _DATE.findall(" ".join(lines))
        countries = [c for c in COUNTRIES if any(ln.lower() == c.lower() for ln in lines)]
        other = [ln for ln in lines if ln != title and not _DATE.search(ln) and len(ln) < 120
                 and ln.lower() not in {c.lower() for c in COUNTRIES}]
        rows.append({"id": notice_id, "title": title or (lines[0] if lines else ""),
                     "deadline": dates[0] if dates else None, "published": dates[1] if len(dates) > 1 else None,
                     "agency": other[0] if other else None,
                     "notice_type": other[1] if len(other) > 1 else None,
                     "reference": other[2] if len(other) > 2 else None,
                     "countries": countries, "lines": lines})
    return rows


class UngmSource:
    """A `TenderSource` (see sources.py): active UN notices for Egypt and Saudi Arabia."""

    def __init__(self, max_pages: int = 5, timeout: float = 20.0, pause_seconds: float = 1.0,
                 http: Callable[..., str] | None = None) -> None:
        self.name = SOURCE_NAME
        self.max_pages = max_pages
        self.timeout = timeout
        self.pause_seconds = pause_seconds
        self._http = http or _Session()                    # tests inject a fake: no network needed
        self.info = FeedInfo(name=SOURCE_NAME, synthetic=False)
        self.skipped = 0
        self._requests = 0

    def _call(self, url: str, body: dict | None = None) -> str:
        if self._requests and self.pause_seconds:
            time.sleep(self.pause_seconds)
        self._requests += 1
        return self._http(url, self.timeout, body)

    def _searches(self) -> tuple[list[tuple[list[str], str]], str]:
        """[(country ids, title keyword)] to run, and how the countries were selected (for the notice)."""
        try:
            ids = country_ids(self._call(NOTICE_PAGE))
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise SourceError(f"UNGM could not be reached ({type(exc).__name__}). "
                              "Check the internet connection; other sources were still scanned.") from exc
        if len(ids) == len(COUNTRIES):
            return [([ids[c] for c in COUNTRIES], "")], "country filter"
        logger.info("UNGM country IDs not found; falling back to keyword search")
        return [([], k) for k in FALLBACK_KEYWORDS], "keyword search, then country check"

    def fetch(self) -> list[RawTender]:
        self.skipped, self._requests = 0, 0
        searches, mode = self._searches()
        found: dict[str, RawTender] = {}
        failures: list[str] = []
        queue = list(searches)
        while queue:
            countries, keyword = queue.pop(0)
            for page in range(self.max_pages):
                try:
                    html = self._call(SEARCH_URL, search_body(page, countries, keyword))
                except HTTPError as exc:
                    detail = exc.read()[:300].decode("utf-8", errors="replace") if exc.fp else ""
                    logger.warning("UNGM search failed: HTTP %s %s", exc.code, re.sub(r"\s+", " ", detail))
                    failures.append(f"HTTP {exc.code}")
                    if exc.code == 400 and countries:              # country filter refused: search keywords
                        queue = [([], k) for k in FALLBACK_KEYWORDS]
                        mode = "country filter refused, keyword search, then country check"
                    break
                except (URLError, TimeoutError, OSError) as exc:
                    logger.warning("UNGM search failed: %s", type(exc).__name__)
                    failures.append(type(exc).__name__)
                    break
                rows = parse_rows(html)
                for row in rows:
                    tender = self._to_raw(row)
                    if tender is None:
                        self.skipped += 1
                    else:
                        found.setdefault(row["id"], tender)
                if len(rows) < PAGE_SIZE:
                    break
        per_country = {c: sum(1 for t in found.values() if t.country == c) for c in COUNTRIES}
        notice = (f"{SOURCE_NAME}: {len(found)} active notice(s) read ("
                  + ", ".join(f"{c}: {n}" for c, n in per_country.items()) + f"; {mode}).")
        if failures:
            notice += f" Search failed ({', '.join(failures)}); results may be incomplete."
        self.info = FeedInfo(name=SOURCE_NAME, synthetic=False, notice=notice)
        return list(found.values())

    def _to_raw(self, row: dict) -> RawTender | None:
        if not row["title"] or len(row["countries"]) != 1:
            return None            # no title, or not exactly one of our two markets (strict rule)
        details = [f"{label}: {value}" for label, value in (
            ("Agency", row["agency"]), ("Notice type", row["notice_type"]), ("Reference", row["reference"])) if value]
        return RawTender(
            title=row["title"][:500],
            organization=row["agency"],
            country=row["countries"][0],
            deadline=row["deadline"],
            published=row["published"],
            description=row["title"],
            full_text="\n".join([row["title"], *details]),
            category=row["notice_type"],
            link=NOTICE_URL.format(id=row["id"]),
            documents=["Notice documents: on the UNGM notice page"],
            source=SOURCE_NAME,
        )
