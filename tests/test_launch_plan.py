"""Course Launch workflow, step 1: one staff sentence -> LaunchPlan. Parsing only; nothing is created."""
from datetime import date

from src.workflows.launch_plan import parse_launch_request

TODAY = date(2026, 11, 12)


def test_full_english_sentence():
    p = parse_launch_request(
        "Launch a new course 'Cloud Computing' covering Linux, Docker and AWS, for beginners, 60 hours, fee 4,000 EGP, 20% early-bird "
        "until Dec 15, email it to our leads and Ali@Example.com", TODAY)
    assert (p.title, p.skill_level, p.duration_hours) == ("Cloud Computing", "beginner", 60)
    assert (p.fee_amount, p.fee_currency) == (4000, "EGP")
    assert (p.offer_percent, p.offer_valid_until) == (20, date(2026, 12, 15))
    assert p.email_leads and p.recipients == ["ali@example.com"]
    assert p.language == "en" and p.missing == []


def test_full_arabic_sentence_with_arabic_digits():
    p = parse_launch_request("أطلق كورس جديد اسمه الحوسبة السحابية للمبتدئين، المحاور: لينكس، دوكر و AWS، ٦٠ ساعة، الرسوم ٤٠٠٠ جنيه، "
                             "خصم ٢٠٪ حتى ١٥ ديسمبر، وابعت ايميل للعملاء المحتملين", TODAY)
    assert p.title == "الحوسبة السحابية" and p.skill_level == "beginner"
    assert (p.duration_hours, p.fee_amount, p.fee_currency) == (60, 4000, "EGP")
    assert (p.offer_percent, p.offer_valid_until) == (20, date(2026, 12, 15))
    assert p.email_leads and p.language == "ar" and p.missing == []


def test_offer_is_optional():
    p = parse_launch_request('New course "Data Visualization" covering charts and dashboards, 40 hours, email to sara@example.com', TODAY)
    assert p.offer_percent is None and p.missing == []


def test_missing_title_and_recipients_are_asked_not_guessed():
    missing = parse_launch_request("launch a new course for beginners, 60 hours", TODAY).missing
    assert any("title" in m for m in missing) and any("receives" in m for m in missing)


def test_offer_without_end_date_is_asked():
    p = parse_launch_request('course "Cloud Computing", 20% discount, email our leads', TODAY)
    assert any("end date" in m for m in p.missing)


def test_past_or_unreadable_end_date_is_a_problem():
    assert any("past" in m for m in parse_launch_request(
        'course "Cloud Computing", 20% until 2026-01-01, email our leads', TODAY).missing)
    assert any("couldn't read" in m for m in parse_launch_request(
        'course "Cloud Computing", 20% until next payday, email our leads', TODAY).missing)


def test_day_month_without_year_means_the_next_occurrence():
    p = parse_launch_request('course "Cloud Computing", 10% until Jan 10, email our leads', TODAY)
    assert p.offer_valid_until == date(2027, 1, 10)


def test_percentage_is_never_read_as_the_fee():
    p = parse_launch_request('course "Cloud Computing", 20% off until Dec 15, email our leads', TODAY)
    assert p.fee_amount is None and p.offer_percent == 20


def test_invalid_email_is_a_problem():
    p = parse_launch_request('course "Cloud Computing", email to bad@x', TODAY)
    assert p.recipients == [] and any("receives" in m for m in p.missing)


def test_topics_are_read_and_stop_at_the_next_field():
    p = parse_launch_request("Launch a new course 'Cloud Computing' covering Linux, Docker and AWS, for beginners, "
                             "fee 4000 EGP, email ali@example.com", TODAY)
    assert p.topics == ["Linux", "Docker", "AWS"]
    ar = parse_launch_request("كورس جديد اسمه التصميم، المحاور: وسائط متعددة، دوكر و AWS، الرسوم ٤٠٠٠ جنيه", TODAY)
    assert ar.topics == ["وسائط متعددة", "دوكر", "AWS"]          # an attached و inside a word is kept


def test_missing_topics_are_asked():
    p = parse_launch_request('Launch a new course "Cloud Computing", email ali@example.com', TODAY)
    assert any("topics" in m for m in p.missing)
