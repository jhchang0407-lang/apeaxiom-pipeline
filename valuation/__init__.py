"""Valuation Models — Industry-Specific.

Dispatcher that selects the right valuation model based on industry
and executes it with data extracted from the quantitative fact sheet.

Models:
  - DCF: Tech, Consumer, Healthcare, Industrials
  - Bank Equity: Banks, Insurance, Credit Services
  - DDM: Utilities, REITs (high-payout regulated companies)
  - NAV: O&G, Mining, REITs
  - Peer Multiples: Airlines, Cyclicals, Groceries (last resort)
"""

from __future__ import annotations

import math
from typing import Any

from valuation.industry_config import (
    detect_valuation_mode,
    ValuationConfig,
    SKIP_DCF_INDUSTRIES,
    INDUSTRY_VALUATION_CONFIG,
)
from valuation.dcf import DCFAnchors, DCFResult, run_dcf
from valuation.bank_equity import BankEquityAnchors, BankEquityResult, run_bank_equity
from valuation.ddm import DDMAnchors, DDMResult, run_ddm
from valuation.nav import NAVAnchors, NAVResult, run_nav
from valuation.peer_multiples import (
    PeerMultipleAnchors,
    PeerMultipleResult,
    run_peer_multiples,
)

__all__ = [
    "run_valuation",
    "detect_valuation_mode",
    "ValuationConfig",
    "SKIP_DCF_INDUSTRIES",
    "INDUSTRY_VALUATION_CONFIG",
    "DCFResult",
    "BankEquityResult",
    "DDMResult",
    "NAVResult",
    "PeerMultipleResult",
]


# ═══════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════


def _safe_num(val: Any, default: float = 0.0) -> float:
    """Coerce *val* to float; return *default* for None / NaN / non-numeric."""
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return default if math.isnan(val) else float(val)
    if isinstance(val, str):
        import re
        cleaned = re.sub(r"[,$%xX]", "", val).strip()
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return default
    return default


def _latest_year(time_series: dict) -> str:
    """Return the latest key (year) from a time-series dict."""
    if not time_series:
        return ""
    return max(
        (k for k in time_series if time_series.get(k) is not None),
        default="",
    )


def _best_latest_year(section: dict, *keys: str) -> str:
    """Find the latest year across multiple time series in a section.

    Useful when one series (e.g. revenue) is sparse but others
    (e.g. EPS, net_income) have data for more recent years.
    """
    best = ""
    for key in keys:
        ts = section.get(key)
        if isinstance(ts, dict):
            ly = _latest_year(ts)
            if ly > best:
                best = ly
    return best


def _get_ly(d: dict, key: str, ly: str, default: Any = None) -> Any:
    """Get d[key][ly] with safety, trying raw and formatted fact sheet paths."""
    ts = d.get(key)
    if isinstance(ts, dict):
        return ts.get(ly, default)
    return ts if ts is not None else default


def _avg_recent(time_series: dict, n: int = 3) -> float | None:
    """Average of the most recent *n* non-None values in a year-keyed dict."""
    if not time_series:
        return None
    years = sorted(k for k in time_series if time_series[k] is not None)
    recent = years[-n:]
    if not recent:
        return None
    vals = [_safe_num(time_series[y]) for y in recent]
    valid = [v for v in vals if v != 0]
    return sum(valid) / len(valid) if valid else None


def _revenue_cagr(rev_series: dict, periods: int = 3) -> float | None:
    """Calculate revenue CAGR over *periods* years from a year-keyed dict."""
    if not rev_series:
        return None
    years = sorted(k for k in rev_series if rev_series[k] is not None)
    if len(years) < 2:
        return None
    end_yr = years[-1]
    start_idx = max(0, len(years) - periods - 1)
    start_yr = years[start_idx]
    actual_periods = len(years) - 1 - start_idx
    if actual_periods <= 0:
        return None
    start_rev = _safe_num(rev_series[start_yr])
    end_rev = _safe_num(rev_series[end_yr])
    if start_rev <= 0 or end_rev <= 0:
        return None
    return (end_rev / start_rev) ** (1 / actual_periods) - 1


# Industry-average multiples when FMP peer data is empty.
# Source: historical medians for major sectors (Damodaran / Bloomberg).
# Conservative (low end of the typical range) to avoid overvaluation.
INDUSTRY_DEFAULT_MULTIPLES: dict[str, dict[str, float]] = {
    "oil_gas": {"ev_ebitda": 5.5, "pe": 11.0, "pb": 1.5},
    "mining": {"ev_ebitda": 6.0, "pe": 12.0, "pb": 1.6},
    "airlines": {"ev_ebitda": 6.5, "pe": 10.0},
    "utilities": {"ev_ebitda": 11.0, "pe": 16.0},
    "reit": {"ev_ebitda": 15.0, "pe": 20.0},
    "telecom": {"ev_ebitda": 7.0, "pe": 13.0},
    "grocery": {"ev_ebitda": 8.5, "pe": 14.0},
    "shipping": {"ev_ebitda": 6.0, "pe": 10.0},
    "default": {"ev_ebitda": 10.0, "pe": 15.0},
}


