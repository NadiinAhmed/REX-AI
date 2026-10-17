"""Role 2 — course schema validation."""
import pytest
from pydantic import ValidationError

from src.models.course import Course, CourseCatalog, Fee


def _minimal(**overrides):
    data = {"course_id": "DS-999", "title": "Test course",
            "source": {"source_type": "synthetic", "reference": "test"}}
    data.update(overrides)
    return data


def test_minimal_course_defaults_to_unknown_values():
    c = Course.model_validate(_minimal())
    assert c.skill_level.value == "unknown"
    assert c.fee.is_known is False
    assert {"fee", "instructor", "skill_level", "delivery_format"} <= set(c.missing_fields())


@pytest.mark.parametrize("bad", [{"course_id": "ds101"}, {"title": ""}, {"source": {"source_type": "made_up", "reference": "x"}}])
def test_invalid_course_fields_are_rejected(bad):
    with pytest.raises(ValidationError):
        Course.model_validate(_minimal(**bad))


def test_currency_is_normalised_and_validated():
    assert Fee(amount=10, currency="egp").currency == "EGP"
    with pytest.raises(ValidationError):
        Fee(amount=10, currency="pounds")
    with pytest.raises(ValidationError):
        Fee(amount=-5, currency="EGP")


def test_catalog_rejects_duplicate_ids():
    info = {"name": "t", "version": "1", "is_synthetic": True, "notice": "demo"}
    with pytest.raises(ValidationError, match="duplicate"):
        CourseCatalog.model_validate({"dataset": info, "courses": [_minimal(), _minimal()]})


def test_demo_dataset_is_labelled_synthetic(catalog):
    assert catalog.dataset.is_synthetic is True
    assert "SYNTHETIC" in catalog.dataset.notice
    assert all(c.source.is_synthetic and not c.source.verified for c in catalog.courses)
