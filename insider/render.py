"""Render the trades table to a static docs/index.html for GitHub Pages.

This is an audit view for the bot itself: for every tracked ticker, show what
the bot currently believes about it and why it has or hasn't alerted. One
card per ticker (not per transaction) - a ticker bought on five different
days is one signal, not five.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from html import escape
from urllib.parse import quote

from insider.config import RS_HIGH, RS_LOW, THRESHOLD
from insider.store import Checkpoint, Recommendation, StoredTrade, has_exited, latest_sales
from insider.track import HORIZONS


@dataclass
class TickerCard:
    ticker: str
    company: str | None
    members: list[str]
    fills: list[StoredTrade]
    first_date: date
    last_date: date
    max_days_to_file: int
    entry_min: float
    entry_max: float
    entry_avg: float
    current: float | None
    pct_vs_avg: float | None
    relative_strength: float | None
    sector_etf: str | None
    alerted_at: str | None
    exited_members: list[str]
    status: str  # "actionable" | "watching" | "unknown" | "exited"


def _pct_and_class(entry: float, current: float) -> tuple[float, str]:
    """Nominal % vs. entry - informational only, doesn't drive the alert gate."""
    pct = (current / entry - 1) * 100
    cls = "good" if pct <= THRESHOLD * 100 else "bad"
    return pct, cls


def _rs_class(relative_strength: float) -> str:
    """Relative-strength band check - this is what actually drives actionable/watching."""
    return "good" if RS_LOW <= relative_strength <= RS_HIGH else "bad"


def _fill_status(t: StoredTrade, exited: bool = False) -> tuple[str, str]:
    """Return (status text, css class) for one individual fill row."""
    if exited:
        return "sold since", "unknown"
    if t.entry_price is None:
        return "price unavailable", "unknown"
    if t.current_price is None:
        label = f"Alerted {t.alerted_at}" if t.alerted_at else "price unavailable"
        return label, "unknown"
    pct, cls = _pct_and_class(t.entry_price, t.current_price)
    if t.alerted_at:
        return f"Alerted {t.alerted_at} @ ${t.current_price:.2f} ({pct:+.1f}%)", cls
    return f"{pct:+.1f}% (needs ≤{THRESHOLD * 100:.0f}%)", cls


def _aggregate(buys: list[StoredTrade], sales: dict[tuple[str, str], date] | None = None) -> list[TickerCard]:
    sales = sales or {}
    by_ticker: dict[str, list[StoredTrade]] = defaultdict(list)
    for t in buys:
        by_ticker[t.ticker].append(t)

    cards = []
    for ticker, fills in by_ticker.items():
        entries = [f.entry_price for f in fills if f.entry_price is not None]
        if not entries:
            continue  # nothing usable to show for this ticker at all
        entry_min, entry_max = min(entries), max(entries)
        entry_avg = sum(entries) / len(entries)

        # current price and relative_strength come from the same freshest
        # observation (run.py always writes them together), so picking one
        # fill for both keeps them a consistent pair rather than mixing a
        # live price with a stale relative-strength value or vice versa.
        priced = [f for f in fills if f.current_price is not None and f.checked_at]
        freshest = max(priced, key=lambda f: f.checked_at) if priced else None
        current = freshest.current_price if freshest else None
        relative_strength = freshest.relative_strength if freshest else None

        pct_vs_avg = None
        if current is not None:
            pct_vs_avg, _ = _pct_and_class(entry_avg, current)

        exited_members = sorted({f.member for f in fills if has_exited(f, sales)})
        all_exited = fills and all(has_exited(f, sales) for f in fills)

        if all_exited:
            status = "exited"
        elif current is None:
            status = "unknown"
        elif relative_strength is not None and _rs_class(relative_strength) == "good":
            status = "actionable"
        else:
            status = "watching"

        company = next((f.company_name for f in fills if f.company_name), None)
        sector_etf = next((f.sector_etf for f in fills if f.sector_etf), None)
        alerted_at = next((f.alerted_at for f in fills if f.alerted_at), None)

        cards.append(
            TickerCard(
                ticker=ticker,
                company=company,
                members=sorted({f.member for f in fills}),
                fills=sorted(fills, key=lambda f: f.tx_date, reverse=True),
                first_date=min(f.tx_date for f in fills),
                last_date=max(f.tx_date for f in fills),
                max_days_to_file=max((f.filing_date - f.tx_date).days for f in fills),
                entry_min=entry_min,
                entry_max=entry_max,
                entry_avg=entry_avg,
                current=current,
                pct_vs_avg=pct_vs_avg,
                relative_strength=relative_strength,
                sector_etf=sector_etf,
                alerted_at=alerted_at,
                exited_members=exited_members,
                status=status,
            )
        )
    return cards


