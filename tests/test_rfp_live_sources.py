"""Agent 2 — Etimad (Saudi Arabia) and UNGM (Egypt + Saudi Arabia) sources, offline: the portals are faked."""
from datetime import date
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse

import pytest

from src.config import load_settings
from src.db.database import Database
from src.db.rfp_repository import RfpRepository
from src.rfp.etimad import PAGE_SIZE, EtimadSource
from src.rfp.extraction import extract, parse_date
from src.rfp.models import Country
from src.rfp.pipeline import RfpScanPipeline
from src.rfp.sources import JsonFileSource, SourceError
from src.rfp.ungm import UngmSource, country_ids, parse_rows

TODAY = date(2026, 11, 11)

# Shaped like Etimad's visitor JSON (field names from the portal); the content is invented for tests.
ETIMAD_TRAINING = {"tenderId": 901, "tenderIdString": "abc+/def==", "tenderName": "تقديم خدمات تدريب الموظفين على تحليل البيانات",
                   "agencyName": "وزارة الاختبار", "branchName": "الإدارة العامة للتدريب", "tenderTypeName": "منافسة عامة",
                   "tenderActivityName": "التعليم والتدريب", "referenceNumber": "261139000123",
                   "submitionDate": "2026-11-02T09:00:00", "lastOfferPresentationDate": "2026-12-01T10:00:00",
                   "condetionalBookletPrice": 500}
ETIMAD_GOODS = {"tenderId": 902, "tenderIdString": "xyz", "tenderName": "توريد أثاث مكتبي",
                "agencyName": "أمانة الاختبار", "tenderTypeName": "منافسة عامة", "tenderActivityName": "التجارة",
                "submitionDate": "2026-11-03T09:00:00", "lastOfferPresentationDate": "2026-12-05T10:00:00"}


def fake_etimad(pages: dict[tuple[str, int], list[dict]], fail: bool = False):
    calls = []

    def get_json(url, timeout):
        q = parse_qs(urlparse(url).query)
        key = (q["MultipleSearch"][0], int(q["PageNumber"][0]))
        calls.append(key)
        assert q["TenderCategory"] == ["2"]                                # open tenders only
        if fail:
            raise URLError("offline")
        return {"data": pages.get(key, []), "totalCount": 0}
    get_json.calls = calls
    return get_json


def test_etimad_maps_fields_dedups_and_pages_in_order():
    full_page = [dict(ETIMAD_GOODS, tenderId=1000 + i) for i in range(PAGE_SIZE)]
    api = fake_etimad({("تدريب", 1): full_page, ("تدريب", 2): [ETIMAD_TRAINING], ("تأهيل", 1): [ETIMAD_TRAINING]})
    source = EtimadSource(keywords=("تدريب", "تأهيل"), pause_seconds=0, get_json=api)
    tenders = source.fetch()
    assert api.calls == [("تدريب", 1), ("تدريب", 2), ("تأهيل", 1)]        # stops after a short page
    assert len(tenders) == PAGE_SIZE + 1                                  # the training tender counted once
    t = next(t for t in tenders if "تدريب" in t.title)
    assert t.country == "Saudi Arabia" and t.organization.startswith("وزارة الاختبار")
    assert t.link.endswith("STenderId=abc%2B%2Fdef%3D%3D")                # kept percent-encoded
    assert not source.info.synthetic and "1 open tender" not in source.info.notice
    opp, training = extract(t)
    assert training and opp.country == Country.SAUDI_ARABIA
    assert opp.deadline == date(2026, 12, 1) and opp.publication_date == date(2026, 11, 2)


def test_etimad_unreachable_raises_source_error():
    with pytest.raises(SourceError, match="Etimad could not be reached"):
        EtimadSource(keywords=("تدريب",), pause_seconds=0, get_json=fake_etimad({}, fail=True)).fetch()


# Shaped like UNGM's search result rows; the content is invented for tests.
UNGM_PAGE = """<select id="selCountry"><option value="11">Egypt</option><option value="191">Saudi Arabia</option>
<option value="5">Kenya</option></select>"""


def ungm_row(notice_id, title, deadline, published, agency, kind, ref, country):
    return (f'<div class="tableRow dataRow notice-table" data-noticeid="{notice_id}">'
            f'<div class="tableCell resultTitle"><a href="/Public/Notice/{notice_id}">'
            f'<span class="ungm-title ungm-title--small">{title}</span></a></div>'
            f'<div class="tableCell resultInfo1 deadline"><span>{deadline} 12:00 (GMT 2.00)</span></div>'
            f'<div class="tableCell resultInfo1"><span>{published}</span></div>'
            f'<div class="tableCell resultAgency"><span>{agency}</span></div>'
            f'<div class="tableCell resultInfo1"><span>{kind}</span></div>'
            f'<div class="tableCell resultInfo1"><span>{ref}</span></div>'
            f'<div class="tableCell resultInfo1"><span>{country}</span></div></div>')


