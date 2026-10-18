"""AI explanation layer: turns raw findings into plain-language explanations and actions.

Workflow
--------
1. The analyzers produce findings locally (statistics, regex, NER). No AI is involved there.
2. `explain_findings` builds a *sanitized* payload per finding: an id, category, severity,
   title, column names and aggregate numbers (shares, ratios, counts, p-values). Raw cell
   values, row data, group labels, label values and the human-readable descriptions (which
   can mention group names) are never included. See `finding_payload`.
3. All findings go to the Anthropic Messages API in ONE request, together with dataset-level
   aggregates (row/column counts, risk score). The model must answer with JSON matching
   RESPONSE_SCHEMA: per finding a 2-3 sentence explanation and one recommendation, plus a
   3-4 sentence overall summary and the top 3 priority actions.
4. The reply is validated with Pydantic. Invalid output is retried once, then abandoned.
5. Template fallback: with no ANTHROPIC_API_KEY, on any API error, a timeout (15 s by
   default), a refusal, or invalid output twice, every finding gets a built-in template
   explanation (`explanation_source="template"`) and the report says `ai_enabled=false`.
   The app is fully usable without a key.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from backend.models.schemas import Finding
from backend.utils import SEVERITY_ORDER, fmt_pct

logger = logging.getLogger(__name__)

# =====================================================================================
# PROMPTS AND MODEL SETTINGS (everything the model sees is defined in this block)
# =====================================================================================

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_EFFORT = "low"
MAX_OUTPUT_TOKENS = 8000

SYSTEM_PROMPT = """\
You are a responsible-AI reviewer. A data team is about to train a machine-learning model and \
an automated audit has screened their dataset for bias, missing data and personal data (PII). \
You explain each finding to someone who is not an expert in ML fairness or privacy.

You only ever receive aggregate statistics and column names, never the data itself. Use only \
the numbers you are given; do not invent figures, group names or causes. When a cause is \
uncertain, say what to check rather than asserting it. Be direct and practical, avoid jargon, \
and do not use markdown."""

USER_PROMPT_TEMPLATE = """\
Dataset overview: {row_count} rows, {column_count} columns. Automated risk score: \
{risk_score}/100 ({risk_level}).

Findings (JSON, one object per finding; "metrics" holds aggregate numbers only):
{findings_json}

Return JSON with:
- "findings": one entry per finding id above, each with
  - "explanation": 2-3 sentences on why this matters for responsible AI (harm to people, \
model quality, legal or privacy risk), using the numbers given.
  - "recommendation": one concrete action the team should take before training.
