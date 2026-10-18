"""PII scanner.

Two detection layers run over every text column of a DataFrame:

1. Regex and heuristic rules (always on): emails, phone numbers, SSN-style national IDs,
   payment card numbers (regex + Luhn check), street addresses, IPv4/IPv6 addresses, plus
   column-name rules for person names, addresses and dates of birth.
2. ML-based named-entity recognition with Microsoft Presidio and the spaCy model
   `en_core_web_sm`, run on a sample of up to 200 distinct non-empty values per text column.
   If Presidio or the model cannot be loaded (or PII_NER_ENABLED=false), the scanner logs a
   warning and continues with regex only. `detector_status()` reports which layers ran.

Findings carry column names, counts and methods only. They never contain the matched values.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from dataclasses import dataclass

import pandas as pd

from backend.models.schemas import Finding
from backend.utils import SEVERITY_ORDER, normalize_column_name

logger = logging.getLogger(__name__)

NER_SAMPLE_SIZE = 200
# A column is flagged by the NER layer only when enough sampled values contain the entity,
# which keeps one-off false positives of the small spaCy model out of the report.
NER_MIN_FRACTION = 0.05
NER_MIN_VALUES = 2
NER_SCORE_THRESHOLD = 0.4


@dataclass(frozen=True)
class PIIType:
    key: str
    label: str
    severity: str
    risk: str


PII_TYPES: dict[str, PIIType] = {
    t.key: t
    for t in [
        PIIType("ssn", "SSN-style national ID numbers", "critical",
                "National ID numbers are permanent identifiers. A leak enables identity theft, "
                "and storing them is regulated in most jurisdictions."),
        PIIType("cnic", "Pakistani CNIC numbers", "critical",
                "A CNIC is a lifelong national identity number. A leak enables identity theft "
                "and SIM or bank-account fraud in the holder's name."),
        PIIType("credit_card", "Payment card numbers", "critical",
                "Card numbers enable direct financial fraud and fall under PCI DSS rules. They "
                "have no place in a training dataset."),
        PIIType("email", "Email addresses", "high",
                "Email addresses can be used to re-identify people even in anonymized datasets, "
                "and they link records across other services."),
        PIIType("phone", "Phone numbers", "high",
                "Phone numbers identify individuals directly and can be used for unwanted "
                "contact or social-engineering attacks."),
        PIIType("person_name", "Person names", "medium",
                "Names identify people directly. Models can memorize and repeat them, and names "
                "can act as hidden proxies for gender or ethnicity."),
        PIIType("address", "Street addresses", "medium",
                "A street address shows where someone lives and, combined with a few other "
                "fields, identifies them uniquely."),
        PIIType("ip_address", "IP addresses", "medium",
                "IP addresses can be traced to a household or device and count as personal data "
                "under laws such as GDPR."),
        PIIType("date_of_birth", "Dates of birth", "medium",
                "Date of birth combined with ZIP code and gender is enough to uniquely identify "
                "most people."),
    ]
}


# --- Layer 1: regex and heuristic rules -------------------------------------------------

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
# Requires separators, parentheses, a leading + or the Pakistani mobile prefix 03 so bare
# 10-digit IDs are not flagged. A match must also hold 8-15 digits (_contains_phone).
PHONE_RE = re.compile(
    r"(?<![\w-])(?:"
    r"(?:\+?1[\s.-]?)?(?:\(\d{3}\)\s?|\d{3}[\s.-])\d{3}[\s.-]\d{4}"  # North America
    r"|\+\d{1,3}[\s.-]?\d{2,5}(?:[\s.-]\d{2,7}){1,4}"  # international with separators
    r"|\+[1-9]\d{9,14}"  # international without separators (E.164)
    r"|03\d{2}[\s-]?\d{7}"  # Pakistani mobile: 0300-1234567, 03001234567
    r")(?![\w-])"
)
SSN_RE = re.compile(r"(?<![\d-])(?:\d{3}-\d{2}-\d{4}|\d{3} \d{2} \d{4})(?![\d-])")
CNIC_RE = re.compile(r"(?<![\d-])\d{5}-\d{7}-\d(?![\d-])")  # Pakistani CNIC: 35202-1234567-1
CARD_CANDIDATE_RE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
STREET_SUFFIXES = (
    r"street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|court|ct|way|place|pl"
    r"|terrace|ter|parkway|pkwy|circle|cir|highway|hwy|square|sq"
)
ADDRESS_RE = re.compile(
    rf"\b\d{{1,6}}\s+(?:[A-Za-z0-9.'-]+\s+){{1,4}}(?:{STREET_SUFFIXES})\b\.?",
    re.IGNORECASE,
)
_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
IPV4_RE = re.compile(rf"(?<![\d.]){_OCTET}(?:\.{_OCTET}){{3}}(?![\d.])")
IPV6_RE = re.compile(r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}(?![0-9A-Fa-f:])")

NAME_COLUMN_RE = re.compile(
    r"^(?:(?:first|last|full|middle|given|family|maiden|sur)_?name|surname|name"
    r"|(?:customer|patient|employee|applicant|client|person|student|member|contact)_?name)$"
)
DOB_COLUMN_RE = re.compile(r"^(?:dob|d_o_b|birth_?date|date_?of_?birth|birthday|birth_?dt)$")
ADDRESS_COLUMN_RE = re.compile(
    r"^(?:(?:street|home|mailing|postal|billing|shipping)_?)?address(?:_?line_?\d)?$"
)
# Phone numbers are often stored as plain digits (or read as integers), which the regex rules
# skip on purpose; a column named like a phone field is enough evidence for 7-15 digit values.
PHONE_COLUMN_RE = re.compile(
    r"^(?:[a-z]+_)?(?:phone|mobile|telephone|tel|whatsapp|cellphone|cell_phone|contact)"
    r"(?:_?(?:no|num|number|nbr))?(?:_?\d)?$|^cell_?(?:no|num|number|nbr)$"
)
CNIC_COLUMN_RE = re.compile(r"^(?:cnic|nic)(?:_?(?:no|num|number))?$")
# Version strings such as 10.0.19045.1 look exactly like IPv4 addresses.
VERSION_COLUMN_RE = re.compile(r"(?:^|_)(?:version|ver|build|release|firmware)(?:_|$)")


def luhn_valid(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = ord(ch) - 48
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _contains_card(text: str) -> bool:
    text = CNIC_RE.sub(" ", text)  # a CNIC has 13 digits and can pass the Luhn check by chance
    for match in CARD_CANDIDATE_RE.finditer(text):
        digits = re.sub(r"\D", "", match.group())
        if 13 <= len(digits) <= 19 and digits[0] in "23456" and luhn_valid(digits):
            return True
    return False


def _contains_phone(text: str) -> bool:
    return any(8 <= sum(ch.isdigit() for ch in m.group()) <= 15 for m in PHONE_RE.finditer(text))


def _plain_digits(text: str) -> str:
    """'0300-1234567' / '(555) 010 1234' / '5550101234.0' -> the digits alone, else ''."""
    text = re.sub(r"\.0$", "", text.strip())
    digits = re.sub(r"[\s().+-]", "", text)
    return digits if digits.isdigit() else ""


def _text_values(series: pd.Series) -> pd.Series:
    """Non-empty values of a column as strings, keeping the original row index."""
    s = series.dropna()
    s = s.astype(str).str.strip()
    return s[s != ""]


def _is_text_column(series: pd.Series) -> bool:
    return pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series)


def _regex_masks(values: pd.Series) -> dict[str, pd.Series]:
    """Boolean mask per PII type over the given values (index preserved)."""
    phone = values.str.contains(PHONE_RE)  # fast pre-filter; the digit count runs on hits only
    if phone.any():
        phone[phone] = values[phone].map(_contains_phone).astype(bool)
    masks = {
        "email": values.str.contains(EMAIL_RE),
        "phone": phone,
        "ssn": values.str.contains(SSN_RE),
        "cnic": values.str.contains(CNIC_RE),
        "address": values.str.contains(ADDRESS_RE),
        "ip_address": values.str.contains(IPV4_RE) | values.str.contains(IPV6_RE),
    }
    digit_rich = values.str.count(r"\d") >= 13
    card = pd.Series(False, index=values.index)
    if digit_rich.any():
        card[digit_rich] = values[digit_rich].map(_contains_card).astype(bool)
    masks["credit_card"] = card
    return {key: mask.astype(bool) for key, mask in masks.items()}


# --- Layer 2: Presidio NER --------------------------------------------------------------

PRESIDIO_ENTITY_MAP = {
    "PERSON": "person_name",
    "EMAIL_ADDRESS": "email",
    "PHONE_NUMBER": "phone",
    "US_SSN": "ssn",
    "CREDIT_CARD": "credit_card",
    "IP_ADDRESS": "ip_address",
}

_presidio_lock = threading.Lock()
_presidio_engine = None
_presidio_status: dict | None = None


def _ner_enabled_by_env() -> bool:
    return os.getenv("PII_NER_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}


def _load_presidio():
    """Load the Presidio analyzer once per process. Returns None if unavailable."""
    global _presidio_engine, _presidio_status
    with _presidio_lock:
        if _presidio_status is not None:
            return _presidio_engine
        if not _ner_enabled_by_env():
            _presidio_status = {"available": False, "reason": "disabled by PII_NER_ENABLED"}
            return None
        try:
            from presidio_analyzer import AnalyzerEngine, BatchAnalyzerEngine
            from presidio_analyzer.nlp_engine import NlpEngineProvider

            provider = NlpEngineProvider(nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
            })
            analyzer = AnalyzerEngine(nlp_engine=provider.create_engine(), supported_languages=["en"])
            _presidio_engine = BatchAnalyzerEngine(analyzer_engine=analyzer)
            _presidio_status = {"available": True, "reason": None}
        except Exception as exc:  # ImportError, missing spaCy model (OSError), etc.
            logger.warning("Presidio NER unavailable, continuing with regex only (%s)", type(exc).__name__)
            _presidio_engine = None
            _presidio_status = {"available": False, "reason": f"failed to load ({type(exc).__name__})"}
        return _presidio_engine


def reset_presidio_cache() -> None:
    """Forget the loaded engine so the next scan retries loading (used by tests)."""
    global _presidio_engine, _presidio_status
    with _presidio_lock:
        _presidio_engine = None
        _presidio_status = None


def detector_status() -> dict:
    """Which detection layers are active. Loads Presidio on first call."""
    _load_presidio()
    status = _presidio_status or {"available": False, "reason": "not loaded"}
    return {
        "regex": True,
        "presidio_ner": bool(status["available"]),
        "presidio_reason": status["reason"],
        "ner_model": "en_core_web_sm" if status["available"] else None,
    }


def _plausible_name(span: str) -> bool:
    """The small spaCy model sometimes tags codes such as 'R0168' as PERSON; names have no digits."""
    return not any(ch.isdigit() for ch in span) and re.search(r"[^\W\d_]{2,}", span) is not None


def _plausible_phone(text: str, start: int, end: int) -> bool:
    """Presidio's phone recognizer also tags short digit runs (SKU-1234-567), bare 10-digit
    order numbers and parts of ID numbers. Like the regex rules, keep only spans with at least
    9 digits, a separator, bracket or leading +, and no overlap with a CNIC or SSN."""
    span = text[start:end]
    if sum(ch.isdigit() for ch in span) < 9 or not re.search(r"[\s().+-]", span):
        return False
    return not any(m.start() < end and start < m.end()
                   for pattern in (CNIC_RE, SSN_RE) for m in pattern.finditer(text))


def _ner_matches(engine, values: pd.Series) -> dict[str, set[str]]:
    """Run NER on up to NER_SAMPLE_SIZE distinct values; return matched values per PII type."""
    uniques = pd.Series(pd.unique(values))
    if len(uniques) > NER_SAMPLE_SIZE:
        uniques = uniques.sample(n=NER_SAMPLE_SIZE, random_state=0)
    sample = [str(v) for v in uniques]
    if not sample:
        return {}
    results = engine.analyze_iterator(
        texts=[v[:1000] for v in sample], language="en",
        entities=list(PRESIDIO_ENTITY_MAP), score_threshold=NER_SCORE_THRESHOLD, batch_size=64,
    )
    hits: dict[str, set[str]] = {}
    for text, found in zip(sample, results):
        for res in found:
            key = PRESIDIO_ENTITY_MAP.get(res.entity_type)
            if key == "person_name" and not _plausible_name(text[res.start:res.end]):
                continue
            if key == "phone" and not _plausible_phone(text, res.start, res.end):
                continue
            if key:
                hits.setdefault(key, set()).add(text)
    threshold = max(NER_MIN_VALUES, NER_MIN_FRACTION * len(sample))
    return {key: vals for key, vals in hits.items() if len(vals) >= threshold}


# --- Scanner ----------------------------------------------------------------------------

def scan_pii(df: pd.DataFrame) -> list[Finding]:
    """Scan a DataFrame for PII and return one Finding per PII type found."""
    engine = _load_presidio()
    # PII type -> column name -> {"mask": row mask over df.index, "methods": set}
    hits: dict[str, dict[str, dict]] = {}

    def record(key: str, column: str, mask: pd.Series, method: str) -> None:
        if not mask.any():
            return
        entry = hits.setdefault(key, {}).setdefault(column, {"mask": None, "methods": set()})
        entry["mask"] = mask if entry["mask"] is None else (entry["mask"] | mask)
        entry["methods"].add(method)

    def full_mask(partial: pd.Series) -> pd.Series:
        return partial.reindex(df.index, fill_value=False).astype(bool)

    for idx in range(df.shape[1]):
        column = str(df.columns[idx])
        series = df.iloc[:, idx]
        values = _text_values(series)
        if values.empty:
            continue
        norm = normalize_column_name(column)
        text_column = _is_text_column(series)
        non_empty = full_mask(pd.Series(True, index=values.index))

        if NAME_COLUMN_RE.match(norm) and text_column:
            record("person_name", column, non_empty, "column_name")
        if DOB_COLUMN_RE.match(norm):
            record("date_of_birth", column, non_empty, "column_name")
        if ADDRESS_COLUMN_RE.match(norm) and text_column:
            record("address", column, non_empty, "column_name")
        if PHONE_COLUMN_RE.match(norm):
            digits = values.map(_plain_digits)
            record("phone", column, full_mask(digits.str.len().between(7, 15)), "column_name")
        if CNIC_COLUMN_RE.match(norm):
            digits = values.map(_plain_digits)
            record("cnic", column, full_mask(digits.str.len() == 13), "column_name")
        skip = {"ip_address"} if VERSION_COLUMN_RE.search(norm) else set()

        if text_column:
            for key, value_mask in _regex_masks(values).items():
                if key not in skip:
                    record(key, column, full_mask(value_mask), "regex")
        elif pd.api.types.is_integer_dtype(series):
            # Card numbers stored as plain integers.
            record("credit_card", column, full_mask(values.map(_contains_card)), "regex")

        if engine is not None and text_column:
            for key, matched in _ner_matches(engine, values).items():
                if key not in skip:
                    record(key, column, full_mask(values.isin(matched)), "presidio_ner")

    findings = [_build_finding(key, columns, len(df)) for key, columns in hits.items()]
    findings.sort(key=lambda f: (SEVERITY_ORDER[f.severity], f.title))
    return findings


def _build_finding(key: str, columns: dict[str, dict], row_count: int) -> Finding:
    pii = PII_TYPES[key]
    any_row = None
    per_column = {}
    for column, entry in columns.items():
        per_column[column] = {"rows": int(entry["mask"].sum()), "methods": sorted(entry["methods"])}
        any_row = entry["mask"] if any_row is None else (any_row | entry["mask"])
    total = int(any_row.sum())
    pct = round(100 * total / row_count, 1) if row_count else 0.0
    names = list(columns)
    methods = sorted({m for e in columns.values() for m in e["methods"]})
    ner_only = [c for c, e in columns.items() if e["methods"] == {"presidio_ner"}]
    ner_note = (
        f" Counts for {', '.join(ner_only)} come from the NER layer, which reads a sample of "
        f"values, so they are a lower bound."
        if ner_only else ""
    )
    return Finding(
        severity=pii.severity,
        category="pii",
        title=f"{pii.label} found in {len(names)} column{'s' if len(names) != 1 else ''}",
        description=(
            f"{pii.label} were detected in {total} of {row_count} rows ({pct}%) in: "
            f"{', '.join(names)}. {pii.risk}{ner_note}"
        ),
        details={
            "pii_type": key,
            "pii_label": pii.label,
            "rows_affected": total,
            "percent_rows": pct,
            "per_column": per_column,
            "detection_methods": methods,
        },
        columns=names,
    )
