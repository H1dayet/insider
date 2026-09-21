# Northstar

Northstar is a transparent stock trend and momentum research dashboard. It screens a documented U.S. common-stock universe after each completed session, explains every pass and failure, and preserves month-end model selections so their subsequent results can be inspected.

The public app is served from `docs/` by GitHub Pages. A scheduled GitHub Action runs the Python research pipeline, updates `docs/data.json`, and appends immutable evaluation files under `data/evaluations/`.

## Run locally

```bash
python -m venv .venv
python -m pip install -r requirements.txt
python -m northstar.pipeline --bootstrap-months 12
python -m http.server 8000 --directory docs
```

Open `http://localhost:8000`. Run tests with `python -m pytest`.

## Research limits

The current starting universe is intentionally explicit and small. It is not a point-in-time reconstruction of every NYSE and Nasdaq common share. Historical results use today’s curated universe and therefore contain survivorship and selection bias. SEC CIK is retained as a permanent issuer identifier; it is not a security-level identifier. Yahoo Finance data is convenient for reproducible public research but is not an institutional corporate-action or delisting-return source. These limits are shown in the app and stored with every evaluation.

Northstar is an educational research tool. It does not provide personalized investment advice, connect to brokers, or promise profits.

