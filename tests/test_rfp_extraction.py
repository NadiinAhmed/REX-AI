"""Agent 2 — extraction rules: dates, country (strict market filter input), categories."""
from datetime import date

import pytest

from src.rfp.extraction import classify, detect_country, extract, parse_date
from src.rfp.models import Category, Country, RawTender


@pytest.mark.parametrize("text, expected", [
    ("2026-11-15", date(2026, 11, 15)),
    ("15/11/2026", date(2026, 11, 15)),          # day-first, as used in Egypt and Saudi Arabia
    ("15-11-2026", date(2026, 11, 15)),
    ("2026/11/15", date(2026, 11, 15)),
    ("Nov 15, 2026", date(2026, 11, 15)),
    ("15 November 2026", date(2026, 11, 15)),
    ("١٥/١١/٢٠٢٦", date(2026, 11, 15)),          # Arabic-Indic digits
    ("١٥ نوفمبر ٢٠٢٦", date(2026, 11, 15)),
])
def test_parse_date_formats(text, expected):
    assert parse_date(text) == expected


@pytest.mark.parametrize("text", ["", None, "soon", "31/02/2026", "1448/06/20 هـ", "20 Jumada 1448 AH"])
def test_unknown_or_hijri_dates_are_none_never_guessed(text):
    assert parse_date(text) is None


@pytest.mark.parametrize("country, expected", [
    ("Egypt", Country.EGYPT), ("مصر", Country.EGYPT), ("EG", Country.EGYPT),
    ("Saudi Arabia", Country.SAUDI_ARABIA), ("KSA", Country.SAUDI_ARABIA), ("المملكة العربية السعودية", Country.SAUDI_ARABIA),
    ("United Arab Emirates", Country.OTHER), ("Germany", Country.OTHER), ("USA", Country.OTHER),
])
def test_explicit_country_field(country, expected):
    assert detect_country(RawTender(title="Training", country=country)) == expected


def test_country_inferred_from_city_or_government_domain():
    assert detect_country(RawTender(title="Training programme in Alexandria")) == Country.EGYPT
    assert detect_country(RawTender(title="برنامج تدريبي في الرياض")) == Country.SAUDI_ARABIA
    assert detect_country(RawTender(title="Training", link="https://portal.gov.eg/t/1")) == Country.EGYPT
    assert detect_country(RawTender(title="Training", link="https://x.gov.sa/t/1")) == Country.SAUDI_ARABIA


def test_country_is_unknown_when_both_or_neither_market_is_mentioned():
    assert detect_country(RawTender(title="Training in Cairo and Riyadh")) == Country.UNKNOWN
    assert detect_country(RawTender(title="Online training programme")) == Country.UNKNOWN


def test_short_words_do_not_cause_false_matches():
    # "it" inside "with", "sa" inside "salary": whole-word matching only
    cats, training = classify(RawTender(title="Salary survey with consultants"))
    assert Category.SOFTWARE_IT_TRAINING not in cats and not training
    assert detect_country(RawTender(title="Consulting with salary benchmarks")) == Country.UNKNOWN


def test_training_signal_separates_training_from_it_procurement():
    cats, training = classify(RawTender(title="Supply of IT hardware and laptops"))
    assert Category.SOFTWARE_IT_TRAINING in cats and training is False
    cats, training = classify(RawTender(title="Upskilling programme in AI and cloud for ministry staff"))
    assert {Category.PROFESSIONAL_DEVELOPMENT, Category.SOFTWARE_IT_TRAINING} <= set(cats) and training


def test_arabic_listing_is_classified():
    cats, training = classify(RawTender(title="مناقصة لتقديم برنامج تدريبي في الذكاء الاصطناعي لموظفي الوزارة"))
    assert training and Category.SOFTWARE_IT_TRAINING in cats


def test_extract_builds_clean_opportunity_and_reports_missing_fields():
    opp, training = extract(RawTender(title="  Data   training  ", organization="Org", country="Egypt",
                                      deadline="01/12/2026", description="Training in data."))
    assert training and opp.title == "Data training" and opp.deadline == date(2026, 12, 1)
    assert opp.opportunity_id.startswith("RFP-") and len(opp.opportunity_id) == 10
    assert set(opp.missing_fields()) == {"publication_date", "eligibility", "submission_link"}
    again, _ = extract(RawTender(title="data training", organization="ORG", country="مصر"))
    assert again.dedup_key == opp.dedup_key            # same tender → same key (case/spacing/language)