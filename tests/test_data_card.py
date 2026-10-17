import html
import re

import pandas as pd
import pytest

from backend.generators.data_card import generate_html, generate_markdown, save_data_cards
from backend.models.schemas import AnalysisReport, AnalysisSettings, Finding
from backend.orchestrator import analyze_dataframe

RAW_PII = ["jane.doe@example.com", "sam.lee@example.org", "(212) 555-0142", "000-12-3456",
           "Priyanka", "Oluwaseun", "4111 1111 1111 1111"]


@pytest.fixture
def frame():
    return pd.DataFrame({
        "first_name": ["Priyanka", "Oluwaseun", "Priyanka", "Oluwaseun"],
        "email": ["jane.doe@example.com", "sam.lee@example.org", "jane.doe@example.com", None],
        "phone": ["(212) 555-0142", "(212) 555-0142", "", None],
        "ssn": ["000-12-3456"] * 4,
        "card": ["4111 1111 1111 1111"] * 4,
        "gender": ["F", "M", "F", "M"],
        "income": [50000.0, None, 61000.0, None],
        "hired": [1, 0, 0, 1],
    })


@pytest.fixture
def mock_report():
    findings = [
        Finding(severity="critical", category="pii", title="SSN-style national ID numbers found in 1 column",
                description="SSN-style national ID numbers were detected in 4 of 4 rows (100.0%) in: ssn.",
                columns=["ssn"], explanation="IDs are permanent identifiers.",
                recommendation="Remove the national ID column.",
                details={"pii_type": "ssn", "pii_label": "SSN-style national ID numbers",
                         "rows_affected": 4, "percent_rows": 100.0}),
        Finding(severity="high", category="bias", title="Outcome disparity by 'gender' (ratio 0.70)",
                description="Rate for 'F' is lower.", columns=["gender", "hired"],
                explanation="Models repeat historical gaps.", recommendation="Investigate <script>.",
                details={"check": "demographic_disparity"}),
        Finding(severity="medium", category="missing_data", title="'income' is 50.0% missing",
                description="2 of 4 rows have no value.", columns=["income"],
                explanation="Gaps shrink the data.", recommendation="Find out why income is missing.",
                details={"check": "column_missing", "missing_share": 0.5}),
        Finding(severity="low", category="bias", title="Too few rows to check fairness for 'race'",
                description="Not enough rows.", columns=["race"], details={}),
    ]
    return AnalysisReport(
        session_id="test-session", dataset_name="hiring <test>.csv", row_count=4, column_count=8,
        detected_settings=AnalysisSettings(label_column="hired", protected_columns=["gender"]),
        findings=findings, risk_score=39, risk_level="Medium", ai_enabled=False,
        overall_summary="Four issues found.", priority_actions=["Remove IDs", "Investigate gender gap", "Fix income"],
        summary_stats={
            "issues_by_severity": {"critical": 1, "high": 1, "medium": 1, "low": 1},
            "pii_detectors": {"regex": True, "presidio_ner": False, "presidio_reason": "disabled"},
            "ai": {"enabled": False, "model": None, "fallback_reason": "no API key configured"},
        },
    )


SECTIONS = ["Dataset Overview", "Feature Descriptions", "Bias Audit Results", "Missing Data Assessment",
            "Privacy / PII Scan Results", "Risk Assessment", "Recommendations", "Methodology", "Limitations"]


def test_markdown_has_all_sections_and_content(mock_report, frame):
    md = generate_markdown(mock_report, frame)
    for section in SECTIONS:
        assert f"## {section}" in md
    assert "| Rows | 4 |" in md
    assert "39/100 (Medium)" in md
    assert "[CRITICAL] SSN-style national ID numbers" in md
    assert "1. Remove IDs" in md
    assert "built-in templates (no API key configured)" in md
    assert "not a compliance" in md
    assert "| gender | " in md and "F, M" in md  # low-cardinality categories may be shown


def test_html_is_self_contained_with_badges(mock_report, frame):
    out = generate_html(mock_report, frame)
    assert out.startswith("<!doctype html>")
    for section in SECTIONS:
        assert html.escape(section) in out
    assert "<style>" in out
    assert not re.search(r"<(script|link)\b", out)              # no scripts or external CSS
    assert not re.search(r"(src|href)=\"https?://", out)          # no external dependencies
    assert '<span class="badge critical">' in out and '<span class="badge low">' in out
    assert "--critical:#c62828" in out and "--low:#1c64c8" in out
    assert "@media print" in out
    # Text from the report is escaped.
    assert "hiring &lt;test&gt;.csv" in out and "Investigate &lt;script&gt;." in out


def test_pii_columns_show_no_examples(mock_report, frame):
    md = generate_markdown(mock_report, frame)
    assert "| ssn | " in md and "withheld (PII)" in md


def test_no_raw_pii_in_either_output_mock_report(mock_report, frame):
    for out in (generate_markdown(mock_report, frame), generate_html(mock_report, frame)):
        for value in RAW_PII:
            assert value not in out
            assert html.escape(value) not in out


def test_no_raw_pii_in_cards_from_real_pipeline(frame):
    report = analyze_dataframe(frame.copy(), "people.csv")
    md, page = generate_markdown(report, frame), generate_html(report, frame)
    for value in RAW_PII:
        assert value not in md and value not in page
        assert html.escape(value) not in page


def test_save_data_cards(tmp_path, mock_report, frame):
    md_path, html_path = save_data_cards(mock_report, frame, tmp_path)
    assert md_path.read_text(encoding="utf-8").startswith("# Data Card")
    assert "<!doctype html>" in html_path.read_text(encoding="utf-8")
