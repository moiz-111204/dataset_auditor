import json
import sys

import pandas as pd
import pytest

from backend.analyzers import pii_scanner
from backend.analyzers.pii_scanner import detector_status, luhn_valid, scan_pii


def by_type(findings):
    return {f.details["pii_type"]: f for f in findings}


@pytest.fixture
def regex_only(monkeypatch):
    """Simulate an environment where presidio_analyzer cannot be imported."""
    monkeypatch.setitem(sys.modules, "presidio_analyzer", None)
    pii_scanner.reset_presidio_cache()
    yield
    pii_scanner.reset_presidio_cache()


def test_email_detected_with_row_count():
    df = pd.DataFrame({"contact": ["a.person@example.com", "other@example.org", "none", None]})
    f = by_type(scan_pii(df))["email"]
    assert f.severity == "high"
    assert f.columns == ["contact"]
    assert f.details["rows_affected"] == 2


def test_phone_formats_detected():
    df = pd.DataFrame({"tel": ["(212) 555-0123", "212-555-0188", "212.555.0101",
                               "+44 20 7946 0958", "call me", "12345"]})
    f = by_type(scan_pii(df))["phone"]
    assert f.severity == "high"
    assert f.details["rows_affected"] == 4


def test_ssn_is_critical_and_not_counted_as_phone():
    df = pd.DataFrame({"national_id": ["000-12-3456", "666-45-1234", "900 11 2222", "n/a"]})
    findings = by_type(scan_pii(df))
    assert findings["ssn"].severity == "critical"
    assert findings["ssn"].details["rows_affected"] == 3
    assert "phone" not in findings


def test_credit_card_requires_luhn():
    assert luhn_valid("4111111111111111")
    assert not luhn_valid("4111111111111112")
    df = pd.DataFrame({"payment": ["4111 1111 1111 1111", "5500-0000-0000-0004",
                                   "4111 1111 1111 1112", "no card"]})
    f = by_type(scan_pii(df))["credit_card"]
    assert f.severity == "critical"
    assert f.details["rows_affected"] == 2


def test_name_and_dob_columns_detected_by_column_name():
    df = pd.DataFrame({
        "First Name": ["Ana", "Ben", "Chen"],
        "last_name": ["Ito", "Okafor", "Silva"],
        "date_of_birth": ["1990-01-02", "1985-05-06", None],
        "product_name": ["Desk", "Lamp", "Chair"],
    })
    findings = by_type(scan_pii(df))
    names = findings["person_name"]
    assert names.severity == "medium"
    assert set(names.columns) >= {"First Name", "last_name"}
    assert "product_name" not in names.columns
    assert findings["date_of_birth"].details["rows_affected"] == 2


def test_ip_and_street_address_detected():
    df = pd.DataFrame({
        "client_ip": ["192.168.1.20", "10.0.0.256", "2001:0db8:85a3:0000:0000:8a2e:0370:7334", "-"],
        "location": ["123 Main Street, Springfield 12345", "42 Elm Ave", "PO Box 9", ""],
    })
    findings = by_type(scan_pii(df))
    assert findings["ip_address"].details["rows_affected"] == 2  # 10.0.0.256 is not valid IPv4
    assert findings["address"].details["rows_affected"] == 2
    assert findings["address"].severity == "medium"


def test_clean_data_has_no_findings():
    df = pd.DataFrame({
        "region": ["North", "South", "East", "West"] * 5,
        "product_category": ["Books", "Toys", "Garden", "Sports"] * 5,
        "score": list(range(20)),
        "order_date": ["2024-01-15"] * 20,
    })
    assert scan_pii(df) == []


def test_presidio_unavailable_falls_back_to_regex(regex_only):
    status = detector_status()
    assert status["regex"] is True
    assert status["presidio_ner"] is False
    assert "failed to load" in status["presidio_reason"]
    df = pd.DataFrame({"email": ["x@example.com", "y@example.com"], "ssn": ["000-11-2222", "x"]})
    findings = by_type(scan_pii(df))
    assert {"email", "ssn"} <= set(findings)
    assert all(f.details["detection_methods"] != ["presidio_ner"] for f in findings.values())


