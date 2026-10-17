from backend.models.schemas import AnalysisReport, AnalysisSettings, Finding


def test_models_construct():
    f = Finding(severity="high", category="pii", title="t", description="d", columns=["a"])
    assert f.explanation_source == "template"
    r = AnalysisReport(
        session_id="s", dataset_name="x.csv", row_count=1, column_count=1,
        detected_settings=AnalysisSettings(), findings=[f],
    )
    assert r.model_dump(mode="json")["findings"][0]["severity"] == "high"
