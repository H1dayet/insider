"""Track record: did copying an alert actually work?

Every alert the bot fires is recorded as a recommendation, priced at what you'd
actually have paid (the price at alert time, not the member's entry). Each
recommendation is then scored at 30/60/90/120 days against its sector benchmark -
the same relative-strength gate the alert itself uses (see run.py:check_alerts).

Checkpoints are frozen once written (store.insert_checkpoint uses INSERT OR IGNORE)
- a scored checkpoint is history, never revised.
"""
from __future__ import annotations

import statistics
import time
from datetime import date

from insider import prices, store

HORIZONS = (30, 60, 90, 120)


def record_recommendations(conn, fired: list[dict], alert_date: date) -> None:
    """Collapse today's fired alerts by ticker into one recommendation each.

    A member averaging into one stock across many filings (seen live: one
    member with 9 separate LTH buys alerting the same day) must not become 9
    identical recommendations - it's one decision to "buy LTH today".
    """
    by_ticker: dict[str, list[dict]] = {}
    for f in fired:
        by_ticker.setdefault(f["ticker"], []).append(f)

    for ticker, fills in by_ticker.items():
        first = fills[0]
        members = sorted({f["member"] for f in fills})
        store.insert_recommendation(
            conn, ticker, alert_date,
            entry_price=first["current"],
            sector_etf=first["sector_etf"],
            bench_entry=first["bench_current"],
            company_name=first["company_name"],
            members=members,
            fill_count=len(fills),
        )


def annotate_exits(conn, trades: list[store.StoredTrade]) -> None:
    """Stamp member_exited_on on recs whose member has since sold - annotation
    only, the checkpoint verdict is never altered by this."""
    sales = store.latest_sales(trades)
    for rec in store.all_recommendations(conn):
        if rec.member_exited_on:
            continue
        exit_dates = [
            sales[(m, rec.ticker)] for m in rec.members if (m, rec.ticker) in sales
        ]
        if exit_dates:
            store.set_member_exited(conn, rec.ticker, rec.alert_date, min(exit_dates))


def score_due_checkpoints(conn, today: date, stats: dict) -> None:
    """Price and score every checkpoint whose date has passed but isn't scored yet."""
    bench_cache: dict[tuple[str, date], float] = {}
    for rec, horizon, cp_date in store.unscored_due_checkpoints(conn, today, HORIZONS):
        try:
            price = prices.lookup(rec.ticker, cp_date).entry
        except prices.PriceUnavailable:
            store.insert_checkpoint(conn, rec.ticker, rec.alert_date, horizon, today,
                                     None, None, None, "no_data")
            stats["checkpoint_no_data"] = stats.get("checkpoint_no_data", 0) + 1
            time.sleep(0.5)
            continue

        bench_key = (rec.sector_etf, cp_date)
        if bench_key not in bench_cache:
            try:
                bench_cache[bench_key] = prices.lookup(rec.sector_etf, cp_date).entry
            except prices.PriceUnavailable:
                store.insert_checkpoint(conn, rec.ticker, rec.alert_date, horizon, today,
                                         price, None, None, "no_data")
                stats["checkpoint_no_data"] = stats.get("checkpoint_no_data", 0) + 1
                time.sleep(0.5)
                continue
        bench_price = bench_cache[bench_key]

        stock_return = price / rec.entry_price - 1
        bench_return = bench_price / rec.bench_entry - 1
        rs = (stock_return - bench_return) * 100
        status = "win" if rs > 0 else "loss"
        store.insert_checkpoint(conn, rec.ticker, rec.alert_date, horizon, today,
                                 price, bench_price, rs, status)
        stats["checkpoints_scored"] = stats.get("checkpoints_scored", 0) + 1
        time.sleep(0.5)


def summary(conn) -> dict:
    """Per-horizon hit rate + median relative strength, cohort maturity, best/worst rec.

    no_data checkpoints are excluded from the hit-rate denominator - a failed
    price lookup is not a loss.
    """
    recs = store.all_recommendations(conn)
    checkpoints = store.all_checkpoints(conn)
    by_horizon: dict[int, list[store.Checkpoint]] = {h: [] for h in HORIZONS}
    for c in checkpoints:
        by_horizon[c.horizon_days].append(c)

    per_horizon = {}
    for h, cps in by_horizon.items():
        scored = [c for c in cps if c.status != "no_data"]
        wins = [c for c in scored if c.status == "win"]
        per_horizon[h] = {
            "scored": len(scored),
            "wins": len(wins),
            "hit_rate": (len(wins) / len(scored) * 100) if scored else None,
            "median_rs": statistics.median(c.relative_strength for c in scored) if scored else None,
        }

    scored_all = [c for c in checkpoints if c.status != "no_data"]
    best = max(scored_all, key=lambda c: c.relative_strength, default=None)
    worst = min(scored_all, key=lambda c: c.relative_strength, default=None)

    return {
        "n_recs": len(recs),
        "per_horizon": per_horizon,
        "best": best,
        "worst": worst,
    }
