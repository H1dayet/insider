"""SEC Form 4 daily index + XML parsing - insider open-market purchase detection.

Daily index: https://www.sec.gov/Archives/edgar/daily-index/{year}/QTR{q}/form.{yyyymmdd}.idx
Filing dir:  https://www.sec.gov/Archives/edgar/data/{issuer_cik}/{accession_nodash}/
             (primary XML filename is NOT consistently named - verified live: real
             filings use "form4-08082026_010821.xml", "wk-form4_1786152993.xml", etc.
             Always discover it via that directory's index.json, never guess.)
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import requests

from insider.config import SEC_USER_AGENT

DAILY_INDEX_URL = "https://www.sec.gov/Archives/edgar/daily-index/{year}/QTR{q}/form.{yyyymmdd}.idx"
FILING_DIR_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/"

# Matches lines like:
#   4                ACADIA PHARMACEUTICALS INC                                    1070494     20260807    edgar/data/1070494/0001899977-26-000008.txt
# Exact form type "4" only - excludes "4/A" amendments.
_IDX_LINE_RE = re.compile(r"^4\s+(.+?)\s+(\d+)\s+(\d{8})\s+(\S+\.txt)\s*$")


@dataclass
class Form4Filing:
    issuer_cik: str
    issuer_name: str
    filing_date: date
    accession_no: str  # dashed, e.g. "0001899977-26-000008"


@dataclass
class InsiderPurchase:
    accession_no: str
    cik: str  # issuer CIK
    ticker: str
    company_name: str | None
    insider_name: str
    insider_title: str | None
    tx_date: date
    shares: float
    price_per_share: float
    filing_date: date


def _quarter(d: date) -> int:
    return (d.month - 1) // 3 + 1


def list_form4_filings_for_day(day: date) -> list[Form4Filing]:
    """Every Form 4 (not 4/A) filed on this calendar day, across all issuers.

    SEC's daily index cross-lists each filing twice - once under the issuer's CIK, once
    under the reporting owner's own personal CIK (verified live: e.g. one row for
    "ADTRAN Holdings, Inc." and a second row for "Santo Timothy P", same accession
    number). Both CIK paths happen to serve the same filing directory, so either would
    "work", but processing both would double every fetch below for no benefit -
    dedupe by accession number, keeping whichever row appeared first.
    """
    url = DAILY_INDEX_URL.format(year=day.year, q=_quarter(day), yyyymmdd=day.strftime("%Y%m%d"))
    resp = requests.get(url, headers={"User-Agent": SEC_USER_AGENT}, timeout=60)
    if resp.status_code == 404:
        return []  # weekend/holiday - no index published that day
    resp.raise_for_status()

    filings = []
    seen_accessions: set[str] = set()
    for line in resp.text.splitlines():
        m = _IDX_LINE_RE.match(line)
        if not m:
            continue
        company, cik, date_str, filename = m.groups()
        accession = filename.rsplit("/", 1)[-1].removesuffix(".txt")
        if accession in seen_accessions:
            continue
        seen_accessions.add(accession)
        filings.append(
            Form4Filing(
                issuer_cik=cik,
                issuer_name=company.strip(),
                filing_date=datetime.strptime(date_str, "%Y%m%d").date(),
                accession_no=accession,
            )
        )
    return filings


def list_form4_filings_since(since: date, until: date | None = None) -> list[Form4Filing]:
    """All Form 4 filings across the [since, until] day range (inclusive).

    One bad day (SEC hasn't published today's index yet - verified live: returns 403,
    not 404, for a not-yet-available day; weekends 404 as expected) shouldn't lose every
    other day in the range, so failures are caught and skipped per-day rather than
    aborting the whole call.
    """
    until = until or date.today()
    filings = []
    d = since
    while d <= until:
        try:
            filings.extend(list_form4_filings_for_day(d))
        except Exception as e:
            print(f"Form 4 daily index fetch failed for {d}: {e}")
        d += timedelta(days=1)
    return filings


def _primary_doc_filename(cik: str, accession_no: str) -> str | None:
    """Discover the actual XML filename for one filing - see module docstring."""
    accession_nodash = accession_no.replace("-", "")
    url = FILING_DIR_URL.format(cik=cik, accession_nodash=accession_nodash) + "index.json"
    resp = requests.get(url, headers={"User-Agent": SEC_USER_AGENT}, timeout=30)
    resp.raise_for_status()
    for item in resp.json().get("directory", {}).get("item", []):
        name = item.get("name", "")
        if name.endswith(".xml"):
            return name
    return None


def _text(el: ET.Element | None, path: str) -> str | None:
    if el is None:
        return None
    node = el.find(path)
    return node.text.strip() if node is not None and node.text else None


def _has_10b5_1_flag(tx: ET.Element) -> bool:
    """Rule 10b5-1 scheduled-plan flag, added to Form 4's XML schema in EDGAR Release
    23.1 (March 2023) as <rule10b51>. Checked at both plausible nesting levels since the
    exact placement isn't confirmed against a live positive example - absence of the tag
    (the common case) means "not flagged", which is what we want to let through."""
    return _text(tx, "rule10b51") == "1" or _text(tx, "transactionCoding/rule10b51") == "1"


def fetch_purchases(filing: Form4Filing) -> list[InsiderPurchase]:
    """Download and parse one Form 4 filing, returning only genuine open-market
    purchases (code P) by an officer or director, excluding Rule 10b5-1 scheduled
    transactions.

    Research (Cohen/Malloy/Pomorski 2012): only these "opportunistic" trades carry
    predictive power - option exercises, gifts, scheduled sales, and 10% owner activity
    are "routine" noise with ~zero documented signal. A real filing pulled live during
    planning had zero P-coded transactions among a mix of exercises/conversions/a
    10b5-1-scheduled sale, confirming this filter does real work, not nothing.

    Returns [] for filings with no qualifying purchases, no tradable ticker, or that
    fail to parse (a handful of very old/malformed filings) - callers can't distinguish
    these cases from the return value alone, which is fine: all mean "nothing usable".
    """
    filename = _primary_doc_filename(filing.issuer_cik, filing.accession_no)
    if filename is None:
        return []
    accession_nodash = filing.accession_no.replace("-", "")
    url = FILING_DIR_URL.format(cik=filing.issuer_cik, accession_nodash=accession_nodash) + filename
    resp = requests.get(url, headers={"User-Agent": SEC_USER_AGENT}, timeout=30)
    resp.raise_for_status()

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError:
        return []

    return _parse_purchases(root, filing)


def _parse_purchases(root: ET.Element, filing: Form4Filing) -> list[InsiderPurchase]:
    issuer = root.find("issuer")
    ticker = _text(issuer, "issuerTradingSymbol")
    company_name = _text(issuer, "issuerName")
    if not ticker:
        return []  # no tradable symbol - can't price it, can't act on it

    # A filing can have multiple joint reportingOwners (e.g. a person + their trust);
    # the transaction table is a document-level sibling, not nested per-owner. To avoid
    # attributing one transaction to multiple "distinct insiders" (which would inflate
    # cluster detection), attribute the whole filing to the first officer/director
    # owner found - the overwhelming majority of filings have exactly one owner anyway.
    qualifying_owner = None
    for owner in root.findall("reportingOwner"):
        rel = owner.find("reportingOwnerRelationship")
        if _text(rel, "isOfficer") == "true" or _text(rel, "isDirector") == "true":
            qualifying_owner = owner
            break
    if qualifying_owner is None:
        return []  # 10%-owner-only filing - excluded per role-filter decision

    insider_name = _text(qualifying_owner, "reportingOwnerId/rptOwnerName") or "Unknown"
    title = _text(qualifying_owner, "reportingOwnerRelationship/officerTitle")

    purchases = []
    for tx in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        if _text(tx, "transactionCoding/transactionCode") != "P":
            continue
        if _has_10b5_1_flag(tx):
            continue
        shares = _text(tx, "transactionAmounts/transactionShares/value")
        price = _text(tx, "transactionAmounts/transactionPricePerShare/value")
        tx_date = _text(tx, "transactionDate/value")
        if not (shares and price and tx_date):
            continue  # footnoted/unpriced transaction - can't value it, skip
        purchases.append(
            InsiderPurchase(
                accession_no=filing.accession_no,
                cik=filing.issuer_cik,
                ticker=ticker,
                company_name=company_name,
                insider_name=insider_name,
                insider_title=title,
                tx_date=date.fromisoformat(tx_date),
                shares=float(shares),
                price_per_share=float(price),
                filing_date=filing.filing_date,
            )
        )
    return purchases
