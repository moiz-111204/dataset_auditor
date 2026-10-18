"""Data card generator.

Builds a Markdown and a self-contained HTML data card from an AnalysisReport and the
in-memory DataFrame, loosely following Google's Data Cards and "Datasheets for Datasets".
Cards never contain PII values: columns flagged as PII show no examples, and examples are
shown only for low-cardinality category columns (at most 10 distinct short values).
"""

from __future__ import annotations

import html
import re
from pathlib import Path

import pandas as pd

from backend.analyzers.missing_data import missing_mask
from backend.models.schemas import AnalysisReport, Finding
from backend.utils import SEVERITY_ORDER

MAX_EXAMPLE_CATEGORIES = 10
MAX_EXAMPLE_LENGTH = 30

LIMITATIONS = [
    "This card comes from an automated screening aid. It is not a compliance certification or "
    "a legal opinion, and passing it does not make a dataset fair or safe.",
    "Protected attributes are detected from column names; attributes with unusual names, or "
    "encoded only indirectly, are missed unless set manually.",
    "PII detection uses patterns and a small NER model. It can miss PII in unusual formats or "
    "languages and can flag values that only look like PII. Free-text NER runs on a sample.",
    "Statistical checks need enough rows: groups under 20 rows are skipped and small datasets "
    "can hide or exaggerate disparities.",
    "Proxy checks look at one feature at a time; combinations of features that together reveal "
    "a protected attribute are not detected.",
    "It cannot judge label quality, measurement bias, consent, or whether the data represents "
    "the population the model will be used on.",
]

METHODOLOGY = [
    ("Class imbalance", "Label class shares; flagged when a class is under 10% or the largest-"
     "to-smallest ratio exceeds 3:1 (critical > 10:1, high > 5:1)."),
    ("Demographic disparity", "Favourable-outcome rate per protected group (numeric attributes "
     "binned) compared with the four-fifths rule; critical below 0.6, high below 0.8, but only "
     "for gaps that are statistically significant (Fisher's exact test, p < 0.05); other gaps "
     "are low severity. Groups under 20 rows are skipped. For 1/0 labels named after a bad "
     "event (default, fraud, churn), 0 counts as favourable."),
    ("Proxy correlation", "Association of each feature with each protected attribute: Pearson r, "
     "bias-corrected Cramer's V or correlation ratio; medium above 0.5, high above 0.7. ID-like "
     "columns are skipped."),
    ("Missing data", "Missing share per column (medium > 5%, high > 20%, critical > 50%), rows "
     "with gaps (> 10%), chi-squared test plus rate ratio of missingness across protected groups, "
     "and co-occurring gaps (phi > 0.5). Placeholders such as '?', '-' and 'unknown' count as "
     "missing."),
    ("PII scan", "Regex and heuristic rules (emails, phones, SSN-style IDs, Pakistani CNICs, Luhn-checked card "
     "numbers, addresses, IPs, name and date-of-birth columns) plus Presidio NER on up to 200 "
     "values per text column when available."),
    ("Risk score", "Critical 25 points each (max 60), high 10 (max 30), medium 3 (max 15), low 1 "
     "(max 5), capped at 100. Critical above 75, High above 50, Medium above 25."),
]


# --- Shared content ----------------------------------------------------------------------

def pii_columns(report: AnalysisReport) -> set[str]:
    return {c for f in report.findings if f.category == "pii" for c in f.columns}


