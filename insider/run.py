"""Fetch new House PTR filings, price the buys, alert on Telegram, render the dashboard.

Usage:
    python -m insider.run                       # normal run
    python -m insider.run --dry-run              # print alerts instead of sending; nothing recorded
    python -m insider.run --no-notify             # skip Telegram but still mark alerted + record
    python -m insider.run --test-notify           # send one hello message, then exit
    python -m insider.run --since 2026-06-01       # override the current/last-month cutoff
"""
from __future__ import annotations

import argparse
import time
from datetime import date, timedelta

from insider import cluster, filings, form4, notify, prices, sector, store, track
from insider.config import RS_HIGH, RS_LOW
from insider.render import render

DB_PATH = "trades.db"
DASHBOARD_PATH = "docs/index.html"

FORM4_LOOKBACK_DAYS = 3  # trailing window fetched fresh each run - short enough to keep
                          # runtime bounded, long enough to self-heal a missed cron day
FORM4_MAX_FILINGS_PER_RUN = 300  # ponytail: EDGAR-wide Form 4 volume (hundreds/day
                                   # across all issuers) dwarfs the Congress pipeline's;
                                   # this caps one run's work so a big backlog (e.g. the
                                   # first-ever run) can't blow out CI time in one go.
                                   # processed_form4_filings makes progress resumable
                                   # across runs - raise or parallelize if it can't keep up.


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


def sync_insider_purchases(conn, stats: dict) -> None:
    """Pull recent Form 4 filings, parse qualifying open-market purchases (see
    form4.fetch_purchases for the filter: officer/director, code P, no 10b5-1 flag),
    and store them. Bounded by FORM4_MAX_FILINGS_PER_RUN - see its comment above.
    """
    since = date.today() - timedelta(days=FORM4_LOOKBACK_DAYS)
    try:
        candidates = form4.list_form4_filings_since(since)
    except Exception as e:
        print(f"Form 4 index fetch failed: {e}")
        return

    processed_this_run = 0
    for filing in candidates:
        if processed_this_run >= FORM4_MAX_FILINGS_PER_RUN:
            break
        if store.is_form4_processed(conn, filing.accession_no):
            continue
        stats["form4_filings_scanned"] = stats.get("form4_filings_scanned", 0) + 1
        try:
            purchases = form4.fetch_purchases(filing)
        except Exception as e:
            print(f"skipping Form 4 {filing.accession_no} ({filing.issuer_name}): {e}")
            stats["form4_fetch_errors"] = stats.get("form4_fetch_errors", 0) + 1
            store.mark_form4_processed(conn, filing.accession_no, filing.filing_date)
            processed_this_run += 1
            time.sleep(0.5)
            continue

        for p in purchases:
            store.insert_insider_purchase(conn, p)
        if purchases:
            stats["form4_purchases_found"] = stats.get("form4_purchases_found", 0) + len(purchases)
        store.mark_form4_processed(conn, filing.accession_no, filing.filing_date)
        processed_this_run += 1
        time.sleep(0.5)  # be polite to SEC EDGAR


def check_alerts(conn, cutoff: date, stats: dict, *, dry_run: bool, no_notify: bool = False) -> tuple[int, list[dict]]:
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

    Returns (count sent, fired) - fired carries everything track.py needs to
    record a recommendation (price at alert time, not the member's entry,
    since that's what copying the trade would actually have cost you).
    dry_run never marks alerted or returns fired rows, so nothing is recorded.
    no_notify marks alerted and records like a real run, just skips Telegram.
    """
    sent = 0
    fired: list[dict] = []
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
            if not no_notify:
                notify.send(
                    f"*{trade.ticker}* — {trade.member} bought on {trade.tx_date}\n"
                    f"Entry ${trade.entry_price:.2f} -> now ${current:.2f} "
                    f"({pct:+.1f}% vs entry, {relative_strength:+.1f}pp vs {etf})\n"
                    f"Filed {trade.filing_date} (STOCK Act, ~45-day disclosure lag)",
                    dry_run=dry_run,
                )
            if not dry_run:
                store.mark_alerted(conn, trade.doc_id, trade.ticker, trade.tx_type, trade.tx_date)
                fired.append({
                    "ticker": trade.ticker, "member": trade.member, "current": current,
                    "sector_etf": etf, "bench_current": bench.current, "company_name": quote.name,
                })
            sent += 1
        time.sleep(0.5)
    return sent, fired


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="print alerts, mark/record nothing")
    parser.add_argument("--no-notify", action="store_true", help="skip Telegram, still mark alerted + record")
    parser.add_argument("--test-notify", action="store_true")
    parser.add_argument("--since", type=date.fromisoformat, default=None,
                         help="override the current/last-month cutoff for testing")
    args = parser.parse_args()

    if args.test_notify:
        notify.send("insider bot: hello, wiring works.")
        return

    cutoff = args.since or month_cutoff(date.today())
    today = date.today()
    stats = {
        "filings_scanned": 0,
        "filings_skipped_old": 0,
        "pdf_fetch_errors": 0,
        "price_lookup_failures": 0,
        "sector_lookup_fallback": 0,
    }

    with store.connect(DB_PATH) as conn:
        sync_filings(conn, cutoff, stats)
        sent, fired = check_alerts(conn, cutoff, stats, dry_run=args.dry_run, no_notify=args.no_notify)
        if fired:
            track.record_recommendations(conn, fired, today)
        track.annotate_exits(conn, store.all_trades(conn))  # sells still present pre-prune
        track.update_live_prices(conn, stats)
        track.score_due_checkpoints(conn, today, stats)
        store.prune_older_than(conn, cutoff)
        all_trades = store.all_trades(conn)
        track_summary = track.summary(conn)
        recommendations = store.all_recommendations(conn)
        checkpoints = store.all_checkpoints(conn)

        sync_insider_purchases(conn, stats)
        cluster_since = today - timedelta(days=cluster.CLUSTER_WINDOW_DAYS)
        detected = cluster.detect_clusters(conn, cluster_since)
        insider_sent = cluster.alert_and_record(conn, detected, stats, dry_run=args.dry_run, no_notify=args.no_notify)
        store.prune_insider_purchases_older_than(conn, cluster_since)
        store.prune_processed_form4_filings_older_than(conn, cluster_since)
        insider_clusters = store.all_insider_clusters(conn)

    render(all_trades, DASHBOARD_PATH, stats,
           track_data={"recommendations": recommendations, "checkpoints": checkpoints, "summary": track_summary},
           insider_clusters=insider_clusters)
    print(f"done: {sent} Congress alert(s), {insider_sent} insider cluster alert(s) sent")


if __name__ == "__main__":
    main()