def _bar_geometry(entry_min: float, entry_max: float, current: float) -> dict:
    """Compute left-offset percentages for the entry band and current marker.

    Axis always auto-scales to include the current price, so the marker is
    never off-scale even when it's far outside what anyone paid (e.g. a price
    that's since dropped well below every recorded entry).
    """
    lo, hi = min(entry_min, current), max(entry_max, current)
    span = hi - lo
    pad = span * 0.08 if span > 0 else max(abs(hi) * 0.05, 1.0)
    axis_lo, axis_hi = lo - pad, hi + pad
    axis_span = axis_hi - axis_lo

    def pos(x: float) -> float:
        return (x - axis_lo) / axis_span * 100

    return {
        "single_fill": entry_min == entry_max,
        "band_left": pos(entry_min),
        "band_width": max(pos(entry_max) - pos(entry_min), 0.0),
        "entry_tick": pos(entry_min),  # used when single_fill
        "marker_left": pos(current),
    }


def _bar_html(entry_min: float, entry_max: float, entry_avg: float, current: float | None) -> str:
    if current is None:
        return '<div class="bar bar-empty"><div class="track"></div></div>'
    geo = _bar_geometry(entry_min, entry_max, current)
    _, marker_cls = _pct_and_class(entry_avg, current)
    band = (
        f'<div class="tick paid" style="left:{geo["entry_tick"]:.2f}%"></div>'
        if geo["single_fill"]
        else f'<div class="band" style="left:{geo["band_left"]:.2f}%;width:{geo["band_width"]:.2f}%"></div>'
    )
    return f"""<div class="bar">
  <div class="track"></div>
  {band}
  <div class="marker {marker_cls}" style="left:{geo["marker_left"]:.2f}%" title="now ${current:.2f}"></div>
</div>
<div class="bar-labels">
  <span>paid ${entry_min:.2f}{f'–${entry_max:.2f}' if not geo["single_fill"] else ''}</span>
  <span>now ${current:.2f}</span>
</div>"""


def _card_html(card: TickerCard, sales: dict[tuple[str, str], date]) -> str:
    company_html = f'<div class="company">{escape(card.company)}</div>' if card.company else ""
    members = ", ".join(escape(m) for m in card.members)
    badge_text = {
        "actionable": "actionable", "watching": "watching", "unknown": "no data", "exited": "exited",
    }[card.status]
    pct_text = f"{card.pct_vs_avg:+.1f}% vs avg entry" if card.pct_vs_avg is not None else "no current price"
    pct_cls = ("good" if (card.pct_vs_avg or 0) <= THRESHOLD * 100 else "bad") if card.pct_vs_avg is not None else "unknown"

    etf = card.sector_etf or "SPY"
    if card.relative_strength is not None:
        rs_text = f"{card.relative_strength:+.1f}pp vs {etf}"
        rs_cls = _rs_class(card.relative_strength)
    else:
        rs_text = "no benchmark data"
        rs_cls = "unknown"

    fill_rows = []
    for f in card.fills:
        status_text, status_cls = _fill_status(f, exited=has_exited(f, sales))
        entry_text = f"${f.entry_price:.2f}" if f.entry_price is not None else "-"
        fill_rows.append(
            f'<div class="fill-row"><span>{f.tx_date.isoformat()}</span><span>{escape(f.member)}</span>'
            f'<span>{entry_text}</span><span class="{status_cls}">{escape(status_text)}</span></div>'
        )
    fills_html = "\n".join(fill_rows)

    return f"""<div class="card {card.status}">
  <div class="card-head">
    <div>
      <a class="ticker" href="https://finance.yahoo.com/quote/{quote(card.ticker)}" target="_blank" rel="noopener noreferrer">{escape(card.ticker)}</a>
      {company_html}
    </div>
    <span class="badge {card.status}">{badge_text}</span>
  </div>
  {_bar_html(card.entry_min, card.entry_max, card.entry_avg, card.current)}
  <div class="card-meta">
    <span>{len(card.fills)} buy{'s' if len(card.fills) != 1 else ''} &middot; {card.first_date.isoformat()}{f' to {card.last_date.isoformat()}' if card.first_date != card.last_date else ''}</span>
  </div>
  <div class="card-meta">
    <span class="pct {pct_cls}">{pct_text}</span>
    <span class="pct {rs_cls}">{rs_text}</span>
  </div>
  <div class="card-meta muted">
    <span>{members}</span>
    <span>up to {card.max_days_to_file}d to file</span>
  </div>
  {f'<div class="card-meta muted"><span>sold since by {escape(", ".join(card.exited_members))}</span></div>' if card.exited_members else ""}
  <details>
    <summary>{len(card.fills)} individual fill{'s' if len(card.fills) != 1 else ''}</summary>
    <div class="fills">{fills_html}</div>
  </details>
</div>"""


