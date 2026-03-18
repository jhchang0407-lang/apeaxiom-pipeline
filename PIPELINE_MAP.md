# OpenClaw Pipeline Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         ENTRY POINTS                                            │
│  test_full_pipeline.py  ─┐                                                      │
│  modal_app.py           ─┤──▶  run_pipeline(ticker, mode, years, quarters)      │
│  test_single_section.py ─┘     orchestrator.py                                  │
└────────────────────────────────────┬────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STAGE 1: FETCH ALL DATA                                    ~3-10s  (parallel)  │
│  data_fetcher.py → fetch_all_data() → PipelineData                              │
│                                                                                 │
│  ┌──────────────────────┐    ┌──────────────────────┐                           │
│  │   SEC EDGAR (XBRL)   │    │     FMP API (HTTP)    │                          │
│  │  ┌────────────────┐  │    │  ┌────────────────┐   │                          │
│  │  │ Annual IS/BS/CF│  │    │  │ Estimates      │   │                          │
│  │  │ Quarterly      │  │    │  │ Surprises      │   │                          │
│  │  │ Ratios/Growth  │  │    │  │ Profile        │   │                          │
│  │  │ Owner Earnings │  │    │  │ Peers (weak)   │   │                          │
│  │  │ Segments       │  │    │  │ Key Metrics    │   │                          │
│  │  │ Profile/SIC    │  │    │  │ IS/BS/CF (fbk) │   │                          │
│  │  │ Sector KPIs    │  │    │  └────────────────┘   │                          │
│  │  │ 10-K / 10-Q    │  │    └──────────────────────┘                           │
│  │  └────────────────┘  │                                                       │
│  └──────────────────────┘    + _backfill_sec_from_fmp()                          │
└────────────────────────────────────┬────────────────────────────────────────────┘
                                     │ PipelineData
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STAGE 2: TRANSFORM + BUILD FACT SHEETS                    ~5-15s  (sequential) │
│                                                                                 │
│  ┌─────────────┐    ┌──────────────────┐    ┌───────────────────────┐           │
│  │ 2A. Pivot   │    │ 2B. Normalize    │    │ 2C. Build Quant Facts │           │
│  │ Annual      │──▶ │ Segments         │──▶ │ quantitative.py       │           │
│  │ Quarterly   │    │ Currency         │    │ → 13-section fact     │           │
│  │ Estimates   │    │ Backfill         │    │   sheet (s1-s13)      │           │
│  │ Surprises   │    └──────────────────┘    └───────────┬───────────┘           │
│  │ OwnerEarn   │                                        │                       │
│  └─────────────┘                                        ▼                       │
│                                              ┌───────────────────────┐           │
│                                              │ 2D. Source Registry   │           │
│                                              │ source_registry.py    │           │
│                                              │ • Inject s1_identity  │           │
│                                              │ • Compute _current_*  │           │
│                                              │   multiples (P/E etc) │           │
│                                              │ • Build citations     │           │
│                                              │   [1]-[N], [F], [M]   │           │
│                                              └───────────┬───────────┘           │
└────────────────────────────────────────────────────────────┬─────────────────────┘
                                     │ formatted_facts
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STAGE 2E: PEER SELECTION                                  ~15-30s  (parallel)  │
│  peer_selection.py → run_peer_pipeline()                                        │
│                                                                                 │
│  ┌──────────────┐   ┌──────────────────┐   ┌────────────────────────┐           │
│  │ 1. LLM picks │   │ 2. Fetch peer    │   │ 3. Refine & rebuild   │           │
│  │    5-8 peers  │──▶│    financials    │──▶│    • Sector KPI score │           │
│  │    (GPT-4o)   │   │    (SEC + FMP)   │   │    • Top 5 coverage   │           │
│  └──────────────┘   └──────────────────┘   │    • Rebuild comp tbls │           │
│                                             │    • REIT P/FFO comps  │           │
│                                             │    • Update medians    │           │
│  Mutations → fact_sheet:                    └────────────────────────┘           │
│    s12_peer_benchmarking.{profitability,growth,valuation,...}_comps              │
│    s13_valuation.peer_valuation_medians                                         │
│    _sec_sector_kpis.kpis (enriched)                                             │
│                                                                                 │
│  stop_after="peer_rebuild" ──▶ EARLY EXIT                                       │
└────────────────────────────────────┬────────────────────────────────────────────┘
                                     │ enriched fact_sheet
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STAGE 3: DISTRIBUTE + WRITE SECTIONS                     ~60-120s              │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────┐                    │
│  │ 3-DIST: distribute_sections()                           │                    │
│  │ distributors.py                                         │                    │
│  │ • Detect sector family → sector-specific S10 schema     │                    │
│  │ • Build 14 section inputs (schema + facts + context)    │                    │
│  │ • Build agent prompts (Jinja2 + 10-K/10-Q text)         │                    │
│  │                                                         │                    │
│  │ stop_after="distribute" ──▶ EARLY EXIT                  │                    │
│  └────────────────────────────────┬────────────────────────┘                    │
│                                   ▼                                             │
│  ┌─────────────────────────────────────────────────────────┐                    │
│  │ 3A: RESEARCH AGENTS (parallel)                 30-50s   │                    │
│  │ writers.py → call_research_agent()                      │                    │
│  │                                                         │                    │
│  │  Agent 1 ─── Growth, competitive advantages             │                    │
│  │  Agent 2 ─── Management, capital allocation             │                    │
│  │  Agent 3 ─── Catalysts, operational drivers             │                    │
│  │                                                         │                    │
│  │  Input: 10-K/10-Q full text + fact sheet excerpt        │                    │
│  │  Model: gpt-4o-mini                                     │                    │
│  └────────────────────────────────┬────────────────────────┘                    │
│                                   ▼                                             │
│  ┌─────────────────────────────────────────────────────────┐                    │
│  │ 3B: BODY SECTIONS (parallel)                   30-60s   │                    │
│  │ writers.py → write_section() × 11                       │                    │
│  │                                                         │                    │
│  │  S2  Company Overview        S7  Customers/Suppliers    │                    │
│  │  S3  Products & Markets      S8  Management & CapAlloc  │                    │
│  │  S4  Competitive Position    S9  Growth & Catalysts     │                    │
│  │  S5  Moats & Advantages      S10 Financial Analysis     │                    │
│  │  S6  Industry Landscape      S11 Peer Benchmarking      │                    │
│  │  S13 Risk Assessment                                    │                    │
│  │                                                         │                    │
│  │  Input: section schema + quant facts + agent outputs    │                    │
│  │  Model: gpt-4o (default)                                │                    │
│  │  Output: structured JSON per schema                     │                    │
│  └────────────────────────────────┬────────────────────────┘                    │
│                                   ▼                                             │
│  ┌─────────────────────────────────────────────────────────┐                    │
│  │ 3C: SYNTHESIS SECTIONS (parallel)              10-20s   │                    │
│  │                                                         │                    │
│  │  S12 Valuation & Price Target  ─┐                       │                    │
│  │  S1  Investment Thesis          ├── read full body as   │                    │
│  │  S14 Investment Summary        ─┘   context (S2-S11)    │                    │
│  │                                                         │                    │
│  │  Model: gpt-4o                                          │                    │
│  └────────────────────────────────┬────────────────────────┘                    │
└────────────────────────────────────┬────────────────────────────────────────────┘
                                     │ section_outputs (S1-S14 structured JSON)
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STAGE 3.5: FACT-CHECK                                     ~5-20s  (sequential) │
│  fact_check.py → fact_check_quarterly()                                         │
│                                                                                 │
│  • Compare prose claims against quantitative ground truth                       │
│  • Auto-patch factual errors in section text                                    │
│  • Log: patches_applied, verified_claims, suspicious_claims                     │
└────────────────────────────────────┬────────────────────────────────────────────┘
                                     │ patched section_outputs
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STAGE 4: ASSEMBLY                                        ~10-30s  (sequential) │
│  assembly.py → assemble_memo()                                                  │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────┐                │
│  │ 1. Extract section prose from structured JSON               │                │
│  │ 2. Detect sector: financial / asset-heavy / REIT / normal   │                │
│  │ 3. Compute DCF (if not skip-DCF sector):                    │                │
│  │    • 5yr revenue projection → FCF → discount at WACC        │                │
│  │    • Terminal value (Gordon growth)                          │                │
│  │    • EV → subtract net debt → ÷ shares = fair value/share   │                │
│  │    • Bull / Base / Bear scenarios                            │                │
│  │ 4. OR peer-multiple valuation (banks, REITs, etc.)          │                │
│  │    • P/E, P/B, P/FFO implied from peer medians              │                │
│  │ 5. Extract scores: moat, growth, quality (0-100)            │                │
│  │ 6. Render markdown tables (DCF, sensitivity, peer comps)    │                │
│  │ 7. Build data_block (scores + fair value + method)          │                │
│  │ 8. Render DATA SUMMARY header (at-a-glance metrics)         │                │
│  │ 9. Join all sections → formatted_memo                       │                │
│  └─────────────────────────────────────────────────────────────┘                │
│                                                                                 │
│  Output: MemoAssembly                                                           │
│    .formatted_memo  ─── Full markdown text (data summary + S1-S15)              │
│    .data_block      ─── {company, ticker, moat, scores, fair_value, method}     │
│    .section_map     ─── {section_N: rendered_text}                              │
│    .word_count      ─── ~12K-18K words                                          │
│    .warnings        ─── Validation/debug messages                               │
└────────────────────────────────────┬────────────────────────────────────────────┘
                                     │ MemoAssembly
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STAGE 5: OUTPUT FORMATTING                                ~5-10s  (sequential) │
│  formatters.py                                                                  │
│                                                                                 │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐              │
│  │   Markdown        │  │   HTML (styled)   │  │   PDF            │              │
│  │   format_markdown │  │   format_html     │  │   WeasyPrint     │              │
│  └──────────────────┘  └──────────────────┘  └──────────────────┘              │
│                                                                                 │
│  ┌──────────────────────────┐  ┌────────────────────────┐                      │
│  │   Scorecard JSON          │  │   Discord Scorecard    │                      │
│  │   build_scorecard_json()  │  │   format_discord_v2()  │                      │
│  │                           │  │                        │                      │
│  │   40+ fields:             │  │   Compact text:        │                      │
│  │   • Identity/pricing      │  │   • Price / Mkt Cap    │                      │
│  │   • Multiples + peers     │  │   • Multiples          │                      │
│  │   • Scores (0-100)        │  │   • Scores             │                      │
│  │   • Fair value / MoS      │  │   • Thesis excerpt     │                      │
│  │   • Financials / margins  │  │                        │                      │
│  │   • Thesis / verdict      │  │   ~500 chars           │                      │
│  │                           │  └────────────────────────┘                      │
│  │   mode="website" strips:  │                                                  │
│  │     fair_value, MoS,      │                                                  │
│  │     upside_pct            │                                                  │
│  └──────────────────────────┘                                                  │
└────────────────────────────────────┬────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  FINAL OUTPUT: MemoResult                                                       │
│                                                                                 │
│  .formatted_facts    ─── Enriched quantitative fact sheet                       │
│  .section_outputs    ─── Raw LLM section JSONs (S1-S14)                        │
│  .memo_body          ─── Full formatted memo (markdown)                        │
│  .markdown           ─── Memo as markdown file                                 │
│  .html               ─── Styled HTML                                           │
│  .pdf                ─── PDF (WeasyPrint)                                      │
│  .scorecard_json     ─── Dashboard JSON (40+ fields)                           │
│  .discord_scorecard  ─── Discord embed text                                    │
│  .data_block         ─── Scores + fair value + method                          │
│  .stage_timings      ─── {fetch: Xs, transform: Xs, write: Xs, ...}           │
│  .errors             ─── Non-fatal errors                                      │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## Supporting Modules

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  sec/                              │  valuation/                                │
│  ├── statements.py  (XBRL fetch)   │  ├── __init__.py  (dispatcher)             │
│  ├── ratios.py      (computed)     │  ├── dcf.py       (standard DCF)           │
│  ├── segments.py    (rev splits)   │  ├── peer_multiples.py                     │
│  ├── profile.py     (SIC mapping)  │  ├── bank_equity.py (P/TBV)               │
│  ├── filings.py     (10-K/10-Q)   │  ├── ddm.py       (dividend discount)      │
│  ├── mapper.py      (XBRL tags)    │  ├── nav.py       (net asset value)        │
│  ├── client.py      (EDGAR HTTP)   │  └── industry_config.py                    │
│  └── sectors/                      │                                            │
│      ├── banking.py   (NIM, NPL)   │  prompts/                                  │
│      ├── reits.py     (FFO/AFFO)   │  ├── agent_1.j2   (growth/moats)           │
│      ├── insurance.py (comb ratio) │  ├── agent_2.j2   (mgmt/capalloc)          │
│      ├── tech.py      (SaaS/cloud) │  └── agent_3.j2   (catalysts)              │
│      ├── healthcare.py             │                                            │
│      ├── energy.py                 │                                            │
│      ├── utilities.py              │                                            │
│      ├── retail.py                 │                                            │
│      ├── consumer_staples.py       │                                            │
│      ├── industrials.py            │                                            │
│      ├── materials.py              │                                            │
│      └── telecom.py               │                                            │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## Sector Detection & Routing

