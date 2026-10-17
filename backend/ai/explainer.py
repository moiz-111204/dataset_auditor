"""LLM explanation layer with template fallback (placeholder)."""

from backend.models.schemas import Finding


def explain_findings(findings: list[Finding]) -> list[Finding]:
    return findings