def feature_rows(report: AnalysisReport, df: pd.DataFrame) -> list[dict]:
    flagged = pii_columns(report)
    settings = report.detected_settings
    # Categories are listed only for the label and protected columns (the groups the audit
    # reports on). Other columns never show values, so PII the scanner missed cannot leak here.
    category_columns = {settings.label_column, *settings.protected_columns} - {None}
    mask = missing_mask(df)
    rows = []
    for idx, col in enumerate(df.columns):
        col = str(col)
        series = df.iloc[:, idx]
        uniques = series.dropna().unique()
        if col in flagged:
            examples = "withheld (PII)"
        elif (col in category_columns and len(uniques) <= MAX_EXAMPLE_CATEGORIES
              and not pd.api.types.is_float_dtype(series)
              and all(len(str(v)) <= MAX_EXAMPLE_LENGTH for v in uniques)):
            examples = ", ".join(sorted(str(v) for v in uniques)) or "-"
        else:
            examples = "-"
        rows.append({
            "name": col,
            "dtype": str(series.dtype),
            "unique": int(len(uniques)),
            "missing_pct": round(100 * float(mask.iloc[:, idx].mean()), 1),
            "examples": examples,
            "pii": col in flagged,
        })
    return rows


def by_category(report: AnalysisReport, category: str) -> list[Finding]:
    return sorted((f for f in report.findings if f.category == category),
                  key=lambda f: SEVERITY_ORDER[f.severity])


def methodology_ai_line(report: AnalysisReport) -> str:
    ai = report.summary_stats.get("ai", {})
    if report.ai_enabled:
        return (f"Explanations and recommendations were written by an LLM ({ai.get('model')}) from "
                f"aggregate statistics only; no cell values were sent.")
    reason = ai.get("fallback_reason") or "AI disabled"
    return f"Explanations and recommendations come from built-in templates ({reason})."


def detectors_line(report: AnalysisReport) -> str:
    det = report.summary_stats.get("pii_detectors", {})
    if det.get("presidio_ner"):
        return f"Regex rules and Presidio NER ({det.get('ner_model')})."
    return f"Regex rules only (Presidio NER not used: {det.get('presidio_reason') or 'unavailable'})."


def recommendations(report: AnalysisReport) -> list[tuple[str, str]]:
    seen, out = set(), []
    for f in sorted(report.findings, key=lambda f: SEVERITY_ORDER[f.severity]):
        if f.recommendation and f.recommendation not in seen:
            seen.add(f.recommendation)
            out.append((f.severity, f.recommendation))
    return out


# --- Markdown ----------------------------------------------------------------------------