def _industry_bucket(industry: str) -> str:
    """Map an industry string to an INDUSTRY_DEFAULT_MULTIPLES key."""
    ind_lower = industry.lower()
    if any(w in ind_lower for w in ("oil", "gas", "petroleum", "energy integrated")):
        return "oil_gas"
    if any(w in ind_lower for w in ("gold", "silver", "copper", "mining", "metal")):
        return "mining"
    if "airline" in ind_lower:
        return "airlines"
    if "utilit" in ind_lower or "electric" in ind_lower or "power" in ind_lower:
        return "utilities"
    if "reit" in ind_lower:
        return "reit"
    if "telecom" in ind_lower:
        return "telecom"
    if any(w in ind_lower for w in ("grocer", "food distribution")):
        return "grocery"
    if "shipping" in ind_lower or "marine" in ind_lower:
        return "shipping"
    return "default"


# ═══════════════════════════════════════════════════════════════════════
# FACT-SHEET DATA EXTRACTION
# ═══════════════════════════════════════════════════════════════════════


def _resolve_section(fs: dict, key: str) -> dict:
    """Get section data, preferring raw (_meta._raw) over formatted."""
    raw = (fs.get("_meta") or {}).get("_raw") or {}
    return raw.get(key) or fs.get(key) or {}


def _compute_ebitda(inc: dict, cf: dict, ly: str) -> float:
    """Compute EBITDA from components when direct EBITDA field is null.

    EBITDA = Net Income + Income Tax + Interest Expense + D&A
    """
    # Try direct EBITDA first
    ebitda = _safe_num(_get_ly(inc, "ebitda_usd_m", ly))
    if ebitda > 0:
        return ebitda
    ebitda = _safe_num(_get_ly(cf, "ebitda_usd_m", ly))
    if ebitda > 0:
        return ebitda

    # Synthesize from components
    net_income = _safe_num(_get_ly(inc, "net_income_usd_m", ly))
    tax = _safe_num(_get_ly(inc, "income_tax_expense_usd_m", ly))
    interest = _safe_num(_get_ly(inc, "interest_expense_usd_m", ly))
    da = _safe_num(_get_ly(cf, "da_usd_m", ly))

    if net_income > 0 and da > 0:
        return net_income + tax + interest + da

    # Last resort: operating cash flow + tax + interest (proxy)
    ocf = _safe_num(_get_ly(cf, "operating_cash_flow_usd_m", ly))
    if ocf > 0:
        return ocf + tax + interest  # OCF ≈ EBITDA - Δ working capital

    return 0.0


def _get_shares(fs: dict, ly: str) -> float:
    """Get shares diluted from multiple sources with fallbacks.

    Tries (in order):
    1. shares_diluted_millions from share data section
    2. Compute from market_cap / price
    3. Compute from net_income / eps
    4. 0.0 — models treat non-positive shares as an error
    """
    share = _resolve_section(fs, "s5_share_data")
    shares = _safe_num(_get_ly(share, "shares_diluted_millions", ly))
    if shares > 0:
        return shares

    # Fallback 1: market_cap / price → shares (in millions)
    ident = fs.get("s1_identity") or {}
    mkt_cap = _safe_num(ident.get("market_cap"))  # In raw $
    price = _safe_num(ident.get("price"))
    if mkt_cap > 0 and price > 0:
        return mkt_cap / price / 1e6  # Convert to millions

    # Fallback 2: net_income / eps → shares (in millions)
    inc = _resolve_section(fs, "s11_income_statement")
    net_income = _safe_num(_get_ly(inc, "net_income_usd_m", ly))
    eps = _safe_num(_get_ly(inc, "eps_diluted", ly))
    if net_income > 0 and eps > 0:
        return net_income / eps  # Already in millions (net_income_usd_m / eps)

    return 0.0  # No reliable share count — let models error out cleanly


def _latest_kpi_value(sector_kpis: dict, key: str) -> float | None:
    """Latest non-None value of *key* from the sector KPI computed rows.

    fs["_sec_sector_kpis"] has shape {"sector", "sic", "sicDescription",
    "kpis": {...}} where kpis holds per-date rows under "computedRatios"
    (banks/insurance) or "computedMetrics" (other sectors).
    """
    kpis = sector_kpis.get("kpis") or {}
    rows = kpis.get("computedRatios") or kpis.get("computedMetrics") or []
    if not isinstance(rows, list):
        return None
    for row in sorted(
        (r for r in rows if isinstance(r, dict)),
        key=lambda r: r.get("date", ""),
        reverse=True,
    ):
        val = row.get(key)
        if isinstance(val, (int, float)):
            return float(val)
    return None


def _latest_series_value(sector_kpis: dict, key: str) -> float | None:
    """Latest 'val' from a raw KPI series like kpis["cet1Ratio"] = [{date, fy, val}]."""
    kpis = sector_kpis.get("kpis") or {}
    series = kpis.get(key)
    if not isinstance(series, list):
        return None
    for entry in sorted(
        (e for e in series if isinstance(e, dict)),
        key=lambda e: e.get("date", ""),
        reverse=True,
    ):
        val = entry.get("val")
        if isinstance(val, (int, float)):
            return float(val)
    return None


