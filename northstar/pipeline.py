from __future__ import annotations

import argparse
import io
import json
import math
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pandas_market_calendars as mcal
import requests
import yfinance as yf

from .calculations import (
    evaluate_filters,
    indicators_at,
    percentile_rank,
)


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
EVALUATIONS = ROOT / "data" / "evaluations"
UNIVERSE_CACHE = ROOT / "data" / "universe.json"
STRATEGY_VERSION = "2.0.0"
INCEPTION = ROOT / "data" / "inception-v2.json"
DATA_VERSION = "nasdaq-directory-yahoo-v2"
BENCHMARK = "SPY"
DEFAULT_COST_BPS = 10
NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
NASDAQ_SCREENER_URL = (
    "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=10000&offset=0&download=true"
)
EXCLUDED_SECURITY_PATTERN = re.compile(
    r"\b(ETF|ETN|exchange.traded|warrants?|rights?|units?|preferred|preference|"
    r"depositary|depository|ADS|ADR|fund|notes?|bonds?|debentures?|when[ -]issued)\b",
    re.IGNORECASE,
)


@dataclass
class Security:
    ticker: str
    name: str
    sector: str
    permanent_id: str
    exchange: str = ""


def clean_number(value: Any) -> float | int | None:
    if value is None or pd.isna(value) or not math.isfinite(float(value)):
        return None
    return round(float(value), 8)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temp.replace(path)


def load_inception(latest_session: str) -> str:
    if INCEPTION.exists():
        return json.loads(INCEPTION.read_text(encoding="utf-8"))["session"]
    write_json(INCEPTION, {"session": latest_session, "strategy_version": STRATEGY_VERSION})
    return latest_session


def sec_cik_map() -> dict[str, str]:
    headers = {
        "User-Agent": os.getenv("SEC_USER_AGENT", "Northstar research contact@example.com"),
        "Accept-Encoding": "gzip, deflate",
    }
    try:
        response = requests.get(
            "https://www.sec.gov/files/company_tickers.json", headers=headers, timeout=30
        )
        response.raise_for_status()
        records = response.json().values()
        return {
            str(item["ticker"]).upper().replace(".", "-"): f"sec-cik:{int(item['cik_str']):010d}"
            for item in records
        }
    except (requests.RequestException, ValueError, KeyError):
        return {}


def yahoo_symbol(symbol: str) -> str:
    return str(symbol).strip().upper().replace(".", "-")


def is_common_share(name: str) -> bool:
    """Conservative instrument-type filter for the official listing directories."""
    return bool(name.strip()) and not bool(EXCLUDED_SECURITY_PATTERN.search(name))


def _directory_table(url: str) -> tuple[pd.DataFrame, str | None]:
    response = requests.get(url, timeout=45)
    response.raise_for_status()
    lines = response.text.splitlines()
    timestamp = next((line.split("|")[0] for line in reversed(lines) if line.startswith("File Creation Time")), None)
    content = "\n".join(line for line in lines if not line.startswith("File Creation Time"))
    return pd.read_csv(io.StringIO(content), sep="|", dtype=str).fillna(""), timestamp


def _screener_metadata() -> dict[str, dict[str, str]]:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; Northstar personal research)",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.nasdaq.com/market-activity/stocks/screener",
    }
    response = requests.get(NASDAQ_SCREENER_URL, headers=headers, timeout=60)
    response.raise_for_status()
    rows = response.json().get("data", {}).get("rows", []) or []
    return {
        yahoo_symbol(row.get("symbol", "")): {
            "sector": (row.get("sector") or "Unclassified").strip(),
            "country": (row.get("country") or "Unknown").strip(),
            "industry": (row.get("industry") or "Unclassified").strip(),
        }
        for row in rows
        if row.get("symbol")
    }