def _checkpoint_chip(checkpoints_by_key: dict, ticker: str, alert_date: date, horizon: int) -> str:
    cp = checkpoints_by_key.get((ticker, alert_date, horizon))
    if cp is None:
        value_html = '<span class="cp-value unknown">pending</span>'
    elif cp.status == "no_data":
        value_html = '<span class="cp-value unknown">no data</span>'
    else:
        cls = "good" if cp.status == "win" else "bad"
        value_html = f'<span class="cp-value {cls}">{cp.relative_strength:+.1f}pp</span>'
    return f'<div class="checkpoint"><span class="cp-label">{horizon}d</span>{value_html}</div>'


def _live_badge(rec: Recommendation) -> tuple[str, str]:
    """Today's status vs. sector - informational, secondary to the frozen checkpoints below it."""
    if rec.current_price is None:
        return "pending", "pending"
    rs = rec.current_relative_strength or 0.0
    if rs > 0:
        return "beating sector", "beating"
    if rs < 0:
        return "lagging", "lagging"
    return "flat", "flat"


def _track_card_html(rec: Recommendation, checkpoints_by_key: dict) -> str:
    company_html = f'<div class="company">{escape(rec.company_name)}</div>' if rec.company_name else ""
    badge_text, badge_cls = _live_badge(rec)

    if rec.current_price is not None:
        pct, pct_cls = _pct_and_class(rec.entry_price, rec.current_price)
        pct_text = f"{pct:+.1f}% vs entry"
        rs = rec.current_relative_strength or 0.0
        rs_cls = "good" if rs > 0 else "bad" if rs < 0 else "unknown"
        rs_text = f"{rs:+.1f}pp vs {rec.sector_etf}"
    else:
        pct_text, pct_cls = "no current price", "unknown"
        rs_text, rs_cls = "no data", "unknown"

    exited_html = (
        f'<div class="card-meta muted"><span>member exited {rec.member_exited_on}</span></div>'
        if rec.member_exited_on else ""
    )
    members = ", ".join(escape(m) for m in rec.members)
    fills_note = (
        f" &middot; {rec.fill_count} fills" if rec.fill_count != len(rec.members) else ""
    )
    checkpoints_html = "".join(_checkpoint_chip(checkpoints_by_key, rec.ticker, rec.alert_date, h) for h in HORIZONS)

    return f"""<div class="card track-card">
  <div class="card-head">
    <div>
      <a class="ticker" href="https://finance.yahoo.com/quote/{quote(rec.ticker)}" target="_blank" rel="noopener noreferrer">{escape(rec.ticker)}</a>
      {company_html}
    </div>
    <span class="badge {badge_cls}">{badge_text}</span>
  </div>
  {exited_html}
  {_bar_html(rec.entry_price, rec.entry_price, rec.entry_price, rec.current_price)}
  <div class="card-meta">
    <span class="pct {pct_cls}">{pct_text}</span>
    <span class="pct {rs_cls}">{rs_text}</span>
  </div>
  <div class="checkpoints">
    {checkpoints_html}
  </div>
  <div class="card-meta muted">
    <span>alerted {rec.alert_date.isoformat()}</span>
  </div>
  <details>
    <summary>{rec.fill_count} buy{'s' if rec.fill_count != 1 else ''} &middot; {len(rec.members)} member{'s' if len(rec.members) != 1 else ''}</summary>
    <div class="fills"><div class="fill-row-track">{members}{fills_note}</div></div>
  </details>
</div>"""


