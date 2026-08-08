"""Runnable checks for insider cluster detection - the criteria that decide what
counts as an "opportunity": 2+ distinct insiders within CLUSTER_WINDOW_DAYS."""
from datetime import date

from insider import cluster, store
from insider.form4 import InsiderPurchase


def _purchase(ticker, insider, tx_date, shares=1000.0, price=10.0, accession=None):
    return InsiderPurchase(
        accession_no=accession or f"{ticker}-{insider}-{tx_date}",
        cik="1", ticker=ticker, company_name=f"{ticker} Inc.",
        insider_name=insider, insider_title="CFO",
        tx_date=date.fromisoformat(tx_date), shares=shares, price_per_share=price,
        filing_date=date.fromisoformat(tx_date),
    )


def test_two_insiders_within_window_form_a_cluster():
    with store.connect(":memory:") as conn:
        store.insert_insider_purchase(conn, _purchase("ACME", "Alice", "2026-07-01"))
        store.insert_insider_purchase(conn, _purchase("ACME", "Bob", "2026-07-10"))  # 9 days later

        clusters = cluster.detect_clusters(conn, date(2026, 6, 1))
        assert len(clusters) == 1
        assert clusters[0]["ticker"] == "ACME"
        assert clusters[0]["cluster_date"] == date(2026, 7, 10)


def test_two_insiders_outside_window_do_not_cluster():
    with store.connect(":memory:") as conn:
        store.insert_insider_purchase(conn, _purchase("ACME", "Alice", "2026-07-01"))
        store.insert_insider_purchase(conn, _purchase("ACME", "Bob", "2026-07-21"))  # 20 days later

        clusters = cluster.detect_clusters(conn, date(2026, 6, 1))
        assert clusters == []


def test_same_insider_buying_twice_is_not_a_cluster():
    with store.connect(":memory:") as conn:
        store.insert_insider_purchase(conn, _purchase("ACME", "Alice", "2026-07-01", accession="a1"))
        store.insert_insider_purchase(conn, _purchase("ACME", "Alice", "2026-07-05", accession="a2"))

        clusters = cluster.detect_clusters(conn, date(2026, 6, 1))
        assert clusters == []  # distinct insiders required, not distinct filings


def test_three_insiders_only_two_within_window_still_clusters():
    with store.connect(":memory:") as conn:
        store.insert_insider_purchase(conn, _purchase("ACME", "Alice", "2026-07-01"))
        store.insert_insider_purchase(conn, _purchase("ACME", "Bob", "2026-07-05"))
        store.insert_insider_purchase(conn, _purchase("ACME", "Carol", "2026-08-01"))  # too late to join

        clusters = cluster.detect_clusters(conn, date(2026, 6, 1))
        assert len(clusters) == 1
        assert {b.insider_name for b in clusters[0]["buys"]} == {"Alice", "Bob"}


def test_already_recorded_cluster_is_not_redetected():
    with store.connect(":memory:") as conn:
        store.insert_insider_purchase(conn, _purchase("ACME", "Alice", "2026-07-01"))
        store.insert_insider_purchase(conn, _purchase("ACME", "Bob", "2026-07-05"))
        store.insert_insider_cluster(conn, "ACME", date(2026, 7, 5), "ACME Inc.", ["Alice", "Bob"], 20000.0, 10.0, 6_000_000.0)

        clusters = cluster.detect_clusters(conn, date(2026, 6, 1))
        assert clusters == []


def test_liquidity_gate_rejects_and_accepts_by_threshold(monkeypatch):
    thin = cluster.MIN_AVG_DAILY_DOLLAR_VOLUME - 1
    thick = cluster.MIN_AVG_DAILY_DOLLAR_VOLUME + 1
    monkeypatch.setattr(cluster, "check_liquidity", lambda ticker: thin if ticker == "THIN" else thick)
    monkeypatch.setattr(cluster.notify, "send", lambda *a, **k: None)

    with store.connect(":memory:") as conn:
        thin_cluster = {
            "ticker": "THIN", "cluster_date": date(2026, 7, 10), "company_name": "Thin Inc.",
            "buys": [_purchase("THIN", "Alice", "2026-07-01"), _purchase("THIN", "Bob", "2026-07-10")],
        }
        thick_cluster = {
            "ticker": "THICK", "cluster_date": date(2026, 7, 10), "company_name": "Thick Inc.",
            "buys": [_purchase("THICK", "Alice", "2026-07-01"), _purchase("THICK", "Bob", "2026-07-10")],
        }
        stats = {}
        sent = cluster.alert_and_record(conn, [thin_cluster, thick_cluster], stats, dry_run=False, no_notify=True)

        assert sent == 1
        assert stats["insider_clusters_illiquid"] == 1
        recorded = {c.ticker for c in store.all_insider_clusters(conn)}
        assert recorded == {"THICK"}


if __name__ == "__main__":
    test_two_insiders_within_window_form_a_cluster()
    test_two_insiders_outside_window_do_not_cluster()
    test_same_insider_buying_twice_is_not_a_cluster()
    test_three_insiders_only_two_within_window_still_clusters()
    test_already_recorded_cluster_is_not_redetected()
    print("ok (run via pytest for the monkeypatch-based liquidity test)")
