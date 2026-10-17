"""Missing data analyzer (placeholder)."""

import pandas as pd

from backend.models.schemas import AnalysisSettings, Finding


def analyze_missing(df: pd.DataFrame, settings: AnalysisSettings) -> list[Finding]:
    return []
