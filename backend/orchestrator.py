"""Analysis pipeline: CSV bytes in memory -> DataFrame -> analyzers -> AnalysisReport.

The DataFrame is returned next to the report (for the session store and data card) and is
never serialized into the report itself. Nothing here writes to disk.
"""

from __future__ import annotations

import io
import logging
import os
import uuid

import pandas as pd

from backend.analyzers.bias_detector import (
    detect_bias, group_positive_rates, label_distribution, resolve_settings,
)
from backend.analyzers.missing_data import analyze_missing, missing_by_column
from backend.analyzers.pii_scanner import detector_status, scan_pii
from backend.models.schemas import AnalysisReport, AnalysisSettings, Finding

logger = logging.getLogger(__name__)

MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 50_000
ENCODINGS = ["utf-8", "utf-8-sig", "latin-1"]

SCORE_RULES = {  # severity: (points each, cap)
    "critical": (25, 60),
    "high": (10, 30),
    "medium": (3, 15),
    "low": (1, 5),
}


class DatasetError(ValueError):
    """A problem with the uploaded data, with the HTTP status the API should return."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _decode(data: bytes) -> tuple[str, str]:
    for encoding in ENCODINGS:
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if encoding == "utf-8" and text.startswith("﻿"):
            continue  # let utf-8-sig strip the byte-order mark
        return text, encoding
    raise DatasetError("Could not decode the file as UTF-8 or Latin-1 text.")


def dedupe_columns(columns) -> list[str]:
    """String column names, stripped, with duplicates renamed 'name.1', 'name.2', ..."""
    out: list[str] = []
    used: set[str] = set()
    for col in columns:
        base = str(col).strip() or "unnamed"
        name, i = base, 0
        while name in used:
            i += 1
            name = f"{base}.{i}"
        used.add(name)
        out.append(name)
    return out


def load_csv(data: bytes) -> pd.DataFrame:
    """Parse CSV bytes held in memory. Raises DatasetError with a user-facing message."""
    if len(data) > MAX_BYTES:
        raise DatasetError("File is larger than the 10 MB limit.", status_code=413)
    if not data.strip():
        raise DatasetError("The file is empty.")
    text, _ = _decode(data)
    try:
        df = pd.read_csv(io.StringIO(text), low_memory=False)
    except pd.errors.EmptyDataError:
        raise DatasetError("The file is empty.") from None
    except (pd.errors.ParserError, ValueError) as exc:
        raise DatasetError(f"Could not parse the file as CSV: {str(exc).splitlines()[0][:200]}") from None
    if df.shape[1] == 0:
        raise DatasetError("The file has no columns.")
    if len(df) == 0:
        raise DatasetError("The file has a header but no data rows.")
    if len(df) > MAX_ROWS:
        raise DatasetError(f"The file has {len(df):,} rows; the limit is {MAX_ROWS:,}.", status_code=413)
    df.columns = dedupe_columns(df.columns)
    return df


def risk_score(findings: list[Finding]) -> tuple[int, str]:
    score = 0
    for severity, (points, cap) in SCORE_RULES.items():
        count = sum(1 for f in findings if f.severity == severity)
        score += min(points * count, cap)
    score = min(score, 100)
    if score > 75:
        level = "Critical"
    elif score > 50:
        level = "High"
    elif score > 25:
        level = "Medium"
    else:
        level = "Low"
    return score, level


def validate_settings(df: pd.DataFrame, settings: AnalysisSettings | None) -> None:
    if settings is None:
        return
    columns = set(df.columns)
    unknown = [c for c in [settings.label_column, *settings.protected_columns] if c and c not in columns]
    if unknown:
        raise DatasetError(f"Unknown column(s) in settings: {', '.join(unknown)}")


def build_charts(df: pd.DataFrame, settings: AnalysisSettings) -> dict:
    charts: dict = {"missing_by_column": missing_by_column(df), "label_distribution": {}, "group_rates": {}}
    label = settings.label_column
    if label:
        charts["label_distribution"] = label_distribution(df, label)
        for p in settings.protected_columns:
            info = group_positive_rates(df, label, p)
            if info and info["group_rates"]:
                charts["group_rates"][p] = {
                    "positive_label": info["positive_label"],
                    "rates": {g: round(v["rate"], 4) for g, v in info["group_rates"].items()},
                }
    return charts


def analyze_dataframe(
    df: pd.DataFrame,
    dataset_name: str,
    settings: AnalysisSettings | None = None,
    session_id: str | None = None,
) -> AnalysisReport:
    """Run every analyzer on an in-memory DataFrame and build the report."""
    df.columns = dedupe_columns(df.columns)
    validate_settings(df, settings)
    resolved = resolve_settings(df, settings)

    findings: list[Finding] = []
    findings += detect_bias(df, resolved)
    findings += analyze_missing(df, resolved)
    findings += scan_pii(df)

    score, level = risk_score(findings)
    severities = ["critical", "high", "medium", "low"]
    summary = {
        "total_issues": len(findings),
        "issues_by_severity": {s: sum(f.severity == s for f in findings) for s in severities},
        "issues_by_category": {
            c: sum(f.category == c for f in findings) for c in ["bias", "missing_data", "pii"]
        },
        "pii_detectors": detector_status(),
        "charts": build_charts(df, resolved),
    }
    return AnalysisReport(
        session_id=session_id or str(uuid.uuid4()),
        dataset_name=safe_dataset_name(dataset_name),
        row_count=len(df),
        column_count=df.shape[1],
        detected_settings=resolved,
        findings=findings,
        summary_stats=summary,
        risk_score=score,
        risk_level=level,
        ai_enabled=False,
    )


def safe_dataset_name(name: str | None) -> str:
    base = os.path.basename((name or "").replace("\\", "/")).strip()
    return base[:200] or "dataset.csv"


def run_analysis(
    data: bytes,
    dataset_name: str,
    settings: AnalysisSettings | None = None,
    session_id: str | None = None,
) -> tuple[AnalysisReport, pd.DataFrame]:
    """Full pipeline from uploaded bytes. Returns the report and the in-memory DataFrame."""
    df = load_csv(data)
    report = analyze_dataframe(df, dataset_name, settings, session_id)
    return report, df
