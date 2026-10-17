import json

import numpy as np
import pandas as pd
import pytest

from backend.models.schemas import AnalysisSettings, Finding
from backend.orchestrator import (
    MAX_COLUMNS, MAX_ROWS, DatasetError, analyze_dataframe, load_csv, risk_score, run_analysis,
)


def to_csv_bytes(df: pd.DataFrame, encoding="utf-8") -> bytes:
    return df.to_csv(index=False).encode(encoding)


def small_df(n=120, seed=0):
    rng = np.random.default_rng(seed)
    gender = rng.choice(["F", "M"], n)
    hired = np.where(gender == "M", rng.random(n) < 0.7, rng.random(n) < 0.2).astype(int)
    income = rng.normal(50000, 8000, n).round()
    income[(gender == "F") & (rng.random(n) < 0.6)] = np.nan
    return pd.DataFrame({
        "applicant_id": range(n),
        "email": [f"user{i}@example.com" for i in range(n)],
        "gender": gender,
        "score": rng.integers(0, 100, n),
        "income": income,
        "hired": hired,
    })


def finding(sev):
    return Finding(severity=sev, category="bias", title="t", description="d")


def test_full_pipeline_on_small_dataframe():
    report, df = run_analysis(to_csv_bytes(small_df()), "people.csv")
    assert report.row_count == 120 and report.column_count == 6
    assert report.detected_settings.label_column == "hired"
    assert report.detected_settings.protected_columns == ["gender"]
    cats = report.summary_stats["issues_by_category"]
    assert cats["bias"] >= 1 and cats["missing_data"] >= 1 and cats["pii"] >= 1
    assert report.summary_stats["total_issues"] == len(report.findings)
    assert 0 < report.risk_score <= 100
    assert report.summary_stats["pii_detectors"]["regex"] is True
    charts = report.summary_stats["charts"]
    assert set(charts["label_distribution"]) == {"0", "1"}
    assert set(charts["group_rates"]["gender"]["rates"]) == {"F", "M"}
    assert charts["missing_by_column"]["income"] > 0
    # The DataFrame is returned separately and its values are not in the report.
    assert isinstance(df, pd.DataFrame) and len(df) == 120
    dumped = report.model_dump_json()
    assert "user7@example.com" not in dumped


def test_explicit_settings_override_detection():
    report, _ = run_analysis(to_csv_bytes(small_df()), "people.csv",
                             AnalysisSettings(label_column="hired", protected_columns=[]))
    assert report.detected_settings.protected_columns == []
    assert not any(f.details.get("check") == "demographic_disparity" for f in report.findings)


def test_unknown_settings_column_is_rejected():
    with pytest.raises(DatasetError, match="Unknown column"):
        run_analysis(to_csv_bytes(small_df()), "x.csv", AnalysisSettings(label_column="nope"))


def test_risk_score_caps_and_levels():
    assert risk_score([]) == (0, "Low")
    assert risk_score([finding("critical")] * 3) == (60, "High")       # 75 capped at 60
    assert risk_score([finding("high")] * 2) == (20, "Low")
    assert risk_score([finding("medium")] * 9) == (15, "Low")          # 27 capped at 15
    assert risk_score([finding("critical")] * 3 + [finding("high")] * 2) == (80, "Critical")
    all_max = [finding("critical")] * 5 + [finding("high")] * 5 + [finding("medium")] * 9 + [finding("low")] * 9
    assert risk_score(all_max) == (100, "Critical")
    assert risk_score([finding("high")] * 3) == (30, "Medium")


def test_empty_file_rejected():
    for data in [b"", b"   \n  "]:
        with pytest.raises(DatasetError, match="empty") as exc:
            load_csv(data)
        assert exc.value.status_code == 400


def test_header_only_rejected():
    with pytest.raises(DatasetError, match="no data rows"):
        load_csv(b"a,b,c\n")


def test_single_column_csv():
    report, _ = run_analysis(b"value\n1\n2\n3\n\n5\n", "one.csv")
    assert report.column_count == 1
    assert report.detected_settings.label_column is None


def test_latin1_encoding():
    data = "name_of_city,count\nMünchen,3\nSão Paulo,4\nZürich,5\n".encode("latin-1")
    df = load_csv(data)
    assert df["name_of_city"].tolist() == ["München", "São Paulo", "Zürich"]


def test_utf8_bom_is_stripped():
    df = load_csv("﻿id,value\n1,2\n".encode("utf-8"))
    assert list(df.columns) == ["id", "value"]


def test_mixed_type_column():
    data = b"mixed,label\n1,yes\nabc,no\n3.5,yes\n,no\nTrue,yes\n"
    report, df = run_analysis(data, "mixed.csv")
    assert report.row_count == 5
    assert df["mixed"].isna().sum() == 1


def test_duplicate_column_names():
    report, df = run_analysis(b"a,a,b\n1,2,x\n3,4,y\n", "dupes.csv")
    assert list(df.columns) == ["a", "a.1", "b"]
    frame = pd.DataFrame([[1, 2], [3, 4]], columns=["x", "x"])
    report2 = analyze_dataframe(frame, "frame.csv")
    assert report2.column_count == 2


def test_malformed_csv_rejected():
    with pytest.raises(DatasetError, match="parse") as exc:
        load_csv(b'a,b\n1,2\n3,4,5,6\n"unterminated,7\n')
    assert exc.value.status_code == 400


def test_too_many_rows_rejected():
    data = ("x\n" + "1\n" * (MAX_ROWS + 1)).encode()
    with pytest.raises(DatasetError, match="limit") as exc:
        load_csv(data)
    assert exc.value.status_code == 413


def test_too_many_columns_rejected():
    header = ",".join(f"c{i}" for i in range(MAX_COLUMNS + 1))
    row = ",".join("1" for _ in range(MAX_COLUMNS + 1))
    with pytest.raises(DatasetError, match=f"{MAX_COLUMNS + 1} columns; the limit is {MAX_COLUMNS}") as exc:
        load_csv(f"{header}\n{row}\n".encode())
    assert exc.value.status_code == 413


def test_column_limit_is_inclusive():
    header = ",".join(f"c{i}" for i in range(MAX_COLUMNS))
    row = ",".join("1" for _ in range(MAX_COLUMNS))
    assert load_csv(f"{header}\n{row}\n".encode()).shape[1] == MAX_COLUMNS


def test_oversized_file_rejected():
    with pytest.raises(DatasetError, match="10 MB") as exc:
        load_csv(b"a\n" + b"x" * (10 * 1024 * 1024))
    assert exc.value.status_code == 413


def test_report_serializes_to_json():
    report, _ = run_analysis(to_csv_bytes(small_df()), "../../etc/passwd")
    assert report.dataset_name == "passwd"
    json.loads(report.model_dump_json())
