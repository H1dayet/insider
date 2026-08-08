"""SQLite persistence: every parsed buy, and which ones we've already alerted on."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, timedelta

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    doc_id      TEXT NOT NULL,
    ticker      TEXT NOT NULL,
    tx_type     TEXT NOT NULL,
    tx_date     TEXT NOT NULL,
    member      TEXT NOT NULL,
    filing_date TEXT NOT NULL,
    entry_price REAL,
    alerted_at  TEXT,
    PRIMARY KEY (doc_id, ticker, tx_type, tx_date)
);
CREATE TABLE IF NOT EXISTS processed_filings (
    doc_id TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS recommendations (
    ticker           TEXT NOT NULL,
    alert_date       TEXT NOT NULL,
    entry_price      REAL NOT NULL,
    sector_etf       TEXT NOT NULL,
    bench_entry      REAL NOT NULL,
    company_name     TEXT,
    members          TEXT,
    fill_count       INTEGER NOT NULL,
    member_exited_on TEXT,
    PRIMARY KEY (ticker, alert_date)
);
CREATE TABLE IF NOT EXISTS checkpoints (
    ticker            TEXT NOT NULL,
    alert_date        TEXT NOT NULL,
    horizon_days      INTEGER NOT NULL,
    scored_on         TEXT NOT NULL,
    price             REAL,
    bench_price       REAL,
    relative_strength REAL,
    status            TEXT NOT NULL,
    PRIMARY KEY (ticker, alert_date, horizon_days)
);
"""

# Columns added after the initial release - existing trades.db files won't have
# them, so connect() adds any that are missing on every startup (idempotent).
_TRADE_MIGRATIONS = {
    "current_price": "ALTER TABLE trades ADD COLUMN current_price REAL",
    "checked_at": "ALTER TABLE trades ADD COLUMN checked_at TEXT",
    "company_name": "ALTER TABLE trades ADD COLUMN company_name TEXT",
    "sector_etf": "ALTER TABLE trades ADD COLUMN sector_etf TEXT",
    "relative_strength": "ALTER TABLE trades ADD COLUMN relative_strength REAL",
}
_RECOMMENDATION_MIGRATIONS = {
    "current_price": "ALTER TABLE recommendations ADD COLUMN current_price REAL",
    "current_relative_strength": "ALTER TABLE recommendations ADD COLUMN current_relative_strength REAL",
    "checked_at": "ALTER TABLE recommendations ADD COLUMN checked_at TEXT",
}


@dataclass
class StoredTrade:
    doc_id: str
    ticker: str
    tx_type: str
    tx_date: date
    member: str
    filing_date: date
    entry_price: float | None
    alerted_at: str | None
    current_price: float | None
    checked_at: str | None
    company_name: str | None
    sector_etf: str | None
    relative_strength: float | None


@dataclass
class Recommendation:
    ticker: str
    alert_date: date
    entry_price: float
    sector_etf: str
    bench_entry: float
    company_name: str | None
    members: list[str]
    fill_count: int
    member_exited_on: str | None
    current_price: float | None
    current_relative_strength: float | None
    checked_at: str | None


@dataclass
class Checkpoint:
    ticker: str
    alert_date: date
    horizon_days: int
    scored_on: str
    price: float | None
    bench_price: float | None
    relative_strength: float | None
    status: str  # "win" | "loss" | "no_data"


def _row_to_trade(r: sqlite3.Row) -> StoredTrade:
    return StoredTrade(
        doc_id=r["doc_id"],
        ticker=r["ticker"],
        tx_type=r["tx_type"],
        tx_date=date.fromisoformat(r["tx_date"]),
        member=r["member"],
        filing_date=date.fromisoformat(r["filing_date"]),
        entry_price=r["entry_price"],
        alerted_at=r["alerted_at"],
        current_price=r["current_price"],
        checked_at=r["checked_at"],
        company_name=r["company_name"],
        sector_etf=r["sector_etf"],
        relative_strength=r["relative_strength"],
    )


def _migrate_table(conn: sqlite3.Connection, table: str, migrations: dict[str, str]) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for column, ddl in migrations.items():
        if column not in existing:
            conn.execute(ddl)


def _migrate(conn: sqlite3.Connection) -> None:
    _migrate_table(conn, "trades", _TRADE_MIGRATIONS)
    _migrate_table(conn, "recommendations", _RECOMMENDATION_MIGRATIONS)


