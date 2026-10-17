"""Role 2 — loading and error reporting."""
import pytest

from src.knowledge_base.loader import KnowledgeBaseError, load_catalog


def test_loads_demo_dataset(catalog):
    assert len(catalog.courses) >= 5


def test_missing_file_gives_clear_error(tmp_path):
    with pytest.raises(KnowledgeBaseError, match="not found"):
        load_catalog(tmp_path / "nope.json")


def test_invalid_json_reports_line(write_json):
    with pytest.raises(KnowledgeBaseError, match="not valid JSON"):
        load_catalog(write_json("{ broken"))


def test_schema_error_names_the_field(write_json, raw_dataset):
    raw_dataset["courses"][0]["fee"]["currency"] = "dollars"
    with pytest.raises(KnowledgeBaseError, match="currency"):
        load_catalog(write_json(raw_dataset))


def test_empty_course_list_is_rejected(write_json, raw_dataset):
    raw_dataset["courses"] = []
    with pytest.raises(KnowledgeBaseError):
        load_catalog(write_json(raw_dataset))
