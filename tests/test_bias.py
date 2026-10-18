import numpy as np
import pandas as pd
import pytest

from backend.analyzers.bias_detector import (
    correlation_ratio, cramers_v, detect_bias, detect_settings, resolve_settings,
)
from backend.models.schemas import AnalysisSettings


def checks(findings, name):
    return [f for f in findings if f.details.get("check") == name]


def disparity_df():
    # 50 men hired at 70%, 50 women hired at 30%: impact ratio 0.30 / 0.70 = 0.43.
    gender = ["M"] * 50 + ["F"] * 50
    hired = [1] * 35 + [0] * 15 + [1] * 15 + [0] * 35
    return pd.DataFrame({"gender": gender, "score": range(100), "hired": hired})


def balanced_df():
    rng = np.random.default_rng(1)
    n = 200
    return pd.DataFrame({
        "gender": ["M", "F"] * (n // 2),
        "experience": rng.integers(0, 20, n),
        "city": rng.choice(["A", "B", "C", "D"], n),
        "approved": [1, 1, 0, 0] * (n // 4),
    })


def test_auto_detects_label_and_protected_columns():
    df = pd.DataFrame({"Applicant Age": [30], "Gender": ["F"], "page_views": [3],
                       "wage": [10], "hired": [1]})
    s = detect_settings(df)
    assert s.label_column == "hired"
    assert s.protected_columns == ["Applicant Age", "Gender"]  # not page_views / wage


def test_label_falls_back_to_low_cardinality_last_column():
    df = pd.DataFrame({"x": range(10), "result": ["a", "b"] * 5})
    assert detect_settings(df).label_column == "result"
    df2 = pd.DataFrame({"x": range(20), "amount": range(20)})
    assert detect_settings(df2).label_column is None


def test_known_disparity_is_flagged_critical():
    findings = detect_bias(disparity_df(), AnalysisSettings())
    disp = checks(findings, "demographic_disparity")
    assert len(disp) == 1
    f = disp[0]
    assert f.severity == "critical"
    assert f.details["reference_group"] == "M"
    assert f.details["positive_label"] == "1"
    assert f.details["min_impact_ratio"] == pytest.approx(0.30 / 0.70, abs=0.01)
    assert "F" in f.details["flagged_groups"]
    assert "70.0%" in f.description and "30.0%" in f.description


def test_disparity_high_between_06_and_08():
    gender = ["M"] * 50 + ["F"] * 50
    hired = [1] * 25 + [0] * 25 + [1] * 18 + [0] * 32  # 0.36 / 0.50 = 0.72
    findings = detect_bias(pd.DataFrame({"gender": gender, "hired": hired}))
    assert checks(findings, "demographic_disparity")[0].severity == "high"


def test_small_groups_ignored_and_mentioned():
    gender = ["M"] * 50 + ["F"] * 50 + ["X"] * 5
    hired = [1] * 35 + [0] * 15 + [1] * 15 + [0] * 35 + [0] * 5
    f = checks(detect_bias(pd.DataFrame({"gender": gender, "hired": hired})), "demographic_disparity")[0]
    assert f.details["ignored_groups"] == {"X": 5}
    assert "X" not in f.details["group_rates"]
    assert "fewer than 20 rows" in f.description


def test_numeric_age_is_binned():
    age = [25] * 40 + [40] * 40 + [60] * 40
    hired = ([1] * 28 + [0] * 12) + ([1] * 26 + [0] * 14) + ([1] * 10 + [0] * 30)
    f = checks(detect_bias(pd.DataFrame({"age": age, "hired": hired})), "demographic_disparity")[0]
    assert set(f.details["group_rates"]) == {"under 30", "30-50", "over 50"}
    assert "over 50" in f.details["flagged_groups"]


def test_class_imbalance_severity_levels():
    def imbalance(pos, neg):
        df = pd.DataFrame({"x": range(pos + neg), "label": [1] * pos + [0] * neg})
        return checks(detect_bias(df), "class_imbalance")

    assert imbalance(50, 50) == []
    assert imbalance(25, 75) == []                         # exactly 3:1 is not flagged
    assert imbalance(20, 80)[0].severity == "medium"       # 4:1
    assert imbalance(15, 85)[0].severity == "high"         # 5.7:1
    assert imbalance(5, 95)[0].severity == "critical"      # 19:1


def test_proxy_detected_with_cramers_v():
    rng = np.random.default_rng(0)
    race = rng.choice(["A", "B", "C"], 300)
    zip_map = {"A": "10001", "B": "20002", "C": "30003"}
    zips = [zip_map[r] if rng.random() < 0.9 else "40004" for r in race]
    df = pd.DataFrame({"race": race, "zip_code": zips, "noise": rng.normal(size=300),
                       "outcome": rng.integers(0, 2, 300)})
    proxies = checks(detect_bias(df), "proxy_correlation")
    assert [p.details["feature"] for p in proxies] == ["zip_code"]
    assert proxies[0].details["statistic"] == "Cramer's V"
    assert proxies[0].severity == "high"


def test_proxy_numeric_uses_pearson_and_eta():
    rng = np.random.default_rng(2)
    age = rng.integers(20, 65, 300)
    df = pd.DataFrame({
        "age": age,
        "experience": age - 18 + rng.normal(0, 2, 300),
        "gender": rng.choice(["F", "M"], 300),
    })
    df["height"] = np.where(df["gender"] == "M", 178, 165) + rng.normal(0, 3, 300)
    proxies = checks(detect_bias(df, AnalysisSettings(protected_columns=["age", "gender"])),
                     "proxy_correlation")
    stats = {(p.details["feature"], p.details["protected_column"]): p.details["statistic"] for p in proxies}
    assert stats[("experience", "age")] == "Pearson correlation"
    assert stats[("height", "gender")] == "correlation ratio (eta)"


def test_id_columns_excluded_from_proxy_check():
    df = disparity_df()
    df["applicant_id"] = [f"id{i}" for i in range(100)]
    proxies = checks(detect_bias(df), "proxy_correlation")
    assert all(p.details["feature"] != "applicant_id" for p in proxies)


def test_balanced_dataframe_produces_no_findings():
    assert detect_bias(balanced_df()) == []


def test_statistics_bounds():
    x = pd.Series(["a", "b"] * 50)
    assert cramers_v(x, x) == pytest.approx(1.0, abs=0.02)
    assert correlation_ratio(x, pd.Series([1.0, 5.0] * 50)) == pytest.approx(1.0)
    assert correlation_ratio(x, pd.Series([3.0] * 100)) == 0.0


def test_resolve_settings_drops_unknown_columns():
    s = resolve_settings(disparity_df(), AnalysisSettings(label_column="hired",
                                                          protected_columns=["gender", "nope", "hired"]))
    assert s.protected_columns == ["gender"]


def two_groups(rate_a, rate_b, positive, negative, label, n=200):
    rows = []
    for group, rate in (("A", rate_a), ("B", rate_b)):
        k = int(rate * n)
        rows += [(group, positive)] * k + [(group, negative)] * (n - k)
    return pd.DataFrame(rows, columns=["group", label])


def disparity_for(df, label):
    findings = detect_bias(df, AnalysisSettings(label_column=label, protected_columns=["group"]))
    return checks(findings, "demographic_disparity")[0]


def test_adverse_label_name_makes_the_other_value_favourable():
    # Group A defaults on 40% of loans, B on 10%: A gets the good outcome (0) less often.
    f = disparity_for(two_groups(0.40, 0.10, 1, 0, "default"), "default")
    assert f.details["positive_label"] == "0"
    assert list(f.details["flagged_groups"]) == ["A"]
    f = disparity_for(two_groups(0.40, 0.10, "yes", "no", "is_fraud"), "is_fraud")
    assert f.details["positive_label"] == "no"
    assert list(f.details["flagged_groups"]) == ["A"]


def test_explicit_words_decide_the_favourable_value():
    f = disparity_for(two_groups(0.45, 0.75, "good", "bad", "credit_risk"), "credit_risk")
    assert f.details["positive_label"] == "good"
    assert list(f.details["flagged_groups"]) == ["A"]
    f = disparity_for(two_groups(0.30, 0.60, "Fully Paid", "Charged Off", "loan_status"), "loan_status")
    assert f.details["positive_label"] == "Fully Paid"
    # Words win over the column name: 'rejected' in the name does not flip approved/rejected.
    f = disparity_for(two_groups(0.30, 0.60, "approved", "rejected", "rejected_or_approved"),
                      "rejected_or_approved")
    assert f.details["positive_label"] == "approved"


def test_more_protected_attribute_names_detected():
    protected = ["Sexual_Orientation", "religious_affiliation", "Citizenship", "caste",
                 "pregnancy_status", "Disabilities", "native_country", "national_origin"]
    not_protected = ["national_id", "country", "page_views", "language", "message"]
    df = pd.DataFrame({c: ["a", "b"] for c in protected + not_protected})
    assert detect_settings(df).protected_columns == protected


def test_common_label_names_need_few_distinct_values():
    for name in ["survived", "churn", "is_fraud", "two_year_recid", "Exited", "loan_status"]:
        df = pd.DataFrame({name: [0, 1] * 10, "x": range(20)})
        assert detect_settings(df).label_column == name, name
    df = pd.DataFrame({"decision": [f"d{i}" for i in range(20)], "x": range(20)})
    assert detect_settings(df).label_column is None  # 20 distinct values: not a label
