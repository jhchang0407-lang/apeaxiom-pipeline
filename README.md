# Ape Axiom — AI Investment Research Pipeline

Automated investment memo generation for the S&P 500. Reads SEC EDGAR filings and FMP market data, runs a multi-stage AI pipeline (data fetch → peer selection → section writing → fact-check → valuation → assembly), and produces a 14-section investment research memo in a few minutes.

Live at [apeaxiom.com](https://apeaxiom.com).

## What's Inside

**Two pipelines:**
- `run_memo.py` — full 10-K research memo (14 AI-written sections + financial appendix)
- `run_quarterly.py` — sector-aware quarterly earnings report

**Daily automation:**
- `run_master.py` — checks FMP for new earnings/10-Ks, triggers the right pipeline
- `run_market_data.py` — pulls prices and metrics from FMP, uploads to R2

**Utilities:**
- `run_batch_sp500.py` — backfill for all S&P 500 tickers, with resume support
- `build_r2_index.py` — rebuild the R2 `_index.json` from a bucket listing

See [ARCHITECTURE.md](ARCHITECTURE.md) for the directory map, data flow, and deployment layout, and [PIPELINE_MAP.md](PIPELINE_MAP.md) for a stage-by-stage deep dive of the memo pipeline.

## Quick Start

Requires Python 3.12+.

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cp .env.template .env
# Edit .env — you need at minimum:
#   FMP_API_KEY      (financialmodelingprep.com)
#   OPENAI_API_KEY
#   SEC_USER_AGENT   (your name + contact email — required by SEC EDGAR)

# Generate a memo locally (writes to output/memo/)
python run_memo.py AAPL

# Generate a quarterly earnings report (writes to output/quarterly/)
python run_quarterly.py AAPL
```

Uploading to a website (`--upload`) additionally requires the Cloudflare R2 variables in `.env.template`. API responses are cached in `cache/` so re-runs are cheap.

> **Note on PDF output:** `weasyprint` needs system libraries (Pango, Cairo, GDK-PixBuf). The pipeline degrades gracefully without them — see `Dockerfile` for the apt package list.

## Key Design Decisions

- **Sector-aware parsing** — 11 industry modules with dedicated SEC XBRL extraction, and sector-routed valuation models (banks use excess returns, REITs use NAV, energy/REITs skip DCF where it's not meaningful)
- **Fact-check layer** — AI-written prose is verified against source data before output
- **NM thresholds** — ratios outside reasonable bounds are flagged as "not meaningful" rather than silently displayed as nonsense
- **Fail loudly on missing data** — valuation models error out rather than fabricate inputs (no placeholder share counts or assumed margins)
- **Structured data contract** — the memo schema encodes an opinionated framework, not a generic template

## Disclaimer

This software generates automated research content using AI. Output may contain errors and is not financial advice. Do your own diligence.

## License

[PolyForm Noncommercial 1.0.0](LICENSE) — you can read, fork, and learn from this code for any non-commercial purpose. Commercial use is not permitted.
