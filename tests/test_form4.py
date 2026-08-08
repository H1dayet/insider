"""Runnable checks for Form 4 XML parsing, against real filings pulled live from EDGAR
during planning (not synthetic examples) - see tests/fixtures/form4_*.xml.

form4_no_purchase.xml: a real officer/director filing with C (conversion), S (10b5-1
scheduled sale), and M (option exercise) transactions - zero P-coded purchases. This
is the common case and confirms the P-only filter does real work, not nothing.

form4_adtran_purchase.xml: a real CFO open-market purchase (code P), the exact shape
this whole pipeline exists to catch.
"""
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

from insider.form4 import Form4Filing, _has_10b5_1_flag, _parse_purchases

FIXTURES = Path(__file__).parent / "fixtures"


def _parse_fixture(name: str, accession_no="0000000000-00-000000"):
    root = ET.parse(FIXTURES / name).getroot()
    filing = Form4Filing(
        issuer_cik="0", issuer_name="test", filing_date=date(2026, 1, 1), accession_no=accession_no
    )
    return _parse_purchases(root, filing)


def test_filing_with_no_open_market_purchases_yields_nothing():
    purchases = _parse_fixture("form4_no_purchase.xml")
    assert purchases == []


def test_real_cfo_open_market_purchase_parses_correctly():
    purchases = _parse_fixture("form4_adtran_purchase.xml")
    assert len(purchases) == 1
    p = purchases[0]
    assert p.ticker == "ADTN"
    assert p.company_name == "ADTRAN Holdings, Inc."
    assert p.insider_name == "Santo Timothy P"
    assert p.insider_title == "SVP of Finance; CFO"
    assert p.tx_date == date(2026, 8, 6)
    assert p.shares == 6579.0
    assert p.price_per_share == 7.60


def test_ten_percent_owner_only_is_excluded():
    root = ET.parse(FIXTURES / "form4_adtran_purchase.xml").getroot()
    rel = root.find("reportingOwner/reportingOwnerRelationship")
    rel.find("isOfficer").text = "false"
    rel.find("isDirector").text = "false"
    filing = Form4Filing(issuer_cik="0", issuer_name="test", filing_date=date(2026, 1, 1), accession_no="x")
    assert _parse_purchases(root, filing) == []


def test_10b5_1_flagged_purchase_is_excluded():
    root = ET.parse(FIXTURES / "form4_adtran_purchase.xml").getroot()
    tx = root.find("nonDerivativeTable/nonDerivativeTransaction")
    flag = ET.SubElement(tx, "rule10b51")
    flag.text = "1"
    filing = Form4Filing(issuer_cik="0", issuer_name="test", filing_date=date(2026, 1, 1), accession_no="x")
    assert _parse_purchases(root, filing) == []


def test_has_10b5_1_flag_checks_both_plausible_nesting_levels():
    tx_flat = ET.fromstring("<t><rule10b51>1</rule10b51></t>")
    tx_nested = ET.fromstring("<t><transactionCoding><rule10b51>1</rule10b51></transactionCoding></t>")
    tx_absent = ET.fromstring("<t></t>")
    assert _has_10b5_1_flag(tx_flat) is True
    assert _has_10b5_1_flag(tx_nested) is True
    assert _has_10b5_1_flag(tx_absent) is False


if __name__ == "__main__":
    test_filing_with_no_open_market_purchases_yields_nothing()
    test_real_cfo_open_market_purchase_parses_correctly()
    test_ten_percent_owner_only_is_excluded()
    test_10b5_1_flagged_purchase_is_excluded()
    test_has_10b5_1_flag_checks_both_plausible_nesting_levels()
    print("ok")
