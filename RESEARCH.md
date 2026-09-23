# Forward research protocol

The original recommendation model remains unchanged. Four independent $10,000
paper portfolios begin together when the updated pipeline first runs:

1. Original: 60% six-month + 40% twelve-month percentile momentum.
2. Skip-month: rank by T[t-21] / T[t-252] - 1.
3. Buffer: original ranking, retain qualified holdings within the top 20;
   fill vacancies only from the top 10.
4. Both: skip-month ranking with the same holding buffer.

All use the original eligibility and market filters, ten slots, 10% target
weights, two stocks per classified sector, 10 bps costs per side, and zero
cash interest. Keeping filters fixed isolates the ranking change. A buffer
can leave cash when sector constraints prevent filling vacancies. Daily
weight rebalancing still incurs costs; this experiment reduces replacement
trades rather than eliminating all trading.

The comparison displays net return, maximum peak-to-trough drawdown, cumulative
transaction costs and observed sessions. These are descriptive results, not
statistical evidence of superiority. Do not select the best variant from a
few days of observations. Freeze these definitions before evaluating later
periods. No retrospective data from today's surviving-stock list is presented
as an unbiased historical backtest. Historical validation would require
point-in-time membership, delisting returns and untouched evaluation periods.

## Accurate observation and accounting

Each run first checks SPY against the latest NYSE close with a 30-minute
finalization allowance. If the provider has not supplied a complete daily bar,
the site publishes a delayed-data notice and preserves all recorded results
and pending signals. The normal 22:30 UTC weekday refresh is supplemented by
04:17 and 07:17 UTC Tuesday–Saturday retries (08:17 and 11:17 Baku time).
GitHub may start scheduled runs late. An incomplete feed produces a workflow
warning, not invented closing prices or an apparent fresh profit calculation.

Signals store their generation time and the next NYSE open available at that
time. A same-session refresh cannot replace a frozen signal. Missed refresh
days value existing holdings but do not invent new recommendations. Legacy
signals without a generation timestamp cannot justify retrospective trades.
This migration preserves existing daily snapshots. The current saved ledger
contained no executed trades at implementation time.

Per-stock trading profit is current value + cumulative gross sales + cash
dividends - cumulative gross purchases - fees. It survives partial sales,
full exits and re-entry. Legacy positions without a full transaction ledger
are flagged incomplete rather than assigned invented historical profit.

The stock growth chart is a separate hypothetical buy-and-hold reference,
starting at the first executed purchase open. It includes reinvested dividends
and splits, continues after sale, and compares SPY from the same open. It does
not represent the return of changing position sizes. If a reference price is
missing, the reference is flagged incomplete instead of bridging an unknown
distribution or split. Actual portfolio valuation uses the last available
price with a warning; a new holding uses its entry price if its close is absent.

## Sources and subsequent candidates

- [Kenneth French daily momentum construction](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/Data_Library/det_mom_factor_daily.html): prior months 2–12; our 21/252-session formula is an approximation.
- [Novy-Marx and Velikov, trading costs](https://mysimon.rochester.edu/novy-marx/research/ToAatTC.pdf): motivation for buy/hold thresholds; top 10/20 is our predefined experiment.
- [Harvey and Liu, Backtesting](https://www.cmegroup.com/content/dam/cmegroup/education/files/backtesting.pdf): multiple-testing bias; do not optimize many variations and report only the winner.
- [Moreira and Muir, Volatility-Managed Portfolios](https://www.nber.org/papers/w22208): inverse-variance exposure. A future unleveraged target-volatility experiment could use min(1, target / estimated portfolio volatility). This is an adaptation, not a replication.
- [Cederburg et al., 2020](https://www.sciencedirect.com/science/article/pii/S0304405X2030132X): mixed real-time evidence for volatility management; assess costs and stability before adoption.
- [Blitz, Huij and Martens, Residual Momentum](https://pure.eur.nl/en/publications/residual-momentum/): rank standardized residual returns from a fitted market/size/value model. Requires longer histories and aligned factor data.
- [Novy-Marx, Gross Profitability](https://mysimon.rochester.edu/novy-marx/research/OSoV.pdf): gross profits / total assets. Requires filing-publication timestamps and sector-aware interpretation.

Volatility control, residual momentum and profitability remain subsequent
research candidates, not enabled recommendation rules. The academic portfolios
are not equivalent to this concentrated long-only strategy.
