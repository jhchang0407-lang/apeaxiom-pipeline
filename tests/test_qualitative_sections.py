"""Validate qualitative section schemas (S2-S9, S13) across all sectors.

Runs fetch → quantitative → format_registry → distribute_sections for each
representative ticker, then verifies:
- Correct schema variant selected (S4/S7 banking for JPM, reits for PLD, etc.)
- Sector KPIs injected into all section contexts
- Correct section titles per sector
- Schema fields match sector variant

Usage:
    python test_qualitative_sections.py
    python test_qualitative_sections.py --ticker JPM   # single ticker
"""

import asyncio
import json
import os
import sys
import time

# Representative tickers by sector (same as quant test)
SECTOR_TICKERS = {
    "Technology":        "MSFT",
    "Banking":           "JPM",
    "Insurance":         "BRK-B",
    "REITs":             "PLD",
    "Retail":            "WMT",
    "Energy":            "XOM",
    "Healthcare":        "JNJ",
    "Industrials":       "HON",
    "Consumer Staples":  "PG",
    "Utilities":         "NEE",
    "Telecom":           "VZ",
    "Materials":         "LIN",
    "Consumer Disc.":    "MCD",
}

# Expected subsector mapping (ticker → expected subsector in _S4/_S7 dispatch)
EXPECTED_SUBSECTOR = {
    "JPM":   "banking",
    "BRK-B": "insurance",
    "PLD":   "reits",
    "XOM":   "energy",
    "NEE":   "utilities",
    # All others → default
}

# Expected S4 titles per subsector
EXPECTED_S4_TITLES = {
    "banking":   "Banking Products & Services",
    "insurance": "Insurance Products & Strategy",
    "reits":     "Property Portfolio & Strategy",
    "energy":    "Operations & Asset Base",
    "utilities": "Generation & Regulatory Strategy",
}
DEFAULT_S4_TITLE = "Product & Technology Strategy"

# Expected S7 titles per subsector
EXPECTED_S7_TITLES = {
    "banking":   "Deposit & Lending Analysis",
    "insurance": "Policyholder & Claims Analysis",
    "reits":     "Tenant & Lease Analysis",
    "energy":    "Production & Commodity Analysis",
    "utilities": "Regulatory & Demand Analysis",
}
DEFAULT_S7_TITLE = "Customer Analysis"

# Expected S4 schema key fields per subsector (first distinctive field)
EXPECTED_S4_KEYS = {
    "banking":   ["lending_portfolio", "deposit_and_funding", "fee_based_services", "digital_and_technology"],
    "insurance": ["product_lines", "underwriting_and_pricing", "investment_portfolio", "distribution_and_technology"],
    "reits":     ["property_portfolio", "development_pipeline", "acquisition_strategy", "property_technology"],
    "energy":    ["upstream_operations", "downstream_and_midstream", "commodity_and_hedging", "energy_transition"],
    "utilities": ["generation_portfolio", "transmission_and_distribution", "regulatory_rate_base", "clean_energy_transition"],
    "default":   ["product_portfolio", "rd_and_technology"],
}

# Expected S7 schema key fields per subsector
EXPECTED_S7_KEYS = {
    "banking":   ["deposit_franchise", "loan_book_composition", "interest_rate_sensitivity", "fee_income_analysis"],
    "insurance": ["policyholder_base", "underwriting_cycle", "claims_and_reserves", "distribution_economics"],
    "reits":     ["tenant_mix_and_quality", "lease_structure", "occupancy_and_retention", "rent_dynamics"],
    "energy":    ["offtake_and_contracts", "commodity_customer_mix", "production_economics", "hedging_and_risk_management"],
    "utilities": ["ratepayer_base", "regulatory_relationships", "rate_case_dynamics", "demand_and_load_patterns"],
    "default":   ["customer_composition", "stickiness_and_retention", "unit_economics", "working_capital"],
}

_HERE = os.path.dirname(os.path.abspath(__file__))


def _extract_schema_property_keys(schema_dict: dict) -> list[str]:
    """Extract property keys from an OpenAI-formatted schema."""
    # OpenAI format wraps in {"type": "json_schema", "json_schema": {"schema": ...}}
    inner = schema_dict
    if "json_schema" in inner:
        inner = inner["json_schema"]
    if "schema" in inner:
        inner = inner["schema"]
    props = inner.get("properties", {})
    return list(props.keys())