def refresh_universe() -> tuple[list[Security], dict[str, Any]]:
    nasdaq, nasdaq_timestamp = _directory_table(NASDAQ_LISTED_URL)
    other, other_timestamp = _directory_table(OTHER_LISTED_URL)
    metadata = _screener_metadata()
    ciks = sec_cik_map()

    nasdaq = nasdaq[
        (nasdaq["Test Issue"] == "N")
        & (nasdaq["ETF"] == "N")
        & (nasdaq["Financial Status"] == "N")
    ].copy()
    nasdaq["ticker"] = nasdaq["Symbol"].map(yahoo_symbol)
    nasdaq["name"] = nasdaq["Security Name"]
    nasdaq["exchange"] = "Nasdaq"

    # N is NYSE. NYSE American, NYSE Arca, Cboe and IEX are outside the
    # explicitly requested initial exchange scope.
    other = other[
        (other["Test Issue"] == "N") & (other["ETF"] == "N") & (other["Exchange"] == "N")
    ].copy()
    other["ticker"] = other["ACT Symbol"].map(yahoo_symbol)
    other["name"] = other["Security Name"]
    other["exchange"] = "NYSE"

    listings = pd.concat(
        [nasdaq[["ticker", "name", "exchange"]], other[["ticker", "name", "exchange"]]],
        ignore_index=True,
    ).drop_duplicates("ticker", keep="first")
    common_symbol = listings["ticker"].str.match(r"^[A-Z]{1,5}(-[A-Z])?$", na=False)
    listings = listings[common_symbol & listings["name"].map(is_common_share)]

    securities = [
        Security(
            ticker=row.ticker,
            name=row.name.strip(),
            sector=metadata.get(row.ticker, {}).get("sector", "Unclassified") or "Unclassified",
            permanent_id=ciks.get(row.ticker, f"issuer-unresolved:{row.ticker}"),
            exchange=row.exchange,
        )
        for row in listings.itertuples(index=False)
    ]
    securities.sort(key=lambda item: item.ticker)
    source = {
        "source": "Nasdaq Trader Symbol Directory",
        "nasdaq_file_timestamp": nasdaq_timestamp,
        "other_listed_file_timestamp": other_timestamp,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "directory_rows": int(len(nasdaq) + len(other)),
        "eligible_common_shares": len(securities),
        "unclassified_sectors": sum(item.sector == "Unclassified" for item in securities),
        "unresolved_issuer_ids": sum(item.permanent_id.startswith("issuer-unresolved:") for item in securities),
    }
    write_json(
        UNIVERSE_CACHE,
        {"meta": source, "securities": [item.__dict__ for item in securities]},
    )
    return securities, source


def load_universe() -> tuple[list[Security], dict[str, Any]]:
    try:
        return refresh_universe()
    except (requests.RequestException, ValueError, KeyError, json.JSONDecodeError) as exc:
        if not UNIVERSE_CACHE.exists():
            raise RuntimeError(f"Unable to refresh listing universe: {exc}") from exc
        cached = json.loads(UNIVERSE_CACHE.read_text(encoding="utf-8"))
        securities = [Security(**item) for item in cached["securities"]]
        metadata = {**cached["meta"], "cache_fallback": True, "refresh_error": str(exc)}
        return securities, metadata