def _kpi_tiles_html(summary: dict) -> str:
    tiles = []
    for h in HORIZONS:
        hs = summary["per_horizon"].get(h, {"scored": 0})
        if not hs.get("scored"):
            value_html = '<div class="kpi-value unknown">pending</div>'
            sub = "not scored yet"
        else:
            cls = "good" if hs["hit_rate"] >= 50 else "bad"
            value_html = f'<div class="kpi-value {cls}">{hs["hit_rate"]:.0f}%</div>'
            sub = f'{hs["wins"]}/{hs["scored"]} &middot; median {hs["median_rs"]:+.1f}pp'
        tiles.append(f"""<div class="kpi-tile">
  <div class="kpi-label">{h}d</div>
  {value_html}
  <div class="kpi-sub">{sub}</div>
</div>""")
    return '<div class="kpi-row">' + "\n".join(tiles) + "</div>"


def _track_panel_html(track_data: dict | None) -> str:
    """Recommendations = trades the bot actually alerted on, scored at 30/60/90/120
    days against their sector ETF. Empty until the first non-dry-run alert fires."""
    track_data = track_data or {}
    recs: list[Recommendation] = track_data.get("recommendations") or []
    checkpoints: list[Checkpoint] = track_data.get("checkpoints") or []
    summary = track_data.get("summary") or {"n_recs": 0, "per_horizon": {}, "best": None, "worst": None}

    if not recs:
        return (
            '<p class="empty">No recommendations recorded yet - this fills in once a real '
            "(non-dry-run) alert fires.</p>"
        )

    checkpoints_by_key = {(c.ticker, c.alert_date, c.horizon_days): c for c in checkpoints}
    maturity: dict[tuple[str, date], set] = defaultdict(set)
    for c in checkpoints:
        maturity[(c.ticker, c.alert_date)].add(c.horizon_days)
    fully_scored = sum(1 for r in recs if len(maturity[(r.ticker, r.alert_date)]) == len(HORIZONS))

    best, worst = summary.get("best"), summary.get("worst")
    best_worst = ""
    if best and worst:
        best_cls = "good" if best.status == "win" else "bad"
        worst_cls = "good" if worst.status == "win" else "bad"
        best_worst = (
            f'<p class="track-summary-line">Best: <span class="{best_cls}">{escape(best.ticker)} '
            f'{best.relative_strength:+.1f}pp</span> ({best.horizon_days}d) &middot; '
            f'Worst: <span class="{worst_cls}">{escape(worst.ticker)} {worst.relative_strength:+.1f}pp</span> '
            f"({worst.horizon_days}d)</p>"
        )

    cards_html = "\n".join(_track_card_html(r, checkpoints_by_key) for r in recs)

    return f"""{_kpi_tiles_html(summary)}
<p class="track-summary-line muted">{len(recs)} recommendation{'s' if len(recs) != 1 else ''} tracked &middot; {fully_scored} fully scored (all {len(HORIZONS)} checkpoints)</p>
{best_worst}
<div class="cards">
{cards_html}
</div>"""


_PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Congressional Buy Signals</title>
<style>
:root {{
  --bg: #0b0e14;
  --surface: #121724;
  --border: #232a3c;
  --border-hover: #303a54;
  --text: #e6e6e6;
  --text-muted: #8b93a3;
  --text-faint: #5b6373;
  --good: #4ade80;
  --good-bg: #14261c;
  --bad: #f87171;
  --bad-bg: #2a1a1a;
  --unknown: #6b7280;
  --unknown-bg: #171b26;
}}
* {{ box-sizing: border-box; }}
body {{
  font: 0.875rem/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
  margin: 0; padding: 1.5rem 1rem 3rem;
  background: var(--bg); color: var(--text);
}}
.wrap {{ max-width: 960px; margin: 0 auto; }}
h1 {{ font-size: 1.375rem; font-weight: 600; margin: 0 0 0.35rem; }}
.subhead {{ color: var(--text-muted); font-size: 0.8125rem; margin: 0 0 0.15rem; }}
.health {{ color: var(--text-faint); font-size: 0.75rem; margin: 0 0 1.5rem; }}
.disclaimer {{ color: var(--text-faint); font-size: 0.75rem; margin: 2rem 0 0; border-top: 1px solid var(--border); padding-top: 1rem; }}

.tab-input {{ position: absolute; opacity: 0; pointer-events: none; }}
.tabs {{ display: flex; gap: 0.25rem; margin: 0.6rem 0 1.25rem; border-bottom: 1px solid var(--border); }}
.tab-label {{
  padding: 0.5rem 0.9rem; font-size: 0.8125rem; font-weight: 600; color: var(--text-faint);
  cursor: pointer; border-bottom: 2px solid transparent; margin-bottom: -1px;
  transition: color 150ms ease-out, border-color 150ms ease-out;
}}
.tab-label:hover {{ color: var(--text-muted); }}
#tab-signals:checked ~ .tabs label[for="tab-signals"],
#tab-track:checked ~ .tabs label[for="tab-track"] {{ color: var(--text); border-bottom-color: var(--text); }}
.panel {{ display: none; }}
#tab-signals:checked ~ .panels #panel-signals,
#tab-track:checked ~ .panels #panel-track {{ display: block; }}
@media (prefers-reduced-motion: reduce) {{ .tab-label {{ transition: none; }} }}

.section-head {{ display: flex; align-items: baseline; gap: 0.5rem; margin: 1.75rem 0 0.75rem; }}
.section-head h2 {{ font-size: 1rem; font-weight: 600; margin: 0; }}
.section-head .count {{ color: var(--text-faint); font-size: 0.8125rem; }}
section.watching {{ opacity: 0.82; }}
section.exited {{ opacity: 0.6; }}

.cards {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 0.75rem; }}
.card {{
  background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
  padding: 0.9rem; display: flex; flex-direction: column; gap: 0.55rem;
  transition: border-color 150ms ease-out;
}}
.card:hover {{ border-color: var(--border-hover); }}
.watching .card {{ padding: 0.7rem 0.9rem; }}

.card-head {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 0.5rem; }}
.ticker {{
  font-size: 1.0625rem; font-weight: 700; letter-spacing: 0.01em;
  color: var(--text); text-decoration: none; border-bottom: 1px solid transparent;
  transition: border-color 150ms ease-out;
}}
.ticker:hover {{ border-color: var(--text-faint); }}
@media (prefers-reduced-motion: reduce) {{ .ticker {{ transition: none; }} }}
.company {{ color: var(--text-muted); font-size: 0.75rem; margin-top: 0.1rem; }}

.badge {{
  font-size: 0.6875rem; font-weight: 600; letter-spacing: 0.02em;
  padding: 0.2rem 0.55rem; border-radius: 999px; white-space: nowrap;
}}
.badge.actionable {{ background: var(--good-bg); color: var(--good); }}
.badge.watching {{ background: var(--unknown-bg); color: var(--text-muted); }}
.badge.unknown {{ background: var(--unknown-bg); color: var(--text-faint); }}
.badge.exited {{ background: var(--unknown-bg); color: var(--text-faint); }}

