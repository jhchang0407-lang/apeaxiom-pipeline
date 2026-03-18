"""Sector KPI Table Builder — transforms _sec_sector_kpis into structured tables.

Reads the `_sec_sector_kpis` dict produced by `sec/sectors/*.py` and builds
renderable table rows for assembly injection.  Each sector gets dedicated
table(s) with industry-appropriate metrics (CET1/NIM for banks, combined
ratio for insurance, FFO for REITs, etc.).

Public API:
    build_sector_kpi_tables(sector_kpis) -> dict[str, list[dict]]
"""

from __future__ import annotations

from typing import Any


# ══════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════

def _to_pct(v: Any) -> float | None:
    """Convert a decimal ratio (0.05 = 5%) to percentage, or pass through None."""
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:  # NaN check
            return None
        # If value is already > 1 it's likely already a percentage
        # (e.g. CET1 = 13.2 means 13.2%, not 1320%)
        if abs(f) > 1:
            return round(f, 2)
        return round(f * 100, 2)
    except (ValueError, TypeError):
        return None


def _raw_val(v: Any) -> float | None:
    """Pass through a raw numeric value (e.g. TBV/share, production)."""
    if v is None:
        return None
    try:
        f = float(v)
        return round(f, 2) if f == f else None
    except (ValueError, TypeError):
        return None


def _extract_raw_timeseries(kpis: dict, key: str) -> dict[str, float]:
    """Extract year -> value map from raw metric list [{date, fy, val}]."""
    series = kpis.get(key) or []
    out: dict[str, float] = {}
    for entry in series:
        if isinstance(entry, dict) and entry.get("val") is not None:
            year = entry.get("fy") or (entry.get("date", "")[:4])
            if year:
                out[str(year)] = entry["val"]
    return out


def _get_computed(kpis: dict) -> list[dict]:
    """Get computed metrics from either 'computedRatios' or 'computedMetrics' key.

    Banking uses 'computedRatios', all other sectors use 'computedMetrics'.
    Returns sorted ascending by date.
    """
    computed = kpis.get("computedRatios") or kpis.get("computedMetrics") or []
    if not computed:
        return []
    return sorted(computed, key=lambda r: r.get("date", ""))


def _has_any_data(rows: list[dict], exclude: set[str] | None = None) -> bool:
    """Check if any row has at least one non-None metric value (excluding 'year')."""
    skip = (exclude or set()) | {"year"}
    return any(
        v is not None
        for row in rows
        for k, v in row.items()
        if k not in skip
    )


# ══════════════════════════════════════════════════════════════
# BANKING TABLES
# ══════════════════════════════════════════════════════════════

def build_banking_tables(kpis: dict) -> dict[str, list[dict]]:
    """Build banking-specific KPI tables from SEC XBRL sector data."""
    computed = _get_computed(kpis)
    if not computed:
        return {}

    # Extract raw CET1 timeseries (not in computedRatios)
    cet1_ts = _extract_raw_timeseries(kpis, "cet1Ratio")

    tables: dict[str, list[dict]] = {}

    # Core Banking Metrics
    core_rows = []
    for row in computed:
        year = row.get("date", "")[:4]
        if not year:
            continue
        core_rows.append({
            "year": year,
            "nim_pct": _to_pct(row.get("netInterestMargin")),
            "efficiency_ratio_pct": _to_pct(row.get("efficiencyRatio")),
            "roa_pct": _to_pct(row.get("roa")),
            "roe_pct": _to_pct(row.get("roe")),
            "fee_income_ratio_pct": _to_pct(row.get("feeIncomeRatio")),
        })
    if _has_any_data(core_rows):
        tables["bank_core_metrics"] = core_rows

    # Credit Quality
    credit_rows = []
    for row in computed:
        year = row.get("date", "")[:4]
        if not year:
            continue
        credit_rows.append({
            "year": year,
            "npl_ratio_pct": _to_pct(row.get("nplRatio")),
            "nco_rate_pct": _to_pct(row.get("netChargeOffRate")),
            "reserve_coverage_pct": _to_pct(row.get("reserveCoverage")),
            "provision_to_loans_pct": _to_pct(row.get("provisionToLoans")),
        })
    if _has_any_data(credit_rows):
        tables["bank_credit_quality"] = credit_rows

    # Capital & Funding
    capital_rows = []
    for row in computed:
        year = row.get("date", "")[:4]
        if not year:
            continue
        capital_rows.append({
            "year": year,
            "cet1_ratio_pct": _to_pct(cet1_ts.get(year)),
            "loan_to_deposit_pct": _to_pct(row.get("loanToDepositRatio")),
            "tbv_per_share": _raw_val(row.get("tangibleBookValuePerShare")),
            "cost_of_deposits_pct": _to_pct(row.get("costOfDeposits")),
        })
    if _has_any_data(capital_rows):
        tables["bank_capital_funding"] = capital_rows

    return tables


