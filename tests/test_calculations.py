import numpy as np
import pandas as pd

from northstar.calculations import (
    evaluate_filters,
    indicators_at,
    percentile_rank,
    split_adjusted_close,
    total_return_index,
)
from northstar.pipeline import is_common_share, process_paper_session, yahoo_symbol


def test_split_adjustment_removes_split_jump_but_not_dividend():
    close = pd.Series([100.0, 50.0, 51.0])
    splits = pd.Series([0.0, 2.0, 0.0])
    assert split_adjusted_close(close, splits).tolist() == [50.0, 50.0, 51.0]


def test_total_return_accounts_for_split_and_dividend():
    close = pd.Series([100.0, 50.0, 51.0])
    dividends = pd.Series([0.0, 1.0, 0.0])
    splits = pd.Series([0.0, 2.0, 0.0])
    result = total_return_index(close, dividends, splits)
    assert result.iloc[1] == 101.0
    assert result.iloc[2] == 103.02


def test_percentile_rank_uses_average_ties_and_singleton_neutral():
    ranked = percentile_rank(pd.Series([1.0, 2.0, 2.0, 4.0]))
    assert ranked.tolist() == [0.0, 50.0, 50.0, 100.0]
    assert percentile_rank(pd.Series([7.0])).iloc[0] == 50.0


def test_indicator_windows_use_only_available_history():
    n = 253
    close = pd.Series(np.arange(100.0, 100.0 + n))
    frame = pd.DataFrame({"close": close, "volume": 1_000_000, "p": close, "t": close})
    result = indicators_at(frame, n - 1)
    assert result["observations"] == 253
    assert result["m12"] == close.iloc[-1] / close.iloc[0] - 1
    assert result["sma50"] == close.tail(50).mean()
    assert result["m12_skip"] == close.iloc[-22] / close.iloc[-253] - 1
    frame.loc[232:, "t"] *= 2
    assert indicators_at(frame, n - 1)["m12_skip"] == result["m12_skip"]


def test_zero_total_return_denominator_returns_insufficient_value():
    n = 253
    close = pd.Series(np.arange(100.0, 100.0 + n))
    total_return = close.copy()
    total_return.iloc[0] = 0
    frame = pd.DataFrame({"close": close, "volume": 1_000_000, "p": close, "t": total_return})
    result = indicators_at(frame, n - 1)
    assert np.isnan(result["m12"])


def test_filter_explains_market_inactive_qualification():
    row = {
        "observations": 253,
        "close": 120.0,
        "sma50": 110.0,
        "sma200": 100.0,
        "slope200": 0.03,
        "m6": 0.20,
        "m12": 0.30,
        "adv20": 30_000_000,
        "vol63": 0.2,
    }
    result = evaluate_filters(row, benchmark_m6=0.10, market_active=False)
    assert result["qualified"] is True
    assert result["status"] == "Qualified, market filter inactive"


def test_listing_filter_keeps_common_shares_and_rejects_fund_instruments():
    assert is_common_share("Example Corporation Common Stock")
    assert is_common_share("Example Corporation Class A Ordinary Shares")
    assert not is_common_share("Example Income ETF")
    assert not is_common_share("Example Corp. Depositary Shares")
    assert not is_common_share("Example Acquisition Warrants")
    assert not is_common_share("Example Preferred Stock")
    assert not is_common_share("Example Corporation Common Stock When-Issued")


def test_yahoo_symbol_normalizes_exchange_class_separator():
    assert yahoo_symbol("brk.b") == "BRK-B"


def test_paper_portfolio_enters_prior_signal_at_next_open():
    session = pd.Timestamp("2026-09-21")
    stock = pd.DataFrame(
        {"open": [100.0], "close": [110.0], "splits": [0.0], "dividends": [0.0]},
        index=[session],
    )
    spy = pd.DataFrame(
        {"open": [500.0], "close": [505.0], "splits": [0.0], "dividends": [0.0]},
        index=[session],
    )
    state = {
        "initial_capital": 10_000.0,
        "cash": 10_000.0,
        "positions": [],
        "benchmark": {"cash": 10_000.0, "shares": 0.0},
        "pending": {
            "signal_session": "2026-09-18",
            "generated_at": "2026-09-18T22:00:00+00:00",
            "execute_on": "2026-09-21",
            "selected": [{"ticker": "TEST", "name": "Test", "sector": "Industrials", "weight": 0.1}],
        },
        "daily_snapshots": [],
    }
    process_paper_session(state, session, {"TEST": stock, "SPY": spy})
    assert state["positions"][0]["shares"] > 9.98
    assert state["daily_snapshots"][0]["portfolio_value"] > 10_098
    assert state["daily_snapshots"][0]["signal_session"] == "2026-09-18"

