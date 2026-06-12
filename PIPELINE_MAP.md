# Ape Axiom — Full Memo Pipeline Architecture

This map covers the full-memo pipeline (`run_pipeline()` in `pipeline/orchestrator.py`).
Sibling entry points not covered here: `run_quarterly.py` (quarterly earnings pipeline),
`run_market_data.py` (FMP market data → R2), `build_r2_index.py` (R2 index utility).

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         ENTRY POINTS                                            │
│  run_memo.py (CLI, --upload → R2)     ─┐                                        │
│  run_master.py (daily automation)     ─┤──▶  run_pipeline(ticker, mode, years,  │
│  run_batch_sp500.py (S&P 500 batch)   ─┤       quarters, stop_after, ...)       │
│  tests/test_full_pipeline.py          ─┤     orchestrator.py                    │
│  tests/test_single_section.py         ─┘                                        │
└────────────────────────────────────┬────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STAGE 1: FETCH ALL DATA                                    ~2-10s  (parallel)  │
│  data_fetcher.py → fetch_all_data() → PipelineData                              │
│                                                                                 │
│  ┌──────────────────────┐    ┌──────────────────────┐                           │
│  │   SEC EDGAR (XBRL)   │    │    FMP API (HTTP)    │                           │
│  │ ┌────────────────┐   │    │ ┌────────────────┐   │                           │
│  │ │ Annual IS/BS/CF│   │    │ │ Estimates      │   │                           │
│  │ │ Quarterly      │   │    │ │ Surprises      │   │                           │
│  │ │ Ratios/Growth  │   │    │ │ Profile        │   │                           │
│  │ │ Owner Earnings │   │    │ │ Peers (weak)   │   │                           │
│  │ │ Segments       │   │    │ │ Key Metrics    │   │                           │
│  │ │ Profile/SIC    │   │    │ │ IS/BS/CF (fbk) │   │                           │
│  │ │ Sector KPIs    │   │    │ └────────────────┘   │                           │
│  │ │ 10-K / 10-Q    │   │    └──────────────────────┘                           │
│  │ └────────────────┘   │                                                       │
│  └──────────────────────┘                                                       │
│                                                                                 │
│  + STAGE 1B: _backfill_sec_from_fmp()  — fill SEC XBRL gaps with FMP data       │
└────────────────────────────────────┬────────────────────────────────────────────┘
                                     │ PipelineData
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STAGE 2: TRANSFORM + BUILD FACT SHEETS                      <1s  (sequential)  │
│                                                                                 │
│  ┌─────────────┐    ┌──────────────────┐    ┌───────────────────────┐           │
│  │ Pivot       │    │ Normalize        │    │ 2B. Build Quant Facts │           │
│  │ transforms  │──▶ │ Segments         │──▶ │ quantitative.py       │           │
│  │ Annual      │    │ Currency         │    │ → fact sheet keyed    │           │
│  │ Quarterly   │    └──────────────────┘    │   _meta + s2-s13      │           │
│  │ Estimates   │                            └───────────────────────┘           │
│  │ Surprises   │                                                                │
│  │ OwnerEarn   │                                                                │
│  └─────────────┘                                                                │
│                                                        │                        │
│                                                        ▼                        │
│                                             ┌───────────────────────┐           │
│                                             │ 2C. Source Registry   │           │
│                                             │ source_registry.py    │           │
│                                             │ • Inject s1_identity  │           │
│                                             │ • Compute _current_*  │           │
│                                             │   multiples (P/E etc) │           │
│                                             │ • Build citations     │           │
│                                             │   [1]-[N], [F], [M]   │           │
│                                             └───────────────────────┘           │
└────────────────────────────────────┬────────────────────────────────────────────┘
                                     │ formatted_facts
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STAGE 2D: PEER SELECTION                                  ~10-45s  (parallel)  │
│  peer_selection.py → run_peer_pipeline()                                        │
│                                                                                 │
│  ┌───────────────┐   ┌──────────────────┐   ┌────────────────────────┐          │
│  │ 1. LLM picks  │──▶│ 2. Fetch peer    │──▶│ 3. Refine & rebuild    │          │
│  │    7-10 peers │   │    financials    │   │    • Sector KPI score  │          │
│  │    (12 cands, │   │    (FMP, conc.)  │   │    • Keep top 5 by     │          │
│  │    gpt-5-mini)│   └──────────────────┘   │      KPI coverage      │          │
│  └───────────────┘                          │    • Rebuild comp tbls │          │
│                                             │    • REIT P/FFO comps  │          │
│                                             │    • Update medians    │          │
│                                             └────────────────────────┘          │
│                                                                                 │
│  Mutations → fact_sheet:                                                        │
│    s12_peer_benchmarking.{profitability,growth,valuation,...}_comps             │
│    s13_valuation.peer_valuation_medians                                         │
│    _sec_sector_kpis.kpis (enriched)                                             │
│                                                                                 │
│  stop_after="peer_rebuild" ──▶ EARLY EXIT                                       │
└────────────────────────────────────┬────────────────────────────────────────────┘
                                     │ enriched fact_sheet
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STAGE 3: DISTRIBUTE + WRITE SECTIONS                      ~2-5 min             │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────┐                    │
│  │ 3-DIST: distribute_sections()                           │                    │
│  │ distributors.py                                         │                    │
│  │ • Detect sector family → sector-aware S4/S7/S10/S11     │                    │
│  │   schemas, titles, and analysis guidance                │                    │
│  │ • Build 14 section inputs (schema + facts +             │                    │
│  │   precomputed tables)                                   │                    │
│  │ • Agent prompts: _build_agent_prompts() (orchestrator)  │                    │
│  │   renders Jinja2 templates + 10-K/10-Q section text     │                    │
│  │                                                         │                    │
│  │ stop_after="distribute" ──▶ EARLY EXIT                  │                    │
│  └─────────────────────────────────────────────────────────┘                    │
│                                   │                                             │
│                                   ▼                                             │
│  ┌─────────────────────────────────────────────────────────┐                    │
│  │ 3A: RESEARCH AGENTS (parallel)                          │                    │
│  │ writers.py → call_research_agent()                      │                    │
│  │                                                         │                    │
│  │  Agent 1 ─── Foundation & business analysis (→ S2-S9)   │                    │
│  │  Agent 2 ─── Deep financial analysis (→ S10-S11)        │                    │
│  │  Agent 3 ─── Investment decision & risk (→ S12-S14)     │                    │
│  │                                                         │                    │
│  │  Input: 10-K/10-Q sections (business, MD&A, risk        │                    │
│  │         factors) + fact-sheet summaries                 │                    │
│  │  Model: gpt-5-mini (RESEARCH_AGENT_MODEL)               │                    │
│  └─────────────────────────────────────────────────────────┘                    │
│                                   │                                             │
│                                   ▼                                             │
│  ┌─────────────────────────────────────────────────────────┐                    │
│  │ 3B: BODY SECTIONS (parallel)                            │                    │
│  │ writers.py → write_section() × 11                       │                    │
│  │                                                         │                    │
│  │  S2  Company Overview        S7  Customer Analysis*     │                    │
│  │  S3  History & Milestones    S8  Mgmt & Cap Allocation  │                    │
│  │  S4  Product & Technology*   S9  Growth & Catalysts     │                    │
│  │  S5  Competitive Moats       S10 Financial Analysis*    │                    │
│  │  S6  Industry Dynamics       S11 Peer Benchmarking*     │                    │
│  │  S13 Risk Assessment         (* sector-aware schema)    │                    │
│  │                                                         │                    │
│  │  Input: section schema + quant facts + agent outputs    │                    │
│  │  Model: gpt-5-mini (WRITER_MODEL, default)              │                    │
│  │  Output: structured JSON per schema                     │                    │
│  └─────────────────────────────────────────────────────────┘                    │
│                                   │                                             │
│                                   ▼                                             │
│  ┌─────────────────────────────────────────────────────────┐                    │
│  │ 3C: SYNTHESIS SECTIONS (parallel)                       │                    │
│  │                                                         │                    │
│  │  S12 Valuation Analysis        ─┐                       │                    │
│  │  S1  Exec Summary & Thesis      ├── read full body as   │                    │
│  │  S14 Conclusion                ─┘   context (S2-S11)    │                    │
│  │                                                         │                    │
│  │  Model: gpt-5-mini (WRITER_MODEL)                       │                    │
│  └─────────────────────────────────────────────────────────┘                    │
└────────────────────────────────────┬────────────────────────────────────────────┘
                                     │ section_outputs (S1-S14 structured JSON)
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STAGE 3.5: FACT-CHECK                                       <1s  (sequential)  │
│  fact_check.py → fact_check_quarterly() — run per section output                │
│                                                                                 │
│  • Deterministic regex checker (no LLM call)                                    │
│  • Extracts numeric claims ($X.XB, X.X%, X.Xx) from prose and matches them      │
│    against a value index built from raw facts + precomputed tables              │
│  • Auto-patches numbers that are close but wrong (within 5% tolerance)          │
│  • Log: patches_applied, verified_claims, suspicious_claims                     │
└────────────────────────────────────┬────────────────────────────────────────────┘
                                     │ patched section_outputs
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STAGE 4: ASSEMBLY                                           <1s  (sequential)  │
│  assembly.py → assemble_memo()                                                  │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────┐                │
│  │ 1. Extract section prose from structured JSON               │                │
│  │ 2. Detect sector: financial / asset-heavy / REIT / normal   │                │
│  │ 3. Fair value via valuation/ dispatcher — picks DCF,        │                │
│  │    bank equity (P/TBV), DDM, NAV, or peer multiples by      │                │
│  │    industry; REITs use the P/FFO peer-median path           │                │
│  │ 4. DCF scenario tables (if not skip-DCF sector):            │                │
│  │    • Dynamic 5/7/10-yr horizon (by revenue CAGR)            │                │
│  │    • NOPAT → FCFF projection → discount at WACC             │                │
│  │    • Terminal value (Gordon growth)                         │                │
│  │    • EV → subtract net debt → ÷ shares = fair value/share   │                │
│  │    • Bull / Base / Bear + WACC × growth sensitivity matrix  │                │
│  │ 5. Extract scores: moat, growth, quality (0-100)            │                │
│  │ 6. Render markdown tables (DCF, sensitivity, peer comps,    │                │
│  │    sector KPIs)                                             │                │
│  │ 7. Build data_block (scores + fair value + method)          │                │
│  │ 8. Prepend "# DATA BLOCK" dump at the top of the memo       │                │
│  │ 9. Join sections 1-15 (S15 = Sources & Citations)           │                │
│  │    → formatted_memo                                         │                │
│  └─────────────────────────────────────────────────────────────┘                │
│                                                                                 │
│  Output: MemoAssembly                                                           │
│    .formatted_memo  ─── Full markdown text (data block + S1-S15)                │
│    .data_block      ─── {company, ticker, moat, scores, fair_value, method}     │
│    .section_map     ─── {section_N: rendered_text}                              │
│    .word_count      ─── ~18K-23K words                                          │
│    .warnings        ─── Validation/debug messages                               │
└────────────────────────────────────┬────────────────────────────────────────────┘
                                     │ MemoAssembly
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STAGE 5: OUTPUT FORMATTING                                 ~1-2s  (sequential) │
│  formatters.py                                                                  │
│                                                                                 │
│  ┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐            │
│  │   Markdown        │  │   HTML (styled)   │  │   PDF (optional)  │            │
│  │   format_markdown │  │   format_html     │  │   WeasyPrint      │            │
│  └───────────────────┘  └───────────────────┘  └───────────────────┘            │
│                                                                                 │
│  ┌───────────────────────────┐  ┌──────────────────────────────┐                │
│  │   Scorecard JSON          │  │   Discord Scorecard          │                │
│  │   build_scorecard_json()  │  │   format_discord_            │                │
│  │                           │  │     scorecard_v2()           │                │
│  │   40+ fields:             │  │                              │                │
│  │   • Identity/pricing      │  │   Compact text from the      │                │
│  │   • Multiples + peers     │  │   scorecard JSON:            │                │
│  │   • Scores (0-100)        │  │   • Price / Mkt Cap          │                │
│  │   • Fair value / MoS      │  │   • Multiples                │                │
│  │   • Financials / margins  │  │   • Scores                   │                │
│  │   • Thesis / verdict      │  │   • Thesis excerpt           │                │
│  │                           │  │                              │                │
│  │   mode="website" strips:  │  │   <2000 chars                │                │
│  │     fair_value*, MoS,     │  └──────────────────────────────┘                │
│  │     upside_pct            │                                                  │
│  └───────────────────────────┘                                                  │
└────────────────────────────────────┬────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  FINAL OUTPUT: MemoResult                                                       │
│                                                                                 │
│  .formatted_facts    ─── Enriched quantitative fact sheet                       │
│  .section_outputs    ─── Raw LLM section JSONs (S1-S14)                         │
│  .memo_body          ─── Full formatted memo (markdown)                         │
│  .markdown           ─── Memo as markdown file                                  │
│  .html               ─── Styled HTML                                            │
│  .pdf                ─── PDF (WeasyPrint, if installed)                         │
│  .scorecard_json     ─── Dashboard JSON (40+ fields)                            │
│  .discord_scorecard  ─── Discord scorecard text                                 │
│  .data_block         ─── Scores + fair value + method                           │
│  .stage_timings      ─── {fetch, transform, quantitative, format_registry,      │
│                           peer_selection, distribute, writing, fact_check,      │
│                           assembly, formatting}                                 │
│  .errors             ─── Non-fatal errors                                       │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## Supporting Modules

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  sec/                             │  valuation/                                 │
│  ├── statements.py  (XBRL fetch)  │  ├── __init__.py  (dispatcher)              │
│  ├── ratios.py      (computed)    │  ├── dcf.py       (standard DCF)            │
│  ├── segments.py    (rev splits)  │  ├── peer_multiples.py                      │
│  ├── profile.py     (SIC mapping) │  ├── bank_equity.py (P/TBV)                 │
│  ├── filings.py     (10-K/10-Q)   │  ├── ddm.py       (dividend discount)       │
│  ├── mapper.py      (XBRL tags)   │  ├── nav.py       (net asset value)         │
│  ├── client.py      (EDGAR HTTP)  │  └── industry_config.py                     │
│  └── sectors/                     │                                             │
│      ├── banks.py     (NIM, NPL)  │  prompts/                                   │
│      ├── insurance.py (comb ratio)│  ├── research_agent_1.jinja2 (foundation)   │
│      ├── reits.py     (FFO/AFFO)  │  ├── research_agent_2.jinja2 (financials)   │
│      ├── tech.py      (Rule of 40)│  └── research_agent_3.jinja2 (decision)     │
│      ├── energy.py                │                                             │
│      ├── healthcare.py            │  config/                                    │
│      ├── retail.py                │  ├── settings.py   (env + model defaults)   │
│      ├── industrials.py           │  ├── fmp_client.py (FMP HTTP)               │
│      ├── consumer_staples.py      │  └── r2.py         (R2 upload + index)      │
│      ├── utilities.py             │                                             │
│      ├── materials.py             │                                             │
│      └── _utils.py                │                                             │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## Sector Detection & Routing