UNGM_RESULTS = (ungm_row(301, "Training of trainers on digital skills for youth", "25-Nov-2026", "05-Nov-2026",
                         "UNDP", "Request for proposal", "UNDP-EGY-00123", "Egypt")
                + ungm_row(302, "Capacity building workshop for SFDA staff", "30-Nov-2026", "06-Nov-2026",
                           "UNIDO", "Request for quotation", "RFQ-SAU-77", "Saudi Arabia")
                + ungm_row(303, "Supply of laptops", "20-Nov-2026", "01-Nov-2026", "UNICEF", "Invitation to bid",
                           "ITB-1", "Kenya"))


def fake_ungm(results: str = UNGM_RESULTS, page_html: str = UNGM_PAGE, fail: bool = False):
    calls = []

    def http(url, timeout, body=None):
        calls.append((url, body))
        if fail:
            raise URLError("offline")
        return page_html if body is None else (results if body["PageIndex"] == 0 else "")
    http.calls = calls
    return http


def test_ungm_country_ids_and_row_parsing():
    assert country_ids(UNGM_PAGE) == {"Egypt": "11", "Saudi Arabia": "191"}
    rows = parse_rows(UNGM_RESULTS)
    assert [r["id"] for r in rows] == ["301", "302", "303"]
    r = rows[0]
    assert r["title"] == "Training of trainers on digital skills for youth" and r["countries"] == ["Egypt"]
    assert (r["deadline"], r["published"], r["agency"], r["reference"]) == ("25-Nov-2026", "05-Nov-2026", "UNDP",
                                                                            "UNDP-EGY-00123")
    assert parse_date(r["deadline"]) == date(2026, 11, 25)


def test_ungm_uses_country_filter_and_keeps_only_our_markets():
    http = fake_ungm()
    source = UngmSource(pause_seconds=0, http=http)
    tenders = source.fetch()
    assert http.calls[1][1]["Countries"] == ["11", "191"] and http.calls[1][1]["IsActive"] is True
    assert {t.country for t in tenders} == {"Egypt", "Saudi Arabia"} and len(tenders) == 2
    assert "Egypt: 1" in source.info.notice and "Saudi Arabia: 1" in source.info.notice
    assert tenders[0].link == "https://www.ungm.org/Public/Notice/301"


def test_ungm_falls_back_to_keywords_without_country_ids():
    http = fake_ungm(page_html="<html>no list</html>")
    source = UngmSource(pause_seconds=0, http=http)
    tenders = source.fetch()
    assert any(c[1] and c[1]["Title"] == "training" for c in http.calls)
    assert len(tenders) == 2 and "keyword search" in source.info.notice


def test_ungm_unreachable_raises_source_error():
    with pytest.raises(SourceError, match="UNGM could not be reached"):
        UngmSource(pause_seconds=0, http=fake_ungm(fail=True)).fetch()


@pytest.fixture
def courses():
    from src.knowledge_base.loader import load_catalog
    from tests.conftest import OFFICIAL_DATASET
    return load_catalog(OFFICIAL_DATASET).courses


def test_scan_with_live_sources_finds_both_markets(courses):
    db = Database("sqlite://")
    db.migrate()
    repo = RfpRepository(db)
    etimad = EtimadSource(keywords=("تدريب",), pause_seconds=0,
                          get_json=fake_etimad({("تدريب", 1): [ETIMAD_TRAINING, ETIMAD_GOODS]}))
    RfpScanPipeline([etimad, UngmSource(pause_seconds=0, http=fake_ungm())], repo,
                    courses=lambda: courses, clock=lambda: TODAY).run()
    rows = repo.list()
    assert {r.country for r in rows} == {"EG", "SA"} and not any(r.synthetic for r in rows)
    statuses = {r.country: r.status for r in rows}
    assert statuses["EG"] == "flagged_for_review" and statuses["SA"] == "monitoring"
    assert not any("أثاث" in r.title for r in rows)                      # goods tender screened out


