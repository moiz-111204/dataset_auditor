"""The bundled samples are reproducible and still meet their audit targets."""

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backend.orchestrator import run_analysis

SAMPLES = Path(__file__).resolve().parent.parent / "sample_data"


@pytest.fixture(scope="module")
def generator():
    spec = importlib.util.spec_from_file_location("generate_samples", SAMPLES / "generate_samples.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generator_reproduces_committed_files(generator):
    rng = np.random.default_rng(generator.SEED)
    hiring, survey = generator.problematic_hiring(rng), generator.clean_survey(rng)
    for df, name in [(hiring, "problematic_hiring.csv"), (survey, "clean_survey.csv")]:
        expected = (SAMPLES / name).read_text(encoding="utf-8").replace("\r\n", "\n")
        assert df.to_csv(index=False, lineterminator="\n") == expected, name


def test_identifiers_are_obviously_fake():
    df = pd.read_csv(SAMPLES / "problematic_hiring.csv", dtype=str)
    assert df["ssn"].str.match(r"^(000|666|9\d\d)-\d{2}-\d{4}$").all()
    assert df["email"].str.endswith("@example.com").all()
    assert df["phone"].str.match(r"^\(\d{3}\) 555-01\d{2}$").all()


def test_problematic_sample_targets():
    report, _ = run_analysis((SAMPLES / "problematic_hiring.csv").read_bytes(), "problematic_hiring.csv")
    cats = report.summary_stats["issues_by_category"]
    assert cats["bias"] >= 3 and cats["missing_data"] >= 2 and cats["pii"] >= 3
    checks = {f.details.get("check") for f in report.findings}
    assert {"class_imbalance", "demographic_disparity", "proxy_correlation", "missing_by_group"} <= checks
    proxies = {(f.details["feature"], f.details["protected_column"]) for f in report.findings
               if f.details.get("check") == "proxy_correlation"}
    assert ("zip_code", "race") in proxies


def test_clean_sample_targets():
    report, _ = run_analysis((SAMPLES / "clean_survey.csv").read_bytes(), "clean_survey.csv")
    assert len(report.findings) <= 2
    assert all(f.severity == "low" for f in report.findings)
