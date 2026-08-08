"""Keyless price lookups via Yahoo Finance's public chart endpoint."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import NamedTuple

import requests

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


class PriceUnavailable(Exception):
    pass


class Quote(NamedTuple):
    entry: float
    current: float
    name: str | None  # company name from Yahoo's meta block, if it has one


def lookup(ticker: str, tx_date: date) -> Quote:
    """Return entry (close on/after tx_date), current price, and company name for ticker.

    Raises PriceUnavailable if Yahoo has no data for this symbol (e.g. it's
    not actually an equity ticker, or was delisted).
    """
    period1 = int(datetime.combine(tx_date, datetime.min.time()).timestamp())
    period2 = int((datetime.utcnow() + timedelta(days=1)).timestamp())
    resp = requests.get(
        CHART_URL.format(ticker=ticker),
        params={"period1": period1, "period2": period2, "interval": "1d"},
        headers={"User-Agent": _UA},
        timeout=30,
    )
    if resp.status_code != 200:
        raise PriceUnavailable(f"{ticker}: HTTP {resp.status_code}")

    data = resp.json()["chart"]
    if data.get("error") or not data.get("result"):
        raise PriceUnavailable(f"{ticker}: {data.get('error')}")

    result = data["result"][0]
    quotes = result.get("indicators", {}).get("quote") or [{}]
    closes = quotes[0].get("close") or []
    meta = result.get("meta", {})
    current = meta.get("regularMarketPrice")
    # first non-null close on/after the transaction date
    entry_close = next((c for c in closes if c is not None), None)
    if entry_close is None or current is None:
        raise PriceUnavailable(f"{ticker}: no usable close price")
    return Quote(entry=float(entry_close), current=float(current), name=meta.get("longName") or meta.get("shortName"))
