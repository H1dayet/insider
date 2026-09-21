from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


TRADING_DAYS = 252


def split_adjusted_close(raw_close: pd.Series, splits: pd.Series) -> pd.Series:
    """Return a split-adjusted, dividend-excluded price series.

    A 4-for-1 split reported on session t multiplies the current share count by
    four. Earlier raw closes are therefore divided by four so the series remains
    continuous while dividends remain excluded.
    """
    factors = splits.replace(0, 1).astype(float)
    future_factor = factors.iloc[::-1].cumprod().iloc[::-1] / factors
    return raw_close.astype(float) / future_factor


def total_return_index(
    raw_close: pd.Series, dividends: pd.Series, splits: pd.Series
) -> pd.Series:
    """Construct a total-return index from raw closes and cash distributions."""
    closes = raw_close.astype(float)
    divs = dividends.fillna(0).astype(float)
    split_factor = splits.replace(0, 1).astype(float)
    gross = (closes * split_factor + divs) / closes.shift(1)
    gross = gross.replace([np.inf, -np.inf], np.nan)
    if len(gross):
        gross.iloc[0] = 1.0
    return gross.fillna(1.0).cumprod() * 100.0


def percentile_rank(series: pd.Series) -> pd.Series:
    count = int(series.notna().sum())
    if count == 0:
        return pd.Series(np.nan, index=series.index)
    if count == 1:
        result = pd.Series(np.nan, index=series.index)
        result.loc[series.notna()] = 50.0
        return result
    return 100.0 * (series.rank(method="average", ascending=True) - 1.0) / (count - 1.0)


def indicators_at(frame: pd.DataFrame, position: int) -> dict[str, float | int | None]:
    """Calculate the documented indicators using data through position only."""
    view = frame.iloc[: position + 1]
    p = view["p"]
    t = view["t"]
    raw = view["close"]
    volume = view["volume"]

    def ratio_back(series: pd.Series, sessions: int) -> float:
        denominator = series.iloc[-sessions - 1] if len(series) > sessions else math.nan
        numerator = series.iloc[-1] if len(series) else math.nan
        if (
            len(series) <= sessions
            or not np.isfinite(denominator)
            or not np.isfinite(numerator)
            or denominator <= 0
            or numerator <= 0
        ):
            return math.nan
        return float(numerator / denominator - 1.0)

    sma50 = float(p.tail(50).mean()) if len(p) >= 50 else math.nan
    sma200 = float(p.tail(200).mean()) if len(p) >= 200 else math.nan
    prior_sma200 = float(p.iloc[:-20].tail(200).mean()) if len(p) >= 220 else math.nan
    dollar_volume = raw * volume
    returns = t.pct_change().dropna().tail(63)
    peak = float(t.tail(252).max()) if len(t) >= 1 else math.nan

    return {
        "observations": int(len(view)),
        "close": float(raw.iloc[-1]),
        "sma50": sma50,
        "sma200": sma200,
        "distance200": float(p.iloc[-1] / sma200 - 1.0) if sma200 else math.nan,
        "slope200": float(sma200 / prior_sma200 - 1.0) if prior_sma200 else math.nan,
        "m3": ratio_back(t, 63),
        "m6": ratio_back(t, 126),
        "m12": ratio_back(t, 252),
        "adv20": float(dollar_volume.tail(20).mean()) if len(dollar_volume) >= 20 else math.nan,
        "vol63": float(returns.std(ddof=1) * math.sqrt(TRADING_DAYS)) if len(returns) == 63 else math.nan,
        "drawdown252": float(t.iloc[-1] / peak - 1.0)
        if np.isfinite(peak) and np.isfinite(t.iloc[-1]) and peak > 0 and t.iloc[-1] > 0
        else math.nan,
    }


def evaluate_filters(row: dict[str, Any], benchmark_m6: float, market_active: bool) -> dict[str, Any]:
    required = ["sma50", "sma200", "slope200", "m6", "m12", "adv20", "vol63"]
    complete = row.get("observations", 0) >= 253 and all(
        pd.notna(row.get(key)) for key in required
    )
    relative_m6 = row.get("m6", math.nan) - benchmark_m6 if complete else math.nan
    checks = {
        "history": bool(row.get("observations", 0) >= 253),
        "price": bool(pd.notna(row.get("close")) and row["close"] >= 10),
        "liquidity": bool(pd.notna(row.get("adv20")) and row["adv20"] >= 20_000_000),
        "price_above_sma200": bool(complete and row["close"] > row["sma200"]),
        "sma50_above_sma200": bool(complete and row["sma50"] > row["sma200"]),
        "slope_positive": bool(complete and row["slope200"] > 0),
        "momentum_positive": bool(complete and row["m6"] > 0),
        "relative_positive": bool(complete and relative_m6 > 0),
    }
    eligible = checks["history"] and checks["price"] and checks["liquidity"] and complete
    trend = all(checks[k] for k in ("price_above_sma200", "sma50_above_sma200", "slope_positive"))
    momentum = checks["momentum_positive"] and checks["relative_positive"]
    qualified = eligible and trend and momentum

    if not complete or not checks["history"]:
        status = "Insufficient data"
    elif not checks["price"] or not checks["liquidity"]:
        status = "Liquidity criteria failed"
    elif not trend:
        status = "Trend criteria failed"
    elif not momentum:
        status = "Momentum criteria failed"
    elif not market_active:
        status = "Qualified, market filter inactive"
    else:
        status = "Qualified"
    return {**row, "relative_m6": relative_m6, "checks": checks, "qualified": qualified, "status": status}

