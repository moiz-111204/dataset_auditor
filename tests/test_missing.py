import numpy as np
import pandas as pd

from backend.analyzers.missing_data import analyze_missing, missing_by_column, missing_mask
from backend.models.schemas import AnalysisSettings


def checks(findings, name):
    return [f for f in findings if f.details.get("check") == name]


def column_finding(findings, col):
    return next(f for f in checks(findings, "column_missing") if f.details["column"] == col)


def with_missing(n_rows, n_missing):
    return [None] * n_missing + list(range(n_rows - n_missing))


def test_column_thresholds():
    n = 100
    df = pd.DataFrame({
        "ok": with_missing(n, 3),         # 3%  -> not flagged
        "medium": with_missing(n, 10),    # 10% -> medium
        "high": with_missing(n, 30),      # 30% -> high
        "critical": with_missing(n, 60),  # 60% -> critical
        "empty": [None] * n,              # 100% -> critical, drop
    })
    findings = analyze_missing(df)
    flagged = {f.details["column"] for f in checks(findings, "column_missing")}
    assert "ok" not in flagged
    assert column_finding(findings, "medium").severity == "medium"
    assert column_finding(findings, "high").severity == "high"
    assert column_finding(findings, "critical").severity == "critical"
    empty = column_finding(findings, "empty")
    assert empty.severity == "critical" and empty.details["recommendation"] == "drop"
    assert "dropped" in empty.description


def test_blank_strings_count_as_missing():
    df = pd.DataFrame({"a": ["x", "", "   ", None, "y"]})
    assert missing_mask(df)["a"].tolist() == [False, True, True, True, False]
    assert missing_by_column(df)["a"] == 0.6


def test_row_level_missingness():
    rng = np.random.default_rng(0)
    df = pd.DataFrame({c: rng.normal(size=100) for c in "abcd"})
    df.loc[:19, "a"] = np.nan  # 20% of rows have a gap
    row = checks(analyze_missing(df), "row_missing")
    assert len(row) == 1
    assert row[0].details["rows_with_missing_share"] == 0.2
    df2 = pd.DataFrame({c: rng.normal(size=100) for c in "abcd"})
    df2.loc[:4, "a"] = np.nan  # 5% -> not flagged
    assert checks(analyze_missing(df2), "row_missing") == []


def test_missingness_concentrated_in_one_group():
    # income missing for 60% of women but 10% of men.
    n = 100
    gender = ["F"] * n + ["M"] * n
    income = [None] * 60 + [50000.0] * 40 + [None] * 10 + [52000.0] * 90
    df = pd.DataFrame({"gender": gender, "income": income})
    findings = analyze_missing(df, AnalysisSettings(protected_columns=["gender"]))
    grp = checks(findings, "missing_by_group")
    assert len(grp) == 1
    f = grp[0]
    assert f.severity == "critical"  # 6x
    assert f.details["highest_group"] == "F"
    assert f.details["rate_ratio"] == 6.0
    assert f.details["p_value"] < 0.01
    assert "60.0%" in f.description and "10.0%" in f.description


def test_even_missingness_is_not_flagged_by_group():
    gender = ["F", "M"] * 100
    income = [None if i % 5 == 0 else 1.0 for i in range(200)]  # 20% in both groups
    findings = analyze_missing(pd.DataFrame({"gender": gender, "income": income}),
                               AnalysisSettings(protected_columns=["gender"]))
    assert checks(findings, "missing_by_group") == []


def test_small_groups_are_ignored():
    gender = ["F"] * 50 + ["M"] * 50 + ["X"] * 10
    income = [None] * 10 + [1.0] * 40 + [None] * 10 + [1.0] * 40 + [None] * 10
    findings = analyze_missing(pd.DataFrame({"gender": gender, "income": income}),
                               AnalysisSettings(protected_columns=["gender"]))
    assert checks(findings, "missing_by_group") == []  # X (10 rows, all missing) is skipped


def test_co_occurring_gaps_reported_as_pattern():
    rng = np.random.default_rng(3)
    df = pd.DataFrame({c: rng.normal(size=200) for c in ["salary", "bonus", "tenure"]})
    gap = rng.random(200) < 0.15
    df.loc[gap, ["salary", "bonus"]] = np.nan
    df.loc[rng.random(200) < 0.1, "tenure"] = np.nan
    patterns = checks(analyze_missing(df), "missing_pattern")
    assert len(patterns) == 1
    assert set(patterns[0].details["columns"]) == {"salary", "bonus"}
    assert "not proof" in patterns[0].description


def test_complete_data_has_no_findings():
    df = pd.DataFrame({"a": range(50), "b": ["x"] * 50})
    assert analyze_missing(df, AnalysisSettings(protected_columns=["b"])) == []
