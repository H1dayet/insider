"""Runnable checks for earnings-watch alerting - the criteria that decide
whether a single insider purchase (no 2+-insider requirement, unlike
cluster.py) is "near enough" to the next earnings report to alert on."""
from datetime import date, timedelta

from insider import earnings_watch, store
from insider.form4 import InsiderPurchase


def _purchase(ticker="ACME", insider="Alice", tx_date="2026-07-01", accession="a1"):
    return InsiderPurchase(
        accession_no=accession, cik="1", ticker=ticker, company_name=f"{ticker} Inc.",
        insider_name=insider, insider_title="CFO",
        tx_date=date.fromisoformat(tx_date), shares=1000.0, price_per_share=10.0,
        filing_date=date.fromisoformat(tx_date),
    )


def test_purchase_within_window_alerts_and_records(monkeypatch):
    monkeypatch.setattr(earnings_watch.cluster, "check_liquidity", lambda ticker: 6_000_000.0)
    monkeypatch.setattr(earnings_watch.prices, "next_earnings_date", lambda ticker: date.today() + timedelta(days=10))
    monkeypatch.setattr(earnings_watch.notify, "send", lambda *a, **k: None)

    with store.connect(":memory:") as conn:
        stats = {}
        alerted = earnings_watch.check_purchase(conn, _purchase(), stats, dry_run=False, no_notify=True)

        assert alerted is True
        assert stats["earnings_watch_alerted"] == 1
        recorded = store.all_earnings_watch(conn)
        assert len(recorded) == 1
        assert recorded[0].ticker == "ACME"


def test_purchase_outside_window_does_not_alert(monkeypatch):
    monkeypatch.setattr(earnings_watch.cluster, "check_liquidity", lambda ticker: 6_000_000.0)
    monkeypatch.setattr(earnings_watch.prices, "next_earnings_date", lambda ticker: date.today() + timedelta(days=30))
    monkeypatch.setattr(earnings_watch.notify, "send", lambda *a, **k: None)

    with store.connect(":memory:") as conn:
        alerted = earnings_watch.check_purchase(conn, _purchase(), {}, dry_run=False, no_notify=True)

        assert alerted is False
        assert store.all_earnings_watch(conn) == []


def test_illiquid_ticker_is_rejected_before_earnings_lookup(monkeypatch):
    monkeypatch.setattr(
        earnings_watch.cluster, "check_liquidity",
        lambda ticker: earnings_watch.cluster.MIN_AVG_DAILY_DOLLAR_VOLUME - 1,
    )

    def fail_if_called(ticker):
        raise AssertionError("earnings lookup should not happen for illiquid tickers")

    monkeypatch.setattr(earnings_watch.prices, "next_earnings_date", fail_if_called)

    with store.connect(":memory:") as conn:
        stats = {}
        alerted = earnings_watch.check_purchase(conn, _purchase(), stats, dry_run=False, no_notify=True)

        assert alerted is False
        assert stats["earnings_watch_illiquid"] == 1


def test_no_earnings_date_available_does_not_alert(monkeypatch):
    monkeypatch.setattr(earnings_watch.cluster, "check_liquidity", lambda ticker: 6_000_000.0)
    monkeypatch.setattr(earnings_watch.prices, "next_earnings_date", lambda ticker: None)

    with store.connect(":memory:") as conn:
        stats = {}
        alerted = earnings_watch.check_purchase(conn, _purchase(), stats, dry_run=False, no_notify=True)

        assert alerted is False
        assert stats["earnings_lookup_failures"] == 1


def test_dry_run_does_not_record():
    with store.connect(":memory:") as conn:
        import insider.earnings_watch as ew
        orig_liq, orig_earn = ew.cluster.check_liquidity, ew.prices.next_earnings_date
        ew.cluster.check_liquidity = lambda ticker: 6_000_000.0
        ew.prices.next_earnings_date = lambda ticker: date.today() + timedelta(days=5)
        try:
            alerted = ew.check_purchase(conn, _purchase(), {}, dry_run=True, no_notify=True)
        finally:
            ew.cluster.check_liquidity, ew.prices.next_earnings_date = orig_liq, orig_earn

        assert alerted is True  # it did qualify
        assert store.all_earnings_watch(conn) == []  # but dry_run records nothing


def test_prune_earnings_watch_drops_past_dates():
    with store.connect(":memory:") as conn:
        store.insert_earnings_watch(conn, _purchase(accession="past"), date.today() - timedelta(days=1))
        store.insert_earnings_watch(conn, _purchase(accession="future"), date.today() + timedelta(days=5))

        store.prune_earnings_watch_past(conn, date.today())

        remaining = {w.company_name for w in store.all_earnings_watch(conn)}
        assert remaining == {"ACME Inc."}
        assert len(store.all_earnings_watch(conn)) == 1


if __name__ == "__main__":
    print("run via pytest for monkeypatch-based tests")