# ══════════════════════════════════════════════════════════════
# INSURANCE TABLES
# ══════════════════════════════════════════════════════════════

def build_insurance_tables(kpis: dict) -> dict[str, list[dict]]:
    """Build insurance-specific KPI tables."""
    computed = _get_computed(kpis)
    if not computed:
        return {}

    tables: dict[str, list[dict]] = {}

    rows = []
    for row in computed:
        year = row.get("date", "")[:4]
        if not year:
            continue
        rows.append({
            "year": year,
            "combined_ratio_pct": _to_pct(row.get("combinedRatio")),
            "loss_ratio_pct": _to_pct(row.get("lossRatio")),
            "expense_ratio_pct": _to_pct(row.get("expenseRatio")),
            "roe_pct": _to_pct(row.get("roe")),
        })
    if _has_any_data(rows):
        tables["insurance_underwriting"] = rows

    return tables


# ══════════════════════════════════════════════════════════════
# REIT TABLES
# ══════════════════════════════════════════════════════════════

def build_reit_tables(kpis: dict) -> dict[str, list[dict]]:
    """Build REIT-specific KPI tables."""
    computed = _get_computed(kpis)
    if not computed:
        return {}

    tables: dict[str, list[dict]] = {}

    rows = []
    for row in computed:
        year = row.get("date", "")[:4]
        if not year:
            continue
        rows.append({
            "year": year,
            "ffo_per_share": _raw_val(row.get("ffoPerShare")),
            "affo_per_share": _raw_val(row.get("affoPerShare")),
            "noi_margin_pct": _to_pct(row.get("noiMargin")),
            "debt_to_assets_pct": _to_pct(row.get("debtToAssets")),
        })
    if _has_any_data(rows):
        tables["reit_operations"] = rows

    return tables


# ══════════════════════════════════════════════════════════════
# ENERGY TABLES
# ══════════════════════════════════════════════════════════════

def build_energy_tables(kpis: dict) -> dict[str, list[dict]]:
    """Build energy-specific KPI tables.

    Energy XBRL data rarely includes per-BOE production metrics; the
    computedMetrics module extracts what's available from income statement
    and balance sheet tags.
    """
    computed = _get_computed(kpis)
    if not computed:
        return {}

    tables: dict[str, list[dict]] = {}

    rows = []
    for row in computed:
        year = row.get("date", "")[:4]
        if not year:
            continue
        rows.append({
            "year": year,
            "production_mboed": _raw_val(row.get("productionMboed")),
            "reserve_replacement_pct": _to_pct(row.get("reserveReplacementRatio")),
            "finding_cost": _raw_val(row.get("findingCostPerBoe")),
            "lifting_cost": _raw_val(row.get("liftingCostPerBoe")),
        })

    # If per-BOE data is sparse, build a margin/returns table instead
    if _has_any_data(rows):
        tables["energy_operations"] = rows

    # Always try to build a margin/capital table from what's available
    margin_rows = []
    for row in computed:
        year = row.get("date", "")[:4]
        if not year:
            continue
        margin_rows.append({
            "year": year,
            "operating_margin_pct": _to_pct(row.get("operatingMargin")),
            "net_margin_pct": _to_pct(row.get("netMargin")),
            "fcf_margin_pct": _to_pct(row.get("fcfMargin")),
            "roce_pct": _to_pct(row.get("roce")),
        })
    if _has_any_data(margin_rows):
        tables["energy_financials"] = margin_rows

    return tables


# ══════════════════════════════════════════════════════════════
# RETAIL TABLES
# ══════════════════════════════════════════════════════════════

def build_retail_tables(kpis: dict) -> dict[str, list[dict]]:
    """Build retail-specific KPI tables."""
    computed = _get_computed(kpis)
    if not computed:
        return {}

    tables: dict[str, list[dict]] = {}

    rows = []
    for row in computed:
        year = row.get("date", "")[:4]
        if not year:
            continue
        rows.append({
            "year": year,
            "inventory_turnover": _raw_val(row.get("inventoryTurnover")),
            "gross_margin_pct": _to_pct(row.get("grossMargin")),
            "sga_to_revenue_pct": _to_pct(row.get("sgaAsPercentOfRevenue") or row.get("sgaToRevenue")),
            "operating_margin_pct": _to_pct(row.get("operatingMargin")),
        })
    if _has_any_data(rows):
        tables["retail_operations"] = rows

    return tables