def _extract_dcf_anchors(fs: dict, overrides: dict) -> DCFAnchors:
    """Build DCFAnchors from fact sheet data."""
    inc = _resolve_section(fs, "s11_income_statement")
    cf = _resolve_section(fs, "s11_cash_flow")
    cap = _resolve_section(fs, "s2_capital_structure")
    share = _resolve_section(fs, "s5_share_data")
    bal = _resolve_section(fs, "s11_balance_sheet")
    returns = _resolve_section(fs, "s11_returns")

    rev_series = inc.get("revenue_usd_m") or {}
    ly = _latest_year(rev_series)

    # Revenue (in millions — units are consistent throughout)
    revenue = _safe_num(_get_ly(inc, "revenue_usd_m", ly, 0))

    # FCF margin — average of last 3 years for stability.
    # No fabricated default: missing margin makes the DCF error out.
    fcf_margin_series = cf.get("fcf_margin_pct") or {}
    fcf_margin_avg = _avg_recent(fcf_margin_series, 3)
    fcf_margin_val = fcf_margin_avg or _safe_num(_get_ly(cf, "fcf_margin_pct", ly))
    fcf_margin = fcf_margin_val / 100.0 if fcf_margin_val else None

    # Revenue growth — blend of recent performance and trend
    # Use the higher of: 3Y CAGR vs weighted recent growth (60% latest + 40% prior)
    cagr_3y = _revenue_cagr(rev_series, 3)
    growth_series = inc.get("revenue_growth_pct") or {}
    growth_years = sorted(k for k in growth_series if growth_series[k] is not None)
    latest_growth = None
    if growth_years:
        latest_growth = _safe_num(growth_series[growth_years[-1]]) / 100.0
        if len(growth_years) >= 2:
            prior_growth = _safe_num(growth_series[growth_years[-2]]) / 100.0
            weighted_growth = 0.6 * latest_growth + 0.4 * prior_growth
        else:
            weighted_growth = latest_growth
    else:
        weighted_growth = None

    # Also check for analyst forward estimates (from FMP)
    estimates = _resolve_section(fs, "s10_forward_estimates")
    fwd_growth = None
    if estimates:
        # Look for next year revenue estimate
        fwd_rev = estimates.get("revenue_est_next_yr") or estimates.get("fwd_revenue_growth_pct")
        if fwd_rev:
            fwd_growth = _safe_num(fwd_rev) / 100.0 if _safe_num(fwd_rev) > 1 else _safe_num(fwd_rev)

    # Pick the best growth estimate:
    # 1. Forward estimates (analyst consensus) if available
    # 2. Higher of (weighted recent, 3Y CAGR) to avoid downward bias from one bad year
    # 3. Clamp to -15%..25% range to avoid extreme projections
    candidates = [g for g in [fwd_growth, weighted_growth, cagr_3y] if g is not None]
    if candidates:
        rev_growth = max(candidates)
        rev_growth = max(-0.15, min(rev_growth, 0.25))  # Clamp -15% to 25%
    else:
        rev_growth = 0.05  # Default 5%

    # Net debt
    net_debt = _safe_num(
        _get_ly(cap, "net_debt_usd_m", ly)
        or _get_ly(bal, "net_debt_usd_m", ly, 0)
    )

    # Shares diluted (millions) — with fallbacks
    shares = _get_shares(fs, ly)

    # SBC
    sbc = _safe_num(_get_ly(share, "sbc_usd_m", ly, 0))

    # WACC — CAPM with quality adjustment
    # Risk-free: 10Y treasury (~3.8-4.2%), ERP: 4.5% (Damodaran median)
    beta = _safe_num(fs.get("s1_identity", {}).get("beta"))
    if beta and 0.3 < beta < 3.0:
        wacc = 0.038 + beta * 0.045  # 3.8% rf + beta * 4.5% ERP
    else:
        wacc = 0.09  # Default 9%

    # Quality adjustment: high-ROIC companies have lower cost of capital
    # (market rewards quality with premium valuations / lower discount rates)
    # NOTE: ROIC is None/0 for banks and insurance (meaningless formula) — skip adjustment.
    roic = _safe_num(_get_ly(returns, "roic_pct", ly))
    if roic > 50:
        wacc *= 0.92  # Very high quality gets ~8% reduction
    elif roic > 30:
        wacc *= 0.95  # High quality gets ~5% reduction
    # Clamp WACC to reasonable range
    wacc = max(0.065, min(wacc, 0.14))

    # Terminal growth
    terminal_growth = 0.025

    # Growth decay — slower for high-quality growth companies
    # (roic == 0 for banks/insurance → falls to default, which is correct)
    if roic > 25:
        decay = 0.90  # High ROIC companies sustain growth longer
    elif roic > 15:
        decay = 0.875
    else:
        decay = 0.85  # Default

    # Apply overrides
    return DCFAnchors(
        revenue_latest=overrides.get("revenue_latest", revenue),
        fcf_margin=overrides.get("fcf_margin", fcf_margin),
        revenue_growth=overrides.get("revenue_growth", rev_growth),
        net_debt=overrides.get("net_debt", net_debt),
        shares_diluted=overrides.get("shares_diluted", shares),
        sbc_annual=overrides.get("sbc_annual", sbc),
        wacc=overrides.get("wacc", wacc),
        terminal_growth=overrides.get("terminal_growth", terminal_growth),
        growth_decay_rate=overrides.get("growth_decay_rate", decay),
    )


