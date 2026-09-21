# Northstar

Northstar is a transparent stock trend and momentum research dashboard. It refreshes the current Nasdaq and NYSE common-share universe from Nasdaq Trader after each completed session, explains every pass and failure, and preserves month-end model selections so their subsequent results can be inspected.

The public app is served from `docs/` by GitHub Pages. A scheduled GitHub Action runs the Python research pipeline, updates `docs/data.json`, and advances the forward-only paper portfolio in `data/paper-portfolio.json`.

## Run locally

```bash
python -m venv .venv
python -m pip install -r requirements.txt
python -m northstar.pipeline
python -m http.server 8000 --directory docs
```

Open `http://localhost:8000`. Run tests with `python -m pytest`.

## Research limits

The current universe covers active Nasdaq and NYSE common-share listings after explicit fund, preferred-share, warrant, right, unit, debt and depositary-receipt exclusions. It is not a point-in-time historical universe. The paper track record begins at its displayed start date and has no historical backfill. SEC CIK is retained as a permanent issuer identifier; it is not a security-level identifier. Yahoo Finance data is convenient for reproducible public research but is not an institutional corporate-action or delisting-return source. These limits are shown in the app and stored with every run.

Northstar is an educational research tool. It does not provide personalized investment advice, connect to brokers, or promise profits.

