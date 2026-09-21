import numpy as np
import pandas as pd

from northstar.calculations import (
    evaluate_filters,
    indicators_at,
    percentile_rank,
    split_adjusted_close,
    total_return_index,
)


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

