"""End-to-end test: start the real server, then use it over HTTP like the browser does.

Run with:  python -m pytest tests/test_e2e.py
The server is started as a separate process (`python backend/app.py`) on a free port, with
ANTHROPIC_API_KEY removed so the run is free and deterministic (template explanations).
"""

import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "sample_data"
PII_COLUMNS = ["first_name", "last_name", "email", "phone", "ssn"]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server():
    port = free_port()
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    env.update(PORT=str(port), ANTHROPIC_API_KEY="", RATE_LIMIT_PER_MINUTE="1000")
    proc = subprocess.Popen([sys.executable, str(ROOT / "backend" / "app.py")], cwd=str(ROOT), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 90
    while time.time() < deadline:
        if proc.poll() is not None:
            pytest.fail(f"server exited early with code {proc.returncode}")
        try:
            if httpx.get(f"{base}/api/health", timeout=2).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.5)
    else:
        proc.terminate()
        pytest.fail("server did not become healthy within 90 s")
    yield base
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def upload(base: str, name: str) -> dict:
    path = SAMPLES / name
    r = httpx.post(f"{base}/api/upload", files={"file": (name, path.read_bytes(), "text/csv")}, timeout=120)
    assert r.status_code == 200, r.text
    return r.json()


def raw_pii_values() -> dict[str, set[str]]:
    df = pd.read_csv(SAMPLES / "problematic_hiring.csv", dtype=str)
    return {col: set(df[col].dropna()) for col in PII_COLUMNS}


def assert_no_raw_pii(text: str) -> None:
    values = raw_pii_values()
    for col in ["email", "phone", "ssn"]:
        for value in values[col]:
            assert value not in text, f"raw {col} value leaked"
    for col in ["first_name", "last_name"]:
        for value in values[col]:
            assert not re.search(rf"\b{re.escape(value)}\b", text), f"raw {col} value leaked"


def test_health(server):
    body = httpx.get(f"{server}/api/health").json()
    assert body["status"] == "ok"
    assert body["ai_enabled"] is False
    assert body["pii_detectors"]["regex"] is True


def test_frontend_is_served(server):
    r = httpx.get(f"{server}/")
    assert r.status_code == 200 and "Dataset Bias" in r.text
    assert httpx.get(f"{server}/app.js").status_code == 200


def test_problematic_sample_end_to_end(server):
    report = upload(server, "problematic_hiring.csv")
    by_cat = report["summary_stats"]["issues_by_category"]
    assert by_cat["bias"] >= 3
    assert by_cat["missing_data"] >= 2
    pii_types = {f["details"]["pii_type"] for f in report["findings"] if f["category"] == "pii"}
    assert len(pii_types) >= 3
    assert report["risk_level"] in {"High", "Critical"}
    assert all(f["explanation"] and f["recommendation"] for f in report["findings"])

    sid = report["session_id"]
    for fmt, marker in [("html", "<!doctype html>"), ("md", "# Data Card")]:
        r = httpx.get(f"{server}/api/download/{sid}/datacard/{fmt}", timeout=30)
        assert r.status_code == 200
        assert len(r.text) > 1000 and marker in r.text
        assert_no_raw_pii(r.text)
    assert_no_raw_pii(str(report))


def test_reanalyze_end_to_end(server):
    sid = upload(server, "problematic_hiring.csv")["session_id"]
    r = httpx.post(f"{server}/api/reanalyze/{sid}", json={"label_column": "hired", "protected_columns": ["gender"]},
                   timeout=120)
    assert r.status_code == 200
    assert r.json()["detected_settings"]["protected_columns"] == ["gender"]


def test_clean_sample_end_to_end(server):
    report = upload(server, "clean_survey.csv")
    assert len(report["findings"]) <= 2
    assert all(f["severity"] == "low" for f in report["findings"])


def test_bundled_sample_endpoint(server):
    names = [d["name"] for d in httpx.get(f"{server}/api/sample-datasets").json()["datasets"]]
    assert names[:2] == ["problematic_hiring.csv", "clean_survey.csv"]
    r = httpx.post(f"{server}/api/analyze-sample/clean_survey.csv", timeout=120)
    assert r.status_code == 200