@contextmanager
def connect(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def is_processed(conn: sqlite3.Connection, doc_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM processed_filings WHERE doc_id = ?", (doc_id,)
    ).fetchone() is not None


def mark_processed(conn: sqlite3.Connection, doc_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO processed_filings (doc_id) VALUES (?)", (doc_id,)
    )


def upsert_trade(
    conn: sqlite3.Connection,
    trade,
    entry_price: float | None,
    company_name: str | None = None,
    sector_etf: str | None = None,
) -> None:
    conn.execute(
        """INSERT INTO trades (doc_id, ticker, tx_type, tx_date, member, filing_date, entry_price, company_name, sector_etf)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (doc_id, ticker, tx_type, tx_date) DO UPDATE SET
               entry_price = COALESCE(trades.entry_price, excluded.entry_price),
               company_name = COALESCE(trades.company_name, excluded.company_name),
               sector_etf = COALESCE(trades.sector_etf, excluded.sector_etf)""",
        (
            trade.doc_id,
            trade.ticker,
            trade.tx_type,
            trade.tx_date.isoformat(),
            trade.member,
            trade.filing_date.isoformat(),
            entry_price,
            company_name,
            sector_etf,
        ),
    )


def mark_alerted(conn: sqlite3.Connection, doc_id: str, ticker: str, tx_type: str, tx_date: date) -> None:
    conn.execute(
        "UPDATE trades SET alerted_at = ? WHERE doc_id=? AND ticker=? AND tx_type=? AND tx_date=?",
        (date.today().isoformat(), doc_id, ticker, tx_type, tx_date.isoformat()),
    )


def update_current_price(
    conn: sqlite3.Connection,
    doc_id: str,
    ticker: str,
    tx_type: str,
    tx_date: date,
    price: float,
    company_name: str | None = None,
    relative_strength: float | None = None,
    sector_etf: str | None = None,
) -> None:
    """Record the latest observed price (and relative strength) for a trade still being watched.

    Never call this after mark_alerted() for the same trade - alerted rows are
    meant to freeze at their alert-time price, and unalerted_buys_since()
    already stops returning a trade once it's alerted, so this happens
    naturally as long as callers only update prices for rows they pulled from
    that query. relative_strength freezes the same way, for the same reason.

    Also opportunistically backfills company_name and sector_etf if still NULL
    - rows parsed before those columns existed otherwise stay NULL forever,
    since sync_filings() only resolves them once per filing and never revisits
    an already-processed one.
    """
    conn.execute(
        """UPDATE trades SET current_price = ?, checked_at = ?, relative_strength = ?,
               company_name = COALESCE(company_name, ?), sector_etf = COALESCE(sector_etf, ?)
           WHERE doc_id=? AND ticker=? AND tx_type=? AND tx_date=?""",
        (
            price,
            date.today().isoformat(),
            relative_strength,
            company_name,
            sector_etf,
            doc_id,
            ticker,
            tx_type,
            tx_date.isoformat(),
        ),
    )


def unalerted_buys_since(conn: sqlite3.Connection, since: date) -> list[StoredTrade]:
    """Purchases from the lookback window that haven't triggered an alert yet.

    Re-checked on every run: a buy that's expensive today may dip into the
    "still cheap" band next week.
    """
    rows = conn.execute(
        """SELECT * FROM trades
           WHERE tx_type = 'P' AND alerted_at IS NULL AND tx_date >= ?
           ORDER BY tx_date DESC""",
        (since.isoformat(),),
    ).fetchall()
    return [_row_to_trade(r) for r in rows]


def prune_older_than(conn: sqlite3.Connection, cutoff: date) -> None:
    """Drop trades older than the cutoff - the user only cares about current/last month."""
    conn.execute("DELETE FROM trades WHERE tx_date < ?", (cutoff.isoformat(),))


def all_trades(conn: sqlite3.Connection) -> list[StoredTrade]:
    rows = conn.execute("SELECT * FROM trades ORDER BY tx_date DESC").fetchall()
    return [_row_to_trade(r) for r in rows]


def latest_sales(trades: list[StoredTrade]) -> dict[tuple[str, str], date]:
    """Most recent disclosed sale date per (member, ticker)."""
    sales: dict[tuple[str, str], date] = {}
    for t in trades:
        if t.tx_type != "S":
            continue
        key = (t.member, t.ticker)
        if key not in sales or t.tx_date > sales[key]:
            sales[key] = t.tx_date
    return sales


def has_exited(trade: StoredTrade, sales: dict[tuple[str, str], date]) -> bool:
    """True if this member disclosed a sale of this ticker on or after the buy date.

    Checked per-buy, not per-(member, ticker) pair: a member who sells in July
    and buys back in August has an open position again, and that later buy
    must still be able to alert.

    # ponytail: PTR filings report dollar ranges, not share counts, so a
    # partial trim looks identical to a full exit here. Any sale suppresses -
    # conservative by design. Revisit only if this proves too aggressive.
    # ponytail: this can only see disclosed sales - the ~45-day STOCK Act lag
    # means a real-world exit may not show up here yet. No fix for that.
    """
    sale_date = sales.get((trade.member, trade.ticker))
    return sale_date is not None and sale_date >= trade.tx_date


def _row_to_recommendation(r: sqlite3.Row) -> Recommendation:
    return Recommendation(
        ticker=r["ticker"],
        alert_date=date.fromisoformat(r["alert_date"]),
        entry_price=r["entry_price"],
        sector_etf=r["sector_etf"],
        bench_entry=r["bench_entry"],
        company_name=r["company_name"],
        members=(r["members"] or "").split(",") if r["members"] else [],
        fill_count=r["fill_count"],
        member_exited_on=r["member_exited_on"],
        current_price=r["current_price"],
        current_relative_strength=r["current_relative_strength"],
        checked_at=r["checked_at"],
    )


def insert_recommendation(
    conn: sqlite3.Connection,
    ticker: str,
    alert_date: date,
    entry_price: float,
    sector_etf: str,
    bench_entry: float,
    company_name: str | None,
    members: list[str],
    fill_count: int,
) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO recommendations
           (ticker, alert_date, entry_price, sector_etf, bench_entry, company_name, members, fill_count)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (ticker, alert_date.isoformat(), entry_price, sector_etf, bench_entry,
         company_name, ",".join(members), fill_count),
    )