async def run_qualitative_test(ticker: str, label: str) -> dict:
    """Run full pipeline through distribute_sections and validate qualitative output."""
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
    from pipeline.distributors import (
        distribute_sections,
        _get_section_4_schema,
        _get_section_7_schema,
        _get_section_4_title,
        _get_section_7_title,
        _get_sector_agent1_guidance,
        _S10_GENERIC_TABLE_SUPPRESSIONS,
        _PEER_PRIMARY_INDUSTRIES,
        INDUSTRY_VALUATION_CONFIG,
    )

    issues = []

    # ── STAGE 1: FETCH ──
    t0 = time.time()
    data = await fetch_all_data(ticker, years=5, quarters=8)
    fetch_time = time.time() - t0

    # ── STAGE 2A: TRANSFORM ──
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

    # ── STAGE 2C: FORMAT REGISTRY ──
    registry_result = build_source_registry(
        fact_sheet=fact_sheet,
        fmp_profile=data.fmp_profile,
        sec_profile=data.sec_profile,
        filing_10k=data.filing_10k,
        filing_10q=data.filing_10q,
    )
    fs = registry_result["fact_sheet"]
    source_registry = registry_result

    # Inject extras like orchestrator does
    fs["_sec_sector_kpis"] = data.sec_sector_kpis

    quant_time = time.time() - t1

    # ── STAGE 3: DISTRIBUTE SECTIONS ──
    t2 = time.time()
    result = distribute_sections(
        fact_sheet=fs,
        source_registry=source_registry,
        ticker=ticker,
        company_name=(data.fmp_profile or {}).get("companyName", ticker),
    )
    dist_time = time.time() - t2

    # ── VALIDATE ──
    expected_sub = EXPECTED_SUBSECTOR.get(ticker, "default")
    sector = (data.fmp_profile or {}).get("sector", "?")
    industry = (data.fmp_profile or {}).get("industry", "?")

    # Detect actual subsector from data
    sector_kpis = fs.get("_sec_sector_kpis") or {}
    ident = fs.get("s1_identity", {})
    actual_sub = ident.get("subsector") or sector_kpis.get("sector", "")
    if not actual_sub:
        actual_sub = "default"

    section_checks = {}

    for sec_num in [2, 3, 4, 5, 6, 7, 8, 9, 13]:
        sec_key = f"section_{sec_num}"
        sec_data = result.get(sec_key, {})

        checks = {
            "exists": bool(sec_data),
            "has_facts": bool(sec_data.get("facts")),
            "has_schema": bool(sec_data.get("schema")),
            "has_title": bool(sec_data.get("section_title")),
        }

        # Check section title
        title = sec_data.get("section_title", "")
        checks["title"] = title

        # Check for sector_kpis in facts
        facts = sec_data.get("facts", {})
        has_sector_kpis = "sector_kpis" in facts
        checks["has_sector_kpis"] = has_sector_kpis

        # Check subsector in facts
        has_subsector = "subsector" in facts
        checks["has_subsector"] = has_subsector

        # Section-specific validations
        if sec_num == 4:
            # Validate S4 schema variant
            schema = sec_data.get("schema", {})
            schema_keys = _extract_schema_property_keys(schema)
            expected_keys = EXPECTED_S4_KEYS.get(expected_sub, EXPECTED_S4_KEYS["default"])
            missing_keys = [k for k in expected_keys if k not in schema_keys]
            checks["schema_variant"] = expected_sub
            checks["expected_keys_present"] = not bool(missing_keys)
            if missing_keys:
                issues.append(f"S4 schema missing keys for {expected_sub}: {missing_keys}")

            # Validate title
            expected_title = EXPECTED_S4_TITLES.get(expected_sub, DEFAULT_S4_TITLE)
            checks["title_correct"] = (title == expected_title)
            if title != expected_title:
                issues.append(f"S4 title mismatch: got '{title}', expected '{expected_title}'")

        elif sec_num == 7:
            # Validate S7 schema variant
            schema = sec_data.get("schema", {})
            schema_keys = _extract_schema_property_keys(schema)
            expected_keys = EXPECTED_S7_KEYS.get(expected_sub, EXPECTED_S7_KEYS["default"])
            missing_keys = [k for k in expected_keys if k not in schema_keys]
            checks["schema_variant"] = expected_sub
            checks["expected_keys_present"] = not bool(missing_keys)
            if missing_keys:
                issues.append(f"S7 schema missing keys for {expected_sub}: {missing_keys}")

            # Validate title
            expected_title = EXPECTED_S7_TITLES.get(expected_sub, DEFAULT_S7_TITLE)
            checks["title_correct"] = (title == expected_title)
            if title != expected_title:
                issues.append(f"S7 title mismatch: got '{title}', expected '{expected_title}'")

        # Check that section has non-empty facts
        non_meta_facts = {k: v for k, v in facts.items()
                         if k not in ("sector_kpis", "subsector", "financial_cite")}
        if not non_meta_facts:
            issues.append(f"S{sec_num} has empty facts (no quantitative data)")
            checks["has_quant_data"] = False
        else:
            checks["has_quant_data"] = True

        section_checks[sec_key] = checks

    # ── Validate S10 generic table suppression ──
    s10_data = result.get("section_10", {})
    _all_generic_keys = {"revenue_growth", "margins", "cash_flow", "returns", "leverage"}
    s10_precomputed = {k.replace("precomputed_", "") for k in s10_data if k.startswith("precomputed_")}
    s10_generic_present = s10_precomputed & _all_generic_keys
    # Use the ACTUAL runtime sector_family (not the expected subsector) since KPI coverage
    # gating may downgrade a sector to "generic" when required KPIs are absent (e.g. BRK-B).
    runtime_family = (s10_data.get("facts") or {}).get("sector_family", "generic")
    expected_suppress = _S10_GENERIC_TABLE_SUPPRESSIONS.get(runtime_family, set())
    s10_generic_expected = _all_generic_keys - expected_suppress

    s10_table_check = {
        "runtime_family": runtime_family,
        "generic_present": sorted(s10_generic_present),
        "generic_expected": sorted(s10_generic_expected),
        "sector_tables": sorted(s10_precomputed - _all_generic_keys),
        "correct": s10_generic_present == s10_generic_expected,
    }
    if not s10_table_check["correct"]:
        extra = s10_generic_present - s10_generic_expected
        missing = s10_generic_expected - s10_generic_present
        if extra:
            issues.append(f"S10 has unsuppressed generic tables for {runtime_family}: {sorted(extra)}")
        if missing:
            issues.append(f"S10 missing expected generic tables for {runtime_family}: {sorted(missing)}")

    # ── Validate S12 valuation schema ──
    s12_data = result.get("section_12", {})
    s12_schema = s12_data.get("schema", {})
    s12_schema_keys = _extract_schema_property_keys(s12_schema)
    s12_quant = s12_data.get("quant_inputs", {})

    # Detect valuation mode — prefer quant_inputs metadata, fall back to schema keys
    _qi_model = s12_quant.get("valuation_model", "")
    if _qi_model == "bank_equity":
        detected_val_mode = "bank_equity"
    elif _qi_model == "ddm":
        detected_val_mode = "ddm"
    elif s12_quant.get("alt_valuation_method"):
        detected_val_mode = "industry_peer"
    elif "dcf_analysis" in s12_schema_keys:
        detected_val_mode = "dcf"
    elif "dcf_not_applicable" in s12_schema_keys:
        detected_val_mode = "bank_equity"
    else:
        detected_val_mode = "dcf"

    # Check peer-primary ordering: peer_valuation before scenario_analysis
    is_peer_primary_industry = industry in _PEER_PRIMARY_INDUSTRIES
    if is_peer_primary_industry and "peer_valuation" in s12_schema_keys and "scenario_analysis" in s12_schema_keys:
        peer_idx = s12_schema_keys.index("peer_valuation")
        scenario_idx = s12_schema_keys.index("scenario_analysis")
        peer_before_scenarios = peer_idx < scenario_idx
    else:
        peer_before_scenarios = None  # not applicable

    # Check REIT-specific fields
    is_reit_industry = industry.startswith("REIT") or industry == "Real Estate Investment Trust"
    has_ffo = s12_quant.get("ffo_per_share") is not None
    has_p_ffo = s12_quant.get("subject_p_ffo") is not None
    has_metric_label = bool(s12_quant.get("metric_label"))

    s12_check = {
        "detected_mode": detected_val_mode,
        "schema_keys": s12_schema_keys,
        "is_peer_primary": is_peer_primary_industry,
        "peer_before_scenarios": peer_before_scenarios,
        "is_reit": is_reit_industry,
        "has_ffo": has_ffo,
        "has_p_ffo": has_p_ffo,
        "has_metric_label": has_metric_label,
    }

    # Validation
    if is_peer_primary_industry and peer_before_scenarios is False:
        issues.append(f"S12: peer_valuation should come BEFORE scenario_analysis for peer-primary industry '{industry}'")
    if is_reit_industry and not has_metric_label:
        issues.append(f"S12: REIT quant_inputs missing metric_label for '{industry}'")

    # ── Validate Agent 1 guidance ──
    agent1_guidance = _get_sector_agent1_guidance(expected_sub if expected_sub != "default" else "")
    has_agent1_guidance = bool(agent1_guidance)

    return {
        "ticker": ticker,
        "label": label,
        "sector": sector,
        "industry": industry,
        "expected_subsector": expected_sub,
        "actual_subsector": actual_sub,
        "fetch_time": fetch_time,
        "quant_time": quant_time,
        "dist_time": dist_time,
        "issues": issues,
        "section_checks": section_checks,
        "s10_table_check": s10_table_check,
        "s12_check": s12_check,
        "has_agent1_guidance": has_agent1_guidance,
    }