# ══════════════════════════════════════════════════════════════
# TECH / SAAS TABLES
# ══════════════════════════════════════════════════════════════

def build_tech_tables(kpis: dict) -> dict[str, list[dict]]:
    """Build tech/SaaS-specific KPI tables from SEC XBRL sector data."""
    computed = _get_computed(kpis)
    if not computed:
        return {}

    tables: dict[str, list[dict]] = {}

    # SaaS / Growth metrics
    rows = []
    for row in computed:
        year = row.get("date", "")[:4]
        if not year:
            continue
        rows.append({
            "year": year,
            "gross_margin_pct": _to_pct(row.get("grossMargin")),
            "operating_margin_pct": _to_pct(row.get("operatingMargin")),
            "fcf_margin_pct": _to_pct(row.get("fcfMargin")),
            "rd_intensity_pct": _to_pct(row.get("rdIntensity")),
        })
    if _has_any_data(rows):
        tables["tech_financials"] = rows

    # SaaS-specific metrics (only if available)
    saas_rows = []
    for row in computed:
        year = row.get("date", "")[:4]
        if not year:
            continue
        rule_of_40 = _raw_val(row.get("ruleOf40"))
        nrr = _to_pct(row.get("nrrProxy"))
        rev_growth = _to_pct(row.get("revenueGrowth"))
        sbc = _to_pct(row.get("sbcAsPercentOfRevenue"))
        saas_rows.append({
            "year": year,
            "revenue_growth_pct": rev_growth,
            "rule_of_40": rule_of_40,
            "nrr_proxy_pct": nrr,
            "sbc_pct_rev": sbc,
        })
    if _has_any_data(saas_rows):
        tables["tech_growth_metrics"] = saas_rows

    return tables


# ══════════════════════════════════════════════════════════════
# HEALTHCARE / PHARMA TABLES
# ══════════════════════════════════════════════════════════════

def build_healthcare_tables(kpis: dict) -> dict[str, list[dict]]:
    """Build healthcare/pharma-specific KPI tables from SEC XBRL data."""
    computed = _get_computed(kpis)
    if not computed:
        return {}

    tables: dict[str, list[dict]] = {}

    rows = []
    for row in computed:
        year = row.get("date", "")[:4]
        if not year:
            continue
        rows.append({
            "year": year,
            "gross_margin_pct": _to_pct(row.get("grossMargin")),
            "rd_intensity_pct": _to_pct(row.get("rdIntensity")),
            "sga_to_revenue_pct": _to_pct(row.get("sgaAsPercentOfRevenue")),
            "net_margin_pct": _to_pct(row.get("netMargin")),
        })
    if _has_any_data(rows):
        tables["healthcare_financials"] = rows

    return tables


# ══════════════════════════════════════════════════════════════
# INDUSTRIALS TABLES
# ══════════════════════════════════════════════════════════════

def build_industrials_tables(kpis: dict) -> dict[str, list[dict]]:
    """Build industrials-specific KPI tables from SEC XBRL data."""
    computed = _get_computed(kpis)
    if not computed:
        return {}

    tables: dict[str, list[dict]] = {}

    rows = []
    for row in computed:
        year = row.get("date", "")[:4]
        if not year:
            continue
        rows.append({
            "year": year,
            "operating_margin_pct": _to_pct(row.get("operatingMargin")),
            "roic_pct": _to_pct(row.get("roic")),
            "book_to_bill": _raw_val(row.get("bookToBill")),
            "backlog_to_revenue": _raw_val(row.get("backlogToRevenue")),
        })
    if _has_any_data(rows):
        tables["industrials_operations"] = rows

    return tables


# ══════════════════════════════════════════════════════════════
# UTILITIES TABLES
# ══════════════════════════════════════════════════════════════

def build_utilities_tables(kpis: dict) -> dict[str, list[dict]]:
    """Build utilities-specific KPI tables."""
    computed = _get_computed(kpis)
    if not computed:
        return {}

    tables: dict[str, list[dict]] = {}

    rows = []
    for row in computed:
        year = row.get("date", "")[:4]
        if not year:
            continue
        rows.append({
            "year": year,
            "operating_margin_pct": _to_pct(row.get("operatingMargin")),
            "net_margin_pct": _to_pct(row.get("netMargin")),
            "roe_pct": _to_pct(row.get("roe")),
            "debt_to_ebitda": _raw_val(row.get("debtToEbitda")),
        })
    if _has_any_data(rows):
        tables["utilities_operations"] = rows

    return tables


