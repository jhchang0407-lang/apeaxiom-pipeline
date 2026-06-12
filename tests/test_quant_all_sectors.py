"""Run quantitative pipeline (Stages 1-2) across all sectors.

Runs fetch → quantitative → format_registry for one representative ticker
per GICS sector, then dumps trace output for inspection.

Usage:
    python tests/test_quant_all_sectors.py
    python tests/test_quant_all_sectors.py --ticker AAPL   # single ticker
"""

import asyncio
import json
import os
import sys
import time

# Representative tickers by sector (chosen for data quality + familiarity)
SECTOR_TICKERS = {
    # Sectors WITH dedicated KPI extractors
    "Technology":        "MSFT",
    "Banking":           "JPM",
    "Insurance":         "BRK-B",
    "REITs":             "PLD",
    "Retail":            "WMT",
    "Energy":            "XOM",
    "Healthcare":        "JNJ",
    "Industrials":       "HON",
    # Sectors WITHOUT dedicated KPI extractors (generic path)
    "Consumer Staples":  "PG",
    "Utilities":         "NEE",
    "Telecom":           "VZ",
    "Materials":         "LIN",
    "Consumer Disc.":    "MCD",
}

_HERE = os.path.dirname(os.path.abspath(__file__))


async def run_quant_pipeline(ticker: str, label: str) -> dict:
    """Run stages 1-2 only: fetch → quantitative → format_registry.

    Returns a summary dict with validation results.
    """
    from pipeline.data_fetcher import fetch_all_data
    from pipeline.quantitative import build_quantitative_facts
    from pipeline.clean_quantitative import clean_quantitative_facts
    from pipeline.source_registry import build_source_registry
    from pipeline.transforms import (
        pivot_annual,
        pivot_quarterly,
        pivot_estimates,
        pivot_surprises,
        pivot_owner_earnings,
        normalize_segments,
        inject_currency,
        aggregate_quantitative,
    )
    from pipeline.trace import PipelineTrace

    out_dir = os.path.join(_HERE, "test_output", f"{ticker}_quant")
    os.makedirs(out_dir, exist_ok=True)

    trace = PipelineTrace(ticker=ticker, output_dir=out_dir, enabled=True)

    # ── STAGE 1: FETCH ──
    t0 = time.time()
    data = await fetch_all_data(ticker, years=5, quarters=8)
    fetch_time = time.time() - t0

    trace.checkpoint("fetch", {
        "sec_annual_count": len(data.sec_financials) if data.sec_financials else 0,
        "sec_quarterly_count": len(data.sec_quarterly) if data.sec_quarterly else 0,
        "fmp_profile": data.fmp_profile or {},
        "sec_segments": data.sec_segments or {},
        "fetch_errors": data.fetch_errors,
    })

    # ── STAGE 1B: BACKFILL SEC GAPS WITH FMP ──
    from pipeline.orchestrator import _backfill_sec_from_fmp
    try:
        fmp_fills = _backfill_sec_from_fmp(data)
        if fmp_fills > 0:
            print(f"  ✓ FMP backfill: {fmp_fills} fields filled")
    except Exception as e:
        print(f"  ⚠ FMP backfill error: {e}")

    # ── STAGE 2A: TRANSFORM (mirrors orchestrator exactly) ──
    t1 = time.time()
    annual = pivot_annual(data.sec_financials, data.sec_segments)
    quarterly = pivot_quarterly(data.sec_quarterly)
    estimates = pivot_estimates(data.fmp_estimates)
    surprises = pivot_surprises(data.fmp_surprises)
    owner_earnings = pivot_owner_earnings(data.sec_financials)

    quant_data = aggregate_quantitative(
        annual=annual,
        quarterly=quarterly,
        estimates=estimates,
        surprises=surprises,
        owner_earnings=owner_earnings,
        peers=data.fmp_peers,
    )

    quant_data = normalize_segments(quant_data)
    quant_data = inject_currency(quant_data, data.sec_financials, data.fmp_profile)

    # ── STAGE 2B: QUANTITATIVE ──
    fact_sheet = build_quantitative_facts(quant_data)
    fact_sheet = clean_quantitative_facts(fact_sheet)

    trace.checkpoint("quantitative", fact_sheet)

    # ── STAGE 2C: FORMAT REGISTRY ──
    registry_result = build_source_registry(
        fact_sheet=fact_sheet,
        fmp_profile=data.fmp_profile,
        sec_profile=data.sec_profile,
        filing_10k=data.filing_10k,
        filing_10q=data.filing_10q,
    )
    fs = registry_result["fact_sheet"]
    quant_time = time.time() - t1

    # Inject extras (sector KPIs, etc.) like orchestrator does
    fs["_sec_sector_kpis"] = data.sec_sector_kpis

    trace.checkpoint("format_registry", fs)

    # ── STAGE 3: DETERMINISTIC TABLE BUILDERS (Sections 10 & 12) ──
    from pipeline.distributors import (
        build_financial_table_rows,
        build_peer_comp_tables,
    )

    # Build the raw data dict that distributors expects (mirrors distributors.py L3353-3359)
    raw_inc = fact_sheet.get("s11_income_statement", {})
    raw_margins = fact_sheet.get("s5_subject_margins", {}) or raw_inc
    raw_cf = fact_sheet.get("s11_cash_flow", {})
    raw_bal = fact_sheet.get("s11_balance_sheet", {})
    raw_returns = fact_sheet.get("s11_returns", {})
    raw_wc = fact_sheet.get("s7_working_capital") or {}
    raw_cap_alloc = fact_sheet.get("s9_capital_allocation", {})
    raw_rd = fact_sheet.get("s4_rd", {}) or fact_sheet.get("s5_share_data", {})
    raw_share_d = fact_sheet.get("s5_share_data", {})

    # Determine annual years from revenue data
    rev_data = raw_inc.get("revenue_usd_m", {})
    annual_years = sorted(rev_data.keys()) if isinstance(rev_data, dict) else []

    fin_tables = build_financial_table_rows({
        "incStmt": raw_inc, "margins": raw_margins, "cfStmt": raw_cf,
        "balSheet": raw_bal, "returns": raw_returns, "wc": raw_wc,
        "capAlloc": raw_cap_alloc, "rd": raw_rd, "shareD": raw_share_d,
    }, annual_years)

    # Peer comp tables
    raw_peer_bench = fact_sheet.get("s12_peer_benchmarking", {})
    peer_tables = build_peer_comp_tables(raw_peer_bench)

    # Audit financial tables: check each table for populated vs empty cells
    fin_table_audit = {}
    for table_name, rows in fin_tables.items():
        total_cells = 0
        populated = 0
        for row in rows:
            for k, v in row.items():
                if k == "year":
                    continue
                total_cells += 1
                if v is not None:
                    populated += 1
        fin_table_audit[table_name] = {
            "rows": len(rows),
            "total_cells": total_cells,
            "populated": populated,
            "fill_rate": f"{populated}/{total_cells}" if total_cells else "0/0",
        }

    # Audit peer comp tables
    peer_table_audit = {}
    for table_name, rows in peer_tables.items():
        if not rows:
            peer_table_audit[table_name] = {"rows": 0, "total_cells": 0, "populated": 0, "fill_rate": "0/0"}
            continue
        total_cells = 0
        populated = 0
        subject_row = None
        for row in rows:
            if row.get("company") == ticker:
                subject_row = row
            for k, v in row.items():
                if k in ("company",):
                    continue
                total_cells += 1
                if v is not None and v != "NM":
                    populated += 1
        peer_table_audit[table_name] = {
            "rows": len(rows),
            "total_cells": total_cells,
            "populated": populated,
            "fill_rate": f"{populated}/{total_cells}" if total_cells else "0/0",
            "subject_row": subject_row,
        }

    trace.checkpoint("tables", {
        "fin_tables": fin_table_audit,
        "peer_tables": peer_table_audit,
        "annual_years": annual_years,
    })

    # ── WRITE SUMMARY ──
    stage_timings = {"fetch": fetch_time, "quantitative": quant_time}
    trace_dir = trace.write_summary(stage_timings=stage_timings)

    # Collect summary for final report
    all_issues = []
    for cp in trace._checkpoints:
        for issue in cp.get("issues", []):
            all_issues.append(issue)

    return {
        "ticker": ticker,
        "label": label,
        "sector": (data.fmp_profile or {}).get("sector", "?"),
        "industry": (data.fmp_profile or {}).get("industry", "?"),
        "fetch_time": fetch_time,
        "quant_time": quant_time,
        "fetch_errors": data.fetch_errors,
        "issues": all_issues,
        "trace_dir": trace_dir,
        "fin_tables": fin_table_audit,
        "peer_tables": peer_table_audit,
        "annual_years": annual_years,
        "checkpoints": [
            {"stage": cp["stage"], "summary": cp["validation_summary"]}
            for cp in trace._checkpoints
        ],
    }


