"""Pydantic models shared by the analyzers, the orchestrator and the API."""

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

Severity = Literal["critical", "high", "medium", "low"]
Category = Literal["bias", "missing_data", "pii"]


class Finding(BaseModel):
    """One issue found in a dataset. Never holds raw cell values."""

    severity: Severity
    category: Category
    title: str
    description: str
    details: dict[str, Any] = Field(default_factory=dict)
    columns: list[str] = Field(default_factory=list)
    explanation: str = ""  # filled later by the AI layer
    recommendation: str = ""  # filled later by the AI layer
    explanation_source: Literal["llm", "template"] = "template"


class AnalysisSettings(BaseModel):
    label_column: str | None = None
    protected_columns: list[str] = Field(default_factory=list)


class AnalysisReport(BaseModel):
    session_id: str
    dataset_name: str
    row_count: int
    column_count: int
    detected_settings: AnalysisSettings
    findings: list[Finding] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    summary_stats: dict[str, Any] = Field(default_factory=dict)
    risk_score: int = 0
    risk_level: str = "Low"
    ai_enabled: bool = False
    overall_summary: str = ""
    priority_actions: list[str] = Field(default_factory=list)