def _extract_bank_anchors(fs: dict, overrides: dict) -> BankEquityAnchors:
    """Build BankEquityAnchors from fact sheet data."""
    inc = _resolve_section(fs, "s11_income_statement")
    bal = _resolve_section(fs, "s11_balance_sheet")
    returns = _resolve_section(fs, "s11_returns")
    sector_kpis = fs.get("_sec_sector_kpis") or {}

    rev_series = inc.get("revenue_usd_m") or {}
    ly = _latest_year(rev_series)

    shares = _get_shares(fs, ly)

    # Book value per share — compute from total_equity / shares if not directly available
    bvps = _safe_num(
        _get_ly(bal, "book_value_per_share", ly)
        or _get_ly(bal, "bvps", ly)
    )
    if bvps <= 0:
        total_equity = _safe_num(_get_ly(bal, "total_equity_usd_m", ly))
        if total_equity > 0 and shares > 0:
            bvps = total_equity / shares

    # Tangible BV per share — compute from (equity - goodwill/intangibles) / shares
    tbvps = _safe_num(_get_ly(bal, "tangible_bv_per_share", ly))
    if tbvps <= 0:
        total_equity = _safe_num(_get_ly(bal, "total_equity_usd_m", ly))
        gi = _safe_num(_get_ly(bal, "goodwill_and_intangibles_usd_m", ly))
        if total_equity > 0 and shares > 0:
            tbvps = (total_equity - gi) / shares

    # ROE — average of last 3 years for stability
    roe_series = returns.get("roe_pct") or {}
    roe_avg = _avg_recent(roe_series, 3)
    roe = (roe_avg if roe_avg else _safe_num(_get_ly(returns, "roe_pct", ly, 10))) / 100.0

    # EPS
    eps = _safe_num(_get_ly(inc, "eps_diluted", ly))

    # Dividend per share — compute from dividends_paid / shares if not available
    cap_alloc = _resolve_section(fs, "s9_capital_allocation")
    dps = _safe_num(_get_ly(cap_alloc, "dividend_per_share", ly))
    if dps <= 0:
        divs_paid = _safe_num(_get_ly(cap_alloc, "dividends_paid_usd_m", ly))
        if divs_paid > 0 and shares > 0:
            dps = divs_paid / shares

    # Cost of equity — CAPM + financial sector risk premium
    # Banks face regulatory, credit cycle, and systemic risks beyond market beta
    beta = _safe_num(fs.get("s1_identity", {}).get("beta"))
    if beta and 0.3 < beta < 3.0:
        coe = 0.038 + beta * 0.045  # 3.8% rf + beta * 4.5% ERP
    else:
        coe = 0.10  # Default 10% for banks
    coe += 0.01  # +1% financial sector risk premium
    coe = max(0.085, min(coe, 0.14))  # Clamp 8.5-14% for financials

    # Sustainable growth — banks rarely grow faster than nominal GDP long-term
    # ROE × retention gives theoretical max, but actual bank growth is GDP-like
    if roe > 0 and dps > 0 and eps > 0:
        payout = dps / eps
        g_theoretical = roe * (1 - payout)
        g = max(0.02, min(g_theoretical, 0.04))  # Max 4% for banks
    else:
        g = 0.03

    # Sector KPIs — pulled from the latest period of the bank extractor output
    # (kpis["cet1Ratio"] raw series; computedRatios rows for the ratios)
    cet1 = _latest_series_value(sector_kpis, "cet1Ratio")
    nim = _latest_kpi_value(sector_kpis, "netInterestMargin")
    eff_ratio = _latest_kpi_value(sector_kpis, "efficiencyRatio")
    npl = _latest_kpi_value(sector_kpis, "nplRatio")

    return BankEquityAnchors(
        book_value_per_share=overrides.get("book_value_per_share", bvps),
        tangible_bv_per_share=overrides.get("tangible_bv_per_share", tbvps),
        roe=overrides.get("roe", roe),
        cost_of_equity=overrides.get("cost_of_equity", coe),
        sustainable_growth=overrides.get("sustainable_growth", g),
        eps_latest=overrides.get("eps_latest", eps),
        dividend_per_share=overrides.get("dividend_per_share", dps),
        cet1_ratio=cet1,
        nim=nim,
        efficiency_ratio=eff_ratio,
        npl_ratio=npl,
    )


def _extract_nav_anchors(fs: dict, industry: str, overrides: dict) -> NAVAnchors:
    """Build NAVAnchors from fact sheet data."""
    inc = _resolve_section(fs, "s11_income_statement")
    cf = _resolve_section(fs, "s11_cash_flow")
    cap = _resolve_section(fs, "s2_capital_structure")
    bal = _resolve_section(fs, "s11_balance_sheet")
    sector_kpis = fs.get("_sec_sector_kpis") or {}
    peers = fs.get("s6_peers") or {}
    peer_medians = peers.get("peer_medians") or {}

    # Use best latest year across multiple series (revenue is sparse for some companies)
    ly = _best_latest_year(
        inc, "revenue_usd_m", "net_income_usd_m", "eps_diluted",
    ) or _best_latest_year(
        cf, "operating_cash_flow_usd_m", "free_cash_flow_usd_m",
    )
    if not ly:
        ly = _latest_year(inc.get("revenue_usd_m") or {})

    net_debt = _safe_num(
        _get_ly(cap, "net_debt_usd_m", ly)
        or _get_ly(bal, "net_debt_usd_m", ly, 0)
    )
    shares = _get_shares(fs, ly)

    # Determine sub-sector
    ind_lower = industry.lower()
    if "oil" in ind_lower or "gas" in ind_lower:
        sector = "oil_gas"
    elif any(w in ind_lower for w in ("gold", "silver", "copper", "mining", "metal")):
        sector = "mining"
    elif "reit" in ind_lower:
        sector = "reit"
    else:
        sector = "other"

    # EBITDA — with computation fallback (net_income + tax + interest + D&A)
    ebitda = _compute_ebitda(inc, cf, ly)

    # Peer EV/EBITDA median for fallback — try peer data, then industry defaults
    peer_ev_ebitda = _safe_num(peer_medians.get("ev_to_ebitda"))
    if not peer_ev_ebitda:
        bucket = _industry_bucket(industry)
        defaults = INDUSTRY_DEFAULT_MULTIPLES.get(bucket, INDUSTRY_DEFAULT_MULTIPLES["default"])
        peer_ev_ebitda = defaults.get("ev_ebitda", 0)

    # Sector-specific data from the SEC sector extractor outputs.
    # Energy: kpis["provedReserves"] raw series (volume units).
    # REITs: computedMetrics rows carry "noi" (raw $) and "capRateProxy".
    # No extractor produces a standardized measure, daily production, or
    # occupancy — those anchors stay None.
    proved_reserves = _latest_series_value(sector_kpis, "provedReserves")
    noi = _latest_kpi_value(sector_kpis, "noi")
    if noi is not None:
        noi = noi / 1e6  # XBRL values are raw dollars; anchors use $M
    cap_rate = _latest_kpi_value(sector_kpis, "capRateProxy")

    return NAVAnchors(
        net_debt=overrides.get("net_debt", net_debt),
        shares_diluted=overrides.get("shares_diluted", shares),
        sector=overrides.get("sector", sector),
        proved_reserves_boe=proved_reserves if proved_reserves else None,
        standardized_measure=None,
        production_boe_per_day=None,
        noi=noi or None,
        cap_rate=cap_rate / 100.0 if cap_rate and cap_rate > 1 else cap_rate or None,
        occupancy_rate=None,
        ebitda=overrides.get("ebitda", ebitda),
        peer_ev_ebitda_median=overrides.get("peer_ev_ebitda_median", peer_ev_ebitda or None),
    )