# ══════════════════════════════════════════════════════════════
# CONSUMER STAPLES TABLES
# ══════════════════════════════════════════════════════════════

def build_consumer_staples_tables(kpis: dict) -> dict[str, list[dict]]:
    """Build consumer-staples KPI tables — pricing power, cash generation, payout."""
    computed = _get_computed(kpis)
    if not computed:
        return {}

    tables: dict[str, list[dict]] = {}

    # Pricing power & margins
    margin_rows = []
    for row in computed:
        year = row.get("date", "")[:4]
        if not year:
            continue
        margin_rows.append({
            "year": year,
            "gross_margin_pct": _to_pct(row.get("grossMargin")),
            "operating_margin_pct": _to_pct(row.get("operatingMargin")),
            "revenue_growth_pct": _to_pct(row.get("revenueGrowth")),
            "rd_intensity_pct": _to_pct(row.get("rdIntensity")),
        })
    if _has_any_data(margin_rows):
        tables["staples_pricing_and_margins"] = margin_rows

    # Cash generation & capital return
    cash_rows = []
    for row in computed:
        year = row.get("date", "")[:4]
        if not year:
            continue
        cash_rows.append({
            "year": year,
            "fcf_conversion_pct": _to_pct(row.get("fcfConversion")),
            "fcf_margin_pct": _to_pct(row.get("fcfMargin")),
            "payout_ratio_pct": _to_pct(row.get("payoutRatio")),
            "dividend_growth_pct": _to_pct(row.get("dividendGrowth")),
        })
    if _has_any_data(cash_rows):
        tables["staples_cash_and_payout"] = cash_rows

    return tables


# ══════════════════════════════════════════════════════════════
# MATERIALS TABLES
# ══════════════════════════════════════════════════════════════

def build_materials_tables(kpis: dict) -> dict[str, list[dict]]:
    """Build materials-sector KPI tables — capital intensity, cycle returns, leverage."""
    computed = _get_computed(kpis)
    if not computed:
        return {}

    tables: dict[str, list[dict]] = {}

    # Operating & capital efficiency
    ops_rows = []
    for row in computed:
        year = row.get("date", "")[:4]
        if not year:
            continue
        ops_rows.append({
            "year": year,
            "operating_margin_pct": _to_pct(row.get("operatingMargin")),
            "ebitda_margin_pct": _to_pct(row.get("ebitdaMargin")),
            "roic_pct": _to_pct(row.get("roic")),
            "roce_pct": _to_pct(row.get("roce")),
        })
    if _has_any_data(ops_rows):
        tables["materials_returns"] = ops_rows

    # Capital intensity & leverage
    cap_rows = []
    for row in computed:
        year = row.get("date", "")[:4]
        if not year:
            continue
        cap_rows.append({
            "year": year,
            "capex_intensity_pct": _to_pct(row.get("capexIntensity")),
            "debt_to_ebitda": _raw_val(row.get("debtToEbitda")),
            "fcf_margin_pct": _to_pct(row.get("fcfMargin")),
            "fixed_asset_turnover": _raw_val(row.get("fixedAssetTurnover")),
        })
    if _has_any_data(cap_rows):
        tables["materials_capital"] = cap_rows

    return tables


# ══════════════════════════════════════════════════════════════
# DISPATCHER
# ══════════════════════════════════════════════════════════════

SECTOR_TABLE_BUILDERS: dict[str, callable] = {
    "banking": build_banking_tables,
    "insurance": build_insurance_tables,
    "reits": build_reit_tables,
    "energy": build_energy_tables,
    "retail": build_retail_tables,
    "tech": build_tech_tables,
    "healthcare": build_healthcare_tables,
    "industrials": build_industrials_tables,
    "utilities": build_utilities_tables,
    "consumer_staples": build_consumer_staples_tables,
    "materials": build_materials_tables,
}


def build_sector_kpi_tables(sector_kpis: dict) -> dict[str, list[dict]]:
    """Main entry: dispatch to sector-specific table builder.

    Args:
        sector_kpis: The _sec_sector_kpis dict from data_fetcher,
                     with keys: sector, sic, kpis, (optional) message

    Returns:
        Dict of table_name -> list[dict] rows, ready for assembly rendering.
    """
    if not sector_kpis:
        return {}

    sector = sector_kpis.get("sector", "")
    kpis = sector_kpis.get("kpis", {})
    if not kpis:
        return {}

    builder = SECTOR_TABLE_BUILDERS.get(sector)
    if not builder:
        return {}

    try:
        return builder(kpis)
    except Exception:
        return {}
