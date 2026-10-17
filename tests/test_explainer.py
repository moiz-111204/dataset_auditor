import json
from types import SimpleNamespace

import pandas as pd
import pytest

from backend.ai import explainer
from backend.ai.explainer import RESPONSE_SCHEMA, SYSTEM_PROMPT, explain_findings
from backend.models.schemas import Finding
from backend.orchestrator import analyze_dataframe


class FakeClient:
    """Stands in for anthropic.Anthropic; records every request and replays scripted replies."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, SimpleNamespace):
            return reply
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=reply)])


def valid_reply(request) -> str:
    """Answer every finding id found in the request, like a well-behaved model would."""
    prompt = request["messages"][0]["content"]
    payload = json.loads(prompt.split("aggregate numbers only):\n", 1)[1].split("\n\nReturn JSON", 1)[0])
    return json.dumps({
        "findings": [{"id": item["id"],
                      "explanation": f"LLM explanation for {item['id']} about why it matters.",
                      "recommendation": f"LLM recommendation for {item['id']}."} for item in payload],
        "overall_summary": "LLM overall summary of the dataset and its main risks.",
        "priority_actions": ["First action", "Second action", "Third action"],
    })


class EchoClient(FakeClient):
    def _create(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text=valid_reply(kwargs))])


def sample_findings():
    return [
        Finding(severity="critical", category="bias", title="Outcome disparity by 'gender' (ratio 0.50)",
                description="d", columns=["gender", "hired"],
                details={"check": "demographic_disparity", "protected_column": "gender",
                         "label_column": "hired", "positive_label": "1",
                         "group_rates": {"Female": {"rate": 0.3, "count": 50},
                                         "Male": {"rate": 0.6, "count": 50}},
                         "reference_group": "Male", "flagged_groups": {"Female": 0.5},
                         "min_impact_ratio": 0.5, "threshold": 0.8, "ignored_groups": {}}),
        Finding(severity="high", category="missing_data", title="'income' is 35.0% missing",
                description="d", columns=["income"],
                details={"check": "column_missing", "column": "income", "missing_count": 35,
                         "missing_share": 0.35}),
        Finding(severity="high", category="pii", title="Email addresses found in 1 column",
                description="d", columns=["email"],
                details={"pii_type": "email", "pii_label": "Email addresses", "rows_affected": 100,
                         "percent_rows": 100.0, "per_column": {"email": {"rows": 100}},
                         "detection_methods": ["regex"]}),
    ]


CONTEXT = {"row_count": 100, "column_count": 5, "risk_score": 45, "risk_level": "Medium"}


def test_fallback_without_api_key():
    result = explain_findings(sample_findings(), CONTEXT)
    assert result.ai_enabled is False
    assert result.fallback_reason == "no API key configured"
    assert all(f.explanation_source == "template" for f in result.findings)
    assert all(len(f.explanation) > 40 and f.recommendation for f in result.findings)
    assert "Female" in result.findings[0].explanation  # templates run locally, so names are fine
    assert "45/100" in result.overall_summary
    assert len(result.priority_actions) == 3


def test_llm_explanations_used_when_valid():
    client = EchoClient([None])
    result = explain_findings(sample_findings(), CONTEXT, client=client)
    assert result.ai_enabled is True
    assert len(client.requests) == 1  # all findings batched into one request
    assert [f.explanation_source for f in result.findings] == ["llm"] * 3
    assert result.findings[1].explanation.startswith("LLM explanation for f2")
    assert result.overall_summary.startswith("LLM overall summary")
    assert result.priority_actions == ["First action", "Second action", "Third action"]
    request = client.requests[0]
    assert request["system"] == SYSTEM_PROMPT
    assert request["output_config"]["format"]["schema"] == RESPONSE_SCHEMA
    assert request["model"] == "claude-opus-5-5"


def test_model_and_effort_from_environment(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "claude-haiku-5-5")
    monkeypatch.setenv("LLM_EFFORT", "")
    client = EchoClient([None])
    explain_findings(sample_findings(), CONTEXT, client=client)
    assert client.requests[0]["model"] == "claude-haiku-5-5"
    assert "effort" not in client.requests[0]["output_config"]


def test_invalid_json_retried_once_then_falls_back():
    client = FakeClient(["this is not json"])
    result = explain_findings(sample_findings(), CONTEXT, client=client)
    assert len(client.requests) == 2
    assert result.ai_enabled is False
    assert result.fallback_reason == "invalid model output"
    assert all(f.explanation_source == "template" for f in result.findings)


def test_invalid_then_valid_uses_second_answer():
    good = valid_reply(explainer.build_request(sample_findings(), CONTEXT, explainer.llm_settings()))
    client = FakeClient(['{"findings": "wrong shape"}', good])
    result = explain_findings(sample_findings(), CONTEXT, client=client)
    assert len(client.requests) == 2
    assert result.ai_enabled is True


def test_refusal_is_treated_as_invalid():
    refusal = SimpleNamespace(stop_reason="refusal", content=[])
    result = explain_findings(sample_findings(), CONTEXT, client=FakeClient([refusal]))
    assert result.ai_enabled is False


def test_api_error_falls_back_without_retry():
    client = FakeClient([TimeoutError("timed out")])
    result = explain_findings(sample_findings(), CONTEXT, client=client)
    assert len(client.requests) == 1
    assert result.ai_enabled is False
    assert "TimeoutError" in result.fallback_reason


def test_skipped_finding_gets_template():
    reply = json.dumps({
        "findings": [{"id": "f1", "explanation": "LLM explanation for the first finding only.",
                      "recommendation": "Do the first thing."}],
        "overall_summary": "A summary that only covers part of the findings.",
        "priority_actions": ["One"],
    })
    result = explain_findings(sample_findings(), CONTEXT, client=FakeClient([reply]))
    assert [f.explanation_source for f in result.findings] == ["llm", "template", "template"]


def test_outgoing_payload_contains_no_raw_cell_values(monkeypatch):
    n = 60
    df = pd.DataFrame({
        "applicant_id": [f"APP-{1000 + i}" for i in range(n)],
        "first_name": ["Priyanka", "Oluwaseun", "Bartholomew"] * 20,
        "email": [f"applicant{i}@example.com" for i in range(n)],
        "phone": [f"(212) 555-01{i % 100:02d}" for i in range(n)],
        "ssn": [f"000-12-{3400 + i}" for i in range(n)],
        "notes": ["Spoke with Johnathan Smithers about relocation"] * n,
        "gender": ["Womanish", "Manlike"] * 30,
        "race": ["Groupalpha"] * 30 + ["Groupbeta"] * 30,
        "income": [None] * 25 + [51234.0] * 35,
        "hired": ["Accepted"] * 20 + ["Rejected"] * 40,
    })
    client = EchoClient([None])
    monkeypatch.setattr(explainer, "_make_client", lambda timeout: client)
    report = analyze_dataframe(df, "fake.csv")
    assert report.ai_enabled is True
    assert len(client.requests) == 1
    sent = json.dumps(client.requests[0])
    for col in df.columns:
        for value in df[col].dropna().astype(str).unique():
            if value in df.columns:
                continue
            assert value not in sent, f"raw value from '{col}' leaked into the LLM payload"
    for fragment in ["Priyanka", "example.com", "555-01", "000-12", "Johnathan", "Womanish",
                     "Groupalpha", "Accepted", "51234"]:
        assert fragment not in sent
    # The model still received the useful aggregate information.
    assert "demographic_disparity" in sent and "pii_scan" in sent and "missing_share" in sent


def test_report_marks_template_source_without_key():
    df = pd.DataFrame({"gender": ["F", "M"] * 30, "hired": [1, 0, 0, 0] * 15})
    report = analyze_dataframe(df, "x.csv")
    assert report.ai_enabled is False
    assert report.summary_stats["ai"]["fallback_reason"] == "no API key configured"
    assert report.overall_summary and report.priority_actions
    assert all(f.explanation and f.explanation_source == "template" for f in report.findings)


def test_templates_cover_every_finding_type():
    small = Finding(severity="low", category="bias", title="Too few rows", description="d",
                    columns=["race", "hired"],
                    details={"check": "demographic_disparity", "protected_column": "race",
                             "label_column": "hired", "ignored_groups": {"A": 5}})
    broken = Finding(severity="low", category="bias", title="t", description="original text",
                     details={"check": "class_imbalance"})  # missing keys
    result = explain_findings([small, broken], CONTEXT)
    assert "Too few rows per group in 'race'" in result.findings[0].explanation
    assert result.findings[1].explanation == "original text"