def _extract_peer_anchors(
    fs: dict, config: ValuationConfig | None, overrides: dict,
    industry: str = "",
) -> PeerMultipleAnchors:
    """Build PeerMultipleAnchors from fact sheet data."""
    inc = _resolve_section(fs, "s11_income_statement")
    cf = _resolve_section(fs, "s11_cash_flow")
    cap = _resolve_section(fs, "s2_capital_structure")
    bal = _resolve_section(fs, "s11_balance_sheet")

    # Use best latest year across multiple series
    ly = _best_latest_year(
        inc, "revenue_usd_m", "net_income_usd_m", "eps_diluted",
    ) or _best_latest_year(
        cf, "operating_cash_flow_usd_m", "free_cash_flow_usd_m",
    )
    if not ly:
        ly = _latest_year(inc.get("revenue_usd_m") or {})

    revenue = _safe_num(_get_ly(inc, "revenue_usd_m", ly, 0))

    # EBITDA — with computation fallback
    ebitda = _compute_ebitda(inc, cf, ly)

    eps = _safe_num(_get_ly(inc, "eps_diluted", ly))
    net_debt = _safe_num(
        _get_ly(cap, "net_debt_usd_m", ly) or _get_ly(bal, "net_debt_usd_m", ly, 0)
    )

    # Shares — use fallback chain (shares → mktcap/price → net_income/eps)
    shares = _get_shares(fs, ly)

    # Identity-based fallbacks when quantitative engine produced empty data
    # (e.g., SEC EDGAR missing revenue for utilities)
    ident = fs.get("s1_identity") or {}
    valuation_sec = _resolve_section(fs, "s13_valuation")

    if eps <= 0:
        # Try FMP profile EPS
        fmp_eps = _safe_num(ident.get("eps"))
        if fmp_eps > 0:
            eps = fmp_eps

    if eps <= 0:
        # Try forward estimates (most recent actual or estimate)
        fwd_est = valuation_sec.get("forward_estimates")
        if isinstance(fwd_est, list) and fwd_est:
            # Sort by period, take latest with eps_avg
            sorted_est = sorted(fwd_est, key=lambda x: x.get("period", ""), reverse=True)
            for est in sorted_est:
                est_eps = _safe_num(est.get("eps_avg"))
                if est_eps > 0:
                    eps = est_eps
                    break

    if ebitda <= 0:
        # Try valuation section for EBITDA
        fmp_ebitda = _safe_num(valuation_sec.get("ebitda"))
        if fmp_ebitda > 0:
            ebitda = fmp_ebitda / 1e6 if fmp_ebitda > 1e8 else fmp_ebitda

    if ebitda <= 0:
        # Try forward estimates for EBITDA
        fwd_est = valuation_sec.get("forward_estimates")
        if isinstance(fwd_est, list) and fwd_est:
            sorted_est = sorted(fwd_est, key=lambda x: x.get("period", ""), reverse=True)
            for est in sorted_est:
                est_ebitda = _safe_num(est.get("ebitda_avg_usd_m"))
                if est_ebitda > 0:
                    ebitda = est_ebitda
                    break

    if net_debt == 0:
        # Try valuation section for net debt
        val_nd = _safe_num(valuation_sec.get("net_debt_usd_m"))
        if val_nd != 0:
            net_debt = val_nd

    if shares <= 1:
        # Try valuation section shares
        val_shares = _safe_num(valuation_sec.get("shares_diluted_millions"))
        if val_shares > 0:
            shares = val_shares

    # BVPS — compute from total_equity / shares if not directly available
    bvps = _safe_num(_get_ly(bal, "book_value_per_share", ly))
    if bvps <= 0:
        total_equity = _safe_num(_get_ly(bal, "total_equity_usd_m", ly))
        if total_equity > 0 and shares > 0:
            bvps = total_equity / shares

    # Collect peer multiples from peer data
    peers = fs.get("s6_peers") or {}
    peer_bench = fs.get("s12_peer_benchmarking") or {}
    by_symbol = peers.get("by_symbol") or peer_bench.get("by_symbol") or {}

    peer_ev_ebitda_list = []
    peer_pe_list = []
    peer_pb_list = []
    peer_ev_sales_list = []

    for sym, pdata in by_symbol.items():
        if not isinstance(pdata, dict):
            continue
        ev_ebitda = _safe_num(pdata.get("ev_to_ebitda") or pdata.get("evToEbitda"))
        if ev_ebitda > 0:
            peer_ev_ebitda_list.append(ev_ebitda)
        pe = _safe_num(pdata.get("price_to_earnings") or pdata.get("pe") or pdata.get("peRatio"))
        if pe > 0:
            peer_pe_list.append(pe)
        pb = _safe_num(pdata.get("price_to_book") or pdata.get("pbRatio"))
        if pb > 0:
            peer_pb_list.append(pb)
        ev_sales = _safe_num(pdata.get("ev_to_sales") or pdata.get("evToSales"))
        if ev_sales > 0:
            peer_ev_sales_list.append(ev_sales)

    # Also try peer_medians if individual peer data sparse
    peer_medians = peers.get("peer_medians") or peer_bench.get("peer_medians") or {}
    if len(peer_ev_ebitda_list) < 2 and peer_medians.get("ev_to_ebitda"):
        peer_ev_ebitda_list = [_safe_num(peer_medians["ev_to_ebitda"])]
    if len(peer_pe_list) < 2 and peer_medians.get("price_to_earnings"):
        peer_pe_list = [_safe_num(peer_medians["price_to_earnings"])]

    # Industry-default multiples when FMP peer data is completely empty
    if not peer_ev_ebitda_list and not peer_pe_list:
        bucket = _industry_bucket(industry or "")
        defaults = INDUSTRY_DEFAULT_MULTIPLES.get(bucket, INDUSTRY_DEFAULT_MULTIPLES["default"])
        if defaults.get("ev_ebitda"):
            peer_ev_ebitda_list = [defaults["ev_ebitda"]]
        if defaults.get("pe"):
            peer_pe_list = [defaults["pe"]]

    # Determine primary method
    primary = "ev_ebitda"
    if config:
        primary = config.method

    return PeerMultipleAnchors(
        ebitda=overrides.get("ebitda", ebitda),
        eps=overrides.get("eps", eps),
        net_debt=overrides.get("net_debt", net_debt),
        shares_diluted=overrides.get("shares_diluted", shares),
        revenue=overrides.get("revenue", revenue),
        book_value_per_share=overrides.get("book_value_per_share", bvps),
        peer_ev_ebitda=peer_ev_ebitda_list,
        peer_pe=peer_pe_list,
        peer_pb=peer_pb_list,
        peer_ev_sales=peer_ev_sales_list,
        primary_method=primary,
    )


