"""Pipeline Orchestrator — Main entry point.

Coordinates the full memo generation pipeline:
  Stage 1: Fetch all data (parallel)
  Stage 2: Transform + build fact sheets (sequential, fast)
  Stage 3A: Research agents (parallel)
  Stage 3B: Body section writers (parallel)
  Stage 3C: Synthesis section writers (parallel, reads full body)
  Stage 4: Final assembly + valuation
  Stage 5: Output formatting

Supports two modes:
  "personal" → full sell-side memo with pricing, price targets, recommendations (local)
  "website"  → same memo without pricing/recommendations (cloud → R2)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from config.settings import PEER_SELECTION_MODEL
from pipeline.data_fetcher import PipelineData, fetch_all_data
from pipeline.trace import PipelineTrace
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
from pipeline.quantitative import build_quantitative_facts
from pipeline.clean_quantitative import clean_quantitative_facts
from pipeline.source_registry import build_source_registry


# ── FMP → SEC field name mappings (for backfilling XBRL gaps) ────────────────

_FMP_IS_MAP = {
    # FMP field name → our internal field name
    "revenue": "revenue",
    "costOfRevenue": "costOfRevenue",
    "grossProfit": "grossProfit",
    "researchAndDevelopmentExpenses": "researchAndDevelopmentExpenses",
    "sellingGeneralAndAdministrativeExpenses": "sellingGeneralAndAdministrativeExpenses",
    "operatingExpenses": "operatingExpenses",
    "operatingIncome": "operatingIncome",
    "interestExpense": "interestExpense",
    "interestIncome": "interestIncome",
    "incomeBeforeTax": "incomeBeforeTax",
    "incomeTaxExpense": "incomeTaxExpense",
    "netIncome": "netIncome",
    "epsDiluted": "epsDiluted",          # FMP stable API casing
    "epsdiluted": "epsDiluted",          # legacy casing
    "eps": "epsBasic",
    "weightedAverageShsOutDil": "weightedAverageSharesDiluted",
    "weightedAverageShsOut": "weightedAverageSharesBasic",
    "ebitda": "ebitda",
    "depreciationAndAmortization": "depreciationAndAmortization",
}

_FMP_BS_MAP = {
    "cashAndCashEquivalents": "cashAndCashEquivalents",
    "shortTermInvestments": "shortTermInvestments",
    "accountsReceivables": "accountsReceivables",  # FMP stable API name
    "netReceivables": "accountsReceivables",        # FMP legacy name fallback
    "inventory": "inventory",
    "totalCurrentAssets": "totalCurrentAssets",
    "propertyPlantEquipmentNet": "propertyPlantAndEquipment",  # FMP stable name
    "propertyPlantAndEquipmentNet": "propertyPlantAndEquipment",  # legacy fallback
    "goodwill": "goodwill",
    "intangibleAssets": "intangibleAssets",
    "totalAssets": "totalAssets",
    "accountPayables": "accountsPayables",
    "shortTermDebt": "shortTermDebt",
    "totalCurrentLiabilities": "totalCurrentLiabilities",
    "longTermDebt": "longTermDebt",
    "totalLiabilities": "totalLiabilities",
    "totalStockholdersEquity": "totalStockholdersEquity",
    "retainedEarnings": "retainedEarnings",
    "totalDebt": "totalDebt",
    "deferredRevenue": "deferredRevenue",
    "deferredRevenueNonCurrent": "deferredRevenueNonCurrent",
}

_FMP_CF_MAP = {
    "operatingCashFlow": "operatingCashFlow",
    "netCashProvidedByOperatingActivities": "operatingCashFlow",  # alt name
    "capitalExpenditure": "capitalExpenditure",
    "acquisitionsNet": "acquisitionsNet",
    "netCashProvidedByInvestingActivities": "investingCashFlow",  # FMP stable name
    "netCashUsedForInvestingActivites": "investingCashFlow",      # legacy spelling
    "commonDividendsPaid": "dividendsPaid",                       # FMP stable name
    "netDividendsPaid": "dividendsPaid",                          # alt
    "commonStockRepurchased": "shareRepurchases",
    "netCashProvidedByFinancingActivities": "financingCashFlow",  # FMP stable name
    "stockBasedCompensation": "stockBasedCompensation",
    "depreciationAndAmortization": "depreciationAndAmortization",
    "changeInWorkingCapital": "changeInWorkingCapital",
    "freeCashFlow": "freeCashFlow",
}


def _fmp_year(row: dict) -> str:
    """Extract calendar year string from an FMP record."""
    cy = row.get("calendarYear")
    if cy:
        return str(cy)
    d = row.get("date", "")
    return d[:4] if len(d) >= 4 else ""


def _fill_gaps(sec_row: dict, fmp_row: dict, field_map: dict) -> int:
    """For each mapped field, fill SEC value if missing. Returns count of fills."""
    fills = 0
    for fmp_field, sec_field in field_map.items():
        if sec_row.get(sec_field) is None:
            fmp_val = fmp_row.get(fmp_field)
            if fmp_val is not None and fmp_val != 0:
                sec_row[sec_field] = fmp_val
                fills += 1
    return fills


def _backfill_sec_from_fmp(data: "PipelineData", errors: list | None = None) -> int:
    """Backfill gaps in SEC financial statements with FMP data.

    Mutates data.sec_financials in place. After backfilling the raw
    statements, recomputes derived fields and ratios.

    Returns total number of fields filled.
    """
    sec_fin = data.sec_financials
    if not sec_fin or not isinstance(sec_fin, dict):
        return 0

    # Build FMP lookup by year
    fmp_is_by_year = {_fmp_year(r): r for r in (data.fmp_income_statement or [])}
    fmp_bs_by_year = {_fmp_year(r): r for r in (data.fmp_balance_sheet or [])}
    fmp_cf_by_year = {_fmp_year(r): r for r in (data.fmp_cash_flow or [])}

    if not fmp_is_by_year and not fmp_bs_by_year and not fmp_cf_by_year:
        return 0

    total_fills = 0

    # ── Backfill income statement ─────────────────────────────────
    sec_is_years = set()
    for sec_row in sec_fin.get("income_statement", []):
        year = sec_row.get("calendarYear", "")
        sec_is_years.add(year)
        fmp_row = fmp_is_by_year.get(year, {})
        if fmp_row:
            total_fills += _fill_gaps(sec_row, fmp_row, _FMP_IS_MAP)

    # ── Backfill balance sheet ────────────────────────────────────
    sec_bs_years = set()
    for sec_row in sec_fin.get("balance_sheet", []):
        year = sec_row.get("calendarYear", "")
        sec_bs_years.add(year)
        fmp_row = fmp_bs_by_year.get(year, {})
        if fmp_row:
            total_fills += _fill_gaps(sec_row, fmp_row, _FMP_BS_MAP)

    # ── Backfill cash flow ────────────────────────────────────────
    sec_cf_years = set()
    for sec_row in sec_fin.get("cash_flow", []):
        year = sec_row.get("calendarYear", "")
        sec_cf_years.add(year)
        fmp_row = fmp_cf_by_year.get(year, {})
        if fmp_row:
            total_fills += _fill_gaps(sec_row, fmp_row, _FMP_CF_MAP)

    # ── Add entirely missing year rows from FMP (safety net) ──────
    # If SEC is missing a year entirely, create a row from FMP data.
    def _fmp_to_sec_row(fmp_row: dict, field_map: dict) -> dict:
        """Convert an FMP record to a SEC-style row."""
        row = {
            "date": fmp_row.get("date", ""),
            "calendarYear": _fmp_year(fmp_row),
            "period": "FY",
        }
        for fmp_field, sec_field in field_map.items():
            val = fmp_row.get(fmp_field)
            if val is not None:
                row[sec_field] = val
        return row

    for year, fmp_row in fmp_is_by_year.items():
        if year and year not in sec_is_years:
            new_row = _fmp_to_sec_row(fmp_row, _FMP_IS_MAP)
            sec_fin.setdefault("income_statement", []).append(new_row)
            total_fills += len([v for v in new_row.values() if v is not None]) - 3  # exclude metadata

    for year, fmp_row in fmp_bs_by_year.items():
        if year and year not in sec_bs_years:
            new_row = _fmp_to_sec_row(fmp_row, _FMP_BS_MAP)
            sec_fin.setdefault("balance_sheet", []).append(new_row)
            total_fills += len([v for v in new_row.values() if v is not None]) - 3

    for year, fmp_row in fmp_cf_by_year.items():
        if year and year not in sec_cf_years:
            new_row = _fmp_to_sec_row(fmp_row, _FMP_CF_MAP)
            sec_fin.setdefault("cash_flow", []).append(new_row)
            total_fills += len([v for v in new_row.values() if v is not None]) - 3

    # Re-sort all statements by date descending after adding FMP rows
    for stmt_key in ("income_statement", "balance_sheet", "cash_flow"):
        stmt = sec_fin.get(stmt_key, [])
        if stmt:
            sec_fin[stmt_key] = sorted(
                stmt, key=lambda x: x.get("date", x.get("calendarYear", "")), reverse=True,
            )

    if total_fills == 0:
        return 0

    # ── Recompute derived fields ──────────────────────────────────
    for row in sec_fin.get("income_statement", []):
        rev = row.get("revenue", 0)
        cogs = row.get("costOfRevenue", 0)
        if "grossProfit" not in row and rev and cogs:
            row["grossProfit"] = rev - cogs
        if "ebitda" not in row:
            op_inc = row.get("operatingIncome", 0)
            da = row.get("depreciationAndAmortization", 0)
            if op_inc and da:
                row["ebitda"] = op_inc + da

    for row in sec_fin.get("cash_flow", []):
        ocf = row.get("operatingCashFlow", 0)
        capex = row.get("capitalExpenditure", 0)
        if ocf and capex:
            row["freeCashFlow"] = ocf - abs(capex)
        elif ocf:
            row["freeCashFlow"] = ocf

    for row in sec_fin.get("balance_sheet", []):
        if "totalDebt" not in row:
            st = abs(row.get("shortTermDebt", 0) or 0)
            lt = abs(row.get("longTermDebt", 0) or 0)
            cplt = abs(row.get("currentPortionOfLongTermDebt", 0) or 0)
            if st or lt or cplt:
                row["totalDebt"] = st + lt + cplt

    # ── Recompute ratios & growth from backfilled statements ──────
    try:
        from sec.ratios import (
            calculate_ratios,
            calculate_growth,
            calculate_key_metrics,
            calculate_owner_earnings,
        )
        from sec.statements import _add_fmp_aliases

        is_data = sec_fin.get("income_statement", [])
        bs_data = sec_fin.get("balance_sheet", [])
        cf_data = sec_fin.get("cash_flow", [])

        is_bank = data.sec_profile.get("isBank", False) if data.sec_profile else False

        sec_fin["ratios"] = calculate_ratios(is_data, bs_data, cf_data, is_bank=is_bank)
        sec_fin["growth"] = calculate_growth(is_data, cf_data)
        sec_fin["key_metrics"] = calculate_key_metrics(is_data, bs_data, cf_data)
        sec_fin["owner_earnings"] = calculate_owner_earnings(is_data, cf_data)

        # Re-add FMP aliases
        _add_fmp_aliases(is_data, bs_data, cf_data)
    except Exception as e:
        if errors is not None:
            errors.append(f"ratio recompute skipped: {e}")

    return total_fills


@dataclass
class MemoResult:
    """Final output of the pipeline."""

    ticker: str = ""
    mode: str = "personal"

    # Fact sheets
    quantitative_facts: dict = field(default_factory=dict)
    formatted_facts: dict = field(default_factory=dict)

    # Sections
    section_inputs: dict = field(default_factory=dict)   # distributor outputs (useful for probes)
    section_outputs: dict = field(default_factory=dict)  # writer outputs

    # Assembly
    memo_body: str = ""
    data_block: dict = field(default_factory=dict)
    scores: dict = field(default_factory=dict)

    # Outputs
    markdown: str = ""
    html: str = ""
    pdf: bytes = b""
    discord_scorecard: str = ""
    scorecard_json: dict = field(default_factory=dict)  # Structured JSON for dashboard / personal

    # Metadata
    pipeline_duration_s: float = 0.0
    stage_timings: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)
    assembly_ok: bool = False
    _financial_appendix: str = ""


class _SkipPeerSelection(Exception):
    pass


def _enrich_sector_kpis_from_fmp(
    fs: dict,
    ticker: str,
    peer_data: dict,
) -> None:
    """Supplement SEC sector KPIs with FMP-sourced data for the subject company.

    The peer pipeline already fetches key-metrics/ratios/financials for the
    subject ticker.  This function extracts useful metrics from that data and
    injects them into the _sec_sector_kpis dict so that sector tables and
    writer guidance can use them.

    Mutates fs["_sec_sector_kpis"]["kpis"] in place.
    """
    sector_kpis = fs.get("_sec_sector_kpis")
    if not sector_kpis or not isinstance(sector_kpis, dict):
        return
    kpis = sector_kpis.get("kpis")
    if not kpis:
        return
    sector = sector_kpis.get("sector", "")

    # Find the subject company's data in the peer results
    ticker_up = ticker.upper()
    by_symbol = peer_data.get("by_symbol", {})
    subj_data = by_symbol.get(ticker_up, {})
    if not subj_data:
        return

    # Get latest year entries
    latest_entries = peer_data.get("latest", [])
    subj_latest = None
    for entry in latest_entries:
        if (entry.get("symbol") or "").upper() == ticker_up:
            subj_latest = entry
            break

    # ── Banking enrichment ──────────────────────────────────────────
    if sector == "banking":
        computed = kpis.get("computedRatios", [])
        if not computed:
            return

        # Build year->index map for computed ratios
        year_idx = {}
        for i, row in enumerate(computed):
            yr = row.get("date", "")[:4]
            if yr:
                year_idx[yr] = i

        # FMP by_symbol has year-keyed data
        for year_str, yr_data in subj_data.items():
            if not isinstance(yr_data, dict) or year_str not in year_idx:
                continue
            idx = year_idx[year_str]
            row = computed[idx]

            # Fill TBV/share if missing
            if row.get("tangibleBookValuePerShare") is None:
                fmp_tbv = yr_data.get("tangible_book_value_per_share")
                if fmp_tbv is not None:
                    row["tangibleBookValuePerShare"] = round(fmp_tbv, 2)

            # Fill ROE if missing
            if row.get("roe") is None:
                fmp_roe = yr_data.get("roe_pct")
                if fmp_roe is not None:
                    row["roe"] = round(fmp_roe / 100, 4)

            # Fill ROA if missing
            if row.get("roa") is None:
                fmp_roa = yr_data.get("roa_pct")
                if fmp_roa is not None:
                    row["roa"] = round(fmp_roa / 100, 4)

    # ── General enrichment for all sectors ──────────────────────────
    # For sectors using computedMetrics, fill in FMP data
    computed = kpis.get("computedMetrics", [])
    if not computed:
        return

    year_idx = {}
    for i, row in enumerate(computed):
        yr = row.get("date", "")[:4]
        if yr:
            year_idx[yr] = i

    for year_str, yr_data in subj_data.items():
        if not isinstance(yr_data, dict) or year_str not in year_idx:
            continue
        idx = year_idx[year_str]
        row = computed[idx]

        # Fill common metrics from FMP if missing in XBRL
        fmp_mappings = [
            ("grossMargin", "gross_margin_pct", 100),
            ("operatingMargin", "operating_margin_pct", 100),
            ("netMargin", "net_margin_pct", 100),
            ("roe", "roe_pct", 100),
            ("roa", "roa_pct", 100),
            ("roic", "roic_pct", 100),
        ]
        for xbrl_key, fmp_key, divisor in fmp_mappings:
            if row.get(xbrl_key) is None:
                fmp_val = yr_data.get(fmp_key)
                if fmp_val is not None:
                    row[xbrl_key] = round(fmp_val / divisor, 4)


def _latest_sector_computed_row(sector_kpis: dict | None) -> dict | None:
    if not sector_kpis or not isinstance(sector_kpis, dict):
        return None
    kpis = sector_kpis.get("kpis", sector_kpis)
    if not isinstance(kpis, dict):
        return None
    computed = kpis.get("computedRatios") or kpis.get("computedMetrics") or []
    if not isinstance(computed, list) or not computed:
        return None
    rows = [r for r in computed if isinstance(r, dict) and r.get("date")]
    if not rows:
        return None
    return max(rows, key=lambda r: r.get("date", ""))


def _median(vals: list[float]) -> float | None:
    vals2 = sorted(v for v in vals if isinstance(v, (int, float)))
    if not vals2:
        return None
    n = len(vals2)
    mid = n // 2
    if n % 2 == 1:
        return float(vals2[mid])
    return float((vals2[mid - 1] + vals2[mid]) / 2)


def _build_reit_valuation_comps(
    *,
    subject_ticker: str,
    fs: dict,
    peer_latest: list[dict],
    years: int = 5,
) -> list[dict]:
    """Build REIT peer valuation comps table with P/FFO and P/AFFO.

    Uses SEC sector KPIs (FFO/AFFO per share) for each peer, and derives price
    from market cap / shares when price is not directly available.
    """
    try:
        from sec.sectors import get_sector_kpis
    except Exception:
        return []

    subj = subject_ticker.upper()
    ident = fs.get("s1_identity") or {}
    subj_price = ident.get("price")

    rows: list[dict] = []
    cache: dict[str, dict] = {}

    for entry in peer_latest or []:
        sym = (entry.get("symbol") or "").upper()
        if not sym:
            continue

        # Market cap & dividend yield from peer pipeline
        mkt_cap = entry.get("market_cap_usd_b")
        div_yield = entry.get("dividend_yield_pct")

        # Derive price (if not subject)
        price = None
        if sym == subj:
            price = subj_price
            if price is None:
                mc = entry.get("market_cap_usd_b")
                sh = entry.get("shares_diluted_m")
                if isinstance(mc, (int, float)) and isinstance(sh, (int, float)) and sh > 0:
                    price = (mc / sh) * 1000  # ($B / M shares) * 1000 = $/share
        else:
            mc = entry.get("market_cap_usd_b")
            sh = entry.get("shares_diluted_m")
            if isinstance(mc, (int, float)) and isinstance(sh, (int, float)) and sh > 0:
                price = (mc / sh) * 1000  # ($B / M shares) * 1000 = $/share

        # Sector KPIs for FFO/AFFO per share
        if sym == subj:
            sk = fs.get("_sec_sector_kpis")
        else:
            if sym in cache:
                sk = cache[sym]
            else:
                # Force SIC-based detection; sector_override is a gentle fallback.
                try:
                    sk = get_sector_kpis(sym, years=years, sector_override="Real Estate")
                except Exception:
                    sk = {}
                cache[sym] = sk

        latest = _latest_sector_computed_row(sk)
        ffo_ps = latest.get("ffoPerShare") if latest else None
        affo_ps = latest.get("affoPerShare") if latest else None
        cap_rate = latest.get("capRateProxy") if latest else None

        p_ffo = (price / ffo_ps) if isinstance(price, (int, float)) and isinstance(ffo_ps, (int, float)) and ffo_ps > 0 else None
        p_affo = (price / affo_ps) if isinstance(price, (int, float)) and isinstance(affo_ps, (int, float)) and affo_ps > 0 else None

        # Cap rate is stored as a decimal in sector KPIs; convert to % for display.
        cap_rate_pct = None
        if isinstance(cap_rate, (int, float)):
            cap_rate_pct = cap_rate * 100 if abs(cap_rate) < 1 else cap_rate

        rows.append({
            "company": sym,
            "market_cap_usd_b": mkt_cap,
            "p_ffo": round(p_ffo, 2) if isinstance(p_ffo, (int, float)) else None,
            "p_affo": round(p_affo, 2) if isinstance(p_affo, (int, float)) else None,
            "dividend_yield_pct": div_yield,
            "cap_rate_proxy": round(cap_rate_pct, 2) if isinstance(cap_rate_pct, (int, float)) else None,
        })

    # Add peer median row
    if rows:
        pffo_med = _median([r.get("p_ffo") for r in rows if isinstance(r.get("p_ffo"), (int, float))])
        paffo_med = _median([r.get("p_affo") for r in rows if isinstance(r.get("p_affo"), (int, float))])
        dy_med = _median([r.get("dividend_yield_pct") for r in rows if isinstance(r.get("dividend_yield_pct"), (int, float))])
        cap_med = _median([r.get("cap_rate_proxy") for r in rows if isinstance(r.get("cap_rate_proxy"), (int, float))])
        rows.append({
            "company": "Peer Median",
            "market_cap_usd_b": None,
            "p_ffo": round(pffo_med, 2) if isinstance(pffo_med, (int, float)) else None,
            "p_affo": round(paffo_med, 2) if isinstance(paffo_med, (int, float)) else None,
            "dividend_yield_pct": round(dy_med, 2) if isinstance(dy_med, (int, float)) else None,
            "cap_rate_proxy": round(cap_med, 2) if isinstance(cap_med, (int, float)) else None,
        })

    return rows


async def run_pipeline(
    ticker: str,
    mode: str = "personal",
    years: int = 5,
    quarters: int = 8,
    output_dir: str | None = None,
    stop_after: str | None = None,
    peer_selection_enabled: bool = True,
) -> MemoResult:
    """Run the full memo generation pipeline.

    Args:
        ticker: Company ticker symbol
        mode: "personal" or "website"
        years: Number of annual periods
        quarters: Number of quarterly periods
        output_dir: Directory for outputs (enables pipeline trace if set)

    Returns:
        MemoResult with all outputs
    """
    result = MemoResult(ticker=ticker.upper(), mode=mode)
    t_start = time.time()

    # Initialize pipeline trace for observability
    trace = PipelineTrace(
        ticker=ticker.upper(),
        output_dir=output_dir or ".",
        enabled=output_dir is not None,
    )

    # ── STAGE 1: FETCH ALL DATA ────────────────────────────────
    t1 = time.time()
    data = await fetch_all_data(ticker, years=years, quarters=quarters)
    result.stage_timings["fetch"] = time.time() - t1
    result.errors.extend(data.fetch_errors)

    trace.checkpoint("fetch", {
        "sec_annual_count": len((data.sec_financials or {}).get("income_statement", [])),
        "sec_quarterly_count": len((data.sec_quarterly or {}).get("income_statement", [])),
        "fmp_profile": data.fmp_profile or {},
        "sec_segments": data.sec_segments or {},
        "fetch_errors": data.fetch_errors,
    })

    # ── STAGE 1B: BACKFILL SEC GAPS WITH FMP ──────────────────
    try:
        fmp_fills = _backfill_sec_from_fmp(data, errors=result.errors)
        if fmp_fills > 0:
            print(f"  ✓ FMP backfill: {fmp_fills} fields filled")
    except Exception as e:
        result.errors.append(f"FMP backfill: {type(e).__name__}: {e}")

    # ── STAGE 2: TRANSFORM + FACT SHEETS ───────────────────────
    t2 = time.time()

    # Pivot all data sources
    annual = pivot_annual(data.sec_financials, data.sec_segments)
    quarterly = pivot_quarterly(data.sec_quarterly)
    estimates = pivot_estimates(data.fmp_estimates)
    surprises = pivot_surprises(data.fmp_surprises)
    owner_earnings = pivot_owner_earnings(data.sec_financials)

    # Aggregate into single structure
    quant_data = aggregate_quantitative(
        annual=annual,
        quarterly=quarterly,
        estimates=estimates,
        surprises=surprises,
        owner_earnings=owner_earnings,
        peers=data.fmp_peers,
    )

    # Normalize segment names
    quant_data = normalize_segments(quant_data)

    # Inject currency
    quant_data = inject_currency(quant_data, data.sec_financials, data.fmp_profile)

    result.stage_timings["transform"] = time.time() - t2

    # ── STAGE 2B: BUILD QUANTITATIVE FACT SHEET ────────────────
    t2b = time.time()
    fact_sheet = build_quantitative_facts(quant_data)
    result.stage_timings["quantitative"] = time.time() - t2b

    trace.checkpoint("quantitative", fact_sheet)

    # ── STAGE 2C: FORMAT + SOURCE REGISTRY ───────────────────
    t2c = time.time()
    formatted_facts = clean_quantitative_facts(fact_sheet)

    registry_result = build_source_registry(
        fact_sheet=formatted_facts,
        fmp_profile=data.fmp_profile,
        sec_profile=data.sec_profile,
        filing_10k=data.filing_10k,
        filing_10q=data.filing_10q,
    )
    result.formatted_facts = registry_result["fact_sheet"]
    result.quantitative_facts = fact_sheet
    result.stage_timings["format_registry"] = time.time() - t2c

    trace.checkpoint("format_registry", result.formatted_facts)

    # Store supplemental data for downstream stages
    _extras = {
        "_filing_10k": data.filing_10k,
        "_filing_10q": data.filing_10q,
        "_fmp_profile": data.fmp_profile,
        "_sec_profile": data.sec_profile,
        "_sec_sector_kpis": data.sec_sector_kpis,
        "_source_registry": registry_result["source_registry"],
        "_named_sources": registry_result.get("named_sources", {}),
        "_sources_appendix": registry_result["sources_appendix"],
        "_financial_cite": registry_result["financial_cite"],
        "_filing_label": registry_result["filing_label"],
    }
    for k, v in _extras.items():
        result.formatted_facts[k] = v

    # ── STAGE 2D: PEER SELECTION (parallel with stage 3 prep) ──
    t2d = time.time()
    ident = result.formatted_facts.get("s1_identity", {})
    company_name = ident.get("company_name", ticker.upper())

    try:
        if not peer_selection_enabled:
            raise _SkipPeerSelection()
        from pipeline.peer_selection import run_peer_pipeline

        peer_result = await run_peer_pipeline(
            ticker=ticker.upper(),
            company_name=company_name,
            sector=ident.get("sector", ""),
            industry=ident.get("industry", ""),
            market_cap=ident.get("market_cap"),
            description=ident.get("description", ""),
            model=PEER_SELECTION_MODEL,
            years=5,
        )

        fs = result.formatted_facts
        subject_overrides: dict = {}

        # Merge peer data into fact sheet
        if peer_result["peer_count"] > 0:
            pd = peer_result["peer_data"]

            # Replace the weak FMP peer data with LLM-curated peers
            fs["s12_peer_benchmarking"]["peers_full"] = {
                "by_symbol": pd["by_symbol"],
                "latest": pd["latest"],
            }
            fs["s12_peer_benchmarking"]["peer_medians"] = pd["peer_medians"]
            fs["s6_peers"] = {
                "latest": pd["latest"],
                "peer_medians": pd["peer_medians"],
                "by_symbol": pd["by_symbol"],
            }

            # Rebuild peer comp tables from the new peer data.
            # Extract SEC XBRL-derived subject metrics from the EXISTING
            # peer comp tables (built by build_quantitative_facts) so the
            # subject row stays consistent with the rest of the memo.
            # FMP peer API computes ratios differently (e.g. ROIC, ROE).
            for _tbl_key in ("profitability_comps", "growth_comps",
                             "valuation_comps", "leverage_comps",
                             "efficiency_comps", "returns_comps",
                             "competitive_landscape"):
                existing = fs.get("s12_peer_benchmarking", {}).get(_tbl_key, [])
                if existing:
                    for k, v in existing[0].items():
                        if k == "company":
                            continue
                        # Include None values so that SEC XBRL "data not
                        # available" overrides FMP's incorrect 0.0 values
                        # (e.g. interest_coverage when interest expense is
                        # not reported in XBRL).
                        subject_overrides[k] = v

            # ── Overlay authoritative metrics onto subject_overrides ──
            # The initial comp table subject row is built from SEC XBRL annual
            # data (via build_quantitative_facts).  Two categories of fields
            # are wrong or missing in that initial build:
            #
            # 1. VALUATION MULTIPLES (P/E, EV/EBITDA, etc.) — SEC XBRL has no
            #    stock price, so these are always None.  But _inject_fmp_valuation
            #    (Stage 2C) already computed them from real-time price + SEC
            #    financials and stored them in s13_valuation._current_*.
            #
            # 2. INCOME STMT RATIOS (SBC/Rev, Capex/Rev) — the initial build
            #    uses FMP key-metrics API values which can differ from the
            #    income statement-derived values shown in the appendix tables.
            #
            # Without these overlays, the subject_overrides contain None for
            # valuation fields, which CLOBBER the valid values the peer pipeline
            # would provide (since subject_overrides are applied via {**peer, **overrides}).

            # Helper: latest year from a year-keyed dict
            def _latest_from_series(section_key: str, metric_key: str):
                series = fs.get(section_key, {}).get(metric_key)
                if isinstance(series, dict) and series:
                    return series.get(max(series.keys()))
                return None

            # (1) Real-time valuation multiples from _inject_fmp_valuation
            _val = fs.get("s13_valuation", {})
            _val_overlays = {
                "market_cap_usd_b":   _val.get("_current_market_cap_b"),
                "ev_to_sales":        _latest_from_series("s13_valuation", "ev_to_sales"),
                "ev_to_ebitda":       _val.get("_current_ev_ebitda"),
                "price_to_earnings":  _val.get("_current_pe"),
                "price_to_fcf":       _val.get("_current_p_fcf"),
                "fcf_yield_pct":      _val.get("_current_fcf_yield_pct"),
                "dividend_yield_pct": _val.get("_current_dividend_yield_pct"),
            }
            for _ok, _ov in _val_overlays.items():
                if _ov is not None:
                    subject_overrides[_ok] = _ov

            # (2) Income-statement-derived ratios (more accurate than FMP key metrics)
            _income_overlays = {
                "sbc_to_revenue_pct": _latest_from_series("s11_income_statement", "sbc_pct_of_revenue"),
                "capex_to_revenue_pct": _latest_from_series("s11_cash_flow", "capex_pct_of_revenue"),
                "fcf_margin_pct": _latest_from_series("s11_cash_flow", "fcf_margin_pct"),
            }
            for _ok, _ov in _income_overlays.items():
                if _ov is not None:
                    subject_overrides[_ok] = _ov

            try:
                from pipeline.quantitative import build_peer_comp_tables
                comp_tables = build_peer_comp_tables(
                    pd, ticker.upper(),
                    subject_overrides=subject_overrides or None,
                )
                for tbl_key in ("profitability_comps", "growth_comps",
                                "valuation_comps", "valuation_comps_financial",
                                "leverage_comps", "efficiency_comps",
                                "returns_comps",
                                "competitive_landscape"):
                    if tbl_key in comp_tables:
                        fs["s12_peer_benchmarking"][tbl_key] = comp_tables[tbl_key]
                # Also update s6_competitive_landscape so assembly renders
                # the competitive landscape table with actual peer data
                if "competitive_landscape" in comp_tables:
                    fs["s6_competitive_landscape"] = comp_tables["competitive_landscape"]

                # ── REIT: add P/FFO + P/AFFO valuation comps ─────────
                try:
                    if (fs.get("_sec_sector_kpis") or {}).get("sector") == "reits":
                        reit_val = _build_reit_valuation_comps(
                            subject_ticker=ticker.upper(),
                            fs=fs,
                            peer_latest=pd.get("latest", []),
                            years=5,
                        )
                        if reit_val:
                            fs["s12_peer_benchmarking"]["valuation_comps_reit"] = reit_val
                except Exception as e:
                    result.errors.append(f"REIT valuation comps skipped: {e}")

            except ImportError as e:
                result.errors.append(f"peer comp table rebuild skipped: {e}")
            except Exception as e:
                result.errors.append(f"peer comp table rebuild skipped: {e}")

            # Update _raw snapshot so assembly reads enriched peer data
            # (clean_quantitative snapshots _raw BEFORE peer enrichment)
            meta_raw = fs.get("_meta", {}).get("_raw", {})
            if meta_raw and "s12_peer_benchmarking" in fs:
                meta_raw["s12_peer_benchmarking"] = fs["s12_peer_benchmarking"]

            # Update peer valuation medians in s13_valuation
            val = fs.get("s13_valuation", {})
            val["peer_valuation_medians"] = {
                k: pd["peer_medians"].get(k)
                for k in ("ev_to_sales", "ev_to_ebitda", "ev_to_fcf",
                           "price_to_earnings", "price_to_fcf", "fcf_yield_pct")
            }

            # ── Enrich sector KPIs with FMP data ────────────────────
            # The peer pipeline already fetched key-metrics/ratios for
            # the subject company.  Use this to fill gaps in XBRL data
            # (e.g. TBV/share, ROE/ROA for recent years).
            try:
                _enrich_sector_kpis_from_fmp(
                    fs, ticker.upper(), pd,
                )
            except Exception as e:
                result.errors.append(f"sector KPI enrichment skipped: {e}")

        # Trace: snapshot the peer rebuild results
        _val_comps = fs.get("s12_peer_benchmarking", {}).get("valuation_comps", [])
        _eff_comps = fs.get("s12_peer_benchmarking", {}).get("efficiency_comps", [])
        trace.checkpoint("peer_rebuild", {
            "subject_overrides": subject_overrides,
            "valuation_comps_subject": _val_comps[0] if _val_comps else {},
            "efficiency_comps_subject": _eff_comps[0] if _eff_comps else {},
            "peer_count": peer_result["peer_count"],
            "peers_selected": peer_result.get("peers_selected", []),
        })

        result.stage_timings["peer_selection"] = time.time() - t2d
    except _SkipPeerSelection:
        result.stage_timings["peer_selection"] = time.time() - t2d
    except Exception as e:
        result.errors.append(f"Peer selection: {type(e).__name__}: {e}")
        result.stage_timings["peer_selection"] = time.time() - t2d

    # Optional early exit after peer rebuild (deterministic probes)
    if stop_after in {"peer_selection", "peer_rebuild", "stage2d"}:
        result.pipeline_duration_s = time.time() - t_start
        return result

    # ── STAGE 3: DISTRIBUTE + WRITE ─────────────────────────
    t3 = time.time()

    try:
        from pipeline.distributors import distribute_sections
        from pipeline.writers import write_all_sections
        from pipeline.assembly import assemble_memo
        from pipeline.formatters import (
            format_markdown,
            format_html,
            build_financial_appendix,
            build_scorecard_json,
            format_discord_scorecard_v2,
        )

        # Build section inputs from the distributor
        include_pricing = mode == "personal"

        section_inputs = distribute_sections(
            fact_sheet=result.formatted_facts,
            source_registry=registry_result,
            include_pricing=include_pricing,
            company_name=company_name,
            ticker=ticker.upper(),
        )
        result.section_inputs = section_inputs

        if stop_after in {"distribute", "stage3_distribute"}:
            result.stage_timings["distribute"] = time.time() - t3
            result.pipeline_duration_s = time.time() - t_start
            return result

        # Override agent prompts with Jinja2-rendered versions
        # (distributors only has placeholder descriptions)
        agent_prompts = _build_agent_prompts(
            ticker=ticker,
            fact_sheet=result.formatted_facts,
            filing_10k=data.filing_10k,
            filing_10q=data.filing_10q,
            sec_sector_kpis=data.sec_sector_kpis,
        )
        section_inputs["agent_prompts"] = agent_prompts

        # Run the 3-stage writing pipeline
        write_result = await write_all_sections(
            section_inputs=section_inputs,
        )

        result.section_outputs = write_result.get("section_outputs", {})
        result.stage_timings["writing"] = time.time() - t3
        result.errors.extend(write_result.get("errors", []))

    except Exception as e:
        import traceback as _tb
        tb_str = _tb.format_exc()
        result.errors.append(f"Stage 3 error: {type(e).__name__}: {e}")
        result.errors.append(f"Stage 3 traceback:\n{tb_str}")
        result.stage_timings["writing"] = time.time() - t3
        result.pipeline_duration_s = time.time() - t_start
        return result

    # ── STAGE 3.5: FACT-CHECK ─────────────────────────────────
    t3_5 = time.time()
    _fact_check_summary = None

    try:
        from pipeline.fact_check import fact_check_quarterly

        # Build precomputed tables from distributor section inputs
        # (results table, beat/miss, segment tables are in various section inputs)
        precomputed_tables: dict = {}
        for skey in ("section_10", "section_11", "section_12", "quarterly"):
            si = section_inputs.get(skey, {})
            for pk in si:
                if pk.startswith("precomputed_"):
                    precomputed_tables[pk] = si[pk]

        # Raw facts = the pre-formatted quantitative fact sheet (numeric values)
        raw_facts = result.quantitative_facts or {}

        total_patches = 0
        total_verified = 0
        total_suspicious: list = []

        for sec_key, sec_output in list(result.section_outputs.items()):
            if not sec_key.startswith("section_"):
                continue
            try:
                fc_result = fact_check_quarterly(
                    writer_output=sec_output,
                    raw_facts=raw_facts,
                    precomputed_tables=precomputed_tables,
                )
                # Replace section output with patched version
                patched = fc_result.get("patched_output", sec_output)
                result.section_outputs[sec_key] = patched
                total_patches += fc_result.get("patches_applied", 0)
                total_verified += fc_result.get("verified_claims", 0)
                suspicious = fc_result.get("suspicious_claims", [])
                for s in suspicious:
                    s["section"] = sec_key
                total_suspicious.extend(suspicious)
            except Exception as fc_err:
                result.errors.append(
                    f"Fact-check {sec_key}: {type(fc_err).__name__}: {fc_err}"
                )

        result.stage_timings["fact_check"] = time.time() - t3_5
        # Store temporarily — assembly overwrites data_block, so we re-inject after
        _fact_check_summary = {
            "patches_applied": total_patches,
            "verified_claims": total_verified,
            "suspicious_claims_count": len(total_suspicious),
            "suspicious": total_suspicious[:20],
        }

    except ImportError:
        _fact_check_summary = None
        result.errors.append("fact_check module not available — skipping")
        result.stage_timings["fact_check"] = time.time() - t3_5
    except Exception as e:
        _fact_check_summary = None
        result.errors.append(f"Fact-check error: {type(e).__name__}: {e}")
        result.stage_timings["fact_check"] = time.time() - t3_5

    # ── STAGE 4: ASSEMBLY ────────────────────────────────────
    t4 = time.time()

    try:
        import json as _json

        # Transform writer outputs into the format assembly expects:
        # section_map: section_N -> text content
        # structured_map: section_N -> structured JSON output
        section_map = {}
        structured_map = {}
        for key, sec_result in result.section_outputs.items():
            if not key.startswith("section_"):
                continue
            output = sec_result.get("output", {})
            if isinstance(output, dict):
                structured_map[key] = output
                # Build text representation for section_map
                raw_text = (
                    output.get("raw_text", "")
                    or output.get("section_text", "")
                    or output.get("content", "")
                    or _json.dumps(output, indent=2, default=str)
                )
                section_map[key] = raw_text
            else:
                section_map[key] = str(output)

        # Pull dcf_anchors and precomputed peer table from distributor
        dist_s12 = section_inputs.get("section_12", {})
        dcf_anchors = dist_s12.get("dcf_anchors", {})
        precomputed_peer_table = dist_s12.get("precomputed_peer_table")

        # Extract catalyst calendar and operational drivers from agent_3 output
        _qual_data = write_result.get("qualitative_data", {})
        _agent3 = _qual_data.get("agent_3", {})
        _catalyst_calendar = _agent3.get("catalyst_calendar", []) if isinstance(_agent3, dict) else []
        _operational_drivers = _agent3.get("operational_drivers", []) if isinstance(_agent3, dict) else []

        assembly_input = {
            "company_name": ident.get("company_name", ticker.upper()),
            "ticker": ticker.upper(),
            "section_map": section_map,
            "structured_map": structured_map,
            "scores": dist_s12.get("scores", {}),
            "dcf_anchors": dcf_anchors,
            "precomputed_peer_table": precomputed_peer_table,
            "sources_appendix": result.formatted_facts.get(
                "_sources_appendix", ""
            ),
            "catalyst_calendar": _catalyst_calendar,
            "operational_drivers": _operational_drivers,
        }

        assembly = assemble_memo(
            section_outputs=assembly_input,
            fact_sheet=result.formatted_facts,
            source_registry=registry_result,
        )
        result.memo_body = assembly.formatted_memo
        result.data_block = assembly.data_block
        result.scores = assembly.data_block.get("scores", {})
        # Re-inject fact-check summary after assembly (assembly builds data_block fresh)
        if _fact_check_summary:
            result.data_block["_fact_check"] = _fact_check_summary
        result.stage_timings["assembly"] = time.time() - t4
        result.assembly_ok = True
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        result.errors.append(f"Stage 4 assembly error: {type(e).__name__}: {e}\n{tb}")
        result.stage_timings["assembly"] = time.time() - t4
        result.assembly_ok = False

    # ── STAGE 5: OUTPUT FORMATTING ──────────────────────────
    t5 = time.time()

    try:
        # Add financial appendix — only if assembly produced real content.
        # Without this guard, a failed assembly produces an appendix-only
        # memo that tricks format_markdown's >500-char check into skipping
        # the fallback path that would render individual section outputs.
        appendix = build_financial_appendix(result.quantitative_facts)
        if appendix and result.memo_body and len(result.memo_body.strip()) > 500:
            result.memo_body += f"\n\n{appendix}"
        elif appendix:
            # Stash appendix so fallback path can still include it
            result._financial_appendix = appendix

        result.markdown = format_markdown(result)
        result.html = format_html(result)

        # Build scorecard JSON
        # Personal mode: full scorecard with pricing + fair value
        # Website mode: static analysis only (no pricing or price targets)
        scorecard_mode = "personal" if mode == "personal" else "website"
        result.scorecard_json = build_scorecard_json(
            fact_sheet=result.formatted_facts,
            data_block=result.data_block,
            section_outputs=result.section_outputs,
            mode=scorecard_mode,
        )

        # Discord scorecard uses the full scorecard JSON (personal mode always)
        result.discord_scorecard = format_discord_scorecard_v2(result.scorecard_json)

        # Generate PDF (if weasyprint available)
        try:
            from weasyprint import HTML as WeasyprintHTML
            pdf_html = result.html or format_html(result)
            result.pdf = WeasyprintHTML(string=pdf_html).write_pdf()
        except ImportError:
            result.errors.append("weasyprint not installed — skipping PDF")
        except Exception as pdf_err:
            result.errors.append(f"PDF generation error: {pdf_err}")

    except Exception as e:
        result.errors.append(f"Stage 5 formatting error: {type(e).__name__}: {e}")

    result.stage_timings["formatting"] = time.time() - t5

    trace.checkpoint("scorecard", result.scorecard_json or {})

    result.pipeline_duration_s = time.time() - t_start

    # Write trace summary
    trace_dir = trace.write_summary(stage_timings=result.stage_timings)
    if trace_dir:
        print(f"\n  📋 Pipeline trace saved to: {trace_dir}/")

    return result


def _build_agent_prompts(
    ticker: str,
    fact_sheet: dict,
    filing_10k: dict | None = None,
    filing_10q: dict | None = None,
    sec_sector_kpis: dict | None = None,
) -> dict:
    """Build research agent prompts from Jinja2 templates.

    Returns dict with agent_1, agent_2, agent_3 prompt strings.
    Templates reference individual filing sections (business, mda, risk_factors)
    and pre-formatted summary variables from the fact sheet.
    """
    import json
    from pathlib import Path

    try:
        from jinja2 import Environment, FileSystemLoader, Undefined
    except ImportError:
        return {}

    prompts_dir = Path(__file__).parent.parent / "prompts"
    if not prompts_dir.exists():
        return {}

    env = Environment(
        loader=FileSystemLoader(str(prompts_dir)),
        undefined=Undefined,
    )

    # ── Identity data ──────────────────────────────────────────
    ident = fact_sheet.get("s1_identity", {})
    meta = fact_sheet.get("_meta", {})
    company_name = ident.get("company_name", ticker)

    # ── Extract individual filing sections ─────────────────────
    # Filing data has top-level keys: business, risk_factors, mda,
    # financial_statements_notes, market_risk, filing_date, period
    f10k = filing_10k or {}
    f10q = filing_10q or {}

    filing_10k_business = f10k.get("business", "")
    filing_10k_risk_factors = f10k.get("risk_factors", "")
    filing_10k_mda = f10k.get("mda", "")
    filing_10k_notes = f10k.get("financial_statements_notes", "")
    filing_10q_mda = f10q.get("mda", "")
    filing_10q_risk_factors = f10q.get("risk_factors", "")

    # ── Build summary strings from fact sheet ──────────────────
    inc = fact_sheet.get("s11_income_statement", {})
    margins = fact_sheet.get("s5_subject_margins", {})
    seg = fact_sheet.get("s3_product_segments", {})
    geo = fact_sheet.get("s3_geographic_segments", {})
    returns = fact_sheet.get("s11_returns", {})
    cash_flow = fact_sheet.get("s11_cash_flow", {})
    bal = fact_sheet.get("s11_balance_sheet", {})
    val = fact_sheet.get("s13_valuation", {})

    latest = meta.get("latest_annual_year", "")
    annual_years = meta.get("annual_years", [])

    def _ly(d, key):
        v = d.get(key) or {}
        return v.get(latest) if isinstance(v, dict) else v

    # Revenue summary
    rev_lines = []
    for yr in annual_years:
        rev_val = (inc.get("revenue_usd_m") or {}).get(yr, "")
        rev_g = (inc.get("revenue_growth_pct") or {}).get(yr, "")
        gm = (margins.get("gross_margin_pct") or {}).get(yr, "")
        om = (margins.get("operating_margin_pct") or {}).get(yr, "")
        nm = (margins.get("net_margin_pct") or {}).get(yr, "")
        rev_lines.append(f"{yr}: Rev {rev_val} | Growth {rev_g} | GM {gm} | OM {om} | NM {nm}")
    revenue_summary = "\n".join(rev_lines) if rev_lines else "Not available"

    # Segment summary
    seg_lines = []
    for s_name, s_data in seg.items():
        if isinstance(s_data, dict) and latest:
            val_item = s_data.get("revenue_usd_m", {}).get(latest) or s_data.get(latest)
            pct = s_data.get("pct_of_total", {}).get(latest, "")
            if val_item:
                seg_lines.append(f"  {s_name}: {val_item} ({pct} of total)")
    segment_summary = "\n".join(seg_lines) if seg_lines else "Not available"

    # Geographic summary
    geo_lines = []
    for g_name, g_data in geo.items():
        if isinstance(g_data, dict) and latest:
            val_item = g_data.get("revenue_usd_m", {}).get(latest) or g_data.get(latest)
            pct = g_data.get("pct_of_total", {}).get(latest, "")
            if val_item:
                geo_lines.append(f"  {g_name}: {val_item} ({pct} of total)")
    geo_summary = "\n".join(geo_lines) if geo_lines else "Not available"

    # Key metrics summary
    key_metrics_lines = [
        f"ROIC: {_ly(returns, 'roic_pct')}",
        f"ROE: {_ly(returns, 'roe_pct')}",
        f"FCF Margin: {_ly(cash_flow, 'fcf_margin_pct')}",
        f"Net Debt/EBITDA: {_ly(bal, 'net_debt_to_ebitda')}",
        f"EPS (Diluted): {_ly(inc, 'eps_diluted')}",
    ]
    if val.get("_current_pe"):
        key_metrics_lines.append(f"P/E: {val['_current_pe']}x")
    if val.get("_current_ev_ebitda"):
        key_metrics_lines.append(f"EV/EBITDA: {val['_current_ev_ebitda']}x")
    key_metrics_summary = "\n".join(key_metrics_lines)

    # Fact sheet as JSON string (truncated)
    # Research agents should only see the sanitized / writer-facing view.
    from pipeline.sanitize import sanitize_for_llm
    fact_sheet_str = json.dumps(sanitize_for_llm(fact_sheet), indent=2, default=str)[:20000]

    # Sector KPIs summary
    sector_kpis_summary = ""
    if sec_sector_kpis:
        sector_kpis_summary = json.dumps(sanitize_for_llm(sec_sector_kpis), indent=2, default=str)[:5000]

    # Sector type for agent 2
    sector_type = ident.get("sector", "")

    # ── Sector-specific guidance for Agent 1 ───────────────────
    sector_analysis_guidance = ""
    try:
        from pipeline.distributors import _get_sector_agent1_guidance
        subsector = ident.get("subsector", "")
        if not subsector and sec_sector_kpis:
            subsector = sec_sector_kpis.get("sector", "")
        sector_analysis_guidance = _get_sector_agent1_guidance(subsector)
    except ImportError:
        pass

    # ── Common template vars ───────────────────────────────────
    common_vars = {
        "ticker": ticker,
        "company_name": company_name,
        "sector": ident.get("sector", ""),
        "industry": ident.get("industry", ""),
        "price": ident.get("price"),
        "market_cap": ident.get("market_cap") or 0,
        "filing_date": f10k.get("filing_date", f10q.get("filing_date", "")),
        # Individual filing sections (not a combined blob)
        "filing_10k_business": filing_10k_business,
        "filing_10k_risk_factors": filing_10k_risk_factors,
        "filing_10k_mda": filing_10k_mda,
        "filing_10k_notes": filing_10k_notes,
        "filing_10q_mda": filing_10q_mda,
        "filing_10q_risk_factors": filing_10q_risk_factors,
        # Pre-formatted summaries
        "revenue_summary": revenue_summary,
        "segment_summary": segment_summary,
        "geo_summary": geo_summary,
        "key_metrics_summary": key_metrics_summary,
        "fact_sheet_summary": fact_sheet_str,
        "sector_kpis_summary": sector_kpis_summary,
        "sector_type": sector_type,
        # Sector-specific guidance for Agent 1
        "sector_analysis_guidance": sector_analysis_guidance,
    }

    result = {}

    # Agent 1: Foundation & Business Analysis
    try:
        tmpl = env.get_template("research_agent_1.jinja2")
        result["agent_1"] = tmpl.render(**common_vars)
    except Exception:
        result["agent_1"] = ""

    # Agent 2: Deep Financial Analysis
    try:
        tmpl = env.get_template("research_agent_2.jinja2")
        result["agent_2"] = tmpl.render(**common_vars)
    except Exception:
        result["agent_2"] = ""

    # Agent 3: Investment Decision & Risk
    # Agent 3 also needs valuation context — detect method from industry
    valuation_method = "dcf"
    valuation_rationale = "Standard FCFF DCF for this sector"
    industry_norm = ident.get("industry", "").strip()
    try:
        from pipeline.distributors import SKIP_DCF_INDUSTRIES, INDUSTRY_VALUATION_CONFIG
        if industry_norm in SKIP_DCF_INDUSTRIES:
            valuation_method = "bank_equity"
            valuation_rationale = f"Financial sector ({industry_norm}) — DCF not applicable, using P/E and P/B peer multiples"
        elif industry_norm in INDUSTRY_VALUATION_CONFIG:
            cfg = INDUSTRY_VALUATION_CONFIG[industry_norm]
            valuation_method = cfg.get("method", "dcf")
            valuation_rationale = cfg.get("rationale", "")
    except ImportError:
        pass

    agent3_vars = {
        **common_vars,
        "valuation_method": valuation_method,
        "valuation_rationale": valuation_rationale,
        "dcf_results_summary": "DCF model will be computed during assembly.",
        "bank_equity_summary": "Bank equity model will be computed during assembly.",
        "nav_summary": "NAV model will be computed during assembly.",
        "peer_multiple_summary": "Peer multiple model will be computed during assembly.",
        "investment_data_summary": fact_sheet_str[:10000],
    }

    try:
        tmpl = env.get_template("research_agent_3.jinja2")
        result["agent_3"] = tmpl.render(**agent3_vars)
    except Exception:
        result["agent_3"] = ""

    return result


async def run_data_only(
    ticker: str,
    years: int = 5,
    quarters: int = 8,
) -> dict:
    """Run only Stage 1 + Stage 2 (data fetch + transforms).

    Useful for testing the data layer without LLM calls.

    Returns:
        Dict with all transformed data ready for the quantitative engine.
    """
    data = await fetch_all_data(ticker, years=years, quarters=quarters)

    # Backfill SEC gaps with FMP
    try:
        _backfill_sec_from_fmp(data)
    except Exception:
        pass

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

    return {
        "ticker": ticker.upper(),
        "data": quant_data,
        "filing_10k": data.filing_10k,
        "filing_10q": data.filing_10q,
        "fmp_profile": data.fmp_profile,
        "sec_profile": data.sec_profile,
        "sec_sector_kpis": data.sec_sector_kpis,
        "fetch_duration_s": data.fetch_duration_s,
        "fetch_errors": data.fetch_errors,
    }


async def run_quant_only(
    ticker: str,
    years: int = 5,
    quarters: int = 8,
) -> dict:
    """Run Stage 1 + Stage 2 + Quantitative Engine (no LLM calls).

    Returns the full formatted quantitative fact sheet.
    """
    raw = await run_data_only(ticker, years=years, quarters=quarters)

    t0 = time.time()
    fact_sheet = build_quantitative_facts(raw["data"])
    quant_duration = time.time() - t0

    t1 = time.time()
    formatted = clean_quantitative_facts(fact_sheet)
    format_duration = time.time() - t1

    t2 = time.time()
    registry_result = build_source_registry(
        fact_sheet=formatted,
        fmp_profile=raw.get("fmp_profile"),
        sec_profile=raw.get("sec_profile"),
    )
    registry_duration = time.time() - t2

    return {
        "ticker": ticker.upper(),
        "fact_sheet": registry_result["fact_sheet"],
        "source_registry": registry_result["source_registry"],
        "sources_appendix": registry_result["sources_appendix"],
        "filing_10k": raw.get("filing_10k"),
        "filing_10q": raw.get("filing_10q"),
        "sec_profile": raw.get("sec_profile"),
        "sec_sector_kpis": raw.get("sec_sector_kpis"),
        "fetch_duration_s": raw["fetch_duration_s"],
        "quant_duration_s": quant_duration,
        "format_duration_s": format_duration,
        "registry_duration_s": registry_duration,
        "fetch_errors": raw["fetch_errors"],
    }
