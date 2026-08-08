"""Ticker -> sector ETF, via SEC EDGAR (free, keyless) - used as the benchmark
for relative-strength scoring instead of the whole market.

Yahoo's own sector endpoint (quoteSummary) now requires an auth crumb we
don't have (401 Invalid Crumb, verified live) - same fragile-auth category
already rejected for Capitol Trades and Senate eFD in this project. SEC EDGAR
gives an authoritative SIC industry code for free instead; there's no official
SIC->sector-ETF crosswalk, so the mapping below is a hand-built approximation
by 2-digit SIC major group, not audited against GICS. Good enough to pick a
sector benchmark; not good enough to trust for anything finer-grained.
Unmapped or failed lookups fall back to SPY (the whole market), which is
always a safe default, never a wrong one.
"""
from __future__ import annotations

import time

import requests

from insider.config import SEC_USER_AGENT

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

# (low, high, etf) inclusive ranges over the 2-digit SIC major group (sic // 100).
# ponytail: coarse by major group, real edge cases exist (e.g. group 35 spans
# both industrial machinery and computer hardware) - good enough for a
# benchmark pick, not a claim of GICS-accurate classification.
_SIC_MAJOR_GROUP_TO_ETF = [
    (1, 9, "XLP"),      # agriculture, forestry, fishing
    (10, 14, "XLB"),    # mining
    (15, 17, "XLI"),    # construction
    (20, 21, "XLP"),    # food, tobacco
    (22, 23, "XLY"),    # textiles, apparel
    (24, 26, "XLB"),    # lumber, wood, paper
    (27, 27, "XLC"),    # printing, publishing
    (28, 28, "XLB"),    # chemicals
    (29, 29, "XLE"),    # petroleum refining
    (30, 30, "XLB"),    # rubber, plastics
    (31, 31, "XLY"),    # leather
    (32, 33, "XLB"),    # stone/clay/glass, primary metals
    (34, 35, "XLI"),    # fabricated metals, industrial machinery
    (36, 36, "XLK"),    # electronic equipment
    (37, 37, "XLI"),    # transportation equipment
    (384, 384, "XLV"),  # medical instruments - must be checked before the 38 bucket below
    (38, 38, "XLK"),    # instruments (measuring/scientific; medical carved out above)
    (39, 39, "XLY"),    # misc manufacturing
    (40, 47, "XLI"),    # transportation
    (48, 48, "XLC"),    # communications
    (49, 49, "XLU"),    # electric/gas/sanitary services
    (50, 51, "XLI"),    # wholesale trade
    (54, 54, "XLP"),    # food stores - must be checked before the 52-59 bucket below
    (52, 59, "XLY"),    # retail trade
    (60, 64, "XLF"),    # finance, insurance
    (65, 65, "XLRE"),   # real estate
    (67, 67, "XLF"),    # holding & investment offices
    (73, 73, "XLK"),    # business services (software-heavy)
    (78, 78, "XLC"),    # motion pictures - must be checked before the 70-79 catch-all below
    (70, 79, "XLY"),    # hotels, personal services, repair, amusement & recreation (incl.
                        # fitness/rec facilities - verified live: Life Time Group's SIC 79
                        # was catching XLC under an earlier, too-broad version of this rule)
    (80, 80, "XLV"),    # health services
]

_ticker_map_cache: dict[str, str] | None = None


def sic_to_etf(sic: int | None) -> str:
    """Pure lookup, no network - SIC code to sector ETF ticker, SPY if unmapped.

    SIC codes are 4 digits. A 2-digit major-group range (e.g. 36) is matched
    against the code's leading 2 digits (sic // 100); a 3-digit industry-group
    range (e.g. 384) is matched against its leading 3 digits (sic // 10).
    """
    if sic is None:
        return "SPY"
    for lo, hi, etf in _SIC_MAJOR_GROUP_TO_ETF:
        key = sic // 10 if lo >= 100 else sic // 100
        if lo <= key <= hi:
            return etf
    return "SPY"


def _load_ticker_map() -> dict[str, str]:
    global _ticker_map_cache
    if _ticker_map_cache is None:
        resp = requests.get(TICKER_MAP_URL, headers={"User-Agent": SEC_USER_AGENT}, timeout=30)
        resp.raise_for_status()
        _ticker_map_cache = {
            row["ticker"].upper(): str(row["cik_str"]).zfill(10) for row in resp.json().values()
        }
    return _ticker_map_cache


def sector_etf_for_ticker(ticker: str) -> str:
    """Resolve a ticker to a sector ETF via SEC EDGAR. Any failure -> "SPY" (never raises)."""
    try:
        cik = _load_ticker_map().get(ticker.upper())
        if cik is None:
            return "SPY"
        time.sleep(0.2)  # be polite to data.sec.gov
        resp = requests.get(
            SUBMISSIONS_URL.format(cik=cik), headers={"User-Agent": SEC_USER_AGENT}, timeout=30
        )
        resp.raise_for_status()
        sic = resp.json().get("sic")  # SEC returns this as a string (e.g. "3674"), not an int
        return sic_to_etf(int(sic) if sic else None)
    except Exception:
        return "SPY"