def _extract_ddm_anchors(fs: dict, overrides: dict) -> DDMAnchors:
    """Build DDMAnchors from fact sheet data.

    Used for utilities and other high-payout regulated companies.
    """
    inc = _resolve_section(fs, "s11_income_statement")
    cap_alloc = _resolve_section(fs, "s9_capital_allocation")
    returns = _resolve_section(fs, "s11_returns")
    bal = _resolve_section(fs, "s11_balance_sheet")

    rev_series = inc.get("revenue_usd_m") or {}
    ly = _latest_year(rev_series) or _best_latest_year(
        inc, "eps_diluted", "net_income_usd_m",
    )

    # EPS
    eps = _safe_num(_get_ly(inc, "eps_diluted", ly))
    if eps <= 0:
        ident = fs.get("s1_identity") or {}
        eps = _safe_num(ident.get("eps"))

    # Dividend per share — from capital allocation or compute from dividends/shares
    shares = _get_shares(fs, ly)
    dps_series = cap_alloc.get("dividend_per_share") or {}
    dps = _safe_num(_get_ly(cap_alloc, "dividend_per_share", ly))
    if dps <= 0:
        divs_paid = _safe_num(_get_ly(cap_alloc, "dividends_paid_usd_m", ly))
        if divs_paid > 0 and shares > 0:
            dps = divs_paid / shares

    # Payout ratio
    payout = 0.0
    if dps > 0 and eps > 0:
        payout = dps / eps
        payout = max(0.0, min(payout, 1.5))  # Cap at 150% (REITs can exceed 100%)

    # Dividend growth — CAGR from DPS series
    dps_growth = 0.0
    if isinstance(dps_series, dict):
        years = sorted(k for k in dps_series if dps_series[k] is not None)
        if len(years) >= 2:
            first_dps = _safe_num(dps_series[years[0]])
            last_dps = _safe_num(dps_series[years[-1]])
            n_periods = len(years) - 1
            if first_dps > 0 and last_dps > 0 and n_periods > 0:
                dps_growth = (last_dps / first_dps) ** (1 / n_periods) - 1

    # Earnings growth — use revenue growth as proxy
    earnings_growth = 0.0
    growth_series = inc.get("revenue_growth_pct") or {}
    if isinstance(growth_series, dict):
        years = sorted(k for k in growth_series if growth_series[k] is not None)
        if years:
            recent = [_safe_num(growth_series[y]) / 100.0 for y in years[-3:]]
            valid = [g for g in recent if 0 < g < 0.25]
            if valid:
                earnings_growth = sum(valid) / len(valid)

    # ROE — average of last 3 years
    roe_series = returns.get("roe_pct") or {}
    roe_avg = _avg_recent(roe_series, 3)
    roe = (roe_avg / 100.0) if roe_avg else 0.10

    # Cost of equity — CAPM with utility sector adjustment
    beta = _safe_num(fs.get("s1_identity", {}).get("beta"))
    if beta and 0.3 < beta < 3.0:
        coe = 0.038 + beta * 0.045  # 3.8% rf + beta * 4.5% ERP
    else:
        coe = 0.08  # Default 8% for utilities
    coe = max(0.065, min(coe, 0.12))  # Clamp 6.5-12% for utilities

    # Terminal growth — utilities grow at GDP-like rates
    g_terminal = 0.025

    # BV per share
    bvps = _safe_num(_get_ly(bal, "book_value_per_share", ly))
    if bvps <= 0:
        total_equity = _safe_num(_get_ly(bal, "total_equity_usd_m", ly))
        if total_equity > 0 and shares > 0:
            bvps = total_equity / shares

    # Price
    price = _safe_num(fs.get("s1_identity", {}).get("price"))

    return DDMAnchors(
        dividend_per_share=overrides.get("dividend_per_share", dps),
        eps_latest=overrides.get("eps_latest", eps),
        payout_ratio=overrides.get("payout_ratio", payout),
        dividend_growth_5yr=overrides.get("dividend_growth_5yr", dps_growth),
        earnings_growth=overrides.get("earnings_growth", earnings_growth),
        cost_of_equity=overrides.get("cost_of_equity", coe),
        terminal_growth=overrides.get("terminal_growth", g_terminal),
        book_value_per_share=overrides.get("book_value_per_share", bvps),
        roe=overrides.get("roe", roe),
        price=overrides.get("price", price),
    )


