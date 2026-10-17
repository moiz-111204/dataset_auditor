"""Missing data analyzer.

Checks:
1. Column-level missingness (medium > 5%, high > 20%, critical > 50% or fully empty).
2. Row-level missingness (flagged when more than 10% of rows have a gap).
3. Missingness vs protected attributes: does a column go missing more often for some groups?
   Chi-squared test (p < 0.01) plus a rate-ratio check (high > 1.5x, critical > 2x).
4. Co-occurring gaps: column pairs whose missing values line up (phi > 0.5). This is reported
   as a pattern worth investigating, not as proof of any missingness mechanism.

Empty strings and whitespace-only cells count as missing, as do values pandas reads as NaN.
"""

from __future__ import annotations

import itertools
import math

import numpy as np
import pandas as pd
from scipy.stats import chi2_contingency

from backend.models.schemas import AnalysisSettings, Finding
from backend.utils import SEVERITY_ORDER, fmt_pct, group_labels, split_groups

COLUMN_THRESHOLDS = [(0.50, "critical"), (0.20, "high"), (0.05, "medium")]
ROW_THRESHOLD = 0.10
P_VALUE = 0.01
RATE_RATIO_HIGH = 1.5
RATE_RATIO_CRITICAL = 2.0
PHI_THRESHOLD = 0.5
MIN_CO_MISSING_ROWS = 5


def missing_mask(df: pd.DataFrame) -> pd.DataFrame:
    """True where a cell is missing (NaN/None or an empty / whitespace-only string)."""
    mask = df.isna()
    for idx in range(df.shape[1]):
        col = df.iloc[:, idx]
        if pd.api.types.is_object_dtype(col) or pd.api.types.is_string_dtype(col):
            blank = col.map(lambda v: isinstance(v, str) and v.strip() == "").astype(bool)
            mask.iloc[:, idx] = mask.iloc[:, idx] | blank
    return mask.astype(bool)


def missing_by_column(df: pd.DataFrame) -> dict[str, float]:
    """Missing share (0-1) per column, for charts."""
    mask = missing_mask(df)
    return {str(c): round(float(mask.iloc[:, i].mean()), 4) for i, c in enumerate(df.columns)}


def check_columns(df: pd.DataFrame, mask: pd.DataFrame) -> list[Finding]:
    findings = []
    n = len(df)
    for idx, col in enumerate(df.columns):
        col = str(col)
        missing = int(mask.iloc[:, idx].sum())
        share = missing / n if n else 0.0
        if share >= 1.0:
            findings.append(Finding(
                severity="critical",
                category="missing_data",
                title=f"Column '{col}' is completely empty",
                description=(
                    f"All {n} rows of '{col}' are missing. The column carries no information and "
                    f"should be dropped before training; if it was expected to hold data, check "
                    f"the export or collection step that produced this file."
                ),
                details={"check": "column_missing", "column": col, "missing_count": missing,
                         "missing_share": 1.0, "recommendation": "drop"},
                columns=[col],
            ))
            continue
        severity = next((sev for limit, sev in COLUMN_THRESHOLDS if share > limit), None)
        if severity is None:
            continue
        findings.append(Finding(
            severity=severity,
            category="missing_data",
            title=f"'{col}' is {fmt_pct(share)} missing",
            description=(
                f"{missing} of {n} rows ({fmt_pct(share)}) have no value for '{col}'. Dropping "
                f"these rows shrinks the dataset and can remove some groups more than others; "
                f"filling them in (imputation) adds guessed values that a model treats as real."
            ),
            details={"check": "column_missing", "column": col, "missing_count": missing,
                     "missing_share": round(share, 4)},
            columns=[col],
        ))
    return findings


def check_rows(df: pd.DataFrame, mask: pd.DataFrame) -> list[Finding]:
    n, width = df.shape
    if n == 0 or width == 0:
        return []
    per_row = mask.sum(axis=1)
    any_share = float((per_row > 0).mean())
    mostly_empty_share = float((per_row > width / 2).mean())
    if any_share <= ROW_THRESHOLD:
        return []
    severity = "high" if mostly_empty_share > 0.05 or any_share > 0.5 else "medium"
    return [Finding(
        severity=severity,
        category="missing_data",
        title=f"{fmt_pct(any_share)} of rows have at least one missing value",
        description=(
            f"{int((per_row > 0).sum())} of {n} rows ({fmt_pct(any_share)}) have a gap somewhere, "
            f"and {fmt_pct(mostly_empty_share)} are missing more than half of their columns. "
            f"Many training tools drop incomplete rows by default, which would silently discard "
            f"this share of the data."
        ),
        details={"check": "row_missing", "rows_with_missing": int((per_row > 0).sum()),
                 "rows_with_missing_share": round(any_share, 4),
                 "rows_mostly_empty_share": round(mostly_empty_share, 4)},
        columns=[],
    )]


