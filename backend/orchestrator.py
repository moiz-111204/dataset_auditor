"""Analysis pipeline (placeholder)."""

from backend.models.schemas import AnalysisReport, AnalysisSettings


def run_analysis(data: bytes, filename: str, settings: AnalysisSettings | None = None) -> AnalysisReport:
    raise NotImplementedError