def test_ner_can_be_disabled_by_env(monkeypatch):
    monkeypatch.setenv("PII_NER_ENABLED", "false")
    pii_scanner.reset_presidio_cache()
    try:
        status = detector_status()
        assert status["presidio_ner"] is False
        assert "disabled" in status["presidio_reason"]
    finally:
        pii_scanner.reset_presidio_cache()


def test_ner_finds_names_in_free_text():
    if not detector_status()["presidio_ner"]:
        pytest.skip("Presidio / en_core_web_sm not installed")
    df = pd.DataFrame({"notes": [
        "Spoke with John Smith yesterday", "Maria Garcia called back", "Refund issued",
        "Sarah Johnson asked for an invoice", "ok", "Robert Brown will visit on Monday",
    ]})
    f = by_type(scan_pii(df))["person_name"]
    assert "presidio_ner" in f.details["detection_methods"]
    assert f.details["rows_affected"] >= 3


def test_findings_never_contain_raw_values():
    raw = {
        "email": ["jane.doe@example.com", "j.smith@example.org"],
        "phone": ["(212) 555-0142", "212-555-0177"],
        "ssn": ["000-12-3456", "666-98-7654"],
        "card": ["4111 1111 1111 1111", "5500-0000-0000-0004"],
        "first_name": ["Priyanka", "Oluwaseun"],
        "home_address": ["77 Sunset Boulevard, Los Angeles 90028", "9 Rue Lane"],
        "ip": ["192.168.10.77", "172.16.254.1"],
        "dob": ["1987-03-14", "1992-11-30"],
        "notes": ["Met with John Smith about the loan", "Maria Garcia asked to be called"],
    }
    findings = scan_pii(pd.DataFrame(raw))
    assert len(findings) >= 7
    dumped = json.dumps([f.model_dump(mode="json") for f in findings])
    for values in raw.values():
        for value in values:
            assert value not in dumped
            for token in value.replace(",", " ").split():
                if len(token) >= 5 and any(c.isdigit() for c in token):
                    assert token not in dumped, token
    for name in ["Priyanka", "Oluwaseun", "John Smith", "Maria Garcia"]:
        assert name not in dumped


def test_pakistani_and_international_phone_formats(regex_only):
    df = pd.DataFrame({"contact": ["0300-1234567", "0321 7654321", "03451234567", "+92 300 1234567",
                                   "+923001234567", "+91 98765 43210", "no phone"]})
    assert by_type(scan_pii(df))["phone"].details["rows_affected"] == 6


def test_cnic_detected_and_not_counted_as_card_or_phone():
    # 35202-1234567-6 passes the Luhn check, so it used to be reported as a payment card.
    df = pd.DataFrame({"id_card": ["35202-1234567-6", "42101-7654321-3", "61101-1111111-1", "n/a"]})
    findings = by_type(scan_pii(df))
    assert findings["cnic"].severity == "critical"
    assert findings["cnic"].details["rows_affected"] == 3
    assert "credit_card" not in findings and "phone" not in findings


def test_phone_and_cnic_columns_holding_plain_digits():
    df = pd.DataFrame({"mobile_no": [5550101234, 3001234567, 3217654321],
                       "cnic": [3520212345671, 4210176543213, 6110111111111],
                       "order_number": [4829301723, 4829301724, 4829301725]})
    findings = by_type(scan_pii(df))
    assert findings["phone"].columns == ["mobile_no"]
    assert findings["cnic"].columns == ["cnic"]


def test_lookalikes_are_not_flagged():
    df = pd.DataFrame({
        "order_number": ["4829301723", "4829301724", "4829301725", "4829301726"],
        "signup_date": ["2024-03-15", "2024-04-01", "2024-05-20", "2024-06-30"],
        "sku": ["SKU-1234-567", "SKU-2345-678", "SKU-3456-789", "SKU-4567-890"],
        "os_version": ["10.0.19045.1", "10.0.22631.2", "6.1.7601.3", "10.0.19044.4"],
    })
    assert scan_pii(df) == []
