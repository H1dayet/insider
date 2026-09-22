"""Predefined forward comparisons; no parameter search or retroactive signals."""
from copy import deepcopy


VARIANTS = {
    "baseline": "Original rules",
    "skip_month": "Skip-month momentum",
    "buffer": "Holding buffer",
    "skip_buffer": "Skip-month + buffer",
}


def select_variant(screen, active, variant, held):
    from .pipeline import select_portfolio

    rows = deepcopy(screen)
    if variant in ("skip_month", "skip_buffer"):
        rows = [r for r in rows if r.get("qualified") and r.get("m12_skip") is not None]
        for row in rows:
            row["score"] = row["m12_skip"]
        rows.sort(key=lambda r: (-r["score"], -r["adv20"], r["permanent_id"]))
    if variant in ("buffer", "skip_buffer"):
        qualified = [r for r in rows if r.get("qualified")]
        retained = [r for r in qualified[:20] if r["ticker"] in held]
        retained_tickers = {r["ticker"] for r in retained}
        # Fill vacancies only from the top ten. Risk and sector limits still apply.
        rows = retained + [r for r in qualified[:10] if r["ticker"] not in retained_tickers]
    return select_portfolio(rows, active)


def metrics(state):
    values = [state["initial_capital"]] + [r["portfolio_value"] for r in state["daily_snapshots"]]
    peak, drawdown = values[0], 0.0
    for value in values:
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1)
    return {"return": values[-1] / values[0] - 1, "max_drawdown": drawdown,
            "costs": sum(r["transaction_cost"] for r in state["daily_snapshots"]),
            "sessions": len(values) - 1, "started_at": state["started_at"]}
