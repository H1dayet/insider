"""Runnable check for the PTR parser: this is the piece most likely to silently
break if the Clerk changes their PDF layout, so it's asserted against two real
filings committed as fixtures."""
from datetime import date
from pathlib import Path

from pypdf import PdfReader

from insider.filings import _TXN_RE

FIXTURES = Path(__file__).parent / "fixtures"


def _extract(pdf_name: str):
    text = "\n".join(
        p.extract_text() or "" for p in PdfReader(FIXTURES / pdf_name).pages
    )
    return _TXN_RE.findall(text)


def test_equity_transaction_parses():
    matches = _extract("wittman_ccl_sell.pdf")
    assert matches == [("CCI", "S", "06/30/2026", "07/02/2026")]


def test_non_equity_asset_is_filtered_out():
    # Treasury bill, asset code [GS] not [ST] - must yield nothing.
    matches = _extract("yakym_tbill.pdf")
    assert matches == []


if __name__ == "__main__":
    test_equity_transaction_parses()
    test_non_equity_asset_is_filtered_out()
    print("ok")
