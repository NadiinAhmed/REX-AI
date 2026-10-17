"""Role 1 — learner profile extraction (English and Arabic)."""
from src.models.course import DeliveryFormat, SkillLevel
from src.workflows.learner_profile import LearnerProfile, contains_contact_details, extract_profile


def test_english_profile_extraction():
    p = extract_profile("I'm a commerce graduate, beginner, I want to become a data analyst, "
                        "online, budget 5000 EGP, 6 hours per week")
    assert p.career_goal == "data analyst"
    assert p.background == "commerce"
    assert p.skill_level == SkillLevel.BEGINNER
    assert p.preferred_format == DeliveryFormat.ONLINE
    assert (p.budget.amount, p.budget.currency) == (5000, "EGP")
    assert p.hours_per_week == 6


def test_arabic_profile_extraction():
    p = extract_profile("أنا خريج تجارة ومبتدئ وعايز اشتغل محلل بيانات اونلاين وميزانيتي 5000 جنيه")
    assert p.career_goal == "محلل بيانات"
    assert p.skill_level == SkillLevel.BEGINNER
    assert p.preferred_format == DeliveryFormat.ONLINE
    assert p.budget.currency == "EGP"
    assert "data" in p.search_query() or "بيانات" in p.search_query()


def test_background_is_not_used_as_search_topic():
    p = extract_profile("I studied marketing and want to be a data analyst")
    assert "marketing" not in p.interests
    assert p.career_goal == "data analyst"


def test_greeting_has_no_topic():
    assert extract_profile("hello").has_topic() is False


def test_contact_details_detected_and_not_stored():
    msg = "my email is test@example.com and phone +20 100 123 4567, I like marketing"
    assert contains_contact_details(msg)
    p = extract_profile(msg)
    assert p.interests == ["marketing"]
    assert "example" not in str(p.model_dump())


def test_merge_keeps_preferences_and_replaces_topic():
    first = extract_profile("beginner, I want data analysis")
    merged = first.merge(extract_profile("online please"))
    assert merged.skill_level == SkillLevel.BEGINNER and merged.preferred_format == DeliveryFormat.ONLINE
    assert "data" in merged.interests
    switched = merged.merge(extract_profile("actually I want cybersecurity"))
    assert switched.interests == ["cybersecurity"] and switched.skill_level == SkillLevel.BEGINNER
    assert LearnerProfile().merge(LearnerProfile()) == LearnerProfile()
