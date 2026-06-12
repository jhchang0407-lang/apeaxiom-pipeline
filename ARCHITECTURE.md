# Ape Axiom — Pipeline Architecture

## Directory Overview

```
.
├── run_master.py            ← CRON: Daily automation trigger
│                              Checks FMP for new earnings (3d) & 10-K filings (10d),
│                              matches against sp500_tickers.json, runs quarterly
│                              (batches of 5) & memo (batches of 2) pipelines
│
├── run_memo.py              ← ENTRY: Full memo pipeline
│                              Runs full 10-K research memo → uploads JSON to R2
│                              Called by run_master.py or standalone
│
├── run_quarterly.py         ← ENTRY: Quarterly earnings pipeline
│                              Runs quarterly earnings report → uploads JSON to R2
│                              Called by run_master.py or standalone
│
├── run_market_data.py       ← CRON: FMP → R2 market data uploader
│                              Pulls prices & metrics from FMP, calculates in Python,
│                              uploads Dashboard/ and Daily Price/ JSON to R2
│
├── run_batch_sp500.py       ← BATCH: Backfill for all S&P 500 tickers
│                              Runs memo and/or quarterly for every ticker,
│                              with resume support (sp500_progress.json)
│
├── build_r2_index.py        ← UTILITY: Rebuild _index.json from a full R2
│                              bucket listing (recovery / first-time setup)
│
├── config/
│   ├── settings.py          ← All env-driven config (API keys, R2, models)
│   ├── r2.py                ← Shared R2 client, upload, and index helpers
│   └── fmp_client.py        ← FMP API HTTP client
│
├── prompts/                 ← Jinja2 templates for the three research agents
│
├── pipeline/                ← FULL MEMO PIPELINE (10-K based)
│   ├── orchestrator.py      ← Main pipeline controller — chains all stages
│   ├── data_fetcher.py      ← Stage 1: SEC EDGAR + FMP data fetch
│   ├── quantitative.py      ← Stage 2: Financial computations & ratios
│   ├── clean_quantitative.py← Stage 2b: Clean/normalize quant data
│   ├── transforms.py        ← Stage 2c: Data transformations
│   ├── sector_tables.py     ← Stage 2d: Sector-specific table generation
│   ├── peer_selection.py    ← Stage 3: AI-powered peer company selection
│   ├── distributors.py      ← Stage 4: Distribute data to section writers (DCF inputs)
│   ├── writers.py           ← Stage 5: AI prose generation per section
│   ├── sanitize.py          ← Stage 5b: Clean writer output
│   ├── fact_check.py        ← Stage 6: Verify numbers in prose vs source data
│   ├── assembly.py          ← Stage 7: DCF valuation + final assembly
│   ├── formatters.py        ← Stage 8: Markdown + HTML output
│   ├── source_registry.py   ← Track data provenance
│   └── trace.py             ← Pipeline execution tracing
│
├── quarterly/               ← QUARTERLY EARNINGS PIPELINE
│   ├── orchestrator.py      ← Main pipeline controller — chains all stages
│   ├── research.py          ← Stage 1: Sector-aware web search agent
│   ├── fact_extract.py      ← Stage 2: Deterministic fact extraction from research
│   ├── distributor.py       ← Stage 3: Pre-compute tables (financials, consensus, KPIs)
│   ├── writer.py            ← Stage 4: AI prose generation
│   ├── fact_check.py        ← Stage 5: Verify numbers in prose vs source data
│   ├── formatter.py         ← Stage 6: Markdown output
│   ├── html_formatter.py    ← Stage 6b: Dark-themed HTML dashboard
│   └── sector_prompts.py    ← 13 sector families (+ generic fallback): research
│                              prompts, KPI schemas, extraction rules, writer guidance
│
├── sec/                     ← SEC EDGAR DATA LAYER
│   ├── client.py            ← EDGAR HTTP client (rate-limited, cached)
│   ├── filings.py           ← 10-K/10-Q filing retrieval
│   ├── statements.py        ← Income, balance sheet, cash flow parsing
│   ├── segments.py          ← Revenue segment breakdown
│   ├── profile.py           ← Company profile data
│   ├── ratios.py            ← Financial ratio computations
│   ├── mapper.py            ← XBRL tag mapping
│   └── sectors/             ← Sector-specific EDGAR parsing (11 modules)
│       ├── banks.py         ← Banking (NII, provisions, CET1)
│       ├── insurance.py     ← Insurance (premiums, combined ratio)
│       ├── reits.py         ← REITs (FFO, NOI, occupancy)
│       ├── tech.py          ← Technology (ARR, RPO, SBC)
│       ├── energy.py        ← Energy (production, reserves, realizations)
│       ├── healthcare.py    ← Healthcare (R&D pipeline, drug revenue)
│       ├── retail.py        ← Retail (SSS, store count, e-commerce)
│       ├── industrials.py   ← Industrials (backlog, book-to-bill)
│       ├── consumer_staples.py
│       ├── utilities.py     ← Utilities (rate base, generation mix)
│       ├── materials.py     ← Materials (commodity prices, volumes)
│       └── _utils.py        ← Shared sector utilities
│
├── valuation/               ← VALUATION MODELS
│   ├── dcf.py               ← Discounted cash flow (FCFF)
│   ├── ddm.py               ← Dividend discount model
│   ├── bank_equity.py       ← Excess returns / justified P/B (banks)
│   ├── nav.py               ← Net asset value (REITs)
│   ├── peer_multiples.py    ← Relative valuation (EV/EBITDA, P/E, etc.)
│   └── industry_config.py   ← Industry → valuation model routing
│
└── tests/                   ← Standalone test scripts (hit live APIs)
    ├── test_full_pipeline.py
    ├── test_writer_validation.py
    ├── test_qualitative_sections.py
    ├── test_quant_all_sectors.py
    ├── test_sector_flow_probe.py
    └── test_single_section.py
```

