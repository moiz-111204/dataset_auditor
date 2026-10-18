"""Bias detector.

Three checks, each producing Finding objects with plain-language "why it matters" text:

1. Class imbalance of the label column.
2. Demographic disparity: positive-outcome rate per group of each protected attribute,
   compared with the four-fifths (80%) rule. Numeric attributes such as age are binned first.
3. Proxy correlation: association between every other feature and each protected attribute,
   using Pearson r (numeric-numeric), bias-corrected Cramer's V (categorical-categorical) or
   the correlation ratio eta (numeric-categorical).

When settings are empty the label and protected columns are auto-detected from column names.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy.stats import chi2_contingency

from backend.models.schemas import AnalysisSettings, Finding
from backend.utils import (
    MIN_GROUP_SIZE, SEVERITY_ORDER, fmt_pct, group_labels, is_id_like, is_numeric_feature,
    label_text, name_tokens, normalize_column_name, resolve_positive_label, split_groups,
)

LABEL_NAMES = ["label", "target", "class", "outcome", "y", "hired", "approved", "default"]
# Common label names from public datasets; used only when the column has 2-10 distinct values.
EXTRA_LABEL_NAMES = [
    "survived", "churn", "churned", "exited", "attrition", "fraud", "is_fraud", "fraudulent",
    "recid", "is_recid", "two_year_recid", "readmitted", "loan_status", "admitted", "accepted",
    "passed", "promoted", "decision",
]
PROTECTED_KEYWORDS = {
    "gender", "sex", "sexual", "race", "ethnicity", "ethnic", "age", "religion", "religious",
    "disability", "disabilities", "disabled", "nationality", "citizenship", "marital", "caste",
    "pregnancy", "pregnant",
}
# Multi-word names, matched as whole tokens inside the normalized column name.
PROTECTED_PHRASES = {"national_origin", "native_country", "country_of_birth", "birth_country",
                     "country_of_origin"}
FOUR_FIFTHS = 0.8
PROXY_THRESHOLD = 0.5
MAX_CATEGORIES = 100  # categorical features with more levels are skipped for proxy checks
MIN_PAIRED_ROWS = 20


# --- Settings auto-detection -------------------------------------------------------------

def is_protected_name(name: object) -> bool:
    padded = f"_{normalize_column_name(name)}_"
    return bool(name_tokens(name) & PROTECTED_KEYWORDS) or any(f"_{p}_" in padded for p in PROTECTED_PHRASES)


def detect_protected_columns(df: pd.DataFrame) -> list[str]:
    return [str(c) for c in df.columns if is_protected_name(c)]


def detect_label_column(df: pd.DataFrame, exclude: list[str] | None = None) -> str | None:
    exclude = set(exclude or [])
    for wanted in LABEL_NAMES:
        for col in df.columns:
            if normalize_column_name(col) == wanted and str(col) not in exclude:
                return str(col)
    for wanted in EXTRA_LABEL_NAMES:
        for idx, col in enumerate(df.columns):
            if normalize_column_name(col) == wanted and str(col) not in exclude \
                    and 2 <= df.iloc[:, idx].nunique(dropna=True) <= 10:
                return str(col)
    if df.shape[1] >= 2:
        last = df.columns[-1]
        if str(last) not in exclude and 2 <= df.iloc[:, -1].nunique(dropna=True) <= 10:
            return str(last)
    return None


def detect_settings(df: pd.DataFrame) -> AnalysisSettings:
    protected = detect_protected_columns(df)
    label = detect_label_column(df, exclude=protected)
    return AnalysisSettings(label_column=label, protected_columns=protected)


def resolve_settings(df: pd.DataFrame, settings: AnalysisSettings | None) -> AnalysisSettings:
    """Auto-detect when settings are empty; drop unknown columns; keep label out of protected."""
    if settings is None or (settings.label_column is None and not settings.protected_columns):
        settings = detect_settings(df)
    columns = {str(c) for c in df.columns}
    label = settings.label_column if settings.label_column in columns else None
    protected = [c for c in dict.fromkeys(settings.protected_columns) if c in columns and c != label]
    return AnalysisSettings(label_column=label, protected_columns=protected)


# --- Chart helpers (also used by the orchestrator) ---------------------------------------

def label_distribution(df: pd.DataFrame, label: str) -> dict[str, int]:
    counts = df[label].dropna().value_counts()
    return {label_text(k): int(v) for k, v in counts.items()}


def group_positive_rates(df: pd.DataFrame, label: str, protected: str) -> dict | None:
    """Positive-outcome rate per group, or None when the label has fewer than two values."""
    labels = df[label]
    if labels.nunique(dropna=True) < 2:
        return None
    positive, reason = resolve_positive_label(labels, label)
    groups = group_labels(df[protected], protected)
    mask = labels.notna() & groups.notna()
    groups, outcome = groups[mask], (labels[mask] == positive)
    eligible, small = split_groups(groups)
    rates = {
        g: {"rate": float(outcome[groups == g].mean()), "count": int((groups == g).sum())}
        for g in eligible
    }
    return {
        "positive_label": label_text(positive),
        "positive_label_reason": reason,
        "group_rates": rates,
        "ignored_groups": small,
    }


# --- Association statistics --------------------------------------------------------------

def cramers_v(x: pd.Series, y: pd.Series) -> float:
    """Bias-corrected Cramer's V (Bergsma 2013)."""
    table = pd.crosstab(x, y)
    r, k = table.shape
    if r < 2 or k < 2:
        return 0.0
    n = table.to_numpy().sum()
    chi2 = chi2_contingency(table, correction=False)[0]
    phi2 = chi2 / n
    phi2_corr = max(0.0, phi2 - (k - 1) * (r - 1) / (n - 1))
    r_corr = r - (r - 1) ** 2 / (n - 1)
    k_corr = k - (k - 1) ** 2 / (n - 1)
    denom = min(k_corr - 1, r_corr - 1)
    return float(math.sqrt(phi2_corr / denom)) if denom > 0 else 0.0