def _md(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _md_findings(findings: list[Finding]) -> list[str]:
    if not findings:
        return ["No issues found by this check.", ""]
    lines = []
    for f in findings:
        lines.append(f"#### [{f.severity.upper()}] {_md(f.title)}")
        lines.append("")
        if f.columns:
            lines.append(f"- **Columns:** {', '.join(_md(c) for c in f.columns)}")
        lines.append(f"- **Finding:** {_md(f.description)}")
        if f.explanation:
            lines.append(f"- **Why it matters:** {_md(f.explanation)}")
        if f.recommendation:
            lines.append(f"- **Recommendation:** {_md(f.recommendation)}")
        lines.append("")
    return lines


def generate_markdown(report: AnalysisReport, df: pd.DataFrame) -> str:
    sev = report.summary_stats.get("issues_by_severity", {})
    settings = report.detected_settings
    lines = [
        f"# Data Card: {_md(report.dataset_name)}",
        "",
        "_Generated by Dataset Bias & Privacy Auditor. A screening aid, not a compliance guarantee._",
        "",
        "## Dataset Overview",
        "",
        "| Property | Value |",
        "|---|---|",
        f"| Dataset name | {_md(report.dataset_name)} |",
        f"| Rows | {report.row_count:,} |",
        f"| Columns | {report.column_count} |",
        f"| Analysis date | {report.timestamp:%Y-%m-%d %H:%M} UTC |",
        f"| Risk score | {report.risk_score}/100 ({report.risk_level}) |",
        f"| Label column | {_md(settings.label_column or 'not set')} |",
        f"| Protected columns | {_md(', '.join(settings.protected_columns) or 'none')} |",
        "",
        "## Feature Descriptions",
        "",
        "| Column | Type | Unique values | Missing | Example categories |",
        "|---|---|---|---|---|",
    ]
    for row in feature_rows(report, df):
        lines.append(f"| {_md(row['name'])} | {row['dtype']} | {row['unique']} | "
                     f"{row['missing_pct']}% | {_md(row['examples'])} |")
    lines += ["", "## Bias Audit Results", ""]
    lines += _md_findings(by_category(report, "bias"))

    lines += ["## Missing Data Assessment", ""]
    missing_cols = [r for r in feature_rows(report, df) if r["missing_pct"] > 0]
    if missing_cols:
        lines += ["| Column | Missing |", "|---|---|"]
        lines += [f"| {_md(r['name'])} | {r['missing_pct']}% |" for r in missing_cols]
        lines.append("")
    else:
        lines += ["No missing values were found.", ""]
    lines += _md_findings(by_category(report, "missing_data"))

    lines += ["## Privacy / PII Scan Results", "", f"Detectors: {detectors_line(report)}", "",
              "Actual PII values are never shown in this card.", ""]
    pii = by_category(report, "pii")
    if pii:
        lines += ["| PII type | Severity | Columns | Rows affected |", "|---|---|---|---|"]
        for f in pii:
            lines.append(f"| {_md(f.details.get('pii_label', f.title))} | {f.severity} | "
                         f"{_md(', '.join(f.columns))} | {f.details.get('rows_affected', '-')} |")
        lines.append("")
    lines += _md_findings(pii)

    lines += [
        "## Risk Assessment",
        "",
        f"**Risk score:** {report.risk_score}/100 ({report.risk_level})",
        "",
        f"Issues: {sev.get('critical', 0)} critical, {sev.get('high', 0)} high, "
        f"{sev.get('medium', 0)} medium, {sev.get('low', 0)} low.",
        "",
    ]
    if report.overall_summary:
        lines += [_md(report.overall_summary), ""]
    lines += ["**Top priority actions:**", ""]
    lines += [f"{i}. {_md(a)}" for i, a in enumerate(report.priority_actions[:3], 1)] or ["None."]
    lines += ["", "## Recommendations", ""]
    recs = recommendations(report)
    lines += [f"- **{s.upper()}:** {_md(r)}" for s, r in recs] or ["- No specific recommendations."]
    lines += ["", "## Methodology", ""]
    lines += [f"- **{name}:** {text}" for name, text in METHODOLOGY]
    lines += [f"- **PII detectors used:** {detectors_line(report)}",
              f"- **Explanations:** {methodology_ai_line(report)}", "",
              "## Limitations", ""]
    lines += [f"- {item}" for item in LIMITATIONS]
    lines.append("")
    return "\n".join(lines)


# --- HTML --------------------------------------------------------------------------------

CSS = """
:root{--ink:#1f2328;--muted:#5b6470;--line:#d8dee4;--bg:#ffffff;--soft:#f6f8fa;
--critical:#c62828;--high:#e8590c;--medium:#c99700;--low:#1c64c8}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,-apple-system,
"Segoe UI",Roboto,Arial,sans-serif}
main{max-width:960px;margin:0 auto;padding:32px 20px 48px}
h1{font-size:28px;margin:0 0 4px}h2{font-size:20px;margin:36px 0 12px;padding-bottom:6px;
border-bottom:2px solid var(--line)}h3{font-size:16px;margin:0}
.sub{color:var(--muted);margin:0 0 24px}
table{width:100%;border-collapse:collapse;margin:8px 0 16px;font-size:14px}
th,td{text-align:left;padding:7px 9px;border-bottom:1px solid var(--line);vertical-align:top;
word-break:break-word}
th{background:var(--soft);font-weight:600}
.badge{display:inline-block;padding:2px 9px;border-radius:999px;font-size:12px;font-weight:700;
letter-spacing:.03em;text-transform:uppercase;color:#fff;white-space:nowrap}
.critical{background:var(--critical)}.high{background:var(--high)}
.medium{background:var(--medium);color:#1f1a00}.low{background:var(--low)}
.finding{border:1px solid var(--line);border-left:5px solid var(--line);border-radius:8px;
padding:12px 14px;margin:10px 0;break-inside:avoid}
.finding.sev-critical{border-left-color:var(--critical)}.finding.sev-high{border-left-color:var(--high)}
.finding.sev-medium{border-left-color:var(--medium)}.finding.sev-low{border-left-color:var(--low)}
.finding header{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:6px}
.finding p{margin:6px 0}.label{font-weight:600}
.cols{color:var(--muted);font-size:13px}
.score{display:flex;gap:18px;align-items:center;flex-wrap:wrap;background:var(--soft);
border-radius:10px;padding:16px 18px}
.score .num{font-size:40px;font-weight:800;line-height:1}
.pii-tag{color:var(--critical);font-weight:600}
.empty{color:var(--muted);font-style:italic}
footer{margin-top:40px;color:var(--muted);font-size:13px}
@media print{body{font-size:12px}main{padding:0}h2{break-after:avoid}
.badge{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
"""


def _e(text: object) -> str:
    return html.escape(str(text), quote=True)


def _badge(severity: str) -> str:
    return f'<span class="badge {_e(severity)}">{_e(severity)}</span>'


def _html_findings(findings: list[Finding]) -> str:
    if not findings:
        return '<p class="empty">No issues found by this check.</p>'
    parts = []
    for f in findings:
        cols = f'<div class="cols">Columns: {_e(", ".join(f.columns))}</div>' if f.columns else ""
        expl = f'<p><span class="label">Why it matters:</span> {_e(f.explanation)}</p>' if f.explanation else ""
        rec = f'<p><span class="label">Recommendation:</span> {_e(f.recommendation)}</p>' if f.recommendation else ""
        parts.append(
            f'<article class="finding sev-{_e(f.severity)}"><header>{_badge(f.severity)}'
            f'<h3>{_e(f.title)}</h3></header>{cols}<p>{_e(f.description)}</p>{expl}{rec}</article>'
        )
    return "\n".join(parts)


def generate_html(report: AnalysisReport, df: pd.DataFrame) -> str:
    sev = report.summary_stats.get("issues_by_severity", {})
    settings = report.detected_settings
    features = feature_rows(report, df)
    feature_html = "".join(
        f"<tr><td>{_e(r['name'])}</td><td>{_e(r['dtype'])}</td><td>{r['unique']}</td>"
        f"<td>{r['missing_pct']}%</td><td>"
        + (f'<span class="pii-tag">{_e(r["examples"])}</span>' if r["pii"] else _e(r["examples"]))
        + "</td></tr>"
        for r in features
    )
    missing_cols = [r for r in features if r["missing_pct"] > 0]
    missing_table = (
        "<table><thead><tr><th>Column</th><th>Missing</th></tr></thead><tbody>"
        + "".join(f"<tr><td>{_e(r['name'])}</td><td>{r['missing_pct']}%</td></tr>" for r in missing_cols)
        + "</tbody></table>"
    ) if missing_cols else '<p class="empty">No missing values were found.</p>'
    pii = by_category(report, "pii")
    pii_table = (
        "<table><thead><tr><th>PII type</th><th>Severity</th><th>Columns</th><th>Rows affected</th>"
        "</tr></thead><tbody>"
        + "".join(
            f"<tr><td>{_e(f.details.get('pii_label', f.title))}</td><td>{_badge(f.severity)}</td>"
            f"<td>{_e(', '.join(f.columns))}</td><td>{_e(f.details.get('rows_affected', '-'))}</td></tr>"
            for f in pii)
        + "</tbody></table>"
    ) if pii else ""
    actions = "".join(f"<li>{_e(a)}</li>" for a in report.priority_actions[:3]) or "<li>None.</li>"
    recs = "".join(f"<li>{_badge(s)} {_e(r)}</li>" for s, r in recommendations(report)) \
        or "<li>No specific recommendations.</li>"
    method = "".join(f"<li><strong>{_e(n)}:</strong> {_e(t)}</li>" for n, t in METHODOLOGY)
    limits = "".join(f"<li>{_e(item)}</li>" for item in LIMITATIONS)
    summary = f"<p>{_e(report.overall_summary)}</p>" if report.overall_summary else ""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Data Card: {_e(report.dataset_name)}</title>
<style>{CSS}</style>
</head>
<body>
<main>
<h1>Data Card: {_e(report.dataset_name)}</h1>
<p class="sub">Generated by Dataset Bias &amp; Privacy Auditor. A screening aid, not a compliance guarantee.</p>

<h2>Dataset Overview</h2>
<table><tbody>
<tr><th>Dataset name</th><td>{_e(report.dataset_name)}</td></tr>
<tr><th>Rows</th><td>{report.row_count:,}</td></tr>
<tr><th>Columns</th><td>{report.column_count}</td></tr>
<tr><th>Analysis date</th><td>{report.timestamp:%Y-%m-%d %H:%M} UTC</td></tr>
<tr><th>Risk score</th><td>{report.risk_score}/100 ({_e(report.risk_level)})</td></tr>
<tr><th>Label column</th><td>{_e(settings.label_column or "not set")}</td></tr>
<tr><th>Protected columns</th><td>{_e(", ".join(settings.protected_columns) or "none")}</td></tr>
</tbody></table>

<h2>Feature Descriptions</h2>
<table><thead><tr><th>Column</th><th>Type</th><th>Unique values</th><th>Missing</th>
<th>Example categories</th></tr></thead><tbody>{feature_html}</tbody></table>

<h2>Bias Audit Results</h2>
{_html_findings(by_category(report, "bias"))}

<h2>Missing Data Assessment</h2>
{missing_table}
{_html_findings(by_category(report, "missing_data"))}

<h2>Privacy / PII Scan Results</h2>
<p>Detectors: {_e(detectors_line(report))} Actual PII values are never shown in this card.</p>
{pii_table}
{_html_findings(pii)}

<h2>Risk Assessment</h2>
<div class="score"><div class="num">{report.risk_score}<small>/100</small></div>
<div><strong>{_e(report.risk_level)} risk</strong><br>
{_badge("critical")} {sev.get("critical", 0)} &nbsp; {_badge("high")} {sev.get("high", 0)} &nbsp;
{_badge("medium")} {sev.get("medium", 0)} &nbsp; {_badge("low")} {sev.get("low", 0)}</div></div>
{summary}
<h3>Top priority actions</h3>
<ol>{actions}</ol>

<h2>Recommendations</h2>
<ul>{recs}</ul>

<h2>Methodology</h2>
<ul>{method}
<li><strong>PII detectors used:</strong> {_e(detectors_line(report))}</li>
<li><strong>Explanations:</strong> {_e(methodology_ai_line(report))}</li></ul>

<h2>Limitations</h2>
<ul>{limits}</ul>

<footer>Session {_e(report.session_id)} &middot; analysed in memory; the uploaded file was not stored.</footer>
</main>
</body>
</html>
"""


def save_data_cards(report: AnalysisReport, df: pd.DataFrame, out_dir: str | Path) -> tuple[Path, Path]:
    """Write both cards to out_dir (for CLI or offline use; the web app streams them instead)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(report.dataset_name).stem).strip("._") or "dataset"
    md_path = out / f"{stem}_data_card.md"
    html_path = out / f"{stem}_data_card.html"
    md_path.write_text(generate_markdown(report, df), encoding="utf-8")
    html_path.write_text(generate_html(report, df), encoding="utf-8")
    return md_path, html_path