def check_by_group(df: pd.DataFrame, mask: pd.DataFrame, protected: list[str]) -> list[Finding]:
    findings = []
    names = [str(c) for c in df.columns]
    for p in protected:
        if p not in names:
            continue
        groups_all = group_labels(df[p], p)
        for idx, col in enumerate(names):
            if col == p:
                continue
            missing = mask.iloc[:, idx]
            if not missing.any() or missing.all():
                continue
            groups = groups_all[groups_all.notna()]
            eligible, small = split_groups(groups)
            if len(eligible) < 2:
                continue
            in_eligible = groups.isin(eligible)
            g, m = groups[in_eligible], missing[groups.index][in_eligible]
            table = pd.crosstab(g, m)
            if table.shape[1] < 2:
                continue
            p_value = float(chi2_contingency(table)[1])
            rates = {str(k): float(v) for k, v in m.groupby(g).mean().items()}
            hi = max(rates, key=rates.get)
            lo = min(rates, key=rates.get)
            ratio = math.inf if rates[lo] == 0 else rates[hi] / rates[lo]
            if p_value >= P_VALUE or ratio <= RATE_RATIO_HIGH:
                continue
            severity = "critical" if ratio > RATE_RATIO_CRITICAL else "high"
            ratio_text = "far more often (the other group has no gaps)" if math.isinf(ratio) \
                else f"{ratio:.1f}x as often"
            small_note = (
                " Groups with fewer than 20 rows were not compared: "
                + ", ".join(f"'{k}' ({v} rows)" for k, v in small.items()) + "."
                if small else ""
            )
            findings.append(Finding(
                severity=severity,
                category="missing_data",
                title=f"'{col}' is missing unevenly across '{p}' groups",
                description=(
                    f"'{col}' is missing for {fmt_pct(rates[hi])} of '{hi}' rows but only "
                    f"{fmt_pct(rates[lo])} of '{lo}' rows, {ratio_text} (chi-squared p = "
                    f"{p_value:.2g}). Dropping or imputing these rows affects one group more than "
                    f"the other, so a model may be less accurate for '{hi}'.{small_note}"
                ),
                details={
                    "check": "missing_by_group",
                    "column": col,
                    "protected_column": p,
                    "group_missing_rates": {k: round(v, 4) for k, v in rates.items()},
                    "highest_group": hi,
                    "lowest_group": lo,
                    "rate_ratio": None if math.isinf(ratio) else round(ratio, 2),
                    "p_value": p_value,
                    "ignored_groups": small,
                },
                columns=[col, p],
            ))
    return findings


def check_patterns(mask: pd.DataFrame, names: list[str]) -> list[Finding]:
    findings = []
    candidates = [i for i in range(mask.shape[1]) if 0 < mask.iloc[:, i].sum() < len(mask)]
    for i, j in itertools.combinations(candidates, 2):
        a, b = mask.iloc[:, i].to_numpy(), mask.iloc[:, j].to_numpy()
        both = int((a & b).sum())
        if both < MIN_CO_MISSING_ROWS:
            continue
        phi = float(np.corrcoef(a.astype(float), b.astype(float))[0, 1])
        if math.isnan(phi) or phi <= PHI_THRESHOLD:
            continue
        ca, cb = names[i], names[j]
        findings.append(Finding(
            severity="medium" if phi > 0.8 else "low",
            category="missing_data",
            title=f"Gaps in '{ca}' and '{cb}' tend to occur together",
            description=(
                f"'{ca}' and '{cb}' are missing in the same {both} rows more often than chance "
                f"would suggest (phi = {phi:.2f}). This may point to a systematic cause, such as "
                f"one form section being skipped or one data source lacking both fields, rather "
                f"than random gaps. It is a pattern worth investigating, not proof of why the "
                f"data is missing."
            ),
            details={"check": "missing_pattern", "columns": [ca, cb], "phi": round(phi, 3),
                     "rows_missing_both": both},
            columns=[ca, cb],
        ))
    return findings


def analyze_missing(df: pd.DataFrame, settings: AnalysisSettings | None = None) -> list[Finding]:
    """Run all missing-data checks. Protected columns come from the (resolved) settings."""
    settings = settings or AnalysisSettings()
    mask = missing_mask(df)
    names = [str(c) for c in df.columns]
    findings = (
        check_columns(df, mask)
        + check_rows(df, mask)
        + check_by_group(df, mask, settings.protected_columns)
        + check_patterns(mask, names)
    )
    findings.sort(key=lambda f: SEVERITY_ORDER[f.severity])
    return findings