# Industries where DDM is the primary valuation model
_DDM_INDUSTRIES = frozenset({
    "Utilities - Regulated Electric",
    "Utilities - Regulated Gas",
    "Utilities - Regulated Water",
    "Utilities - Diversified",
    "Regulated Electric",
    "Regulated Gas",
    "Regulated Water",
    # Also applicable to some REITs with stable dividends
})


# ═══════════════════════════════════════════════════════════════════════
# MAIN DISPATCHER
# ═══════════════════════════════════════════════════════════════════════


def run_valuation(
    industry: str,
    sic_code: str = "",
    fact_sheet: dict | None = None,
    **overrides,
) -> dict:
    """Run the appropriate valuation model for a company.

    Auto-detects the correct model based on industry string or SIC code,
    extracts anchors from the fact sheet, and executes the model.

    Args:
        industry: FMP industry string
        sic_code: SIC code from SEC
        fact_sheet: Full quantitative fact sheet (for extracting anchors)
        **overrides: Override specific anchor values

    Returns:
        Dict with:
          - valuation_mode: "dcf" | "financial_peer" | "industry_peer"
          - method: The specific model run (e.g., "dcf", "bank_equity", "peer_multiples")
          - fair_value: Fair value per share (float or None)
          - model_result: The model-specific result object (as dict)
          - config: ValuationConfig metadata
          - error: Error string if model failed
    """
    mode, config = detect_valuation_mode(industry, sic_code)
    fs = fact_sheet or {}

    result: dict[str, Any] = {
        "valuation_mode": mode,
        "industry": industry,
        "sic_code": sic_code,
        "config": {
            "method": config.method if config else ("dcf" if mode == "dcf" else "pe"),
            "secondary": config.secondary if config else None,
            "rationale": (
                config.rationale
                if config
                else "Standard DCF is appropriate for this industry"
            ),
            "sector_note": config.sector_note if config else None,
        },
        "fair_value": None,
        "fair_value_method": None,
        "model_result": None,
        "cross_checks": {},
        "error": None,
    }

    if not fs:
        result["error"] = "No fact sheet provided — cannot extract anchors"
        return result

    try:
        if mode == "dcf":
            result = _run_dcf_mode(fs, industry, result, overrides)
        elif mode == "financial_peer":
            result = _run_financial_mode(fs, industry, result, overrides)
        elif mode == "industry_peer":
            result = _run_industry_peer_mode(fs, industry, config, result, overrides)
        else:
            result["error"] = f"Unknown valuation mode: {mode}"
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"

    return result


def _run_dcf_mode(
    fs: dict, industry: str, result: dict, overrides: dict,
) -> dict:
    """Run standard DCF with peer multiples as cross-check."""
    anchors = _extract_dcf_anchors(fs, overrides)
    dcf_result = run_dcf(anchors)

    if dcf_result.error:
        result["error"] = f"DCF error: {dcf_result.error}"
        # Fall back to peer multiples
        peer_anchors = _extract_peer_anchors(fs, None, overrides, industry=industry)
        peer_result = run_peer_multiples(peer_anchors)
        if peer_result.fair_value_per_share:
            result["fair_value"] = peer_result.fair_value_per_share
            result["fair_value_method"] = "peer_multiples_fallback"
            result["model_result"] = _result_to_dict(peer_result)
        return result

    result["fair_value"] = dcf_result.fair_value_per_share
    result["fair_value_method"] = "dcf"
    result["method"] = "dcf"
    result["model_result"] = _result_to_dict(dcf_result)

    # Peer multiples as cross-check
    try:
        peer_anchors = _extract_peer_anchors(fs, None, overrides, industry=industry)
        peer_result = run_peer_multiples(peer_anchors)
        result["cross_checks"] = {
            "ev_ebitda_implied": peer_result.ev_ebitda_implied,
            "pe_implied": peer_result.pe_implied,
            "pb_implied": peer_result.pb_implied,
            "peer_medians": peer_result.peer_medians,
        }
    except Exception as e:
        result["cross_checks"] = {
            "error": f"peer cross-check failed: {type(e).__name__}: {e}"
        }

    return result


