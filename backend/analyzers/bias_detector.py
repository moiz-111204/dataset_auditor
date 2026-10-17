"""Bias detector (placeholder)."""

import pandas as pd

from backend.models.schemas import AnalysisSettings, Finding


def detect_bias(df: pd.DataFrame, settings: AnalysisSettings) -> list[Finding]:
    return []
