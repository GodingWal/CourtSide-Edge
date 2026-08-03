import pytest
from wnba_services.ingestion.adapters.wnba_injuries import latest_report


def test_latest_official_report_uses_last_index_link() -> None:
    index = {
        "links": [
            {"href": "https://example.test/early.pdf", "label": "1:00 p.m."},
            {"href": "https://example.test/latest.pdf", "label": "1:15 p.m."},
        ]
    }
    assert latest_report(index) == ("https://example.test/latest.pdf", "1:15 p.m.")


def test_official_report_requires_at_least_one_link() -> None:
    with pytest.raises(ValueError, match="no reports"):
        latest_report({"links": []})