def _run_financial_mode(
    fs: dict, industry: str, result: dict, overrides: dict,
) -> dict:
    """Run bank equity model for financial institutions."""
    # Primary: Bank Equity (Justified P/B)
    bank_anchors = _extract_bank_anchors(fs, overrides)
    bank_result = run_bank_equity(bank_anchors)

    if bank_result.error:
        result["error"] = f"Bank equity error: {bank_result.error}"
        # Fall back to simple peer P/E or P/B
        peer_anchors = _extract_peer_anchors(fs, None, overrides, industry=industry)
        peer_anchors.primary_method = "pe"  # P/E primary for financials
        peer_result = run_peer_multiples(peer_anchors)
        if peer_result.fair_value_per_share:
            result["fair_value"] = peer_result.fair_value_per_share
            result["fair_value_method"] = "peer_pe_fallback"
            result["model_result"] = _result_to_dict(peer_result)
        return result

    result["fair_value"] = bank_result.fair_value_per_share
    result["fair_value_method"] = "bank_equity"
    result["method"] = "bank_equity"
    result["model_result"] = _result_to_dict(bank_result)

    # Store cross-checks
    result["cross_checks"] = {
        "justified_pb": bank_result.justified_pb,
        "excess_return_value": bank_result.excess_return_value,
        "pe_implied_value": bank_result.pe_implied_value,
        "ddm_value": bank_result.ddm_value,
    }

    return result


def _run_industry_peer_mode(
    fs: dict,
    industry: str,
    config: ValuationConfig | None,
    result: dict,
    overrides: dict,
) -> dict:
    """Run sector-appropriate model for commodity/cyclical/regulated industries.

    Model priority:
      1. DDM (Dividend Discount Model) — for utilities and other high-payout
         regulated companies where dividend growth is the primary value driver
      2. NAV (Net Asset Value) — for O&G, mining, REITs where reserves/assets
         drive value
      3. Peer Multiples (EV/EBITDA or P/E) — fallback when no sector-specific
         model applies or when model data is unavailable
    """
    ind_lower = industry.lower()

    # ── Try DDM first for utilities ──
    try_ddm = industry in _DDM_INDUSTRIES or any(
        w in ind_lower for w in ("utilit", "regulated", "electric", "water")
    )
    ddm_result_obj = None
    if try_ddm:
        ddm_anchors = _extract_ddm_anchors(fs, overrides)
        ddm_result_obj = run_ddm(ddm_anchors)
        if ddm_result_obj.fair_value_per_share and not ddm_result_obj.error:
            result["fair_value"] = ddm_result_obj.fair_value_per_share
            result["fair_value_method"] = "ddm"
            result["method"] = "ddm"
            result["model_result"] = _result_to_dict(ddm_result_obj)

    # ── Try NAV for O&G, mining, REITs ──
    try_nav = any(
        w in ind_lower
        for w in ("oil", "gas", "gold", "silver", "copper", "mining", "reit")
    )
    nav_result_obj = None
    if try_nav and result.get("fair_value") is None:
        nav_anchors = _extract_nav_anchors(fs, industry, overrides)
        nav_result_obj = run_nav(nav_anchors)
        if nav_result_obj.fair_value_per_share and not nav_result_obj.used_fallback:
            result["fair_value"] = nav_result_obj.fair_value_per_share
            result["fair_value_method"] = "nav"
            result["method"] = "nav"
            result["model_result"] = _result_to_dict(nav_result_obj)

    # ── Peer multiples (fallback / cross-check) ──
    peer_anchors = _extract_peer_anchors(fs, config, overrides, industry=industry)
    peer_result = run_peer_multiples(peer_anchors)

    if result.get("fair_value") is None:
        # No sector-specific model produced a value — use peer multiples
        if peer_result.fair_value_per_share:
            result["fair_value"] = peer_result.fair_value_per_share
            result["fair_value_method"] = (
                f"peer_{peer_anchors.primary_method}"
            )
            result["method"] = "peer_multiples"
            result["model_result"] = _result_to_dict(peer_result)
        elif peer_result.error:
            result["error"] = f"Peer multiples error: {peer_result.error}"

    # ── Cross-checks ──
    cross = {}
    if peer_result.ev_ebitda_implied:
        cross["ev_ebitda_implied"] = peer_result.ev_ebitda_implied
    if peer_result.pe_implied:
        cross["pe_implied"] = peer_result.pe_implied
    if peer_result.pb_implied:
        cross["pb_implied"] = peer_result.pb_implied
    if peer_result.peer_medians:
        cross["peer_medians"] = peer_result.peer_medians
    if ddm_result_obj and ddm_result_obj.pe_crosscheck:
        cross["ddm_pe_crosscheck"] = ddm_result_obj.pe_crosscheck
    if ddm_result_obj and ddm_result_obj.implied_yield:
        cross["ddm_implied_yield"] = ddm_result_obj.implied_yield
    if nav_result_obj and nav_result_obj.ev_ebitda_crosscheck:
        cross["nav_ev_ebitda_crosscheck"] = nav_result_obj.ev_ebitda_crosscheck
    result["cross_checks"] = cross

    return result


def _result_to_dict(result_obj: Any) -> dict:
    """Convert a dataclass result to a plain dict for JSON serialization."""
    if hasattr(result_obj, "__dataclass_fields__"):
        from dataclasses import asdict
        return asdict(result_obj)
    return dict(result_obj) if isinstance(result_obj, dict) else {"raw": str(result_obj)}