```
                    _detect_sector(dcf_anchors, industry)
            → (is_financial, is_asset_heavy, is_reit, industry)
                                │
           ┌────────────────────┼────────────────────┐
           │                    │                    │
     is_financial          is_asset_heavy         normal
     (banks, insurance,    (shipping, oil & gas, (tech, health,
      REITs, credit)        metals & mining)      consumer, etc.)
           │                    │                    │
     Skip DCF              Skip DCF              Full DCF
     Peer multiples        Peer multiples        Dynamic 5/7/10-yr
     P/E, P/B, P/TBV       EV/EBITDA, P/FCF      NOPAT→FCFF projection
     (REITs: P/FFO)                              Terminal value, WACC
```

## Section Writing Order

```
     3A: Research Agents (parallel)
     ┌──────┬──────┬──────┐
     │ Ag1  │ Ag2  │ Ag3  │   Read 10-K/10-Q text
     └──┬───┴──┬───┴──┬───┘
        │      │      │
        ▼      ▼      ▼
     3B: Body Sections (parallel)
     ┌──┬──┬──┬──┬──┬──┬──┬──┬───┬───┬───┐
     │S2│S3│S4│S5│S6│S7│S8│S9│S10│S11│S13│
     └──┴──┴──┴──┴──┴──┴──┴──┴───┴───┴─┬─┘
                                        │ full body as context
                                        ▼
     3C: Synthesis Sections (parallel)
     ┌─────┬─────┬─────┐
     │ S12 │ S1  │ S14 │   Read all body sections
     └─────┴─────┴─────┘
```

## Timing Breakdown (median ~4 min total; measured across ~1,000 S&P 500 runs)

```
Stage 1   Fetch            ██░░░░░░░░░░░░░░░░░░░░░░░░░░░░   ~2-10s (up to ~40s on slow EDGAR)
Stage 2   Transform+Facts  ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░    <1s
Stage 2D  Peer Selection   ████░░░░░░░░░░░░░░░░░░░░░░░░░░   ~10-45s
Stage 3   Write (3A+3B+3C) ██████████████████████████░░░░   ~2-5 min  (dominant stage)
Stage 3.5 Fact-check       ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░    <1s  (deterministic)
Stage 4   Assembly         ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░    <1s  (deterministic)
Stage 5   Format           ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░   ~1-2s (+PDF when weasyprint installed)
```

Stage names map to `MemoResult.stage_timings` keys: `fetch`, `transform`,
`quantitative`, `format_registry`, `peer_selection`, `distribute`, `writing`,
`fact_check`, `assembly`, `formatting`.
