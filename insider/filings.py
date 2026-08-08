"""House Clerk filing index + PTR PDF parsing.

Index:  https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.ZIP
PDF:    https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc_id}.pdf
"""
from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime

import requests
from pypdf import PdfReader

INDEX_URL = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.ZIP"
PDF_URL = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc_id}.pdf"

# Data rows look like:
#   Crown Castle Inc. Common Stock\n(CCI) [ST]\nS 06/30/202607/02/2026$1,001 - $15,000
# i.e. two MM/DD/YYYY dates butted together with no separator.
_TXN_RE = re.compile(
    r"\(([A-Z][A-Z.\-]{0,5})\)\s*\[ST\]\s*"
    r"([PS])\s+(\d{2}/\d{2}/\d{4})(\d{2}/\d{2}/\d{4})"
)


@dataclass
class Filing:
    doc_id: str
    member: str
    filing_date: date


@dataclass
class Trade:
    doc_id: str
    member: str
    ticker: str
    tx_type: str  # "P" (purchase) or "S" (sale)
    tx_date: date
    filing_date: date


def list_new_ptr_filings(year: int) -> list[Filing]:
    """Download the annual index and return every Periodic Transaction Report filing."""
    resp = requests.get(INDEX_URL.format(year=year), timeout=60)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        xml_bytes = zf.read(f"{year}FD.xml")
    root = ET.fromstring(xml_bytes)

    filings = []
    for member in root.findall("Member"):
        if (member.findtext("FilingType") or "").strip() != "P":
            continue
        doc_id = (member.findtext("DocID") or "").strip()
        filing_date_raw = (member.findtext("FilingDate") or "").strip()
        if not doc_id or not filing_date_raw:
            continue
        name = " ".join(
            p.strip()
            for p in (member.findtext("First"), member.findtext("Last"))
            if p and p.strip()
        )
        filings.append(
            Filing(
                doc_id=doc_id,
                member=name,
                filing_date=datetime.strptime(filing_date_raw, "%m/%d/%Y").date(),
            )
        )
    return filings


def fetch_trades(filing: Filing, year: int) -> list[Trade]:
    """Download one PTR PDF and extract its equity (asset code [ST]) transactions.

    Returns [] both when the filing has no equity transactions and when the
    PDF is a scanned image with no extractable text (~10% of filings) -
    caller can't tell those apart from the return value alone, which is fine:
    both mean "nothing usable here".
    """
    resp = requests.get(PDF_URL.format(year=year, doc_id=filing.doc_id), timeout=60)
    resp.raise_for_status()
    reader = PdfReader(io.BytesIO(resp.content))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)

    trades = []
    for ticker, tx_type, date1, _date2 in _TXN_RE.findall(text):
        trades.append(
            Trade(
                doc_id=filing.doc_id,
                member=filing.member,
                ticker=ticker,
                tx_type=tx_type,
                tx_date=datetime.strptime(date1, "%m/%d/%Y").date(),
                filing_date=filing.filing_date,
            )
        )
    return trades
