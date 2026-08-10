"""Single-insider-purchase earnings-proximity alerts: any qualifying officer/
director open-market purchase (see form4.fetch_purchases for the filter) where
the company's next earnings report is within EARNINGS_WINDOW_DAYS.

Independent of cluster.py's 2+-insider requirement - a lone purchase counts
here. Checked once, right when the purchase is first parsed (see run.py); it
never gets re-evaluated later, so a purchase that was far from earnings when
made is not retroactively flagged once earnings approach. Reuses cluster.py's
liquidity gate to keep this from firing on illiquid microcaps.
"""
from __future__ import annotations

import time
from datetime import date

from insider import cluster, notify, prices, store
from insider.form4 import InsiderPurchase

EARNINGS_WINDOW_DAYS = 14


def check_purchase(conn, p: InsiderPurchase, stats: dict, *, dry_run: bool, no_notify: bool) -> bool:
    """Liquidity-filter, then earnings-date-check one purchase; alert + record
    if it qualifies. Returns whether it alerted."""
    adv = cluster.check_liquidity(p.ticker)
    if adv is None or adv < cluster.MIN_AVG_DAILY_DOLLAR_VOLUME:
        stats["earnings_watch_illiquid"] = stats.get("earnings_watch_illiquid", 0) + 1
        time.sleep(0.5)
        return False

    try:
        earnings = prices.next_earnings_date(p.ticker)
    except prices.PriceUnavailable:
        earnings = None
    if earnings is None:
        stats["earnings_lookup_failures"] = stats.get("earnings_lookup_failures", 0) + 1
        time.sleep(0.5)
        return False

    days_until = (earnings - date.today()).days
    if not (0 <= days_until <= EARNINGS_WINDOW_DAYS):
        time.sleep(0.5)
        return False

    insider_label = f"{p.insider_name} ({p.insider_title})" if p.insider_title else p.insider_name
    total_value = p.shares * p.price_per_share
    company = p.company_name or p.ticker

    if not no_notify:
        notify.send(
            f"*{p.ticker}* — insider buy ahead of earnings ({company})\n"
            f"{insider_label} bought ${total_value:,.0f} at ${p.price_per_share:.2f}/share on {p.tx_date}\n"
            f"Earnings expected {earnings.isoformat()} ({days_until}d)",
            dry_run=dry_run,
        )
    if not dry_run:
        store.insert_earnings_watch(conn, p, earnings)
        stats["earnings_watch_alerted"] = stats.get("earnings_watch_alerted", 0) + 1
    time.sleep(0.5)
    return True