def correlation_ratio(categories: pd.Series, values: pd.Series) -> float:
    """Eta: share of the numeric variable's spread explained by the categories (0 to 1)."""
    values = values.astype(float)
    total_ss = float(((values - values.mean()) ** 2).sum())
    if total_ss == 0:
        return 0.0
    stats = values.groupby(categories).agg(["mean", "count"])
    between_ss = float((stats["count"] * (stats["mean"] - values.mean()) ** 2).sum())
    return float(math.sqrt(between_ss / total_ss))


def association(df: pd.DataFrame, feature: str, protected: str) -> tuple[float, str] | None:
    """Return (strength 0-1, statistic name) for a column pair, or None if not measurable."""
    pair = df[[feature, protected]].dropna()
    if len(pair) < MIN_PAIRED_ROWS:
        return None
    f, p = pair[feature], pair[protected]
    f_num, p_num = is_numeric_feature(df[feature], feature), is_numeric_feature(df[protected], protected)
    if not f_num and f.nunique() > MAX_CATEGORIES:
        return None
    if f_num and p_num:
        if f.std() == 0 or p.std() == 0:
            return None
        return abs(float(np.corrcoef(f.astype(float), p.astype(float))[0, 1])), "Pearson correlation"
    if not f_num and not p_num:
        return cramers_v(f.astype(str), p.astype(str)), "Cramer's V"
    numeric, categorical = (f, p) if f_num else (p, f)
    return correlation_ratio(categorical.astype(str), numeric), "correlation ratio (eta)"


# --- Checks ------------------------------------------------------------------------------

def check_class_imbalance(df: pd.DataFrame, label: str) -> list[Finding]:
    dist = label_distribution(df, label)
    if len(dist) < 2 or len(dist) > 10:
        return []
    total = sum(dist.values())
    largest, smallest = max(dist, key=dist.get), min(dist, key=dist.get)
    ratio = dist[largest] / dist[smallest]
    min_share = dist[smallest] / total
    if ratio > 10:
        severity = "critical"
    elif ratio > 5:
        severity = "high"
    elif ratio > 3:
        severity = "medium"
    elif min_share < 0.10:
        severity = "low"
    else:
        return []
    shares = {k: round(v / total, 4) for k, v in dist.items()}
    return [Finding(
        severity=severity,
        category="bias",
        title=f"Class imbalance in '{label}' ({ratio:.1f}:1)",
        description=(
            f"The label '{label}' is unevenly split: '{smallest}' makes up only "
            f"{fmt_pct(min_share)} of {total} labelled rows, while '{largest}' makes up "
            f"{fmt_pct(dist[largest] / total)} (a {ratio:.1f}:1 ratio). A model trained on this "
            f"data can score well by mostly predicting '{largest}', so it may perform poorly "
            f"exactly on the rarer '{smallest}' cases."
        ),
        details={
            "check": "class_imbalance",
            "label_column": label,
            "distribution": dist,
            "shares": shares,
            "imbalance_ratio": round(ratio, 2),
            "minority_class": smallest,
            "minority_share": round(min_share, 4),
        },
        columns=[label],
    )]