async def main():
    # Allow single-ticker mode
    if "--ticker" in sys.argv:
        idx = sys.argv.index("--ticker")
        ticker = sys.argv[idx + 1].upper()
        tickers = {ticker: ticker}
    else:
        tickers = SECTOR_TICKERS

    print(f"\n{'='*80}")
    print(f"  Qualitative Section Schemas — All Sectors Test")
    print(f"  Tickers: {', '.join(tickers.values())}")
    print(f"{'='*80}\n")

    results = []
    for label, ticker in tickers.items():
        print(f"\n{'─'*80}")
        print(f"  [{label}] {ticker}")
        print(f"{'─'*80}")
        try:
            r = await run_qualitative_test(ticker, label)
            results.append(r)
            # Print inline status
            n_issues = len(r["issues"])
            status = "✅" if n_issues == 0 else f"⚠️ ({n_issues} issues)"
            print(f"  {status}  subsector={r['expected_subsector']}  "
                  f"fetch={r['fetch_time']:.1f}s  dist={r['dist_time']:.2f}s")
            for issue in r["issues"]:
                print(f"    ⚠  {issue}")
        except Exception as e:
            import traceback
            print(f"  ❌ FAILED: {e}")
            traceback.print_exc()
            results.append({
                "ticker": ticker,
                "label": label,
                "sector": "?",
                "industry": "?",
                "expected_subsector": EXPECTED_SUBSECTOR.get(ticker, "default"),
                "actual_subsector": "?",
                "issues": [f"PIPELINE CRASH: {e}"],
                "section_checks": {},
                "has_agent1_guidance": False,
            })

    # ── FINAL REPORT ──
    print(f"\n\n{'='*80}")
    print(f"  FINAL REPORT — QUALITATIVE SECTION SCHEMAS")
    print(f"{'='*80}\n")

    # Section-level summary
    print(f"  {'Ticker':<7} {'Sector':<14} {'Sub':<10} "
          f"S2  S3  S4         S5  S6  S7         S8  S9  S13  Agent1")
    print(f"  {'─'*7} {'─'*14} {'─'*10} "
          f"{'─'*3} {'─'*3} {'─'*10} {'─'*3} {'─'*3} {'─'*10} {'─'*3} {'─'*3} {'─'*4} {'─'*6}")

    total_issues = 0
    for r in results:
        issues = r.get("issues", [])
        total_issues += len(issues)
        sc = r.get("section_checks", {})
        expected_sub = r.get("expected_subsector", "default")

        cols = []
        for sec_num in [2, 3, 4, 5, 6, 7, 8, 9, 13]:
            sec_key = f"section_{sec_num}"
            checks = sc.get(sec_key, {})
            ok = (checks.get("exists") and checks.get("has_facts")
                  and checks.get("has_schema") and checks.get("has_quant_data"))

            if sec_num == 4:
                variant = expected_sub if expected_sub != "default" else "def"
                title_ok = checks.get("title_correct", True)
                keys_ok = checks.get("expected_keys_present", True)
                if ok and title_ok and keys_ok:
                    cols.append(f"✅({variant[:3]})")
                else:
                    cols.append(f"❌({variant[:3]})")
            elif sec_num == 7:
                variant = expected_sub if expected_sub != "default" else "def"
                title_ok = checks.get("title_correct", True)
                keys_ok = checks.get("expected_keys_present", True)
                if ok and title_ok and keys_ok:
                    cols.append(f"✅({variant[:3]})")
                else:
                    cols.append(f"❌({variant[:3]})")
            else:
                cols.append("✅" if ok else "❌")

        agent1 = "✅" if r.get("has_agent1_guidance") or expected_sub == "default" else "❌"

        print(f"  {r['ticker']:<7} {r.get('sector','?'):<14} {expected_sub:<10} "
              f"{cols[0]:<3} {cols[1]:<3} {cols[2]:<10} {cols[3]:<3} {cols[4]:<3} "
              f"{cols[5]:<10} {cols[6]:<3} {cols[7]:<3} {cols[8]:<4} {agent1}")

    # ── SECTOR KPI INJECTION ──
    print(f"\n{'='*80}")
    print(f"  SECTOR KPI INJECTION (per section)")
    print(f"{'='*80}\n")
    print(f"  {'Ticker':<7} S2   S3   S4   S5   S6   S7   S8   S9   S13")
    print(f"  {'─'*7} {'─'*4} {'─'*4} {'─'*4} {'─'*4} {'─'*4} {'─'*4} {'─'*4} {'─'*4} {'─'*4}")
    for r in results:
        sc = r.get("section_checks", {})
        kpi_cols = []
        for sec_num in [2, 3, 4, 5, 6, 7, 8, 9, 13]:
            sec_key = f"section_{sec_num}"
            checks = sc.get(sec_key, {})
            has_kpi = checks.get("has_sector_kpis", False)
            kpi_cols.append("✅" if has_kpi else "—")
        print(f"  {r['ticker']:<7} {kpi_cols[0]:<4} {kpi_cols[1]:<4} {kpi_cols[2]:<4} "
              f"{kpi_cols[3]:<4} {kpi_cols[4]:<4} {kpi_cols[5]:<4} {kpi_cols[6]:<4} "
              f"{kpi_cols[7]:<4} {kpi_cols[8]:<4}")

    # ── S10 GENERIC TABLE SUPPRESSION ──
    print(f"\n{'='*80}")
    print(f"  S10 GENERIC TABLE SUPPRESSION")
    print(f"{'='*80}\n")
    print(f"  {'Ticker':<7} {'Family':<14} {'Generic Present':<40} {'Sector Tables':<40} {'OK'}")
    print(f"  {'─'*7} {'─'*14} {'─'*40} {'─'*40} {'─'*3}")
    for r in results:
        tc = r.get("s10_table_check", {})
        gp = ", ".join(tc.get("generic_present", []))
        st = ", ".join(tc.get("sector_tables", []))
        ok = "✅" if tc.get("correct", False) else "❌"
        fam = tc.get("runtime_family", "?")
        print(f"  {r['ticker']:<7} {fam:<14} {gp:<40} {st:<40} {ok}")

    # ── S12 VALUATION ──
    print(f"\n{'='*80}")
    print(f"  S12 VALUATION SCHEMA VALIDATION")
    print(f"{'='*80}\n")
    print(f"  {'Ticker':<7} {'Industry':<30} {'Mode':<14} {'Peer-Primary':<13} {'Peer→Scen':<10} {'FFO':<5} {'P/FFO':<6} {'Label':<6}")
    print(f"  {'─'*7} {'─'*30} {'─'*14} {'─'*13} {'─'*10} {'─'*5} {'─'*6} {'─'*6}")
    for r in results:
        sc = r.get("s12_check", {})
        mode = sc.get("detected_mode", "?")
        is_pp = "YES" if sc.get("is_peer_primary") else "—"
        pbs = "✅" if sc.get("peer_before_scenarios") is True else ("❌" if sc.get("peer_before_scenarios") is False else "—")
        ffo = "✅" if sc.get("has_ffo") else "—"
        pffo = "✅" if sc.get("has_p_ffo") else "—"
        lbl = "✅" if sc.get("has_metric_label") else "—"
        ind = r.get("industry", "?")[:30]
        print(f"  {r['ticker']:<7} {ind:<30} {mode:<14} {is_pp:<13} {pbs:<10} {ffo:<5} {pffo:<6} {lbl:<6}")

    # ── SECTION TITLES ──
    print(f"\n{'='*80}")
    print(f"  SECTION TITLES (S4 & S7)")
    print(f"{'='*80}\n")
    for r in results:
        sc = r.get("section_checks", {})
        s4_title = sc.get("section_4", {}).get("title", "?")
        s7_title = sc.get("section_7", {}).get("title", "?")
        print(f"  {r['ticker']:<7}  S4: {s4_title}")
        print(f"  {'':7}  S7: {s7_title}")

    # ── ISSUES DETAIL ──
    if total_issues > 0:
        print(f"\n{'='*80}")
        print(f"  ISSUES DETAIL")
        print(f"{'='*80}\n")
        for r in results:
            if r.get("issues"):
                print(f"  {r['ticker']}:")
                for issue in r["issues"]:
                    print(f"    ⚠  {issue}")

    print(f"\n{'─'*80}")
    print(f"  Total: {len(results)} tickers, {total_issues} issues")
    print(f"{'─'*80}\n")

    # Save report JSON
    out_dir = os.path.join(_HERE, "test_output")
    os.makedirs(out_dir, exist_ok=True)
    report_path = os.path.join(out_dir, "qualitative_sections_report.json")
    with open(report_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"  Full report: {report_path}\n")


if __name__ == "__main__":
    asyncio.run(main())
