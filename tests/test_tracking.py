from copy import deepcopy

import pandas as pd
import pytest

from northstar.pipeline import observed_signal, process_paper_session, advance_observed
from northstar.experiments import select_variant


def fixture():
    dates = pd.to_datetime(["2026-09-21", "2026-09-22", "2026-09-23"])
    stock = pd.DataFrame({"open": [100., 55., 60.], "close": [110., 55., 66.],
                          "splits": [0., 2., 0.], "dividends": [0., 1., 0.]}, index=dates)
    spy = pd.DataFrame({"open": [100., 100., 100.], "close": [100., 100., 100.],
                        "splits": [0., 0., 0.], "dividends": [0., 0., 0.]}, index=dates)
    target = {"ticker": "A", "name": "A", "sector": "Test", "weight": .1}
    state = {"initial_capital": 10000., "cash": 10000., "positions": [],
             "benchmark": {"cash": 10000., "shares": 0.}, "daily_snapshots": [],
             "last_processed_session": "2026-09-18",
             "pending": observed_signal(pd.Timestamp("2026-09-18"), [target], {"active": True}, "2026-09-18T22:00Z")}
    return state, {"A": stock, "SPY": spy}, dates, target


def test_cashflow_ledger_reconciles_splits_dividends_sale_and_reentry():
    state, frames, dates, target = fixture()
    process_paper_session(state, dates[0], frames)
    state["pending"] = observed_signal(dates[0], [], {"active": False}, "2026-09-21T22:00Z")
    process_paper_session(state, dates[1], frames)
    assert not state["positions"]
    record = state["daily_snapshots"][-1]["stock_records"][0]
    assert record["profit"] == pytest.approx(117.9)
    assert record["growth"] == pytest.approx(1.12)
    assert state["cash"] - 10000 == pytest.approx(record["profit"])
    state["pending"] = observed_signal(dates[1], [target], {"active": True}, "2026-09-22T22:00Z")
    process_paper_session(state, dates[2], frames)
    record = state["daily_snapshots"][-1]["stock_records"][0]
    assert record["growth"] == pytest.approx(1.344)
    assert record["profit"] == pytest.approx(state["daily_snapshots"][-1]["portfolio_value"] - 10000)


def test_signal_created_after_open_cannot_buy_that_open():
    state, frames, dates, target = fixture()
    state["pending"] = observed_signal(pd.Timestamp("2026-09-18"), [target], {"active": True}, "2026-09-21T15:00Z")
    assert state["pending"]["execute_on"] == "2026-09-22"
    process_paper_session(state, dates[0], frames)
    assert state["cash"] == 10000
    assert state["benchmark"]["shares"] == 0
    state["pending"]["execute_on"] = "2026-09-21"
    with pytest.raises(ValueError, match="available"):
        process_paper_session(state, dates[0], frames)


def test_reruns_freeze_signal_and_do_not_duplicate_snapshots():
    state, frames, dates, target = fixture()
    advance_observed(state, frames, dates[1], [], {"active": False}, "2026-09-22T22:00Z")
    original = deepcopy(state)
    advance_observed(state, frames, dates[1], [target], {"active": True}, "2026-09-22T23:00Z")
    assert state == original
    assert state["daily_snapshots"][1]["executed_signal"] is False


def test_missing_close_uses_entry_price_instead_of_zero():
    state, frames, dates, _ = fixture()
    frames["A"].loc[dates[0], "close"] = float("nan")
    process_paper_session(state, dates[0], frames)
    assert state["daily_snapshots"][0]["portfolio_value"] == pytest.approx(9999.)


def test_stale_feed_does_not_backfill_before_inception():
    state, frames, dates, target = fixture()
    state["started_at"] = "2026-09-22"
    state["pending"] = observed_signal(pd.Timestamp("2026-09-18"), [target], {"active": True}, "2026-09-22T08:00Z")
    advance_observed(state, frames, dates[1], [], {"active": False}, "2026-09-22T22:00Z")
    assert [r["date"] for r in state["daily_snapshots"]] == ["2026-09-22"]


def test_buffer_retains_rank_15_but_rejects_unqualified_and_rank_21():
    rows = [{"ticker": str(i), "permanent_id": str(i), "name": str(i), "sector": "Unclassified",
             "qualified": True, "score": 100-i, "adv20": 1e8, "close": 100., "m12_skip": i/100} for i in range(1, 26)]
    result = select_variant(rows, True, "buffer", {"15", "21"})
    assert "15" in {r["ticker"] for r in result}
    assert "21" not in {r["ticker"] for r in result}
    assert len(result) == 10
    rows[14]["qualified"] = False
    assert "15" not in {r["ticker"] for r in select_variant(rows, True, "buffer", {"15"})}
    assert select_variant(rows, False, "buffer", {"15"}) == []
    assert select_variant(rows, True, "skip_month", set())[0]["ticker"] == "25"


def test_experiments_start_together_and_freeze_same_session(tmp_path, monkeypatch):
    import northstar.pipeline as pipeline
    from datetime import datetime as real_datetime, timezone

    class Clock:
        @classmethod
        def now(cls, tz=None):
            return real_datetime(2026, 9, 18, 22, tzinfo=timezone.utc)

    monkeypatch.setattr(pipeline, "datetime", Clock)
    monkeypatch.setattr(pipeline, "EXPERIMENTS", tmp_path / "experiments.json")
    _, frames, dates, _ = fixture()
    row = {"ticker": "A", "name": "A", "sector": "Test", "permanent_id": "A",
           "qualified": True, "score": 90, "adv20": 1e8, "close": 100, "m12_skip": .2}
    market = {"active": True}
    initial = pipeline.update_experiments(frames, pd.Timestamp("2026-09-18"), [row], market)
    assert len(initial) == 4
    assert {v["sessions"] for v in initial} == {0}
    before = pipeline.EXPERIMENTS.read_text()
    pipeline.update_experiments(frames, pd.Timestamp("2026-09-18"), [], market)
    assert pipeline.EXPERIMENTS.read_text() == before
    result = pipeline.update_experiments(frames, dates[0], [row], market)
    assert {v["sessions"] for v in result} == {1}
    assert len({v["return"] for v in result}) == 1