- "overall_summary": 3-4 sentences summarising the dataset's overall readiness and main risks.
- "priority_actions": exactly 3 actions for the whole dataset, most urgent first."""

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "explanation": {"type": "string"},
                    "recommendation": {"type": "string"},
                },
                "required": ["id", "explanation", "recommendation"],
                "additionalProperties": False,
            },
        },
        "overall_summary": {"type": "string"},
        "priority_actions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["findings", "overall_summary", "priority_actions"],
    "additionalProperties": False,
}

# =====================================================================================


class LLMFindingText(BaseModel):
    id: str
    explanation: str = Field(min_length=20, max_length=1500)
    recommendation: str = Field(min_length=10, max_length=800)


class LLMResponse(BaseModel):
    findings: list[LLMFindingText]
    overall_summary: str = Field(min_length=20, max_length=2500)
    priority_actions: list[str] = Field(min_length=1, max_length=5)


@dataclass
class ExplanationResult:
    findings: list[Finding]
    overall_summary: str
    priority_actions: list[str]
    ai_enabled: bool
    fallback_reason: str | None = None


def llm_settings() -> dict:
    """Model configuration from the environment. Never exposes the key itself."""
    try:
        timeout = float(os.getenv("LLM_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS))
    except ValueError:
        timeout = DEFAULT_TIMEOUT_SECONDS
    return {
        "api_key_set": bool(os.getenv("ANTHROPIC_API_KEY", "").strip()),
        "model": os.getenv("LLM_MODEL", "").strip() or DEFAULT_MODEL,
        "timeout": timeout,
        "effort": os.getenv("LLM_EFFORT", DEFAULT_EFFORT).strip(),
    }


def ai_available() -> bool:
    return llm_settings()["api_key_set"]


def _make_client(timeout: float):
    """Anthropic client, or None without a key. Retries off: the 15 s budget is total."""
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return None
    import anthropic

    return anthropic.Anthropic(api_key=key, timeout=timeout, max_retries=0)


# --- Sanitized payload -----------------------------------------------------------------

def _round(value: Any) -> Any:
    return round(value, 4) if isinstance(value, float) else value


def finding_metrics(f: Finding) -> dict:
    """Aggregate numbers only. Group labels, label values and cell values are dropped."""
    d = f.details
    check = d.get("check") or ("pii_scan" if f.category == "pii" else "other")
    m: dict[str, Any] = {"check": check}
    if check == "class_imbalance":
        m.update(imbalance_ratio=d.get("imbalance_ratio"), minority_share=d.get("minority_share"),
                 number_of_classes=len(d.get("distribution", {})))
    elif check == "demographic_disparity":
        rates = [v["rate"] for v in d.get("group_rates", {}).values()]
        flagged = d.get("flagged_groups", {})
        m.update(min_impact_ratio=d.get("min_impact_ratio"), threshold=d.get("threshold"),
                 groups_compared=len(rates), groups_below_threshold=len(flagged),
                 highest_group_rate=max(rates) if rates else None,
                 lowest_group_rate=min(rates) if rates else None,
                 small_groups_ignored=len(d.get("ignored_groups", {})))
    elif check == "proxy_correlation":
        m.update(statistic=d.get("statistic"), strength=d.get("strength"), threshold=d.get("threshold"))
    elif check == "column_missing":
        m.update(missing_share=d.get("missing_share"), missing_count=d.get("missing_count"),
                 completely_empty=d.get("recommendation") == "drop")
    elif check == "row_missing":
        m.update(rows_with_missing_share=d.get("rows_with_missing_share"),
                 rows_mostly_empty_share=d.get("rows_mostly_empty_share"))
    elif check == "missing_by_group":
        rates = list(d.get("group_missing_rates", {}).values())
        m.update(highest_group_missing_rate=max(rates) if rates else None,
                 lowest_group_missing_rate=min(rates) if rates else None,
                 rate_ratio=d.get("rate_ratio"), p_value=d.get("p_value"), groups_compared=len(rates))
    elif check == "missing_pattern":
        m.update(phi=d.get("phi"), rows_missing_both=d.get("rows_missing_both"))
    elif check == "pii_scan":
        m.update(pii_type=d.get("pii_type"), rows_affected=d.get("rows_affected"),
                 percent_rows=d.get("percent_rows"), detection_methods=d.get("detection_methods"))
    return {k: _round(v) for k, v in m.items() if v is not None}


def finding_payload(f: Finding, finding_id: str) -> dict:
    return {
        "id": finding_id,
        "category": f.category,
        "severity": f.severity,
        "title": f.title,
        "columns": list(f.columns),
        "metrics": finding_metrics(f),
    }


def build_request(findings: list[Finding], context: dict, settings: dict) -> dict:
    payload = [finding_payload(f, f"f{i + 1}") for i, f in enumerate(findings)]
    user = USER_PROMPT_TEMPLATE.format(
        row_count=int(context.get("row_count", 0)),
        column_count=int(context.get("column_count", 0)),
        risk_score=int(context.get("risk_score", 0)),
        risk_level=str(context.get("risk_level", "")),
        findings_json=json.dumps(payload, indent=1),
    )
    output_config: dict[str, Any] = {"format": {"type": "json_schema", "schema": RESPONSE_SCHEMA}}
    if settings["effort"]:
        output_config["effort"] = settings["effort"]
    return {
        "model": settings["model"],
        "max_tokens": MAX_OUTPUT_TOKENS,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user}],
        "output_config": output_config,
    }


def _parse_response(response, expected_ids: set[str]) -> LLMResponse:
    if getattr(response, "stop_reason", None) in {"refusal", "max_tokens"}:
        raise ValueError(f"unusable stop_reason {response.stop_reason}")
    text = next((b.text for b in response.content if getattr(b, "type", None) == "text"), None)
    if not text:
        raise ValueError("no text block in response")
    parsed = LLMResponse.model_validate_json(text)
    if expected_ids and not expected_ids & {item.id for item in parsed.findings}:
        raise ValueError("response does not reference any finding id")
    return parsed


# --- Templates ---------------------------------------------------------------------------

def template_text(f: Finding) -> tuple[str, str]:
    """Built-in explanation and recommendation for one finding."""
    try:
        return _template_text(f)
    except (KeyError, TypeError, ValueError):
        return (f.description, "Review this issue before training.")


def _template_text(f: Finding) -> tuple[str, str]:
    d = f.details
    check = d.get("check") or ("pii_scan" if f.category == "pii" else "")
    cols = ", ".join(f"'{c}'" for c in f.columns) or "the dataset"

    if check == "demographic_disparity" and "flagged_groups" not in d:
        return (
            f"Too few rows per group in '{d.get('protected_column')}' to compare outcomes "
            f"reliably. A model can still treat small groups unfairly; the data just cannot show "
            f"it yet.",
            "Collect more data for under-represented groups, or evaluate the trained model on a "
            "dedicated test set that covers them.",
        )
    if check == "class_imbalance":
        return (
            f"Only {fmt_pct(d['minority_share'])} of labelled rows belong to the rarest class "
            f"('{d['minority_class']}'), a {d['imbalance_ratio']}:1 imbalance. Models trained on "
            f"skewed labels tend to ignore the rare class, and overall accuracy hides that failure.",
            "Report per-class precision and recall instead of accuracy, and consider collecting "
            "more minority-class examples, re-weighting, or stratified resampling.",
        )
    if check == "demographic_disparity":
        flagged = ", ".join(f"'{g}'" for g in d["flagged_groups"])
        return (
            f"In '{d['protected_column']}', the group(s) {flagged} reach the positive outcome at "
            f"under 80% of the rate of '{d['reference_group']}' (lowest ratio "
            f"{d['min_impact_ratio']:.2f}). A model learns these historical patterns and can "
            f"turn them into automated, repeated discrimination.",
            f"Investigate why outcomes differ by '{d['protected_column']}' before training, and "
            f"measure fairness metrics (selection rate, equal opportunity) on the trained model "
            f"for each group.",
        )
    if check == "proxy_correlation":
        return (
            f"'{d['feature']}' is strongly tied to '{d['protected_column']}' ({d['statistic']} "
            f"{d['strength']:.2f}). Dropping the protected column does not remove the bias, "
            f"because the model can reconstruct it from this feature.",
            f"Decide whether '{d['feature']}' is truly needed; if it is, test the trained model's "
            f"outcomes across '{d['protected_column']}' groups rather than assuming it is neutral.",
        )
    if check == "column_missing":
        if d.get("recommendation") == "drop":
            return (
                f"{cols} contains no values at all, so it adds nothing but noise and confusion "
                f"to the dataset.",
                f"Drop {cols} before training, or fix the export if data was expected.",
            )
        return (
            f"{cols} is missing in {fmt_pct(d['missing_share'])} of rows. Dropping those rows "
            f"shrinks the data and may remove some people more than others, while imputing "
            f"invents values the model treats as real.",
            f"Find out why {cols} is missing, then choose deliberately between collecting it, "
            f"imputing it with a documented method, or adding a 'was missing' indicator.",
        )
    if check == "row_missing":
        return (
            f"{fmt_pct(d['rows_with_missing_share'])} of rows have at least one gap. Many tools "
            f"drop incomplete rows silently, so a large part of the data could vanish before "
            f"training without anyone noticing.",
            "Decide on an explicit missing-value strategy per column instead of relying on "
            "default row dropping, and record it in the data card.",
        )
    if check == "missing_by_group":
        return (
            f"'{d['column']}' is missing for {fmt_pct(d['group_missing_rates'][d['highest_group']])} "
            f"of '{d['highest_group']}' rows versus "
            f"{fmt_pct(d['group_missing_rates'][d['lowest_group']])} of '{d['lowest_group']}' "
            f"rows. Any handling of these gaps affects one group more, so the model may be less "
            f"accurate or less fair for them.",
            f"Investigate how '{d['column']}' was collected for each '{d['protected_column']}' "
            f"group and evaluate model performance separately for '{d['highest_group']}'.",
        )
    if check == "missing_pattern":
        a, b = d["columns"]
        return (
            f"'{a}' and '{b}' tend to be missing in the same rows (phi {d['phi']:.2f}). Shared "
            f"gaps often point to a systematic cause, such as a skipped form section or a source "
            f"that lacks both fields, which can bias whatever rows remain.",
            f"Trace where '{a}' and '{b}' come from and check whether the rows missing both share "
            f"other characteristics before imputing or dropping them.",
        )
    if check == "pii_scan":
        recs = {
            "ssn": "Remove the national ID column entirely; it is never a useful model feature.",
            "cnic": "Remove the CNIC column entirely; it is never a useful model feature.",
            "credit_card": "Delete payment card numbers immediately and check how they got into "
                           "this dataset.",
            "email": "Drop email addresses or replace them with a salted hash if records must be "
                     "linked.",
            "phone": "Drop phone numbers or replace them with a salted hash if records must be "
                     "linked.",
            "person_name": "Remove name columns and redact names from free text before training.",
            "address": "Remove street addresses or generalise them to a coarse region.",
            "ip_address": "Remove IP addresses or truncate them to a network prefix.",
            "date_of_birth": "Replace date of birth with an age band.",
        }
        return (
            f"{d.get('pii_label', 'Personal data')} appear in {d.get('rows_affected', 0)} rows "
            f"({d.get('percent_rows', 0)}%) across {cols}. Personal data in training sets can be "
            f"memorised and leaked by models and creates legal obligations under privacy laws.",
            recs.get(d.get("pii_type"), "Remove or pseudonymise this column before training."),
        )
    return (f.description, "Review this issue before training.")


def template_summary(findings: list[Finding], context: dict) -> tuple[str, list[str]]:
    counts = {s: sum(f.severity == s for f in findings) for s in SEVERITY_ORDER}
    score, level = context.get("risk_score", 0), context.get("risk_level", "Low")
    if not findings:
        return (
            f"No issues were found by the automated checks (risk score {score}/100, {level}). "
            f"This is a screening result, not a guarantee: review how the data was collected "
            f"and evaluate the trained model on each relevant group.",
            ["Document the dataset's origin and intended use in the data card.",
             "Evaluate the trained model separately for each relevant group.",
             "Re-run this audit whenever the data changes."],
        )
    names = {"bias": "bias", "missing_data": "missing data", "pii": "personal data (PII)"}
    cats = sorted({names.get(f.category, f.category) for f in findings})
    worst = sorted(findings, key=lambda f: SEVERITY_ORDER[f.severity])
    summary = (
        f"The audit found {len(findings)} issue(s) before training: {counts['critical']} critical, "
        f"{counts['high']} high, {counts['medium']} medium and {counts['low']} low, giving a risk "
        f"score of {score}/100 ({level}). Problems span {', '.join(cats)}. The most serious is: "
        f"{worst[0].title}. Address the critical and high items before using this data to train "
        f"a model."
    )
    actions, seen = [], set()
    for f in worst:
        key = f.details.get("check") or f.details.get("pii_type") or f.title
        if f.category == "pii":
            key = "pii"
        if key in seen:
            continue
        seen.add(key)
        rec = f.recommendation or template_text(f)[1]
        if f.category == "pii":
            rec = "Remove or pseudonymise personal data columns (" + ", ".join(
                sorted({c for g in findings if g.category == "pii" for c in g.columns})) + ")."
        actions.append(rec)
        if len(actions) == 3:
            break
    return summary, actions


def _apply_templates(findings: list[Finding]) -> list[Finding]:
    out = []
    for f in findings:
        explanation, recommendation = template_text(f)
        out.append(f.model_copy(update={
            "explanation": explanation, "recommendation": recommendation,
            "explanation_source": "template",
        }))
    return out


def _fallback(findings: list[Finding], context: dict, reason: str) -> ExplanationResult:
    templated = _apply_templates(findings)
    summary, actions = template_summary(templated, context)
    return ExplanationResult(templated, summary, actions, ai_enabled=False, fallback_reason=reason)


# --- Entry point -------------------------------------------------------------------------

def explain_findings(findings: list[Finding], context: dict | None = None, client=None) -> ExplanationResult:
    """Fill in explanations and recommendations; LLM first, templates as fallback.

    `context` holds dataset-level aggregates only: row_count, column_count, risk_score,
    risk_level. `client` can be injected for tests.
    """
    context = context or {}
    settings = llm_settings()
    if client is None:
        client = _make_client(settings["timeout"])
    if client is None:
        return _fallback(findings, context, "no API key configured")

    request = build_request(findings, context, settings)
    ids = {f"f{i + 1}": i for i in range(len(findings))}
    parsed = None
    for attempt in (1, 2):
        try:
            response = client.messages.create(**request)
        except Exception as exc:  # timeout, connection, auth, rate limit, server error
            logger.warning("LLM request failed (%s); using template explanations", type(exc).__name__)
            return _fallback(findings, context, f"API error ({type(exc).__name__})")
        try:
            parsed = _parse_response(response, set(ids))
            break
        except (ValidationError, ValueError) as exc:
            logger.warning("LLM returned invalid output on attempt %d (%s)", attempt, type(exc).__name__)
    if parsed is None:
        return _fallback(findings, context, "invalid model output")

    by_id = {item.id: item for item in parsed.findings if item.id in ids}
    explained = []
    for fid, idx in ids.items():
        f = findings[idx]
        item = by_id.get(fid)
        if item:
            explained.append(f.model_copy(update={
                "explanation": item.explanation.strip(),
                "recommendation": item.recommendation.strip(),
                "explanation_source": "llm",
            }))
        else:  # the model skipped this finding
            explanation, recommendation = template_text(f)
            explained.append(f.model_copy(update={
                "explanation": explanation, "recommendation": recommendation,
                "explanation_source": "template",
            }))
    actions = [a.strip() for a in parsed.priority_actions if a.strip()][:3]
    return ExplanationResult(explained, parsed.overall_summary.strip(), actions, ai_enabled=True)
