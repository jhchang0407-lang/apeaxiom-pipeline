# Ape Axiom — AI Investment Research Pipeline

Automated investment memo generation for the S&P 500. Reads SEC EDGAR filings and FMP market data, runs a multi-stage AI pipeline (data fetch → peer selection → section writing → fact-check → valuation → assembly), and produces a 14-section investment research memo in ~5 minutes.

Live at [apeaxiom.com](https://apeaxiom.com).

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full pipeline map, data flow, and R2 bucket structure.

**Two pipelines:**
- `run_memo.py` — full 10-K research memo (14 sections + financial appendix)
- `run_quarterly.py` — sector-aware quarterly earnings report

**Daily automation:**
- `run_master.py` — checks FMP for new earnings/10-Ks, triggers the right pipeline
- `run_market_data.py` — pulls prices and metrics from FMP, uploads to R2

**Batch:**
- `run_batch_sp500.py` — one-time backfill for all S&P 500 tickers, with resume support

## Key Design Decisions

- **Sector-aware parsing** — 12 industries with dedicated SEC XBRL extraction and valuation models (banks use excess returns, REITs use NAV, etc.)
- **Fact-check layer** — AI-written prose is verified against source data before output
- **NM thresholds** — ratios outside reasonable bounds are flagged as "not meaningful" rather than silently displayed as nonsense
- **Structured data contract** — the memo schema encodes an opinionated framework, not a generic template

## License

[PolyForm Noncommercial 1.0.0](LICENSE) — you can read, fork, and learn from this code for any non-commercial purpose. Commercial use is not permitted.