async def main():
    # Allow single-ticker mode
    if "--ticker" in sys.argv:
        idx = sys.argv.index("--ticker")
        ticker = sys.argv[idx + 1].upper()
        tickers = {ticker: ticker}
    else:
        tickers = SECTOR_TICKERS

    print(f"\n{'='*70}")
    print(f"  Quantitative Pipeline — All Sectors Test")
    print(f"  Tickers: {', '.join(tickers.values())}")
    print(f"{'='*70}\n")

    results = []
    for label, ticker in tickers.items():
        print(f"\n{'─'*70}")
        print(f"  [{label}] {ticker}")
        print(f"{'─'*70}")
        try:
            r = await run_quant_pipeline(ticker, label)
            results.append(r)
        except Exception as e:
            import traceback
            print(f"  ❌ FAILED: {e}")
            traceback.print_exc()
            results.append({
                "ticker": ticker,
                "label": label,
                "sector": "?",
                "industry": "?",
                "fetch_errors": [str(e)],
                "issues": [f"PIPELINE CRASH: {e}"],
                "checkpoints": [],
            })

    # ── FINAL REPORT ──
    print(f"\n\n{'='*70}")
    print(f"  FINAL REPORT — ALL SECTORS")
    print(f"{'='*70}\n")

    total_issues = 0
    for r in results:
        issues = r.get("issues", [])
        total_issues += len(issues)
        status = "✅" if not issues else f"⚠️ ({len(issues)} issues)"
        checkpoints = " → ".join(
            f"{cp['stage']}[{cp['summary']}]"
            for cp in r.get("checkpoints", [])
        )
        print(f"  {r['ticker']:6s} [{r.get('sector','?'):20s}] {status}")
        if checkpoints:
            print(f"         {checkpoints}")
        for issue in issues:
            print(f"         {issue}")

    # ── FINANCIAL TABLE COVERAGE ──
    print(f"\n{'='*70}")
    print(f"  SECTION 10 — FINANCIAL TABLE FILL RATES")
    print(f"{'='*70}\n")
    print(f"  {'Ticker':<7} {'Years':<8} {'Revenue':<10} {'Margins':<10} {'CashFlow':<10} {'Returns':<10} {'Leverage':<10} {'CapAlloc':<10}")
    print(f"  {'─'*7} {'─'*8} {'─'*10} {'─'*10} {'─'*10} {'─'*10} {'─'*10} {'─'*10}")
    for r in results:
        ft = r.get("fin_tables", {})
        years = r.get("annual_years", [])
        yr_str = f"{len(years)}y" if years else "?"
        cols = ["revenue_growth", "margins", "cash_flow", "returns", "leverage", "capital_allocation"]
        fills = []
        for c in cols:
            t = ft.get(c, {})
            fills.append(t.get("fill_rate", "?"))
        print(f"  {r['ticker']:<7} {yr_str:<8} {fills[0]:<10} {fills[1]:<10} {fills[2]:<10} {fills[3]:<10} {fills[4]:<10} {fills[5]:<10}")

    # ── PEER COMP TABLE COVERAGE ──
    print(f"\n{'='*70}")
    print(f"  SECTION 12 — PEER COMP TABLE FILL RATES")
    print(f"{'='*70}\n")
    print(f"  {'Ticker':<7} {'ValComps':<12} {'ProfComps':<12} {'GrowthC':<12} {'LeverC':<12} {'EffComps':<12} {'GeoComps':<12}")
    print(f"  {'─'*7} {'─'*12} {'─'*12} {'─'*12} {'─'*12} {'─'*12} {'─'*12}")
    for r in results:
        pt = r.get("peer_tables", {})
        cols = ["valuation_comps", "profitability_comps", "growth_comps", "leverage_comps", "efficiency_comps", "geographic_comps"]
        fills = []
        for c in cols:
            t = pt.get(c, {})
            rows_n = t.get("rows", 0)
            fr = t.get("fill_rate", "0/0")
            fills.append(f"{rows_n}r {fr}")
        print(f"  {r['ticker']:<7} {fills[0]:<12} {fills[1]:<12} {fills[2]:<12} {fills[3]:<12} {fills[4]:<12} {fills[5]:<12}")

    # ── SUBJECT ROW IN VALUATION COMPS ──
    print(f"\n{'='*70}")
    print(f"  SUBJECT ROW — VALUATION COMPS (what the memo reader sees)")
    print(f"{'='*70}\n")
    for r in results:
        pt = r.get("peer_tables", {})
        vc = pt.get("valuation_comps", {})
        subj = vc.get("subject_row")
        if subj:
            parts = []
            for k, v in subj.items():
                if k == "company":
                    continue
                parts.append(f"{k}={v}")
            print(f"  {r['ticker']:<7} {' | '.join(parts)}")
        else:
            print(f"  {r['ticker']:<7} ❌ NO SUBJECT ROW")

    print(f"\n{'─'*70}")
    print(f"  Total: {len(results)} tickers, {total_issues} issues")
    print(f"{'─'*70}\n")

    # Save full report JSON
    report_path = os.path.join(_HERE, "test_output", "quant_all_sectors_report.json")
    with open(report_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"  Full report: {report_path}\n")


if __name__ == "__main__":
    asyncio.run(main())
