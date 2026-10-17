import tempfile

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend import app as app_module
from backend.app import app, rate_limiter, store
from backend.models.schemas import AnalysisReport, AnalysisSettings
from backend.session_store import SessionStore, max_sessions_from_env


@pytest.fixture(autouse=True)
def fresh_state():
    store.clear()
    rate_limiter.reset()
    yield
    store.clear()
    rate_limiter.reset()


@pytest.fixture
def client():
    return TestClient(app)


def hiring_csv(n=120, seed=0, marker="alpha") -> bytes:
    rng = np.random.default_rng(seed)
    gender = rng.choice(["F", "M"], n)
    df = pd.DataFrame({
        "email": [f"{marker}{i}@example.com" for i in range(n)],
        "gender": gender,
        "race": rng.choice(["A", "B"], n),
        "score": rng.integers(0, 100, n),
        "hired": np.where(gender == "M", rng.random(n) < 0.7, rng.random(n) < 0.25).astype(int),
    })
    return df.to_csv(index=False).encode()


def upload(client, data: bytes, name="data.csv", **form):
    return client.post("/api/upload", files={"file": (name, data, "text/csv")}, data=form)


def test_root_serves_frontend(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Dataset Bias" in r.text
    assert "Content-Security-Policy" in r.headers


def test_health_reports_ai_status_without_key(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["ai_enabled"] is False
    assert "api_key" not in r.text.lower() and "sk-" not in r.text


def test_upload_returns_report_with_session_and_settings(client):
    r = upload(client, hiring_csv())
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["session_id"]) == 36
    assert body["detected_settings"] == {"label_column": "hired", "protected_columns": ["gender", "race"]}
    assert body["row_count"] == 120
    assert any(f["category"] == "pii" for f in body["findings"])
    assert all(f["explanation"] for f in body["findings"])
    assert "alpha0@example.com" not in r.text


def test_upload_with_explicit_settings(client):
    r = upload(client, hiring_csv(), label_column="hired", protected_columns='["gender"]')
    assert r.json()["detected_settings"]["protected_columns"] == ["gender"]
    r2 = upload(client, hiring_csv(), label_column="hired", protected_columns="race, gender")
    assert r2.json()["detected_settings"]["protected_columns"] == ["race", "gender"]


def test_upload_rejects_non_csv(client):
    r = upload(client, b"a,b\n1,2\n", name="data.xlsx")
    assert r.status_code == 415
    r = upload(client, b"PK\x03\x04\x00\x00binary", name="renamed.csv")
    assert r.status_code == 415


def test_upload_rejects_oversized_file(client):
    big = b"a\n" + b"1\n" * (6 * 1024 * 1024)  # ~12 MB, rejected by the body limit
    r = upload(client, big)
    assert r.status_code == 413
    just_over = b"a\n" + b"x" * (10 * 1024 * 1024)  # passes the body cap, caught by the loader
    r = upload(client, just_over)
    assert r.status_code == 413
    assert "10 MB" in r.json()["detail"]


def test_upload_rejects_too_many_columns(client):
    header = ",".join(f"c{i}" for i in range(150))
    r = upload(client, f"{header}\n{','.join('1' * 150)}\n".encode())
    assert r.status_code == 413
    assert "150 columns" in r.json()["detail"]


def test_upload_rejects_empty_malformed_and_header_only(client):
    assert upload(client, b"").status_code == 400
    assert upload(client, b"a,b,c\n").status_code == 400
    r = upload(client, b'a,b\n1,2\n3,4,5,6\n"bad,7\n')
    assert r.status_code == 400
    assert "parse" in r.json()["detail"]


def test_upload_without_file_field(client):
    r = client.post("/api/upload", data={"label_column": "x"}, files={"other": ("x.txt", b"x")})
    assert r.status_code == 400


def test_upload_stays_in_memory(client, monkeypatch):
    """A 3 MB upload must never roll over from memory into a temporary file on disk."""
    def no_rollover(self):
        raise AssertionError("upload was spooled to disk")
    monkeypatch.setattr(tempfile.SpooledTemporaryFile, "rollover", no_rollover)
    rows = "\n".join(f"{i},{'x' * 90},{i % 2}" for i in range(40_000))
    data = ("id,comment,label\n" + rows + "\n").encode()
    assert len(data) > 3 * 1024 * 1024
    r = upload(client, data)
    assert r.status_code == 200, r.text


def test_reanalyze_updates_settings_in_same_session(client):
    sid = upload(client, hiring_csv()).json()["session_id"]
    r = client.post(f"/api/reanalyze/{sid}", json={"label_column": "hired", "protected_columns": ["race"]})
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == sid
    assert body["detected_settings"]["protected_columns"] == ["race"]
    assert store.get(sid).settings.protected_columns == ["race"]


def test_reanalyze_errors(client):
    sid = upload(client, hiring_csv()).json()["session_id"]
    r = client.post(f"/api/reanalyze/{sid}", json={"label_column": "nope", "protected_columns": []})
    assert r.status_code == 400
    r = client.post(f"/api/reanalyze/{sid}", json={"label_column": 5, "protected_columns": "x"})
    assert r.status_code == 422
    r = client.post("/api/reanalyze/does-not-exist", json={"label_column": None, "protected_columns": []})
    assert r.status_code == 404


def test_expired_session_returns_404(client, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(store, "clock", lambda: now[0])
    sid = upload(client, hiring_csv()).json()["session_id"]
    assert client.get(f"/api/download/{sid}/datacard/md").status_code == 200
    now[0] += 31 * 60
    r = client.get(f"/api/download/{sid}/datacard/md")
    assert r.status_code == 404
    assert "expired" in r.json()["detail"]
    assert client.post(f"/api/reanalyze/{sid}", json={}).status_code == 404


def test_download_datacards(client):
    sid = upload(client, hiring_csv(), name="My Data.csv").json()["session_id"]
    r = client.get(f"/api/download/{sid}/datacard/html")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert 'filename="My_Data_data_card.html"' in r.headers["content-disposition"]
    assert "<!doctype html>" in r.text and "alpha0@example.com" not in r.text
    r = client.get(f"/api/download/{sid}/datacard/md")
    assert r.status_code == 200 and r.text.startswith("# Data Card: My Data.csv")
    assert client.get(f"/api/download/{sid}/datacard/pdf").status_code == 400
    assert client.get("/api/download/missing/datacard/md").status_code == 404


def test_sessions_are_isolated(client):
    a = upload(client, hiring_csv(marker="tabone"), name="first.csv").json()
    b = upload(client, hiring_csv(seed=1, marker="tabtwo"), name="second.csv").json()
    assert a["session_id"] != b["session_id"]
    card_a = client.get(f"/api/download/{a['session_id']}/datacard/md").text
    card_b = client.get(f"/api/download/{b['session_id']}/datacard/md").text
    assert "first.csv" in card_a and "second.csv" not in card_a
    assert "second.csv" in card_b and "first.csv" not in card_b


def test_sample_endpoints_validate_names(client, tmp_path, monkeypatch):
    (tmp_path / "demo.csv").write_bytes(hiring_csv())
    (tmp_path / "notes.txt").write_text("not a sample")
    monkeypatch.setattr(app_module, "SAMPLE_DIR", tmp_path)
    listed = client.get("/api/sample-datasets").json()["datasets"]
    assert [d["name"] for d in listed] == ["demo.csv"]
    r = client.post("/api/analyze-sample/demo.csv")
    assert r.status_code == 200 and r.json()["dataset_name"] == "demo.csv"
    for bad in ["notes.txt", "missing.csv", "..%5C..%5Capp.py"]:
        assert client.post(f"/api/analyze-sample/{bad}").status_code == 404
    # An encoded slash leaves the route entirely; nothing is analysed or served.
    r = client.post("/api/analyze-sample/..%2Fbackend%2Fapp.py")
    assert r.status_code in (404, 405) and "import" not in r.text


def test_rate_limit(client, monkeypatch):
    monkeypatch.setattr(rate_limiter, "limit", 3)
    codes = [client.get("/api/sample-datasets").status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    r = client.get("/api/sample-datasets")
    assert int(r.headers["retry-after"]) >= 1
    assert client.get("/api/health").status_code == 200  # health is not rate limited


def test_unexpected_errors_return_json_500(client, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("secret cell value jane@example.com")
    monkeypatch.setattr(app_module, "run_analysis", boom)
    r = upload(client, hiring_csv())
    assert r.status_code == 500
    assert "jane@example.com" not in r.text


def make_report(sid: str) -> AnalysisReport:
    return AnalysisReport(session_id=sid, dataset_name="x.csv", row_count=1, column_count=1,
                          detected_settings=AnalysisSettings())


def test_session_store_limits_and_lru():
    now = [0.0]
    s = SessionStore(ttl_seconds=60, max_sessions=3, clock=lambda: now[0])
    df = pd.DataFrame({"a": [1]})
    for i in range(3):
        s.save(make_report(f"s{i}"), df)
    s.get("s0")  # touch s0 so s1 becomes least recently used
    s.save(make_report("s3"), df)
    assert len(s) == 3 and s.get("s1") is None and s.get("s0") is not None
    now[0] = 61
    assert s.get("s0") is None and len(s) == 0


def test_max_sessions_comes_from_env(monkeypatch):
    assert store.max_sessions == max_sessions_from_env()  # the app's store uses the setting
    monkeypatch.delenv("MAX_SESSIONS", raising=False)
    assert max_sessions_from_env() == 50
    monkeypatch.setenv("MAX_SESSIONS", "10")
    assert max_sessions_from_env() == 10
    monkeypatch.setenv("MAX_SESSIONS", "0")
    assert max_sessions_from_env() == 1
