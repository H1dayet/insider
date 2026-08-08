"""Runnable check for the trades.db schema migration - the one branch that can
silently corrupt an existing, already-populated database if it's wrong."""
import sqlite3
import tempfile
from datetime import date
from pathlib import Path

from insider import store


def _trade(member, ticker, tx_type, tx_date):
    return store.StoredTrade(
        doc_id="1", ticker=ticker, tx_type=tx_type, tx_date=date.fromisoformat(tx_date),
        member=member, filing_date=date.fromisoformat(tx_date), entry_price=100.0,
        alerted_at=None, current_price=None, checked_at=None, company_name=None,
        sector_etf=None, relative_strength=None,
    )


def test_sale_suppresses_only_preceding_buys():
    trades = [
        _trade("A", "XYZ", "P", "2026-07-01"),
        _trade("A", "XYZ", "S", "2026-07-10"),
        _trade("A", "XYZ", "P", "2026-07-20"),  # re-bought after the sale - not exited
        _trade("B", "ABC", "S", "2026-07-01"),
        _trade("B", "ABC", "P", "2026-07-10"),  # buy comes after the sale - not exited
        _trade("C", "SDS", "P", "2026-07-05"),
        _trade("C", "SDS", "S", "2026-07-05"),  # same-day sell - exited
        _trade("D", "XYZ", "P", "2026-07-01"),  # different member, same ticker - not exited
    ]
    sales = store.latest_sales(trades)

    buy_a1 = trades[0]  # A/XYZ bought 07-01, sold 07-10
    buy_a2 = trades[2]  # A/XYZ bought 07-20, after the sale
    buy_b = trades[4]   # B/ABC bought 07-10, sale predates it
    buy_c = trades[5]   # C/SDS same-day buy+sell
    buy_d = trades[7]   # D/XYZ, unrelated member

    assert store.has_exited(buy_a1, sales) is True
    assert store.has_exited(buy_a2, sales) is False
    assert store.has_exited(buy_b, sales) is False
    assert store.has_exited(buy_c, sales) is True
    assert store.has_exited(buy_d, sales) is False


def test_migration_adds_columns_to_pre_existing_db():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "old.db")

        # Simulate a trades.db created before current_price/checked_at existed.
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE trades (
                doc_id TEXT, ticker TEXT, tx_type TEXT, tx_date TEXT,
                member TEXT, filing_date TEXT, entry_price REAL, alerted_at TEXT,
                PRIMARY KEY (doc_id, ticker, tx_type, tx_date)
            );
            INSERT INTO trades VALUES ('1', 'CCI', 'P', '2026-07-01', 'Someone', '2026-07-10', 100.0, NULL);
        """)
        conn.commit()
        conn.close()

        with store.connect(db_path) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(trades)")}
            assert {"current_price", "checked_at"} <= columns

            trades = store.all_trades(conn)
            assert len(trades) == 1
            assert trades[0].ticker == "CCI"
            assert trades[0].current_price is None  # pre-existing row, not yet checked


def test_recommendation_migration_adds_live_price_columns():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "old.db")

        # Simulate a recommendations table from before the live-price columns
        # existed (this shipped and got real rows before current_price/
        # current_relative_strength/checked_at were added).
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE recommendations (
                ticker TEXT NOT NULL, alert_date TEXT NOT NULL, entry_price REAL NOT NULL,
                sector_etf TEXT NOT NULL, bench_entry REAL NOT NULL, company_name TEXT,
                members TEXT, fill_count INTEGER NOT NULL, member_exited_on TEXT,
                PRIMARY KEY (ticker, alert_date)
            );
            INSERT INTO recommendations VALUES
                ('KO', '2026-07-01', 60.0, 'XLP', 50.0, 'Coca-Cola', 'Someone', 1, NULL);
        """)
        conn.commit()
        conn.close()

        with store.connect(db_path) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(recommendations)")}
            assert {"current_price", "current_relative_strength", "checked_at"} <= columns

            recs = store.all_recommendations(conn)
            assert len(recs) == 1
            assert recs[0].current_price is None  # pre-existing row, not yet live-priced

            store.update_recommendation_price(conn, "KO", date(2026, 7, 1), 65.0, 3.2)
            recs = store.all_recommendations(conn)
            assert recs[0].current_price == 65.0
            assert recs[0].current_relative_strength == 3.2


if __name__ == "__main__":
    test_sale_suppresses_only_preceding_buys()
    test_migration_adds_columns_to_pre_existing_db()
    test_recommendation_migration_adds_live_price_columns()
    print("ok")