def check_demographic_disparity(df: pd.DataFrame, label: str, protected: str) -> list[Finding]:
    info = group_positive_rates(df, label, protected)
    if info is None:
        return []
    rates, small = info["group_rates"], info["ignored_groups"]
    small_note = (
        f" Groups with fewer than {MIN_GROUP_SIZE} rows were not compared: "
        + ", ".join(f"'{g}' ({n} rows)" for g, n in small.items()) + "."
        if small else ""
    )
    if len(rates) < 2:
        if len(rates) + len(small) < 2:
            return []
        return [Finding(
            severity="low",
            category="bias",
            title=f"Too few rows to check fairness for '{protected}'",
            description=(
                f"Fewer than two groups in '{protected}' have at least {MIN_GROUP_SIZE} rows, "
                f"so outcome rates could not be compared reliably.{small_note} Collect more data "
                f"for under-represented groups before trusting a model on them."
            ),
            details={"check": "demographic_disparity", "protected_column": protected,
                     "label_column": label, "ignored_groups": small},
            columns=[protected, label],
        )]
    best = max(rates, key=lambda g: rates[g]["rate"])
    best_rate = rates[best]["rate"]
    if best_rate == 0:
        return []
    ratios = {g: round(v["rate"] / best_rate, 3) for g, v in rates.items()}
    flagged = {g: r for g, r in ratios.items() if r < FOUR_FIFTHS}
    if not flagged:
        return []
    worst = min(flagged, key=flagged.get)
    worst_ratio = flagged[worst]
    severity = "critical" if worst_ratio < 0.6 else "high"
    positive = info["positive_label"]
    group_text = "; ".join(
        f"'{g}' {fmt_pct(rates[g]['rate'])} (ratio {flagged[g]:.2f})"
        for g in sorted(flagged, key=flagged.get)
    )
    return [Finding(
        severity=severity,
        category="bias",
        title=f"Outcome disparity by '{protected}' (ratio {worst_ratio:.2f})",
        description=(
            f"Rows where '{label}' = '{positive}' are much rarer for some '{protected}' groups. "
            f"The highest rate is {fmt_pct(best_rate)} for '{best}', but: {group_text}. "
            f"Under the four-fifths rule used in US hiring audits, a group whose rate is below "
            f"80% of the top group's rate signals possible adverse impact. A model trained on "
            f"this data is likely to learn and repeat the gap.{small_note}"
        ),
        details={
            "check": "demographic_disparity",
            "protected_column": protected,
            "label_column": label,
            "positive_label": positive,
            "positive_label_reason": info["positive_label_reason"],
            "group_rates": {g: {"rate": round(v["rate"], 4), "count": v["count"]} for g, v in rates.items()},
            "reference_group": best,
            "impact_ratios": ratios,
            "flagged_groups": flagged,
            "min_impact_ratio": worst_ratio,
            "threshold": FOUR_FIFTHS,
            "ignored_groups": small,
        },
        columns=[protected, label],
    )]


def check_proxies(df: pd.DataFrame, label: str | None, protected: list[str]) -> list[Finding]:
    findings = []
    features = [
        str(c) for c in df.columns
        if str(c) not in protected and str(c) != label and not is_id_like(df[c], c)
    ]
    for p in protected:
        for f in features:
            result = association(df, f, p)
            if result is None:
                continue
            strength, stat = result
            if strength <= PROXY_THRESHOLD or math.isnan(strength):
                continue
            severity = "high" if strength > 0.7 else "medium"
            findings.append(Finding(
                severity=severity,
                category="bias",
                title=f"'{f}' may be a proxy for '{p}' ({stat} {strength:.2f})",
                description=(
                    f"'{f}' is strongly associated with the protected attribute '{p}' "
                    f"({stat} = {strength:.2f}, where 0 means unrelated and 1 means fully "
                    f"determined). Even if '{p}' is removed before training, a model can use "
                    f"'{f}' to infer it and discriminate indirectly."
                ),
                details={
                    "check": "proxy_correlation",
                    "feature": f,
                    "protected_column": p,
                    "statistic": stat,
                    "strength": round(strength, 3),
                    "threshold": PROXY_THRESHOLD,
                },
                columns=[f, p],
            ))
    return findings


def detect_bias(df: pd.DataFrame, settings: AnalysisSettings | None = None) -> list[Finding]:
    """Run all bias checks. Empty settings are auto-detected."""
    settings = resolve_settings(df, settings)
    label, protected = settings.label_column, settings.protected_columns
    findings: list[Finding] = []
    if label:
        findings += check_class_imbalance(df, label)
        for p in protected:
            findings += check_demographic_disparity(df, label, p)
    if protected:
        findings += check_proxies(df, label, protected)
    findings.sort(key=lambda f: SEVERITY_ORDER[f.severity])
    return findings