.bar {{ position: relative; height: 6px; background: var(--border); border-radius: 3px; margin: 0.2rem 0 0; }}
.bar-empty {{ opacity: 0.5; }}
.bar .band {{ position: absolute; top: 0; bottom: 0; background: #3a4257; border-radius: 3px; min-width: 3px; }}
.bar .tick {{ position: absolute; top: -3px; width: 2px; height: 12px; border-radius: 1px; background: var(--text-faint); }}
.bar .marker {{ position: absolute; top: -3px; width: 2px; height: 12px; border-radius: 1px; transform: translateX(-1px); }}
.bar .marker.good {{ background: var(--good); }}
.bar .marker.bad {{ background: var(--bad); }}
.bar .marker.unknown {{ background: var(--unknown); }}
.bar-labels {{ display: flex; justify-content: space-between; font-size: 0.6875rem; color: var(--text-faint); }}

.card-meta {{ display: flex; justify-content: space-between; gap: 0.5rem; font-size: 0.8125rem; }}
.card-meta.muted {{ color: var(--text-faint); font-size: 0.75rem; }}
.card-meta .pct.good {{ color: var(--good); }}
.card-meta .pct.bad {{ color: var(--bad); }}
.card-meta .pct.unknown {{ color: var(--text-faint); }}

details {{ font-size: 0.75rem; }}
summary {{ color: var(--text-faint); cursor: pointer; }}
summary:hover {{ color: var(--text-muted); }}
.fills {{ margin-top: 0.5rem; display: flex; flex-direction: column; gap: 0.3rem; }}
.fill-row {{ display: grid; grid-template-columns: 5.5rem 1fr 4rem 1fr; gap: 0.5rem; color: var(--text-muted); }}
.fill-row .good {{ color: var(--good); }}
.fill-row .bad {{ color: var(--bad); }}
.fill-row .unknown {{ color: var(--text-faint); }}

.empty {{ color: var(--text-faint); padding: 2rem 0; text-align: center; }}
.muted {{ color: var(--text-faint); }}

.track-summary-line {{ margin: 0 0 0.4rem; font-size: 0.8125rem; }}
.track-summary-line.muted {{ font-size: 0.75rem; }}
.track-summary-line .good {{ color: var(--good); }}
.track-summary-line .bad {{ color: var(--bad); }}

.kpi-row {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 0.6rem; margin: 0.75rem 0 1rem; }}
.kpi-tile {{ background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 0.75rem 0.9rem; }}
.kpi-label {{ color: var(--text-faint); font-size: 0.6875rem; font-weight: 600; letter-spacing: 0.03em; text-transform: uppercase; }}
.kpi-value {{ font-size: 1.375rem; font-weight: 700; margin: 0.15rem 0; }}
.kpi-value.good {{ color: var(--good); }}
.kpi-value.bad {{ color: var(--bad); }}
.kpi-value.unknown {{ color: var(--text-faint); }}
.kpi-sub {{ color: var(--text-faint); font-size: 0.6875rem; }}

.badge.beating {{ background: var(--good-bg); color: var(--good); }}
.badge.flat {{ background: var(--unknown-bg); color: var(--text-muted); }}
.badge.lagging {{ background: var(--bad-bg); color: var(--bad); }}
.badge.pending {{ background: var(--unknown-bg); color: var(--text-faint); }}

.checkpoints {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 0.4rem; margin-top: 0.2rem; }}
.checkpoint {{
  display: flex; flex-direction: column; align-items: center; gap: 0.15rem;
  background: var(--bg); border: 1px solid var(--border); border-radius: 8px; padding: 0.35rem 0.2rem;
}}
.cp-label {{ color: var(--text-faint); font-size: 0.625rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.02em; }}
.cp-value {{ font-size: 0.75rem; font-weight: 600; }}
.cp-value.good {{ color: var(--good); }}
.cp-value.bad {{ color: var(--bad); }}
.cp-value.unknown {{ color: var(--text-faint); }}

.fill-row-track {{ color: var(--text-muted); font-size: 0.75rem; }}

@media (prefers-reduced-motion: reduce) {{
  .card {{ transition: none; }}
}}
</style></head>
<body>
<div class="wrap">
<h1>Congressional Buy Signals</h1>
<p class="subhead">Generated {generated} UTC &middot; {n_tickers} tickers tracked ({n_fills} buys)</p>

