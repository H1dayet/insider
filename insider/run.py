"""Fetch new House PTR filings, price the buys, alert on Telegram, render the dashboard.

Usage:
    python -m insider.run                       # normal run
    python -m insider.run --dry-run              # print alerts instead of sending
    python -m insider.run --test-notify           # send one hello message, then exit
    python -m insider.run --since 2026-06-01       # override the current/last-month cutoff
"""
from __future__ import annotations

import argparse
import time
from datetime import date

from insider import filings, notify, prices, sector, store
from insider.config import RS_HIGH, RS_LOW
from insider.render import render

DB_PATH = "trades.db"
DASHBOARD_PATH = "docs/index.html"


def month_cutoff(today: date) -> date:
    """First day of last month - trades older than this are useless for copy-trading."""
    first_of_this_month = today.replace(day=1)
    if first_of_this_month.month == 1:
        return first_of_this_month.replace(year=first_of_this_month.year - 1, month=12)
    return first_of_this_month.replace(month=first_of_this_month.month - 1)


def sync_filings(conn, cutoff: date, stats: dict) -> None:
    """Pull new PTR filings for this year and last (covers year-boundary lag) into the DB.

    Filings dated before `cutoff` are skipped without downloading their PDF -
    there's no point fetching a filing whose disclosed transaction is already
    too old to act on.
    """
    for year in (date.today().year, date.today().year - 1):
        try:
            year_filings = filings.list_new_ptr_filings(year)
        except Exception as e:
            print(f"index fetch failed for {year}: {e}")
            continue

        for filing in year_filings:
            if filing.filing_date < cutoff:
                stats["filings_skipped_old"] += 1
                continue
            if store.is_processed(conn, filing.doc_id):
                continue
            stats["filings_scanned"] += 1
            try:
                trades = filings.fetch_trades(filing, year)
            except Exception as e:
                print(f"skipping filing {filing.doc_id} ({filing.member}): {e}")
                stats["pdf_fetch_errors"] += 1
                continue

            for trade in trades:
                if trade.tx_date < cutoff:
                    continue  # rare late filer disclosing an old transaction
                entry_price = None
                company_name = None
                sector_etf = None
                if trade.tx_type == "P":
                    try:
                        quote = prices.lookup(trade.ticker, trade.tx_date)
                        entry_price, company_name = quote.entry, quote.name
                    except prices.PriceUnavailable as e:
                        print(f"price lookup failed: {e}")
                        stats["price_lookup_failures"] += 1
                    sector_etf = sector.sector_etf_for_ticker(trade.ticker)
                    if sector_etf == "SPY":  # sector_etf_for_ticker never raises; "SPY" means it fell back
                        stats["sector_lookup_fallback"] += 1
                store.upsert_trade(conn, trade, entry_price, company_name, sector_etf)

            store.mark_processed(conn, filing.doc_id)
            time.sleep(0.5)  # be polite to the Clerk's server and Yahoo alike


def check_alerts(conn, cutoff: date, stats: dict, *, dry_run: bool) -> int:
    """Re-evaluate every unalerted buy from the current-or-last month; alert on relative strength.

    Persists the freshly observed price and relative_strength for every trade
    checked here, whether or not it alerts - this is what makes them live for
    watched rows. Once a trade alerts it drops out of unalerted_buys_since()
    and this loop never touches it again, which is what freezes both at alert
    time.

    Gate is relative strength vs. a sector benchmark (stock return minus
    benchmark return, both since tx_date), not nominal price - see RS_LOW/
    RS_HIGH in config.py for why. Benchmark quotes are cached per (etf, date)
    for this run only, since many trades share a filing date.
    """
    sent = 0
    bench_cache: dict[tuple[str, date], prices.Quote] = {}
    sales = store.latest_sales(store.all_trades(conn))

    for trade in store.unalerted_buys_since(conn, cutoff):
        if trade.entry_price is None:
            continue
        try:
            quote = prices.lookup(trade.ticker, trade.tx_date)
        except prices.PriceUnavailable:
            stats["price_lookup_failures"] += 1
            time.sleep(0.5)
            continue
        current = quote.current

        etf = trade.sector_etf
        if etf is None:
            # backfill for rows parsed before sector_etf existed - sync_filings()
            # only resolves this once per filing and won't revisit this trade
            etf = sector.sector_etf_for_ticker(trade.ticker)
            if etf == "SPY":
                stats["sector_lookup_fallback"] += 1
        bench_key = (etf, trade.tx_date)
        if bench_key not in bench_cache:
            try:
                bench_cache[bench_key] = prices.lookup(etf, trade.tx_date)
            except prices.PriceUnavailable:
                stats["price_lookup_failures"] += 1
                time.sleep(0.5)
                continue
        bench = bench_cache[bench_key]

        stock_return = current / trade.entry_price - 1
        bench_return = bench.current / bench.entry - 1
        relative_strength = (stock_return - bench_return) * 100

        store.update_current_price(
            conn, trade.doc_id, trade.ticker, trade.tx_type, trade.tx_date, current,
            quote.name, relative_strength, etf,
        )

        if store.has_exited(trade, sales):
            time.sleep(0.5)
            continue  # member already sold this position - price stays live for the dashboard

        if RS_LOW <= relative_strength <= RS_HIGH:
            pct = stock_return * 100
            notify.send(
                f"*{trade.ticker}* — {trade.member} bought on {trade.tx_date}\n"
                f"Entry ${trade.entry_price:.2f} -> now ${current:.2f} "
                f"({pct:+.1f}% vs entry, {relative_strength:+.1f}pp vs {etf})\n"
                f"Filed {trade.filing_date} (STOCK Act, ~45-day disclosure lag)",
                dry_run=dry_run,
            )
            if not dry_run:
                store.mark_alerted(conn, trade.doc_id, trade.ticker, trade.tx_type, trade.tx_date)
            sent += 1
        time.sleep(0.5)
    return sent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--test-notify", action="store_true")
    parser.add_argument("--since", type=date.fromisoformat, default=None,
                         help="override the current/last-month cutoff for testing")
    args = parser.parse_args()

    if args.test_notify:
        notify.send("insider bot: hello, wiring works.")
        return

    cutoff = args.since or month_cutoff(date.today())
    stats = {
        "filings_scanned": 0,
        "filings_skipped_old": 0,
        "pdf_fetch_errors": 0,
        "price_lookup_failures": 0,
        "sector_lookup_fallback": 0,
    }

    with store.connect(DB_PATH) as conn:
        sync_filings(conn, cutoff, stats)
        sent = check_alerts(conn, cutoff, stats, dry_run=args.dry_run)
        store.prune_older_than(conn, cutoff)
        all_trades = store.all_trades(conn)

    render(all_trades, DASHBOARD_PATH, stats)
    print(f"done: {sent} alert(s) sent")


if __name__ == "__main__":
    main()
