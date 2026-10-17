"""Data card generator (placeholder)."""

import pandas as pd

from backend.models.schemas import AnalysisReport


def generate_markdown(report: AnalysisReport, df: pd.DataFrame) -> str:
    return ""


def generate_html(report: AnalysisReport, df: pd.DataFrame) -> str:
    return ""
