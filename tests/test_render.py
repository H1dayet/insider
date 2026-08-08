"""Runnable checks for the ticker-card aggregation and bar geometry - the two
non-trivial pieces of the dashboard redesign. A wrong grouping or an
off-scale marker would silently misrepresent every card on the page."""
from datetime import date

from insider.render import _aggregate, _bar_geometry, _pct_and_class, _rs_class
from insider.store import StoredTrade


def _trade(
    ticker,
    tx_date,
    entry,
    current=None,
    checked_at=None,
    member="Someone",
    alerted_at=None,
    doc_id="1",
    relative_strength=None,
    sector_etf=None,
):
    return StoredTrade(
        doc_id=doc_id,
        ticker=ticker,
        tx_type="P",
        tx_date=date.fromisoformat(tx_date),
        member=member,
        filing_date=date.fromisoformat(tx_date),
        entry_price=entry,
        alerted_at=alerted_at,
        current_price=current,
        checked_at=checked_at,
        company_name=None,
        sector_etf=sector_etf,
        relative_strength=relative_strength,
    )


def test_grouping_collapses_repeated_ticker_into_one_card():
    lth_fills = [_trade("LTH", f"2026-07-{d:02d}", 44.0 + d * 0.1, doc_id=str(d)) for d in range(17, 26)]  # 9 fills
    mu_fill = [_trade("MU", "2026-07-10", 991.64, doc_id="99")]

    cards = _aggregate(lth_fills + mu_fill)
    by_ticker = {c.ticker: c for c in cards}

    assert len(cards) == 2
    assert len(by_ticker["LTH"].fills) == 9
    assert len(by_ticker["MU"].fills) == 1


def test_entry_stats_are_correct():
    fills = [
        _trade("LTH", "2026-07-17", 41.93, current=43.81, checked_at="2026-08-08"),
        _trade("LTH", "2026-07-24", 45.70, current=43.81, checked_at="2026-08-07"),
    ]
    card = _aggregate(fills)[0]
    assert card.entry_min == 41.93
    assert card.entry_max == 45.70
    assert round(card.entry_avg, 2) == round((41.93 + 45.70) / 2, 2)


def test_current_price_and_relative_strength_come_from_freshest_checked_at():
    fills = [
        _trade("LTH", "2026-07-17", 41.93, current=99.0, checked_at="2026-08-01", relative_strength=50.0),  # stale
        _trade("LTH", "2026-07-24", 45.70, current=43.81, checked_at="2026-08-08", relative_strength=-1.5),  # freshest
    ]
    card = _aggregate(fills)[0]
    assert card.current == 43.81
    assert card.relative_strength == -1.5  # paired with the same fresh observation, not mixed


def test_status_classification_is_driven_by_relative_strength_not_nominal_pct():
    # nominal price is UP for both, but relative strength (vs its sector) differs -
    # this is exactly the scenario the algorithm redesign exists for.
    in_band = _aggregate(
        [_trade("A", "2026-07-01", 100.0, current=101.0, checked_at="2026-08-08", relative_strength=-1.0)]
    )[0]
    out_of_band = _aggregate(
        [_trade("B", "2026-07-01", 100.0, current=101.0, checked_at="2026-08-08", relative_strength=15.0)]
    )[0]
    no_price = _aggregate([_trade("C", "2026-07-01", 100.0, current=None)])[0]

    assert in_band.status == "actionable"
    assert out_of_band.status == "watching"
    assert no_price.status == "unknown"
    assert no_price.pct_vs_avg is None
    assert no_price.relative_strength is None


def test_rs_class_boundary():
    assert _rs_class(-10.0) == "good"  # RS_LOW inclusive
    assert _rs_class(2.0) == "good"    # RS_HIGH inclusive
    assert _rs_class(2.01) == "bad"
    assert _rs_class(-10.01) == "bad"


def test_bar_marker_within_bounds_when_current_inside_band():
    geo = _bar_geometry(entry_min=41.93, entry_max=45.70, current=43.81)
    assert 0 <= geo["marker_left"] <= 100
    assert 0 <= geo["band_left"] <= 100
    assert not geo["single_fill"]


def test_bar_marker_within_bounds_when_current_far_below_band():
    # real case: MU bought around $991.64, now trading at $877.57 - well
    # outside the paid range, so the axis must expand to include it.
    geo = _bar_geometry(entry_min=877.57 * 1.1, entry_max=991.64, current=877.57)
    assert 0 <= geo["marker_left"] <= 100
    assert geo["marker_left"] < geo["band_left"]  # marker sits left of the band, as expected


def test_bar_single_fill_renders_tick_not_band():
    geo = _bar_geometry(entry_min=100.0, entry_max=100.0, current=95.0)
    assert geo["single_fill"] is True
    assert 0 <= geo["marker_left"] <= 100
    assert 0 <= geo["entry_tick"] <= 100


def test_bar_degenerate_axis_when_price_never_moved():
    # entry == current, single fill - hi == lo, must not divide by zero
    geo = _bar_geometry(entry_min=50.0, entry_max=50.0, current=50.0)
    assert 0 <= geo["marker_left"] <= 100
    assert 0 <= geo["entry_tick"] <= 100


def test_pct_and_class_boundary():
    pct, cls = _pct_and_class(entry=100.0, current=100.0)  # exactly at threshold (0%)
    assert cls == "good"
    pct, cls = _pct_and_class(entry=100.0, current=100.01)
    assert cls == "bad"


if __name__ == "__main__":
    test_grouping_collapses_repeated_ticker_into_one_card()
    test_entry_stats_are_correct()
    test_current_price_and_relative_strength_come_from_freshest_checked_at()
    test_status_classification_is_driven_by_relative_strength_not_nominal_pct()
    test_rs_class_boundary()
    test_bar_marker_within_bounds_when_current_inside_band()
    test_bar_marker_within_bounds_when_current_far_below_band()
    test_bar_single_fill_renders_tick_not_band()
    test_bar_degenerate_axis_when_price_never_moved()
    test_pct_and_class_boundary()
    print("ok")
