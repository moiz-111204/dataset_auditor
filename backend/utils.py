"""Helpers shared by the analyzers: column naming, type checks, grouping and label handling."""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

MIN_GROUP_SIZE = 20
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

POSITIVE_TOKENS = {
    "1", "1.0", "yes", "y", "true", "t", "hired", "approved", "accept", "accepted", "positive",
    "pass", "passed", "granted", "admitted", "selected", "success", "eligible",
}
CATEGORICAL_NAME_TOKENS = {"zip", "zipcode", "postal", "postcode", "code", "id", "category", "type"}


def normalize_column_name(name: object) -> str:
    """'Applicant Age' / 'applicantAge' -> 'applicant_age'."""
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(name).strip())
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def name_tokens(name: object) -> set[str]:
    return set(filter(None, normalize_column_name(name).split("_")))


ID_NAME_TOKENS = {"id", "uuid", "guid", "key", "pk"}


def is_id_like(series: pd.Series, name: object = "") -> bool:
    """A record identifier: (almost) one distinct value per row.

    Continuous measurements (floats, or integers spread over a wide range such as income)
    are also unique per row, so numeric columns only count when named like an ID or when
    they form a near-contiguous counter.
    """
    s = series.dropna()
    if len(s) < 10 or s.nunique() < 0.95 * len(s):
        return False
    if pd.api.types.is_float_dtype(s) or pd.api.types.is_bool_dtype(s):
        return False
    if pd.api.types.is_integer_dtype(s):
        if name_tokens(name) & ID_NAME_TOKENS:
            return True
        return (s.max() - s.min() + 1) <= 1.1 * len(s)
    return True


def is_numeric_feature(series: pd.Series, name: object = "") -> bool:
    """Numeric and continuous enough to treat as a number rather than as categories."""
    if not pd.api.types.is_numeric_dtype(series) or pd.api.types.is_bool_dtype(series):
        return False
    if name_tokens(name) & CATEGORICAL_NAME_TOKENS:
        return False
    return series.nunique(dropna=True) > 10


def group_labels(series: pd.Series, name: object = "") -> pd.Series:
    """Group label per row for a protected attribute; numeric columns are binned first."""
    is_age = "age" in name_tokens(name) and pd.api.types.is_numeric_dtype(series) \
        and not pd.api.types.is_bool_dtype(series)
    if is_age or is_numeric_feature(series, name):
        values = pd.to_numeric(series, errors="coerce")
        if is_age:
            binned = pd.cut(values, bins=[-np.inf, 30, 50, np.inf],
                            labels=["under 30", "30-50", "over 50"], right=False)
        else:
            try:
                binned = pd.qcut(values, q=3, duplicates="drop")
            except ValueError:
                return values.astype(object)
        return binned.astype(object).where(values.notna(), None).map(
            lambda v: v if v is None else str(v))
    return series.map(lambda v: None if pd.isna(v) else str(v).strip()).astype(object)


def split_groups(groups: pd.Series, min_size: int = MIN_GROUP_SIZE) -> tuple[list[str], dict[str, int]]:
    """Return (groups with at least min_size rows, {small group: size})."""
    counts = groups.dropna().value_counts()
    eligible = [str(g) for g, n in counts.items() if n >= min_size]
    small = {str(g): int(n) for g, n in counts.items() if n < min_size}
    return eligible, small


def looks_positive(value: object) -> bool:
    text = str(value).strip().lower()
    if text in POSITIVE_TOKENS:
        return True
    try:
        return float(text) == 1.0
    except ValueError:
        return False


def resolve_positive_label(labels: pd.Series) -> tuple[object, str]:
    """Pick the label value treated as the positive outcome and say why."""
    counts = labels.dropna().value_counts()
    for value in counts.index:
        if looks_positive(value):
            return value, "looks like a positive outcome"
    return counts.index[-1], "least frequent value (no value looked like a positive outcome)"


def label_text(value: object) -> str:
    """Readable label value: 1.0 -> '1'."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def fmt_pct(value: float) -> str:
    return f"{100 * value:.1f}%"