Local-only directories created at runtime (gitignored): `cache/` (SEC/FMP
API response cache), `output/` (generated memos and reports).

---

## Data Flow

### Full Memo Pipeline (`run_memo.py`)

```
                    ┌─── SEC EDGAR (10-K, segments, ratios)
  run_memo.py ──→ data_fetcher
                    └─── FMP (estimates, peers, market data)
        │
        ▼
  quantitative ──→ clean_quantitative ──→ transforms ──→ sector_tables
        │
        ▼
  peer_selection (AI agent picks comparable companies)
        │
        ▼
  distributors (prepares section-specific data bundles + DCF inputs)
        │
        ▼
  writers (14 AI-written sections: 11 body + 3 synthesis, sector-aware prompts)
        │
        ▼
  sanitize ──→ fact_check (verify numbers vs source data)
        │
        ▼
  assembly (DCF/DDM/NAV valuation + probability-weighted fair value)
        │
        ▼
  formatters (Markdown + HTML) ──→ R2 upload
```

See [PIPELINE_MAP.md](PIPELINE_MAP.md) for a stage-by-stage deep dive.

### Quarterly Earnings Pipeline (`run_quarterly.py`)

```
  run_quarterly.py ──→ FMP Profile (detect sector)
        │
        ▼
  research (sector-aware web search agent)
        │
        ▼
  fact_extract (deterministic — pull numbers from research)
        │
        ▼
  distributor (pre-compute tables: financials, consensus, sector KPIs)
        │
        ▼
  writer (AI prose, sector-specific guidance)
        │
        ▼
  fact_check (verify numbers in prose vs extracted facts)
        │
        ▼
  formatter (Markdown) + html_formatter (HTML dashboard) ──→ R2 upload
```

### Daily Automation (`run_master.py`)

```
  run_master.py (daily cron)
        │
        ├──→ FMP Earnings Calendar (last 3 days)
        │         ∩
        │    sp500_tickers.json (local S&P 500 list)
        │         │
        │         ▼
        │    Matches ──→ run_quarterly.py {ticker} (batches of 5)
        │
        └──→ FMP 10-K Filings (last 10 days)
                  ∩
             sp500_tickers.json (local S&P 500 list)
                  │
                  ▼
             Matches ──→ run_memo.py {ticker} (batches of 2)
```

### Market Data Upload (`run_market_data.py`)

```
  run_market_data.py (daily cron)
        │
        ├──→ FMP API → calculate metrics in Python ──→ R2: Dashboard/{yyyy}/{MM}/...
        │
        └──→ FMP API → 252 trading days closing prices ──→ R2: Daily Price/{yyyy}/{MM}/...
```

---

## R2 Bucket Structure

```
<bucket>/
├── _index.json              ← type → ticker → latest key (maintained
│                              incrementally; rebuild with build_r2_index.py)
├── Memo/{yyyy}/{MM}/{TICKER}/{TICKER} {MM}-{MMM} {dd}, {yyyy}-{yy}.json
├── Quarterly/{yyyy}/{MM}/{TICKER}/Quarterly {TICKER} {MM}-{MMM} {dd}, {yyyy}-{yy}.json
├── Daily Price/{yyyy}/{MM}/DailyPrices {MM-DD-yy}.json
└── Dashboard/{yyyy}/{MM}/dashboard {MM-DD-yy}.json
```

---

## Deployment (Railway cron, or any scheduler)

Two services, both defined by Dockerfiles in this repo. Service-specific
settings (Dockerfile path, start command, cron schedule, env vars) are
configured in the Railway UI — `railway.toml` documents the mapping.

| Job | Image | Script | Schedule | What it does |
|-----|-------|--------|----------|-------------|
| Market Data | `Dockerfile.market_data` | `python run_market_data.py` | Daily | FMP → metrics + prices → R2 |
| Master | `Dockerfile` | `python run_master.py` | Daily | Detect new earnings/10-Ks → run pipelines |

Both services need the environment variables listed in
[.env.template](.env.template).