```
                    _detect_sector(dcf_anchors, industry)
                                │
           ┌────────────────────┼────────────────────┐
           │                    │                     │
     is_financial          is_asset_heavy         is_normal
     (banks, ins,          (utilities,            (tech, health,
      REITs)               materials)             consumer, etc.)
           │                    │                     │
     Skip DCF              Skip DCF              Full DCF
     Peer multiples        Peer multiples         5yr FCF projection
     P/E, P/B, P/FFO      EV/EBITDA, P/FCF       Terminal value
                                                  WACC discount
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

## Timing Breakdown (~2-3 min total)

```
Stage 1  Fetch         ████░░░░░░░░░░░░░░░░░░░░░░░░░░   3-10s
Stage 2  Transform     ██████░░░░░░░░░░░░░░░░░░░░░░░░   5-15s
Stage 2E Peers         █████████░░░░░░░░░░░░░░░░░░░░░  15-30s
Stage 3A Agents        █████████████████░░░░░░░░░░░░░  30-50s
Stage 3B Writers       █████████████████████░░░░░░░░░  30-60s
Stage 3C Synthesis     ██████░░░░░░░░░░░░░░░░░░░░░░░░  10-20s
Stage 3.5 Fact-check   ██████░░░░░░░░░░░░░░░░░░░░░░░░   5-20s
Stage 4  Assembly      ████████░░░░░░░░░░░░░░░░░░░░░░  10-30s
Stage 5  Format        ████░░░░░░░░░░░░░░░░░░░░░░░░░░   5-10s
```