def normalize_history(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        raise ValueError("provider returned no rows")
    frame.index = pd.DatetimeIndex(frame.index).tz_localize(None).normalize()
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    columns = {
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Adj Close": "adj_close",
        "Volume": "volume",
        "Dividends": "dividends",
        "Stock Splits": "splits",
    }
    frame = frame.rename(columns=columns)
    for column in columns.values():
        if column not in frame:
            frame[column] = 0.0 if column in {"dividends", "splits"} else np.nan
    frame = frame[list(columns.values())].copy()
    frame = frame.dropna(subset=["open", "high", "low", "close", "adj_close", "volume"])
    # Yahoo's OHLC history is already normalized for later splits. Rebuild the
    # contemporaneous nominal prices for eligibility and execution assumptions,
    # retain Yahoo Close as P, and use Adj Close as the distribution-aware T.
    yahoo_close = frame["close"].copy()
    factors = frame["splits"].replace(0, 1).astype(float)
    future_factor = factors.iloc[::-1].cumprod().iloc[::-1] / factors
    for column in ("open", "high", "low", "close"):
        frame[column] = frame[column].astype(float) * future_factor
    frame["p"] = yahoo_close.astype(float)
    frame["t"] = frame["adj_close"].astype(float) / float(frame["adj_close"].iloc[0]) * 100.0
    return frame


def download_history(ticker: str, period: str = "3y") -> pd.DataFrame:
    frame = yf.Ticker(ticker).history(
        period=period, interval="1d", auto_adjust=False, actions=True, repair=False
    )
    return normalize_history(frame)


def download_histories(
    tickers: list[str], period: str = "3y", batch_size: int = 100
) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    """Download a large universe in bounded concurrent batches.

    Failed symbols are retried individually once so one malformed ticker cannot
    discard an otherwise successful batch.
    """
    frames: dict[str, pd.DataFrame] = {}
    failures: dict[str, str] = {}
    total_batches = math.ceil(len(tickers) / batch_size)
    for batch_number, start in enumerate(range(0, len(tickers), batch_size), 1):
        batch = tickers[start : start + batch_size]
        print(f"Downloading batch {batch_number}/{total_batches} ({len(batch)} symbols)", flush=True)
        try:
            downloaded = yf.download(
                batch,
                period=period,
                interval="1d",
                group_by="ticker",
                auto_adjust=False,
                actions=True,
                repair=False,
                threads=True,
                progress=False,
                timeout=30,
            )
        except Exception as exc:
            downloaded = pd.DataFrame()
            for ticker in batch:
                failures[ticker] = f"batch error: {exc}"

        for ticker in batch:
            try:
                if downloaded.empty:
                    raise ValueError(failures.get(ticker, "empty batch response"))
                if isinstance(downloaded.columns, pd.MultiIndex):
                    if ticker not in downloaded.columns.get_level_values(0):
                        raise ValueError("symbol absent from batch response")
                    candidate = downloaded[ticker].dropna(how="all")
                else:
                    candidate = downloaded.dropna(how="all")
                frames[ticker] = normalize_history(candidate)
                failures.pop(ticker, None)
            except Exception as exc:
                failures[ticker] = str(exc)

    retry = list(failures)
    if retry:
        print(f"Retrying {len(retry)} failed symbols individually", flush=True)
    for ticker in retry:
        try:
            frames[ticker] = download_history(ticker, period=period)
            failures.pop(ticker, None)
        except Exception as exc:
            failures[ticker] = str(exc)
    return frames, failures


def quality_warnings(frame: pd.DataFrame) -> list[str]:
    warnings: list[str] = []
    if frame.index.duplicated().any():
        warnings.append("Duplicate sessions")
    if (frame[["open", "high", "low", "close"]] <= 0).any().any():
        warnings.append("Non-positive OHLC value")
    invalid_range = (
        (frame["low"] > frame["open"])
        | (frame["low"] > frame["close"])
        | (frame["high"] < frame["open"])
        | (frame["high"] < frame["close"])
    )
    if invalid_range.any():
        warnings.append("OHLC range inconsistency")
    if (frame["volume"] < 0).any():
        warnings.append("Negative volume")
    if len(frame) >= 6 and frame["close"].tail(6).nunique() == 1:
        warnings.append("Price may be stale")
    return warnings


def common_session(frames: dict[str, pd.DataFrame]) -> pd.Timestamp:
    sessions = [set(frame.index) for frame in frames.values() if not frame.empty]
    common = set.intersection(*sessions)
    if not common:
        raise RuntimeError("No completed session is shared across the downloaded series")
    first, last = min(common), max(common)
    schedule = mcal.get_calendar("NYSE").schedule(start_date=first, end_date=last)
    now = pd.Timestamp.now(tz="UTC")
    completed = schedule.index[schedule["market_close"] <= now]
    if len(completed) == 0:
        raise RuntimeError("No downloaded session has completed on the exchange calendar")
    last_completed = pd.Timestamp(completed[-1]).tz_localize(None).normalize()
    eligible = [session for session in common if session <= last_completed]
    if not eligible:
        raise RuntimeError("No common downloaded session is finalized")
    return max(eligible)


def result_for_date(
    security: Security,
    frame: pd.DataFrame,
    benchmark: pd.DataFrame,
    session: pd.Timestamp,
) -> dict[str, Any]:
    position = int(frame.index.get_indexer([session])[0])
    benchmark_position = int(benchmark.index.get_indexer([session])[0])
    if position < 0 or benchmark_position < 0:
        return {
            "ticker": security.ticker,
            "name": security.name,
            "sector": security.sector,
            "exchange": security.exchange,
            "permanent_id": security.permanent_id,
            "status": "Insufficient data",
            "qualified": False,
            "warnings": ["No observation on evaluation session"],
            "checks": {},
        }
    values = indicators_at(frame, position)
    benchmark_values = indicators_at(benchmark, benchmark_position)
    market_active = bool(
        pd.notna(benchmark_values["sma200"])
        and benchmark.iloc[benchmark_position]["p"] > benchmark_values["sma200"]
    )
    evaluated = evaluate_filters(values, float(benchmark_values["m6"]), market_active)
    warnings = quality_warnings(frame.loc[:session])
    if warnings:
        evaluated["qualified"] = False
        evaluated["status"] = "Data quality exclusion"
    return {
        "ticker": security.ticker,
        "name": security.name,
        "sector": security.sector,
        "exchange": security.exchange,
        "permanent_id": security.permanent_id,
        **{key: clean_number(value) for key, value in evaluated.items() if key not in {"checks", "qualified", "status"}},
        "checks": evaluated["checks"],
        "qualified": evaluated["qualified"],
        "status": evaluated["status"],
        "warnings": warnings,
    }


def rank_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    qualifying = [row for row in results if row.get("qualified")]
    if qualifying:
        m6 = pd.Series({row["ticker"]: row["m6"] for row in qualifying})
        m12 = pd.Series({row["ticker"]: row["m12"] for row in qualifying})
        rank6 = percentile_rank(m6)
        rank12 = percentile_rank(m12)
        for row in qualifying:
            ticker = row["ticker"]
            row["m6_percentile"] = clean_number(rank6[ticker])
            row["m12_percentile"] = clean_number(rank12[ticker])
            row["score"] = clean_number(0.6 * rank6[ticker] + 0.4 * rank12[ticker])
            row["small_universe"] = len(qualifying) == 1
    for row in results:
        row.setdefault("m6_percentile", None)
        row.setdefault("m12_percentile", None)
        row.setdefault("score", None)
        row.setdefault("small_universe", False)
    return sorted(
        results,
        key=lambda row: (
            row.get("score") is not None,
            row.get("score") or -1,
            row.get("adv20") or -1,
            row["permanent_id"],
        ),
        reverse=True,
    )


def select_portfolio(results: list[dict[str, Any]], market_active: bool) -> list[dict[str, Any]]:
    if not market_active:
        return []
    selected: list[dict[str, Any]] = []
    sectors: dict[str, int] = {}
    for row in results:
        if not row.get("qualified") or len(selected) == 10:
            continue
        sector = row["sector"]
        sector_key = sector if sector != "Unclassified" else f"unclassified:{row['ticker']}"
        if sectors.get(sector_key, 0) >= 2:
            continue
        sectors[sector_key] = sectors.get(sector_key, 0) + 1
        selected.append(
            {
                "ticker": row["ticker"],
                "permanent_id": row["permanent_id"],
                "name": row["name"],
                "sector": sector,
                "score": row["score"],
                "signal_close": row["close"],
                "weight": 0.1,
            }
        )
    return selected


def benchmark_state(benchmark: pd.DataFrame, session: pd.Timestamp) -> dict[str, Any]:
    position = int(benchmark.index.get_indexer([session])[0])
    values = indicators_at(benchmark, position)
    active = bool(values["sma200"] and benchmark.iloc[position]["p"] > values["sma200"])
    return {
        "ticker": BENCHMARK,
        "close": clean_number(benchmark.iloc[position]["close"]),
        "sma200": clean_number(values["sma200"]),
        "m6": clean_number(values["m6"]),
        "active": active,
    }


def month_end_sessions(benchmark: pd.DataFrame, through: pd.Timestamp, months: int) -> list[pd.Timestamp]:
    schedule = mcal.get_calendar("NYSE").schedule(
        start_date=benchmark.index.min(), end_date=through + pd.offsets.MonthEnd(1)
    )
    sessions = pd.DatetimeIndex(schedule.index).tz_localize(None).normalize()
    session_series = sessions.to_series(index=sessions)
    official_ends = session_series.groupby(session_series.dt.to_period("M")).max()
    completed_ends = official_ends[official_ends <= through]
    available = [session for session in completed_ends if session in benchmark.index]
    return [pd.Timestamp(item) for item in available[-months:]]


def build_evaluation(
    session: pd.Timestamp,
    mode: str,
    universe: list[Security],
    frames: dict[str, pd.DataFrame],
) -> dict[str, Any]:
    benchmark = frames[BENCHMARK]
    results = rank_results(
        [result_for_date(sec, frames[sec.ticker], benchmark, session) for sec in universe]
    )
    market = benchmark_state(benchmark, session)
    return {
        "evaluation_session": session.date().isoformat(),
        "record_type": mode,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "strategy_version": STRATEGY_VERSION,
        "data_version": DATA_VERSION,
        "market": market,
        "universe_size": len(results),
        "qualified_count": sum(bool(item["qualified"]) for item in results),
        "selected": select_portfolio(results, market["active"]),
        "disclosures": [
            "Reconstructed results use the current curated universe and contain survivorship and selection bias."
            if mode == "reconstructed"
            else "Recorded forward after the strategy inception date; the original signal values are preserved.",
            "SEC CIK identifies the issuer, not an individual share class.",
            "Yahoo Finance data does not provide complete delisting returns or institutional point-in-time reference data.",
        ],
    }


def save_evaluations(
    universe: list[Security],
    frames: dict[str, pd.DataFrame],
    latest: pd.Timestamp,
    bootstrap_months: int,
    inception: str,
) -> list[dict[str, Any]]:
    EVALUATIONS.mkdir(parents=True, exist_ok=True)
    sessions = month_end_sessions(frames[BENCHMARK], latest, max(bootstrap_months, 1))
    version_tag = STRATEGY_VERSION.split(".")[0]
    for session in sessions:
        path = EVALUATIONS / f"{session.date().isoformat()}-v{version_tag}.json"
        if path.exists():
            continue
        mode = "recorded-forward" if session.date().isoformat() >= inception else "reconstructed"
        write_json(path, build_evaluation(session, mode, universe, frames))
    return [
        payload
        for path in sorted(EVALUATIONS.glob("*.json"))
        for payload in [json.loads(path.read_text(encoding="utf-8"))]
        if payload["evaluation_session"] <= latest.date().isoformat()
        and payload.get("strategy_version") == STRATEGY_VERSION
    ]


def price_return(
    frame: pd.DataFrame,
    signal_date: str,
    end_date: str | None = None,
    end_at_open: bool = False,
) -> tuple[float | None, str | None, float | None]:
    signal = pd.Timestamp(signal_date)
    later = frame.index[frame.index > signal]
    if len(later) == 0:
        return None, None, None
    entry_date = later[0]
    entry_open = float(frame.loc[entry_date, "open"])
    entry_close = float(frame.loc[entry_date, "close"])
    entry_t = float(frame.loc[entry_date, "t"])
    if end_date:
        target = pd.Timestamp(end_date)
        candidates = frame.index[frame.index >= target]
        if len(candidates) == 0:
            return None, entry_date.date().isoformat(), entry_open
        end = candidates[0]
    else:
        end = frame.index[-1]
    end_t = float(frame.loc[end, "t"])
    gross = (end_t / entry_t) * (entry_close / entry_open)
    if end_at_open:
        gross *= float(frame.loc[end, "open"]) / float(frame.loc[end, "close"])
    return gross - 1.0, entry_date.date().isoformat(), entry_open


def enrich_track_record(
    evaluations: list[dict[str, Any]], frames: dict[str, pd.DataFrame], latest: pd.Timestamp
) -> dict[str, Any]:
    cohorts: list[dict[str, Any]] = []
    equity = 1.0
    benchmark_equity = 1.0
    equity_points: list[dict[str, Any]] = []
    monthly_returns: list[float] = []
    previous_weights: dict[str, float] = {}

    for index, evaluation in enumerate(evaluations):
        signal_date = evaluation["evaluation_session"]
        next_signal = evaluations[index + 1]["evaluation_session"] if index + 1 < len(evaluations) else None
        selected_details = []
        current_returns = []
        period_return = 0.0
        weights = {item["ticker"]: item["weight"] for item in evaluation["selected"]}
        turnover = sum(
            abs(weights.get(ticker, 0.0) - previous_weights.get(ticker, 0.0))
            for ticker in set(weights) | set(previous_weights)
        )
        for item in evaluation["selected"]:
            frame = frames.get(item["ticker"])
            if frame is None:
                continue
            current_return, entry_date, entry_open = price_return(frame, signal_date)
            period, _, _ = price_return(frame, signal_date, next_signal, end_at_open=bool(next_signal))
            selected_details.append(
                {
                    **item,
                    "entry_date": entry_date,
                    "entry_open": clean_number(entry_open),
                    "current_return": clean_number(current_return),
                }
            )
            if current_return is not None:
                current_returns.append(current_return)
            if period is not None:
                period_return += item["weight"] * period
        cost = turnover * (DEFAULT_COST_BPS / 10_000)
        period_return -= cost
        equity *= 1.0 + period_return
        monthly_returns.append(period_return)

        benchmark_period, _, _ = price_return(
            frames[BENCHMARK], signal_date, next_signal, end_at_open=bool(next_signal)
        )
        if benchmark_period is not None:
            benchmark_equity *= 1.0 + benchmark_period
        cohort_return = sum(current_returns) / len(current_returns) if current_returns else None
        cohorts.append(
            {
                "evaluation_session": signal_date,
                "record_type": evaluation["record_type"],
                "market_active": evaluation["market"]["active"],
                "qualified_count": evaluation["qualified_count"],
                "selected": selected_details,
                "cohort_return": clean_number(cohort_return),
                "turnover": clean_number(turnover),
            }
        )
        equity_points.append(
            {
                "date": next_signal or latest.date().isoformat(),
                "strategy": clean_number(equity),
                "benchmark": clean_number(benchmark_equity),
            }
        )
        previous_weights = weights

    elapsed_years = 0.0
    if evaluations:
        elapsed_years = max(
            (latest.date() - date.fromisoformat(evaluations[0]["evaluation_session"])).days / 365.25,
            1 / 365.25,
        )
    values = pd.Series([1.0] + [float(p["strategy"]) for p in equity_points])
    drawdowns = values / values.cummax() - 1.0
    monthly = pd.Series(monthly_returns, dtype=float)
    return {
        "cost_bps_per_side": DEFAULT_COST_BPS,
        "strategy_return": clean_number(equity - 1.0),
        "benchmark_return": clean_number(benchmark_equity - 1.0),
        "cagr": clean_number(equity ** (1 / elapsed_years) - 1.0) if evaluations else None,
        "annualized_volatility": clean_number(monthly.std(ddof=1) * math.sqrt(12)) if len(monthly) > 1 else None,
        "max_drawdown": clean_number(drawdowns.min()) if len(drawdowns) else None,
        "evaluations": len(evaluations),
        "forward_evaluations": sum(item["record_type"] == "recorded-forward" for item in evaluations),
        "equity_curve": equity_points,
        "cohorts": list(reversed(cohorts)),
    }


def previous_statuses(evaluations: list[dict[str, Any]]) -> dict[str, str]:
    if not evaluations:
        return {}
    selected = {item["ticker"] for item in evaluations[-1]["selected"]}
    return {ticker: "Selected" for ticker in selected}


def run(bootstrap_months: int) -> dict[str, Any]:
    universe, universe_source = load_universe()
    tickers = [BENCHMARK] + [item.ticker for item in universe if item.ticker != BENCHMARK]
    frames, failures = download_histories(tickers)
    if BENCHMARK not in frames:
        raise RuntimeError(f"Benchmark download failed: {failures.get(BENCHMARK, 'unknown error')}")
    available = [security for security in universe if security.ticker in frames]
    latest = common_session({BENCHMARK: frames[BENCHMARK]})
    inception = load_inception(latest.date().isoformat())
    evaluations = save_evaluations(available, frames, latest, bootstrap_months, inception)
    benchmark = benchmark_state(frames[BENCHMARK], latest)
    screen = rank_results(
        [result_for_date(sec, frames[sec.ticker], frames[BENCHMARK], latest) for sec in available]
    )
    previous = previous_statuses(evaluations[:-1])
    for row in screen:
        row["previous_status"] = previous.get(row["ticker"], "Not selected")
        row["status_change"] = (
            "Newly qualifies" if row["qualified"] and row["previous_status"] != "Selected"
            else "No longer selected" if not row["qualified"] and row["previous_status"] == "Selected"
            else "Unchanged"
        )
    payload = {
        "meta": {
            "title": "Northstar",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "latest_completed_session": latest.date().isoformat(),
            "provider": "Nasdaq Trader listing directories; Yahoo Finance via yfinance; SEC company_tickers for issuer IDs",
            "data_status": "final provider bars",
            "strategy_version": STRATEGY_VERSION,
            "data_version": DATA_VERSION,
            "universe_definition": "Current Nasdaq and NYSE common-share listings after instrument-type exclusions",
            "universe_count": len(universe),
            "available_count": len(available),
            "failed_count": len(failures),
            "failed_downloads": failures,
            "universe_source": universe_source,
            "adjustment_method": "Yahoo split-normalized OHLC is retained as P; contemporaneous raw OHLC is reconstructed from split factors; Yahoo Adj Close is normalized as T and includes distributions.",
            "research_status": "Unvalidated hypothesis",
        },
        "market": benchmark,
        "thresholds": {
            "minimum_raw_close": 10,
            "minimum_adv20": 20_000_000,
            "minimum_observations": 253,
            "portfolio_size": 10,
            "sector_cap": 2,
        },
        "screen": screen,
        "track_record": enrich_track_record(evaluations, frames, latest),
        "methodology": {
            "formulas": [
                "SMAₙ(t) = Σ P[t−k] / n",
                "Distance200 = Pₜ / SMA200 − 1",
                "Slope200 = SMA200ₜ / SMA200ₜ₋₂₀ − 1",
                "M3, M6, M12 = Tₜ / Tₜ₋ₙ − 1 for n = 63, 126, 252",
                "RelativeM6 = M6 − SPY M6",
                "ADV20 = mean(Cₜ × Vₜ) over 20 sessions",
                "Vol63 = sample stdev(daily total returns) × √252",
                "Drawdown252 = Tₜ / max(T trailing 252) − 1",
                "Score = 0.60 × percentile(M6) + 0.40 × percentile(M12)",
            ],
            "limitations": [
                "The current listing universe is broad but not point-in-time complete; reconstructed results contain survivorship bias.",
                "Delisted, acquired and bankrupt securities are not fully represented, and delisting returns are unavailable.",
                "SEC CIK is a permanent issuer identifier, not a security-level identifier.",
                "Provider bars are not independently cross-checked in this public-data version.",
                "The model remains unvalidated until ranking tests, untouched out-of-sample tests and broader point-in-time data are completed.",
                "Cash earns 0% in the current simulation; taxes and currency effects are excluded.",
            ],
        },
    }
    write_json(DOCS / "data.json", payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the Northstar research dataset")
    parser.add_argument("--bootstrap-months", type=int, default=12)
    args = parser.parse_args()
    payload = run(max(args.bootstrap_months, 1))
    print(
        f"Built {len(payload['screen'])} securities through "
        f"{payload['meta']['latest_completed_session']} with "
        f"{payload['track_record']['evaluations']} evaluations"
    )


if __name__ == "__main__":
    main()