def all_recommendations(conn: sqlite3.Connection) -> list[Recommendation]:
    rows = conn.execute("SELECT * FROM recommendations ORDER BY alert_date DESC").fetchall()
    return [_row_to_recommendation(r) for r in rows]


def update_recommendation_price(
    conn: sqlite3.Connection, ticker: str, alert_date: date, price: float, relative_strength: float
) -> None:
    """Refresh today's price/relative-strength for a recommendation.

    Unlike checkpoints (frozen milestones at 30/60/90/120 days), this is
    overwritten every run - same live-tracking pattern as trades.current_price
    for unalerted buys, just kept going indefinitely instead of freezing at alert.
    """
    conn.execute(
        "UPDATE recommendations SET current_price=?, current_relative_strength=?, checked_at=? "
        "WHERE ticker=? AND alert_date=?",
        (price, relative_strength, date.today().isoformat(), ticker, alert_date.isoformat()),
    )


def set_member_exited(conn: sqlite3.Connection, ticker: str, alert_date: date, exited_on: date) -> None:
    conn.execute(
        "UPDATE recommendations SET member_exited_on = ? WHERE ticker=? AND alert_date=?",
        (exited_on.isoformat(), ticker, alert_date.isoformat()),
    )


def _row_to_checkpoint(r: sqlite3.Row) -> Checkpoint:
    return Checkpoint(
        ticker=r["ticker"],
        alert_date=date.fromisoformat(r["alert_date"]),
        horizon_days=r["horizon_days"],
        scored_on=r["scored_on"],
        price=r["price"],
        bench_price=r["bench_price"],
        relative_strength=r["relative_strength"],
        status=r["status"],
    )


def insert_checkpoint(
    conn: sqlite3.Connection,
    ticker: str,
    alert_date: date,
    horizon_days: int,
    scored_on: date,
    price: float | None,
    bench_price: float | None,
    relative_strength: float | None,
    status: str,
) -> None:
    """Write-once: a scored checkpoint is history and is never revised."""
    conn.execute(
        """INSERT OR IGNORE INTO checkpoints
           (ticker, alert_date, horizon_days, scored_on, price, bench_price, relative_strength, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (ticker, alert_date.isoformat(), horizon_days, scored_on.isoformat(),
         price, bench_price, relative_strength, status),
    )


def checkpoints_for(conn: sqlite3.Connection, ticker: str, alert_date: date) -> list[Checkpoint]:
    rows = conn.execute(
        "SELECT * FROM checkpoints WHERE ticker=? AND alert_date=? ORDER BY horizon_days",
        (ticker, alert_date.isoformat()),
    ).fetchall()
    return [_row_to_checkpoint(r) for r in rows]


def all_checkpoints(conn: sqlite3.Connection) -> list[Checkpoint]:
    rows = conn.execute("SELECT * FROM checkpoints").fetchall()
    return [_row_to_checkpoint(r) for r in rows]


def unscored_due_checkpoints(
    conn: sqlite3.Connection, today: date, horizons: tuple[int, ...]
) -> list[tuple[Recommendation, int, date]]:
    """(recommendation, horizon_days, checkpoint_date) for every horizon whose date has
    passed and has no checkpoint row yet."""
    due = []
    for rec in all_recommendations(conn):
        scored = {c.horizon_days for c in checkpoints_for(conn, rec.ticker, rec.alert_date)}
        for h in horizons:
            cp_date = rec.alert_date + timedelta(days=h)
            if h not in scored and cp_date <= today:
                due.append((rec, h, cp_date))
    return due