def test_demo_rows_are_removed_when_the_demo_feed_is_off(test_env, courses):
    from src.bootstrap import build_app
    from tests.test_rfp_agent import DEMO_FEED
    env = test_env()
    ctx = build_app(load_settings(env), enable_rag=False)                 # tests use RFP_SOURCES=file
    RfpScanPipeline([JsonFileSource(DEMO_FEED)], ctx.rfp_repository, courses=lambda: courses,
                    clock=lambda: date(2026, 10, 24)).run()
    assert any(r.synthetic for r in ctx.rfp_repository.list())
    live = build_app(load_settings(dict(env, RFP_SOURCES="ungm")), enable_rag=False)
    assert not any(r.synthetic for r in live.rfp_repository.list())


def test_default_sources_are_live_only():
    from src.bootstrap import build_rfp_sources
    settings = load_settings({})
    assert settings.rfp_sources == ("worldbank", "etimad", "ungm")
    assert [type(s).__name__ for s in build_rfp_sources(settings)] == ["WorldBankSource", "EtimadSource", "UngmSource"]


# Real Etimad titles from a live check (2026-11-11): training words, but goods or works.
@pytest.mark.parametrize("title, training", [
    ("تأثيث مقر جناح تدريب حرس الحدود بمنطقة مكة المكرمة", False),
    ("صيانة وحدات التكييف المركزي بمراكز التدريب بميناء رأس الغار العسكري", False),
    ("عملية صيانة ونظافة وتشغيل مدينة تدريب الامن العام بمنطقة مكة المكرمة", False),
    ("تأهيل مبنى فرع المؤسسة بمنطقة حائل", False),
    ("طباعة الحقائب التدريبية للبرامج التدريبية لمركز الاعمال", False),
    ("مشروع استبدال موكيت معرض المكتبة المركزية بمبنى المعهد بالرياض", False),
    ("مشروع اكاديمية التأمين وتهيئة الموظفين", True),
    ("تقديم خدمات تدريب الموظفين على تحليل البيانات", True),
    ("Developing and conducting specialized skills training on air quality management", True),
    ("Supply of furniture for the training centre", False),
    ("بشأن تفعيل ماكينة الأسلحة بمركز التدريب .", False),
    ("تأمين تجهيزات أمنية وتدريبية للأمن العام", False),
    ("إعادة تأهيل وتعميق وتنظيف ابار مدينة بريدة مرحلة ثالثة", False),
    ("عملية إعادة تأهيل المولدات الاحتياطية بمعهد الدراسات الفنية للقوات البحرية", False),
    ("تأهيل مطبخ حملة حجاج وزارة الدفاع بالمشاعر المقدسة (عرفات)", False),
    ("تدريب منسوبي الهيئة", True),
    ("تأمين ميداليات وونقات دورات فيصل حرس الشرف", False),
    ("عقد دورات للغة الإنجليزية بالقوات البرية", True),
    ("تطوير وتشغيل الربط والتكامل التقني مع المنصة الوطنية للتعليم الإلكتروني", False),
    ("Procurement, installation, and commissioning of SME training stations - UNIDO Project", False),
    ("Hotel Services _SSI Training on 9-10 December", False),
    ("Call for Proposal: Implementing Partner to conduct Vocational Skills Training, Career Guidance", True),
    ("PROVISION OF SERVICES TO INTEGRATE RESOURCE EFFICIENT AND CLEANER PRODUCTION (RECP) INTO HIGHER EDUCATION", True),
    ("برنامج تأهيل مسؤولي الحماية من الإشعاع", True),
])
def test_goods_and_works_that_mention_training_are_not_training(title, training):
    from src.rfp.extraction import classify
    from src.rfp.models import RawTender
    assert classify(RawTender(title=title, description=title))[1] is training


def test_ungm_request_matches_the_browser():
    from src.rfp.ungm import search_body, verification_token
    body = search_body(0, ["11"], today=date(2026, 11, 11))
    assert (body["PublishedTo"], body["DeadlineFrom"], body["SortField"], body["SortAscending"]) == \
        ("11-Nov-2026", "11-Nov-2026", "Deadline", True)
    assert verification_token('<input name="__RequestVerificationToken" type="hidden" value="tok-123_abc" />') == "tok-123_abc"
    assert verification_token('<input type="hidden" value="tok-456" name="__RequestVerificationToken">') == "tok-456"


def test_ungm_country_filter_refused_falls_back_to_keywords():
    from urllib.error import HTTPError

    def http(url, timeout, body=None):
        if body is None:
            return UNGM_PAGE
        if body["Countries"]:
            raise HTTPError(url, 400, "Bad Request", {}, None)
        return UNGM_RESULTS if body["PageIndex"] == 0 else ""
    source = UngmSource(pause_seconds=0, http=http)
    assert len(source.fetch()) == 2 and "keyword search" in source.info.notice
