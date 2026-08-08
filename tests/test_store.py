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


if __name__ == "__main__":
    test_sale_suppresses_only_preceding_buys()
    test_migration_adds_columns_to_pre_existing_db()
    print("ok")
