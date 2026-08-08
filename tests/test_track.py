"""Runnable checks for the track-record feature: recommendation dedupe (the
9x-LTH-same-day case seen live), checkpoint due-dating, and win/loss/no_data
scoring. score_due_checkpoints() hits the network via prices.lookup(), so
these monkeypatch it with a canned quote table instead."""
from datetime import date, timedelta

from insider import store, track
from insider.prices import PriceUnavailable, Quote


def _fired(ticker, member, current=100.0, sector_etf="XLK", bench_current=50.0, company_name="Co"):
    return {
        "ticker": ticker, "member": member, "current": current,
        "sector_etf": sector_etf, "bench_current": bench_current, "company_name": company_name,
    }


def test_same_day_same_member_fills_collapse_to_one_recommendation():
    with store.connect(":memory:") as conn:
        alert_date = date(2026, 7, 31)
        fired = [_fired("LTH", "Delaney") for _ in range(9)]
        track.record_recommendations(conn, fired, alert_date)

        recs = store.all_recommendations(conn)
        assert len(recs) == 1
        assert recs[0].fill_count == 9
        assert recs[0].members == ["Delaney"]


def test_two_members_same_ticker_same_day_collapse_with_both_names():
    with store.connect(":memory:") as conn:
        alert_date = date(2026, 7, 31)
        fired = [_fired("KO", "Alice"), _fired("KO", "Bob")]
        track.record_recommendations(conn, fired, alert_date)

        recs = store.all_recommendations(conn)
        assert len(recs) == 1
        assert recs[0].members == ["Alice", "Bob"]
        assert recs[0].fill_count == 2


def test_later_realert_of_same_ticker_is_a_second_recommendation():
    with store.connect(":memory:") as conn:
        track.record_recommendations(conn, [_fired("KO", "Alice")], date(2026, 7, 1))
        track.record_recommendations(conn, [_fired("KO", "Alice")], date(2026, 8, 1))

        recs = store.all_recommendations(conn)
        assert len(recs) == 2
        assert {r.alert_date for r in recs} == {date(2026, 7, 1), date(2026, 8, 1)}


def test_future_checkpoint_is_not_due():
    with store.connect(":memory:") as conn:
        today = date(2026, 8, 8)
        track.record_recommendations(conn, [_fired("KO", "Alice")], today)  # alerted today

        due = store.unscored_due_checkpoints(conn, today, track.HORIZONS)
        assert due == []  # 30d checkpoint is 22 days in the future


def test_past_checkpoints_are_due_and_get_scored():
    with store.connect(":memory:") as conn:
        today = date(2026, 8, 8)
        old_alert = today - timedelta(days=200)  # all 4 horizons have passed
        track.record_recommendations(conn, [_fired("KO", "Alice", current=100.0, bench_current=50.0)], old_alert)

        due = store.unscored_due_checkpoints(conn, today, track.HORIZONS)
        assert {h for _, h, _ in due} == set(track.HORIZONS)


def test_win_loss_boundary_is_strict_positive_relative_strength():
    # stock and bench both flat -> rs == 0 exactly -> loss, not win (`rs > 0`, not >=)
    quotes = {("KO", "cp"): Quote(100.0, 100.0, None), ("XLK", "cp"): Quote(50.0, 50.0, None)}

    def fake_lookup(ticker, d):
        q = quotes.get((ticker, "cp"))
        if q is None:
            raise PriceUnavailable(ticker)
        return q

    with store.connect(":memory:") as conn:
        today = date(2026, 8, 8)
        old_alert = today - timedelta(days=200)
        track.record_recommendations(conn, [_fired("KO", "Alice", current=100.0, bench_current=50.0)], old_alert)

        real_lookup = track.prices.lookup
        real_sleep = track.time.sleep
        track.prices.lookup = fake_lookup
        track.time.sleep = lambda _: None
        try:
            track.score_due_checkpoints(conn, today, {})
        finally:
            track.prices.lookup = real_lookup
            track.time.sleep = real_sleep

        checkpoints = store.all_checkpoints(conn)
        assert len(checkpoints) == 4
        assert all(c.status == "loss" for c in checkpoints)  # rs == 0 everywhere
        assert all(c.relative_strength == 0.0 for c in checkpoints)


def test_update_live_prices_refreshes_every_run_unlike_frozen_checkpoints():
    # stock up 20% since alert, bench flat -> rs should be +20pp
    quotes = {"KO": Quote(100.0, 120.0, None), "XLP": Quote(50.0, 50.0, None)}

    def fake_lookup(ticker, d):
        q = quotes.get(ticker)
        if q is None:
            raise PriceUnavailable(ticker)
        return q

    with store.connect(":memory:") as conn:
        alert_date = date(2026, 7, 31)  # today - nowhere near a checkpoint
        track.record_recommendations(
            conn, [_fired("KO", "Alice", current=100.0, sector_etf="XLP", bench_current=50.0)], alert_date
        )

        real_lookup = track.prices.lookup
        real_sleep = track.time.sleep
        track.prices.lookup = fake_lookup
        track.time.sleep = lambda _: None
        try:
            track.update_live_prices(conn, {})
        finally:
            track.prices.lookup = real_lookup
            track.time.sleep = real_sleep

        rec = store.all_recommendations(conn)[0]
        assert rec.current_price == 120.0
        assert round(rec.current_relative_strength, 1) == 20.0
        # no checkpoints written - this is the live number, not a milestone verdict
        assert store.all_checkpoints(conn) == []


def test_no_data_is_excluded_from_hit_rate_denominator():
    def failing_lookup(ticker, d):
        raise PriceUnavailable(ticker)

    with store.connect(":memory:") as conn:
        today = date(2026, 8, 8)
        old_alert = today - timedelta(days=200)
        track.record_recommendations(conn, [_fired("DELISTED", "Alice")], old_alert)

        real_lookup = track.prices.lookup
        real_sleep = track.time.sleep
        track.prices.lookup = failing_lookup
        track.time.sleep = lambda _: None
        try:
            track.score_due_checkpoints(conn, today, {})
        finally:
            track.prices.lookup = real_lookup
            track.time.sleep = real_sleep

        checkpoints = store.all_checkpoints(conn)
        assert all(c.status == "no_data" for c in checkpoints)

        summary = track.summary(conn)
        for h in track.HORIZONS:
            assert summary["per_horizon"][h]["scored"] == 0  # no_data isn't "scored"
            assert summary["per_horizon"][h]["hit_rate"] is None


if __name__ == "__main__":
    test_same_day_same_member_fills_collapse_to_one_recommendation()
    test_two_members_same_ticker_same_day_collapse_with_both_names()
    test_later_realert_of_same_ticker_is_a_second_recommendation()
    test_future_checkpoint_is_not_due()
    test_past_checkpoints_are_due_and_get_scored()
    test_win_loss_boundary_is_strict_positive_relative_strength()
    test_update_live_prices_refreshes_every_run_unlike_frozen_checkpoints()
    test_no_data_is_excluded_from_hit_rate_denominator()
    print("ok")