<input type="radio" name="tab" id="tab-signals" class="tab-input" checked>
<input type="radio" name="tab" id="tab-track" class="tab-input">
<div class="tabs">
  <label for="tab-signals" class="tab-label">Signals</label>
  <label for="tab-track" class="tab-label">Track record</label>
</div>

<div class="panels">
<section id="panel-signals" class="panel">
<p class="health">{health}</p>

{sections}

<p class="disclaimer">Not financial advice. Disclosure lag is ~52 days on average; entry price is the market close on the transaction date, averaged across fills. "Actionable" means relative strength vs. a sector-ETF benchmark (stock return minus benchmark return since the trade date) falls in a neutral band - not that the stock is nominally cheap. "Exited" means the member disclosed a sale of that ticker on or after the buy date - alerts are suppressed for these, but the same disclosure lag applies, so a sale may exist and not show up here yet. Sector mapping is an approximation (SEC EDGAR SIC code, hand-mapped to a sector ETF); unmapped tickers fall back to the S&amp;P 500. Data: House Clerk STOCK Act filings + Yahoo Finance + SEC EDGAR.</p>
</section>

<section id="panel-track" class="panel">
{track_panel}

<p class="disclaimer">Track record covers only trades the bot actually alerted on (not every disclosed buy), priced from the alert date - what copying it would really have cost you, not the member's own entry. "Now" is refreshed every run and is informational only; the 30/60/90/120-day checkpoints are the frozen win/loss verdicts, scored by holding to that checkpoint with no early exit against the same sector-ETF benchmark the alert gate uses, and never change once set. "Member exited" is an annotation, not a rule - it does not change the verdict. The first cohort shares one alert date, so its hit rate is not yet meaningful; it becomes informative once several independent alert dates have accumulated.</p>
</section>
</div>

</div>
</body></html>
"""


def render(trades: list[StoredTrade], out_path: str, stats: dict | None = None, track_data: dict | None = None) -> None:
    buys = [t for t in trades if t.tx_type == "P"]
    sales = latest_sales(trades)
    cards = _aggregate(buys, sales)

    actionable = sorted(
        (c for c in cards if c.status == "actionable"), key=lambda c: c.relative_strength
    )
    watching = sorted(
        (c for c in cards if c.status == "watching" or c.status == "unknown"),
        key=lambda c: c.relative_strength if c.relative_strength is not None else float("inf"),
    )
    exited = sorted(
        (c for c in cards if c.status == "exited"),
        key=lambda c: c.relative_strength if c.relative_strength is not None else float("inf"),
    )

    sections = []
    if actionable:
        sections.append(
            f'<section class="actionable"><div class="section-head"><h2>Actionable now</h2>'
            f'<span class="count">{len(actionable)}</span></div><div class="cards">'
            + "\n".join(_card_html(c, sales) for c in actionable) + "</div></section>"
        )
    if watching:
        sections.append(
            f'<section class="watching"><div class="section-head"><h2>Watching</h2>'
            f'<span class="count">{len(watching)}</span></div><div class="cards">'
            + "\n".join(_card_html(c, sales) for c in watching) + "</div></section>"
        )
    if exited:
        sections.append(
            f'<section class="exited"><div class="section-head"><h2>Exited</h2>'
            f'<span class="count">{len(exited)}</span></div><div class="cards">'
            + "\n".join(_card_html(c, sales) for c in exited) + "</div></section>"
        )
    if not sections:
        sections.append('<p class="empty">No purchases tracked yet.</p>')

    stats = stats or {}
    health = (
        f"Scanned {stats.get('filings_scanned', 0)} filings &middot; "
        f"{stats.get('price_lookup_failures', 0)} price lookups failed &middot; "
        f"{stats.get('filings_skipped_old', 0)} filings skipped (too old) &middot; "
        f"{stats.get('pdf_fetch_errors', 0)} PDF fetch errors &middot; "
        f"{stats.get('sector_lookup_fallback', 0)} sector lookups fell back to SPY"
    )

    html = _PAGE.format(
        generated=datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
        n_tickers=len(cards),
        n_fills=len(buys),
        health=health,
        sections="\n".join(sections),
        track_panel=_track_panel_html(track_data),
    )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
