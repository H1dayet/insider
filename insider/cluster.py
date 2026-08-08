"""Insider cluster-buy detection: the strongest documented sub-signal in the insider-
trading literature. A single officer's open-market purchase is easy to dismiss as
personal financial planning; multiple insiders independently buying the same company
in a short window is much harder to explain away as coincidence.

No RS-vs-sector gate here (unlike the Congress pipeline) - Form 4's ~2-business-day
disclosure lag is too short for the "already ran up before you saw it" problem that
gate exists to solve. Liquidity is the real filter: 2025 Finance Research Letters,
"Does it pay to be fast?" found naive Form-4-copying returns evaporate once position
size is capped to something actually tradable, concentrated in illiquid microcaps.
"""
from __future__ import annotations

import time
from datetime import date

from insider import notify, prices, store
from insider.form4 import InsiderPurchase

CLUSTER_WINDOW_DAYS = 14  # tuning knob, not a backtested constant - see plan notes
MIN_CLUSTER_INSIDERS = 2
MIN_AVG_DAILY_DOLLAR_VOLUME = 5_000_000.0


def detect_clusters(conn, since: date) -> list[dict]:
    """Group recent insider_purchases by ticker; a cluster forms wherever 2+ distinct
    insiders have purchases within CLUSTER_WINDOW_DAYS of each other and that cluster
    isn't already recorded.

    Detects at most one new cluster per ticker per call (the earliest-completing one) -
    a deliberate simplification. A second, later, non-overlapping cluster on the same
    ticker will surface on a subsequent run once this one is recorded and the window
    moves forward; building a fully general non-overlapping-cluster partitioner isn't
    worth the complexity for what's expected to be rare in practice.
    """
    by_ticker: dict[str, list[InsiderPurchase]] = {}
    for p in store.recent_insider_purchases(conn, since):
        by_ticker.setdefault(p.ticker, []).append(p)

    clusters = []
    for ticker, buys in by_ticker.items():
        buys.sort(key=lambda b: b.tx_date)
        for i, anchor in enumerate(buys):
            window = [b for b in buys[i:] if (b.tx_date - anchor.tx_date).days <= CLUSTER_WINDOW_DAYS]
            distinct_insiders = {b.insider_name for b in window}
            if len(distinct_insiders) < MIN_CLUSTER_INSIDERS:
                continue
            cluster_date = max(b.tx_date for b in window)
            if store.cluster_exists(conn, ticker, cluster_date):
                continue
            clusters.append({
                "ticker": ticker,
                "cluster_date": cluster_date,
                "company_name": next((b.company_name for b in window if b.company_name), None),
                "buys": window,
            })
            break  # one new cluster per ticker per run - see docstring
    return clusters


def check_liquidity(ticker: str) -> float | None:
    """Average daily dollar volume, or None if Yahoo has no data for this symbol."""
    try:
        return prices.average_daily_dollar_volume(ticker)
    except prices.PriceUnavailable:
        return None


def alert_and_record(conn, clusters: list[dict], stats: dict, *, dry_run: bool, no_notify: bool) -> int:
    """Liquidity-filter each detected cluster, then alert + record the survivors.

    Recording happens for real clusters only (never for dry_run) - mirrors
    run.py:check_alerts()'s dry_run semantics exactly, so --dry-run stays fully
    read-only for this pipeline too.
    """
    sent = 0
    for c in clusters:
        adv = check_liquidity(c["ticker"])
        if adv is None or adv < MIN_AVG_DAILY_DOLLAR_VOLUME:
            stats["insider_clusters_illiquid"] = stats.get("insider_clusters_illiquid", 0) + 1
            time.sleep(0.5)
            continue

        buys = c["buys"]
        insiders = sorted({
            f"{b.insider_name} ({b.insider_title})" if b.insider_title else b.insider_name
            for b in buys
        })
        total_shares = sum(b.shares for b in buys)
        total_value = sum(b.shares * b.price_per_share for b in buys)
        avg_entry_price = total_value / total_shares
        span_days = (c["cluster_date"] - min(b.tx_date for b in buys)).days
        company = c["company_name"] or c["ticker"]

        if not no_notify:
            notify.send(
                f"*{c['ticker']}* — insider cluster buy ({company})\n"
                f"{len(insiders)} insiders: {', '.join(insiders)}\n"
                f"${total_value:,.0f} total across {span_days}d, avg ${avg_entry_price:.2f}/share "
                f"(avg daily $ volume: ${adv:,.0f})",
                dry_run=dry_run,
            )
        if not dry_run:
            store.insert_insider_cluster(
                conn, c["ticker"], c["cluster_date"], c["company_name"], insiders,
                total_value, avg_entry_price, adv,
            )
            stats["insider_clusters_alerted"] = stats.get("insider_clusters_alerted", 0) + 1
            sent += 1
        time.sleep(0.5)
    return sent
