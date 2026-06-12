"""
Quantitative Fact Sheet Engine — Python port of Quantitative_Facts.js (v4)

Builds a comprehensive financial fact sheet from pivoted FMP data.
Bank revenue override: uses grossProfit instead of revenue for bank tickers.
FMP revenue = gross interest income for banks (60-70% overstated).
grossProfit maps to net revenue within ~5% for JPM, WFC, C.

Input:  dict with keys:
    annual_financials, quarterly_financials, estimates, surprises,
    owner_earnings, peers

Output: dict matching the JS output structure with sections _meta,
        s2_capital_structure, s4_rd, s5_subject_margins, s5_share_data,
        s6_competitive_landscape, s7_working_capital, s9_capital_allocation,
        s9_guidance_beat_miss, s10_s13_forward_estimates,
        s11_income_statement, s11_cash_flow, s11_balance_sheet,
        s11_returns, s12_peer_benchmarking, s13_valuation
"""

from __future__ import annotations

import math
import re
from datetime import date
from typing import Any

from config.settings import BANK_TICKERS


# ── HELPER FUNCTIONS ─────────────────────────────────────────────

def _round(v: Any, n: int = 4) -> float | None:
    """Round *v* to *n* decimal places.  Returns None for null/NaN."""
    if v is None:
        return None
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(fv) or math.isinf(fv):
        return None
    return round(fv, n)


def _pct(v: Any) -> float | None:
    """Decimal -> percentage (×100), rounded to 2 dp."""
    if v is None:
        return None
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(fv) or math.isinf(fv):
        return None
    return _round(fv * 100, 2)


def _to_m(v: Any) -> float | None:
    """Convert a raw number to USD millions, rounded to 1 dp."""
    if v is None:
        return None
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(fv) or math.isinf(fv):
        return None
    return _round(fv / 1_000_000, 1)


def _to_b(v: Any) -> float | None:
    """Convert a raw number to USD billions, rounded to 3 dp."""
    if v is None:
        return None
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(fv) or math.isinf(fv):
        return None
    return _round(fv / 1_000_000_000, 3)


def _to_abs_m(v: Any) -> float | None:
    """Absolute value → USD millions, rounded to 1 dp."""
    if v is None:
        return None
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(fv) or math.isinf(fv):
        return None
    return _round(abs(fv) / 1_000_000, 1)


def _safe_div(num: Any, den: Any) -> float | None:
    """Safe division returning None when either operand is None/0-denom."""
    if num is None or den is None:
        return None
    try:
        fn = float(num)
        fd = float(den)
    except (TypeError, ValueError):
        return None
    if fd == 0 or math.isnan(fn) or math.isnan(fd):
        return None
    return fn / fd


def _median(values: list) -> float | None:
    """Median of a list, ignoring None values."""
    cleaned = sorted(v for v in values if v is not None)
    if not cleaned:
        return None
    mid = len(cleaned) // 2
    if len(cleaned) % 2:
        return _round(cleaned[mid], 2)
    return _round((cleaned[mid - 1] + cleaned[mid]) / 2, 2)


# NM thresholds for capping extreme outliers in peer comp tables
_NM_CAPS: dict[str, dict] = {
    "price_to_earnings":    {"max": 150, "min": 0},
    "price_to_fcf":         {"max": 150, "min": 0},
    "ev_to_ebitda":         {"max": 100, "min": 0},
    "ev_to_sales":          {"max": 50,  "min": 0},
    "ev_to_fcf":            {"max": 200, "min": 0},
    "revenue_growth_pct":          {"absMax": 500},
    "operating_income_growth_pct": {"absMax": 500},
    "eps_diluted_growth_pct":      {"absMax": 500},
    "fcf_growth_pct":              {"absMax": 500},
    "roe_pct":              {"absMax": 200},
    "roic_pct":             {"absMax": 200},
    "net_debt_to_ebitda":   {"max": 30, "min": -5},
    "debt_to_equity":       {"max": 20, "min": -0.01},
    "interest_coverage":    {"max": 200, "min": -200},
}


def _nm_cap(val: Any, field: str) -> Any:
    """Return None if value exceeds NM thresholds."""
    if val is None or field not in _NM_CAPS:
        return val
    try:
        n = float(val)
    except (TypeError, ValueError):
        return val
    if math.isnan(n):
        return None
    t = _NM_CAPS[field]
    if "absMax" in t and abs(n) > t["absMax"]:
        return None
    if "max" in t and n > t["max"]:
        return None
    if "min" in t and n < t["min"]:
        return None
    return val


# ── MAIN ENGINE ──────────────────────────────────────────────────

def build_quantitative_facts(data: dict) -> dict:
    """Build the full quantitative fact sheet from pivoted FMP data.

    Parameters
    ----------
    data : dict
        Must contain keys: annual_financials, quarterly_financials,
        estimates, surprises, owner_earnings, peers.

    Returns
    -------
    dict  – structured fact sheet matching the JS output.
    """

    # ── INDEX LOOKUP DICTS ───────────────────────────────────────

    annual: dict[str, dict] = {}
    for row in data.get("annual_financials", []):
        annual[row["metric"]] = row

    quarterly: dict[str, dict] = {}
    for row in data.get("quarterly_financials", []):
        quarterly[row["metric"]] = row

    estimates_idx: dict[str, dict] = {}
    for row in data.get("estimates", []):
        estimates_idx[row["metric"]] = row

    surprises_idx: dict[str, dict] = {}
    for row in data.get("surprises", []):
        surprises_idx[row["metric"]] = row

    # ── YEAR / QUARTER KEYS ──────────────────────────────────────

    _rev_row = annual.get("revenue", {})
    annual_years = sorted(
        k for k in _rev_row
        if k not in ("metric", "symbol")
    )

    _qrev_row = quarterly.get("revenue", {})
    quarterly_keys = sorted(
        k for k in _qrev_row
        if k not in ("metric", "symbol")
    )

    def _last_n(n: int) -> list[str]:
        return annual_years[-n:]

    last5 = _last_n(5)
    last3 = _last_n(3)

    # ── VALUE ACCESSORS ──────────────────────────────────────────

    def av(metric: str, year_key: str | None) -> Any:
        """Annual value lookup."""
        if year_key is None:
            return None
        row = annual.get(metric)
        if row is None:
            return None
        v = row.get(year_key)
        return v if v is not None else None

    def qv(metric: str, q_key: str | None) -> Any:
        """Quarterly value lookup."""
        if q_key is None:
            return None
        row = quarterly.get(metric)
        if row is None:
            return None
        v = row.get(q_key)
        return v if v is not None else None

    # ── BANK / INSURANCE DETECTION ────────────────────────────────

    ticker = _rev_row.get("symbol", "")
    is_bank = ticker in BANK_TICKERS

    # Detect insurance companies by the presence of premiumsEarned in annual data.
    # Insurance companies (MET, AIG, ALL, PRU, etc.) have COGS/Gross Margin/R&D
    # that are meaningless — same treatment as banks.
    _premiums_row = annual.get("premiumsEarned", {})
    _has_premiums = any(
        v is not None and v != 0
        for k, v in _premiums_row.items()
        if k not in ("metric", "symbol")
    )
    is_insurance = _has_premiums and not is_bank

    # Convenience: exclude COGS/Gross Margin/R&D for both banks and insurance
    _skip_cogs = is_bank or is_insurance

    def get_revenue(y: str | None) -> Any:
        if y is None:
            return None
        if is_bank:
            gp = av("grossProfit", y)
            return gp if gp is not None else av("netInterestIncome", y)
        if is_insurance:
            # Insurance total revenue = premiums + investment income
            # SEC Revenues tag may return a narrow value; use premiums-based calc
            prem = av("premiumsEarned", y)
            if prem is not None:
                inv = av("netInvestmentIncome", y) or 0
                return prem + inv
            # Fall back to revenue if no premiums data
            return av("revenue", y)
        return av("revenue", y)

    def get_peer_revenue(p: dict) -> Any:
        if p.get("symbol") not in BANK_TICKERS:
            return p.get("revenue")
        gp = p.get("grossProfit")
        return gp if gp is not None else p.get("netInterestIncome")

    # ── LATEST YEAR DETERMINATION ────────────────────────────────

    _raw_latest_year = annual_years[-1] if annual_years else None

    latest_year = _raw_latest_year
    for i in range(len(annual_years) - 1, -1, -1):
        rev = av("revenue", annual_years[i])
        if rev is not None and rev > 0:
            latest_year = annual_years[i]
            break

    def _year_offset(offset: int) -> str | None:
        if latest_year is None:
            return None
        try:
            idx = annual_years.index(latest_year)
        except ValueError:
            return None
        target = idx - offset
        return annual_years[target] if target >= 0 else None

    prior_year = _year_offset(1)
    year2ago = _year_offset(2)

    last8q = sorted(quarterly_keys, reverse=True)[:8]

    # ── SERIES HELPERS ───────────────────────────────────────────

    def pct_series_5(metric: str) -> dict:
        return {y: _pct(av(metric, y)) for y in last5}

    def round_series_5(metric: str, decimals: int = 2) -> dict:
        return {y: _round(av(metric, y), decimals) for y in last5}

    def to_m_series_5(metric: str) -> dict:
        return {y: _to_m(av(metric, y)) for y in last5}

    def to_m_series_3(metric: str) -> dict:
        return {y: _to_m(av(metric, y)) for y in last3}

    def to_abs_m_series_3(metric: str) -> dict:
        return {y: _to_abs_m(av(metric, y)) for y in last3}

    # ── BEAT / MISS from surprises ───────────────────────────────

    def _build_beat_miss() -> dict:
        eps_actual = surprises_idx.get("epsActual", {})
        eps_est = surprises_idx.get("epsEstimated", {})
        rev_actual = surprises_idx.get("revenueActual", {})
        rev_est = surprises_idx.get("revenueEstimated", {})

        dates = sorted(
            (k for k in eps_actual
             if k not in ("metric", "symbol") and eps_actual[k] is not None),
            reverse=True,
        )[:8]

        quarters = []
        for d in dates:
            ea = eps_actual.get(d)
            ee = eps_est.get(d)
            ra = rev_actual.get(d)
            re_ = rev_est.get(d)

            eps_beat = (ea > ee) if (ea is not None and ee is not None) else None
            rev_beat = (ra > re_) if (ra is not None and re_ is not None) else None

            eps_surp = _round(ea - ee, 4) if (ea is not None and ee is not None) else None
            rev_surp = _to_m(ra - re_) if (ra is not None and re_ is not None) else None

            eps_surp_pct = (
                _pct((ea - ee) / abs(ee))
                if (ea is not None and ee is not None and ee != 0)
                else None
            )
            rev_surp_pct = (
                _pct((ra - re_) / abs(re_))
                if (ra is not None and re_ is not None and re_ != 0)
                else None
            )

            quarters.append({
                "date": d,
                "eps_actual": ea,
                "eps_estimated": ee,
                "eps_beat": eps_beat,
                "eps_surprise": eps_surp,
                "eps_surprise_pct": eps_surp_pct,
                "revenue_actual_usd_m": _to_m(ra),
                "revenue_estimated_usd_m": _to_m(re_),
                "revenue_beat": rev_beat,
                "revenue_surprise_usd_m": rev_surp,
                "revenue_surprise_pct": rev_surp_pct,
            })

        eps_beats = sum(1 for q in quarters if q["eps_beat"] is True)
        rev_beats = sum(1 for q in quarters if q["revenue_beat"] is True)
        total = len(quarters)

        eps_surp_pcts = [q["eps_surprise_pct"] for q in quarters if q["eps_surprise_pct"] is not None]
        rev_surp_pcts = [q["revenue_surprise_pct"] for q in quarters if q["revenue_surprise_pct"] is not None]

        avg_eps_surp_pct = (
            sum(eps_surp_pcts) / len(eps_surp_pcts) if eps_surp_pcts else 0
        )
        avg_rev_surp_pct = (
            sum(rev_surp_pcts) / len(rev_surp_pcts) if rev_surp_pcts else 0
        )

        return {
            "quarters": quarters,
            "summary": {
                "total_quarters_analyzed": total,
                "eps_beat_count": eps_beats,
                "eps_beat_pct": _pct(eps_beats / total) if total else None,
                "revenue_beat_count": rev_beats,
                "revenue_beat_pct": _pct(rev_beats / total) if total else None,
                "avg_eps_surprise_pct": _round(avg_eps_surp_pct, 2),
                "avg_revenue_surprise_pct": _round(avg_rev_surp_pct, 2),
            },
        }

    beat_miss_data = _build_beat_miss()

    # ── FORWARD ESTIMATES ────────────────────────────────────────

    def _build_forward_estimates() -> list[dict]:
        rev_avg = estimates_idx.get("revenueAvg", {})
        rev_low = estimates_idx.get("revenueLow", {})
        rev_high = estimates_idx.get("revenueHigh", {})
        eps_avg = estimates_idx.get("epsAvg", {})
        eps_low = estimates_idx.get("epsLow", {})
        eps_high = estimates_idx.get("epsHigh", {})
        ebitda_avg = estimates_idx.get("ebitdaAvg", {})
        num_analysts = estimates_idx.get("numAnalystsRevenue", {})

        all_est_dates = sorted(
            k for k in rev_avg if k not in ("metric", "symbol")
        )

        # Only keep estimates for periods AFTER the latest reported fiscal year.
        # annual_years contains filed fiscal-year-end dates (e.g. "2025-09-27").
        latest_fy = annual_years[-1] if annual_years else ""
        est_dates = [d for d in all_est_dates if d > latest_fy][:3]

        # Fallback: if no future dates found (e.g. data lag), take the last 3
        if not est_dates:
            est_dates = all_est_dates[-3:]

        return [
            {
                "period": d,
                "revenue_avg_usd_m": _to_m(rev_avg.get(d)),
                "revenue_low_usd_m": _to_m(rev_low.get(d)),
                "revenue_high_usd_m": _to_m(rev_high.get(d)),
                "ebitda_avg_usd_m": _to_m(ebitda_avg.get(d)),
                "eps_avg": eps_avg.get(d) or None,
                "eps_low": eps_low.get(d) or None,
                "eps_high": eps_high.get(d) or None,
                "num_analysts": num_analysts.get(d) or None,
            }
            for d in est_dates
        ]

    forward_estimates = _build_forward_estimates()

    # ── OWNER EARNINGS ───────────────────────────────────────────

    owner_earnings = [
        {
            "fiscal_year": oe.get("fiscalYear"),
            "period": oe.get("period"),
            "date": oe.get("date"),
            "maintenance_capex_usd_m": _to_m(oe.get("maintenanceCapex")),
            "growth_capex_usd_m": _to_m(oe.get("growthCapex")),
            "owners_earnings_usd_m": _to_m(oe.get("ownersEarnings")),
            "owners_earnings_per_share": oe.get("ownersEarningsPerShare"),
            "avg_ppe_ratio": oe.get("averagePPE"),
        }
        for oe in data.get("owner_earnings", [])
    ]

    # ── SEGMENT REVENUE SPLITS ───────────────────────────────────

    def _build_segment_splits() -> dict:
        row = annual.get("Segment Rev Split", {})
        out: dict = {}
        for y in last5:
            raw = row.get(y)
            if raw and isinstance(raw, dict):
                out[y] = {seg: _to_m(val) for seg, val in raw.items()}
            else:
                out[y] = None
        return out

    segment_splits = _build_segment_splits()

    # ── GEOGRAPHIC REVENUE SPLITS ────────────────────────────────

    def _build_geo_splits() -> dict:
        row = annual.get("Geographic Rev Split", {})
        out: dict = {}
        for y in last5:
            raw = row.get(y)
            if raw and isinstance(raw, dict):
                out[y] = {region: _to_m(val) for region, val in raw.items()}
            else:
                out[y] = None
        return out

    geo_splits = _build_geo_splits()

    # ── SEGMENT % OF TOTAL ───────────────────────────────────────

    def _build_segment_pcts() -> dict:
        out: dict = {}
        for y in last5:
            segs = segment_splits.get(y)
            total_rev = get_revenue(y)
            if not segs or not total_rev:
                out[y] = None
                continue
            total_rev_m = total_rev / 1_000_000
            out[y] = {}
            for seg, val in segs.items():
                if val is not None and total_rev_m != 0:
                    out[y][seg] = _pct(val / total_rev_m) / 100 if _pct(val / total_rev_m) is not None else None
                else:
                    out[y][seg] = None
        return out

    segment_pcts = _build_segment_pcts()

    # ── SEGMENT YOY GROWTH ───────────────────────────────────────

    def _build_segment_growth() -> dict:
        out: dict = {}
        for i in range(1, len(last5)):
            curr_y, prev_y = last5[i], last5[i - 1]
            curr = segment_splits.get(curr_y)
            prev = segment_splits.get(prev_y)
            if not curr or not prev:
                out[curr_y] = None
                continue
            out[curr_y] = {}
            for seg in curr:
                c = curr.get(seg)
                p = prev.get(seg)
                if c is not None and p is not None and p != 0:
                    out[curr_y][seg] = _pct((c - p) / abs(p))
                else:
                    out[curr_y][seg] = None
        return out

    segment_growth = _build_segment_growth()

    # ── GEO % OF TOTAL ───────────────────────────────────────────

    def _build_geo_pcts() -> dict:
        out: dict = {}
        for y in last5:
            regions = geo_splits.get(y)
            total_rev = get_revenue(y)
            if not regions or not total_rev:
                out[y] = None
                continue
            out[y] = {}
            for region, val in regions.items():
                if val is not None:
                    out[y][region] = _pct(val * 1_000_000 / total_rev)
                else:
                    out[y][region] = None
        return out

    geo_pcts = _build_geo_pcts()

    # Sanity-check: if any year's geo percentages sum to >110%, the
    # numerator/denominator use incompatible revenue definitions
    # (common for banks where XBRL segments use gross revenue but the
    # income statement uses NII). Drop broken years entirely.
    for y in list(geo_pcts.keys()):
        yr_data = geo_pcts.get(y)
        if yr_data and isinstance(yr_data, dict):
            total_pct = sum(v for v in yr_data.values() if isinstance(v, (int, float)))
            if total_pct > 110:
                geo_pcts[y] = None

    # ── GEO YOY GROWTH ──────────────────────────────────────────

    def _build_geo_growth() -> dict:
        out: dict = {}
        for i in range(1, len(last5)):
            curr_y, prev_y = last5[i], last5[i - 1]
            curr = geo_splits.get(curr_y)
            prev = geo_splits.get(prev_y)
            if not curr or not prev:
                out[curr_y] = None
                continue
            out[curr_y] = {}
            for region in curr:
                c = curr.get(region)
                p = prev.get(region)
                if c is not None and p is not None and p != 0:
                    out[curr_y][region] = _pct((c - p) / abs(p))
                else:
                    out[curr_y][region] = None
        return out

    geo_growth = _build_geo_growth()

    # ── DERIVED METRICS ──────────────────────────────────────────

    def _cagr(years: list[str]) -> float | None:
        if len(years) < 2:
            return None
        first = get_revenue(years[0])
        last = get_revenue(years[-1])
        if not first or not last or first == 0:
            return None
        periods = len(years) - 1
        return _pct(math.pow(last / first, 1 / periods) - 1)

    rev_cagr_5yr = _cagr(last5)
    rev_cagr_3yr = _cagr(last3)

    # FCF margin (5yr)
    fcf_margin: dict = {}
    for y in last5:
        fcf = av("freeCashFlow", y)
        rev = get_revenue(y)
        fcf_margin[y] = _pct(fcf / rev) if (fcf is not None and rev and rev != 0) else None

    # OCF margin (5yr)
    ocf_margin: dict = {}
    for y in last5:
        ocf = av("operatingCashFlow", y)
        rev = get_revenue(y)
        ocf_margin[y] = _pct(ocf / rev) if (ocf is not None and rev and rev != 0) else None

    # Capex % of revenue (fallback: derive capex from OCF - FCF when null)
    capex_pct_rev: dict = {}
    for y in last5:
        capex = av("capitalExpenditure", y)
        if capex is None:
            _ocf_fb = av("operatingCashFlow", y)
            _fcf_fb = av("freeCashFlow", y)
            if _ocf_fb is not None and _fcf_fb is not None:
                capex = _ocf_fb - _fcf_fb  # capex = OCF - FCF
        rev = get_revenue(y)
        capex_pct_rev[y] = _pct(abs(capex) / rev) if (capex is not None and rev and rev != 0) else None

    # FCF growth YoY (computed from absolute FCF, used as fallback when FMP series is null)
    fcf_growth_computed: dict = {}
    for i, y in enumerate(last5):
        if i == 0:
            fcf_growth_computed[y] = None
            continue
        prev_y = last5[i - 1]
        curr_fcf = av("freeCashFlow", y)
        prev_fcf = av("freeCashFlow", prev_y)
        if curr_fcf is not None and prev_fcf is not None and prev_fcf != 0:
            g = (curr_fcf - prev_fcf) / abs(prev_fcf)
            fcf_growth_computed[y] = _pct(g) if abs(g) <= 5 else None
        else:
            fcf_growth_computed[y] = None

    def _fcf_growth_series() -> dict:
        """FCF growth series: prefer FMP data, fallback to computed."""
        fmp = pct_series_5("freeCashFlowGrowth")
        if any(v is not None for v in fmp.values()):
            return fmp
        return fcf_growth_computed

    # FCF conversion (FCF / |net income|)
    fcf_conversion: dict = {}
    for y in last5:
        fcf = av("freeCashFlow", y)
        ni = av("netIncome", y)
        fcf_conversion[y] = _pct(fcf / abs(ni)) if (fcf is not None and ni is not None and ni != 0) else None

    # SBC % of revenue
    sbc_pct_rev: dict = {}
    for y in last5:
        sbc = av("stockBasedCompensation", y)
        rev = get_revenue(y)
        sbc_pct_rev[y] = _pct(sbc / rev) if (sbc is not None and rev and rev != 0) else None

    # Share count YoY change
    share_count_change: dict = {}
    for i in range(1, len(last5)):
        curr_y, prev_y = last5[i], last5[i - 1]
        curr = av("weightedAverageShsOutDil", curr_y)
        prev = av("weightedAverageShsOutDil", prev_y)
        share_count_change[curr_y] = (
            _pct((curr - prev) / prev)
            if (curr is not None and prev is not None and prev != 0)
            else None
        )

    # Goodwill % of total assets
    goodwill_pct_assets: dict = {}
    for y in last5:
        gw = av("goodwill", y)
        ta = av("totalAssets", y)
        goodwill_pct_assets[y] = _pct(gw / ta) if (gw is not None and ta and ta != 0) else None

    # AR vs revenue growth
    ar_vs_rev_growth: dict = {}
    for y in last5:
        ar_growth = _pct(av("receivablesGrowth", y))
        idx = last5.index(y)
        if idx <= 0:
            rev_growth = None
        else:
            curr_r = get_revenue(y)
            prev_r = get_revenue(last5[idx - 1])
            rev_growth = (
                _pct((curr_r - prev_r) / abs(prev_r))
                if (curr_r is not None and prev_r is not None and prev_r != 0)
                else None
            )
        divergence = (
            _round(ar_growth - rev_growth, 2)
            if (ar_growth is not None and rev_growth is not None)
            else None
        )
        flag = None
        if ar_growth is not None and rev_growth is not None and ar_growth > rev_growth + 5:
            flag = "AR growing faster than revenue — review revenue quality"

        ar_vs_rev_growth[y] = {
            "ar_growth_pct": ar_growth,
            "revenue_growth_pct": rev_growth,
            "divergence_pct": divergence,
            "flag": flag,
        }

    # ── QUARTERLY INCOME STATEMENT (last 8 quarters) ─────────────

    def _build_quarterly_income() -> list[dict]:
        result = []
        for q in last8q:
            if is_bank:
                q_rev_val = qv("grossProfit", q)
                if q_rev_val is None:
                    q_rev_val = qv("netInterestIncome", q)
            else:
                q_rev_val = qv("revenue", q)

            gp = qv("grossProfit", q)
            oi = qv("operatingIncome", q)
            ni = qv("netIncome", q)

            result.append({
                "period": q,
                "revenue_usd_m": _to_m(q_rev_val),
                "gross_profit_usd_m": None if _skip_cogs else _to_m(gp),
                "gross_margin_pct": (
                    _pct(gp / q_rev_val)
                    if (not _skip_cogs and gp is not None and q_rev_val)
                    else None
                ),
                "operating_income_usd_m": _to_m(oi),
                "operating_margin_pct": (
                    _pct(oi / q_rev_val)
                    if (oi is not None and q_rev_val)
                    else None
                ),
                "net_income_usd_m": _to_m(ni),
                "net_margin_pct": (
                    _pct(ni / q_rev_val)
                    if (ni is not None and q_rev_val)
                    else None
                ),
                "eps_diluted": qv("epsDiluted", q),
                "ebitda_usd_m": _to_m(qv("ebitda", q)),
                "rd_usd_m": None if _skip_cogs else _to_m(qv("researchAndDevelopmentExpenses", q)),
                "sbc_usd_m": _to_m(qv("stockBasedCompensation", q)),
            })
        return result

    quarterly_income = _build_quarterly_income()

    # ── QUARTERLY CASH FLOW (last 8 quarters) ────────────────────

    def _build_quarterly_cash_flow() -> list[dict]:
        result = []
        for q in last8q:
            if is_bank:
                q_rev_val = qv("grossProfit", q)
                if q_rev_val is None:
                    q_rev_val = qv("netInterestIncome", q)
            else:
                q_rev_val = qv("revenue", q)

            fcf_val = qv("freeCashFlow", q)

            result.append({
                "period": q,
                "ocf_usd_m": _to_m(qv("operatingCashFlow", q)),
                "capex_usd_m": _to_m(qv("capitalExpenditure", q)),
                "fcf_usd_m": None if _skip_cogs else _to_m(fcf_val),
                "fcf_margin_pct": (
                    _pct(fcf_val / q_rev_val)
                    if (not _skip_cogs and fcf_val is not None and q_rev_val)
                    else None
                ),
                "sbc_usd_m": _to_m(qv("stockBasedCompensation", q)),
                "acquisitions_usd_m": _to_m(qv("acquisitionsNet", q)),
                "buybacks_usd_m": _to_m(qv("commonStockRepurchased", q)),
                "dividends_paid_usd_m": _to_m(qv("commonDividendsPaid", q)),
            })
        return result

    quarterly_cash_flow = _build_quarterly_cash_flow()

    # ══════════════════════════════════════════════════════════════
    # PEERS
    # ══════════════════════════════════════════════════════════════

    def _build_peers_grouped() -> dict:
        by_symbol: dict[str, dict] = {}
        for p in data.get("peers", []):
            sym = p.get("symbol", "")
            if sym not in by_symbol:
                by_symbol[sym] = {}

            peer_is_bank = sym in BANK_TICKERS

            # Geographic rev split
            geo_raw = p.get("Geographic Rev Split")
            geo_usd_b = None
            geo_pct_val = None
            if geo_raw and isinstance(geo_raw, dict):
                geo_usd_b = {region: _to_b(val) for region, val in geo_raw.items()}
                total_geo = sum(v or 0 for v in geo_raw.values())
                if total_geo:
                    geo_pct_val = {region: _pct(val / total_geo) for region, val in geo_raw.items()}

            # Segment rev split (top 5 + Other)
            seg_raw = p.get("Segment Rev Split")
            seg_usd_b = None
            seg_concentration = None
            if seg_raw and isinstance(seg_raw, dict):
                entries = sorted(seg_raw.items(), key=lambda x: x[1] or 0, reverse=True)
                top5_entries = entries[:5]
                rest_entries = entries[5:]
                seg_usd_b = {name: _to_b(val) for name, val in top5_entries}
                if rest_entries:
                    seg_usd_b["Other"] = _to_b(sum(v or 0 for _, v in rest_entries))
                # concentration
                vals = [v for v in seg_raw.values() if v is not None and v > 0]
                if vals:
                    total_seg = sum(vals)
                    max_seg = max(vals)
                    seg_concentration = _pct(max_seg / total_seg) if total_seg > 0 else None

            by_symbol[sym][p.get("fiscalYear", "")] = {
                "fiscal_year": p.get("fiscalYear"),
                "period": p.get("period"),

                # Absolute Financials
                "revenue_usd_b": _to_b(get_peer_revenue(p)),
                "gross_profit_usd_b": None if _skip_cogs else (None if peer_is_bank else _to_b(p.get("grossProfit"))),
                "operating_income_usd_b": _to_b(p.get("operatingIncome")),
                "ebitda_usd_b": _to_b(p.get("ebitda")),
                "net_income_usd_b": _to_b(p.get("netIncome")),
                "rd_expense_usd_b": None if _skip_cogs else _to_b(p.get("researchAndDevelopmentExpenses")),
                "sga_expense_usd_b": _to_b(p.get("sellingGeneralAndAdministrativeExpenses")),
                "da_usd_b": _to_b(p.get("depreciationAndAmortization")),
                "interest_expense_usd_b": _to_b(p.get("interestExpense")),
                "eps_diluted": _round(p.get("epsDiluted"), 2),
                "shares_diluted_m": _round((p.get("weightedAverageShsOutDil") or 0) / 1_000_000, 1),

                # Market Data
                "market_cap_usd_b": _to_b(p.get("marketCap")),
                "enterprise_value_usd_b": _to_b(p.get("enterpriseValue")),

                # Valuation Multiples
                "ev_to_sales": _round(p.get("evToSales"), 2),
                "ev_to_ebitda": _round(p.get("evToEBITDA"), 2),
                "ev_to_fcf": _round(p.get("evToFreeCashFlow"), 2),
                "ev_to_ocf": _round(p.get("evToOperatingCashFlow"), 2),
                "price_to_earnings": _round(p.get("priceToEarningsRatio"), 2),
                "price_to_fcf": _round(p.get("priceToFreeCashFlowRatio"), 2),
                "price_to_sales": _round(p.get("priceToSalesRatio"), 2),
                "price_to_book": _round(p.get("priceToBookRatio"), 2),

                # Margins — null inapplicable metrics when subject is bank/insurance
                "gross_margin_pct": None if _skip_cogs else (None if peer_is_bank else _pct(p.get("grossProfitMargin"))),
                "operating_margin_pct": _pct(p.get("operatingProfitMargin")),
                "ebitda_margin_pct": _pct(p.get("ebitdaMargin")),
                "net_margin_pct": _pct(p.get("netProfitMargin")),

                # Returns — ROIC nulled for entire table when subject is _skip_cogs
                "roic_pct": None if _skip_cogs else _pct(p.get("returnOnInvestedCapital")),
                "roe_pct": _pct(p.get("returnOnEquity")),
                "roa_pct": _pct(p.get("returnOnAssets")),

                # Leverage
                "net_debt_to_ebitda": _round(p.get("netDebtToEBITDA"), 2),
                "debt_to_equity": _round(p.get("debtToEquityRatio"), 2),
                "current_ratio": _round(p.get("currentRatio"), 2),
                "interest_coverage": _round(p.get("interestCoverageRatio"), 2),

                # Efficiency
                "rd_to_revenue_pct": None if _skip_cogs else _pct(p.get("researchAndDevelopementToRevenue")),
                "sbc_to_revenue_pct": _pct(p.get("stockBasedCompensationToRevenue")),
                "capex_to_revenue_pct": _pct(p.get("capexToRevenue")),
                "sga_to_revenue_pct": _pct(p.get("salesGeneralAndAdministrativeToRevenue")),

                # Working Capital — null for banks/insurance (subject _skip_cogs)
                "dso_days": None if _skip_cogs else _round(p.get("daysOfSalesOutstanding"), 1),
                "dpo_days": None if _skip_cogs else _round(p.get("daysOfPayablesOutstanding"), 1),
                "ccc_days": None if _skip_cogs else _round(p.get("cashConversionCycle"), 1),

                # Growth
                "revenue_growth_pct": _pct(p.get("revenueGrowth")),
                "gross_profit_growth_pct": _pct(p.get("grossProfitGrowth")),
                "operating_income_growth_pct": _pct(p.get("operatingIncomeGrowth")),
                "net_income_growth_pct": _pct(p.get("netIncomeGrowth")),
                "eps_diluted_growth_pct": _pct(p.get("epsdilutedGrowth")),
                "fcf_growth_pct": _pct(p.get("freeCashFlowGrowth")),

                # Yield / Dividends
                "earnings_yield_pct": _pct(p.get("earningsYield")),
                "fcf_yield_pct": _pct(p.get("freeCashFlowYield")),
                "income_quality": _round(p.get("incomeQuality"), 2),
                "dividend_yield_pct": _pct(p.get("dividendYield")),
                "dividend_payout_ratio_pct": _pct(p.get("dividendPayoutRatio")),

                # Geographic Revenue Split
                "geographic_revenue_usd_b": geo_usd_b,
                "geographic_revenue_pct": geo_pct_val,

                # Segment Revenue Split
                "segment_revenue_usd_b": seg_usd_b,
                "segment_concentration_pct": seg_concentration,
            }

        # Latest fiscal year for each peer
        latest_only: list[dict] = []
        for sym, years_data in by_symbol.items():
            latest_fy = sorted(years_data.keys(), reverse=True)[0]
            entry = {"symbol": sym}
            entry.update(years_data[latest_fy])
            latest_only.append(entry)

        # ── Peer data quality filter ──────────────────────────────
        # 1. Drop peers with mostly null data (e.g. non-US-listed
        #    companies where FMP returns null price).
        # 2. Drop peers with extreme/nonsensical values that indicate
        #    bad underlying data (P/B of 500, ROIC 1500%, etc.).
        _QUALITY_KEYS = [
            "revenue_usd_b", "market_cap_usd_b", "operating_margin_pct",
            "roe_pct", "price_to_earnings", "price_to_book",
            "ev_to_ebitda", "net_margin_pct",
        ]
        # Thresholds for "insane" values — any single field breaching
        # these means the peer's data is unreliable.
        _INSANE_THRESHOLDS = {
            "price_to_book": 100,
            "price_to_earnings": 500,
            "ev_to_ebitda": 200,
            "ev_to_sales": 100,
            "roic_pct": 500,
            "roe_pct": 500,
            "operating_margin_pct": 200,
        }
        _filtered: list[dict] = []
        for p in latest_only:
            # Check 1: enough non-null key fields
            non_null_count = sum(1 for k in _QUALITY_KEYS if p.get(k) is not None)
            if non_null_count < 4:
                continue
            # Check 2: no insane outlier values
            _insane = False
            for field, threshold in _INSANE_THRESHOLDS.items():
                val = p.get(field)
                if val is not None:
                    try:
                        if abs(float(val)) > threshold:
                            _insane = True
                            break
                    except (TypeError, ValueError):
                        pass
            if not _insane:
                _filtered.append(p)
        latest_only = _filtered

        # Peer medians
        median_keys = [
            "revenue_usd_b", "gross_margin_pct", "operating_margin_pct",
            "ebitda_margin_pct", "net_margin_pct", "roic_pct", "roe_pct",
            "ev_to_sales", "ev_to_ebitda", "ev_to_fcf", "price_to_earnings",
            "price_to_fcf", "net_debt_to_ebitda", "rd_to_revenue_pct",
            "revenue_growth_pct", "eps_diluted_growth_pct", "fcf_growth_pct",
            "dso_days", "ccc_days", "dividend_yield_pct", "fcf_yield_pct",
            "interest_coverage",
        ]
        peer_medians: dict = {}
        for k in median_keys:
            peer_medians[k] = _median([p.get(k) for p in latest_only])

        return {
            "by_symbol": by_symbol,
            "latest": latest_only,
            "peer_medians": peer_medians,
        }

    peers_grouped = _build_peers_grouped()
    peers_latest = peers_grouped["latest"]

    # ══════════════════════════════════════════════════════════════
    # PRE-COMPUTED PEER COMPARISON TABLES
    # ══════════════════════════════════════════════════════════════

    def _build_peer_comp_tables() -> dict:
        subject_symbol = _rev_row.get("symbol", "")
        # Exclude subject from peer list to avoid duplicate rows
        lp = [
            p for p in peers_grouped["latest"]
            if (p.get("symbol") or "").upper() != subject_symbol.upper()
        ]

        # Subject financials (latest year)
        def _subject_rev_growth() -> float | None:
            curr = get_revenue(latest_year)
            prev = get_revenue(prior_year)
            if curr is not None and prev is not None and prev != 0:
                return _pct((curr - prev) / abs(prev))
            return None

        def _fcf_growth_fallback() -> float | None:
            """Compute FCF growth from absolute FCF when FMP growth metric is null."""
            curr = av("freeCashFlow", latest_year)
            prev = av("freeCashFlow", prior_year)
            if curr is not None and prev is not None and prev != 0:
                g = (curr - prev) / abs(prev)
                return _pct(g) if abs(g) <= 5 else None  # cap at 500%
            return None

        def _subject_seg_concentration() -> float | None:
            segs = segment_splits.get(latest_year)
            if not segs:
                return None
            vals = [v for v in segs.values() if v is not None and v > 0]
            if not vals:
                return None
            total = sum(vals)
            return _pct(max(vals) / total) if total > 0 else None

        subject_financials = {
            "symbol": subject_symbol,
            "revenue_usd_b": _to_b(get_revenue(latest_year)),
            "gross_profit_usd_b": None if _skip_cogs else _to_b(av("grossProfit", latest_year)),
            "operating_income_usd_b": _to_b(av("operatingIncome", latest_year)),
            "ebitda_usd_b": _to_b(av("ebitda", latest_year)),
            "net_income_usd_b": _to_b(av("netIncome", latest_year)),
            "market_cap_usd_b": _to_b(av("marketCap", latest_year)),
            "enterprise_value_usd_b": _to_b(av("enterpriseValue", latest_year)),
            "ev_to_sales": _round(av("evToSales", latest_year), 2),
            "ev_to_ebitda": _round(av("evToEBITDA", latest_year), 2),
            "ev_to_fcf": _round(av("evToFreeCashFlow", latest_year), 2),
            "price_to_earnings": _round(av("priceToEarningsRatio", latest_year), 2),
            "price_to_fcf": _round(av("priceToFreeCashFlowRatio", latest_year), 2),
            "fcf_yield_pct": _pct(av("freeCashFlowYield", latest_year)),
            "dividend_yield_pct": _pct(av("dividendYield", latest_year)),
            "gross_margin_pct": None if _skip_cogs else _pct(av("grossProfitMargin", latest_year)),
            "operating_margin_pct": _pct(av("operatingProfitMargin", latest_year)),
            "ebitda_margin_pct": _pct(av("ebitdaMargin", latest_year)),
            "net_margin_pct": _pct(av("netProfitMargin", latest_year)),
            "roic_pct": None if _skip_cogs else _pct(av("returnOnInvestedCapital", latest_year)),
            "roe_pct": _pct(av("returnOnEquity", latest_year)),
            "roa_pct": _pct(av("returnOnAssets", latest_year)),
            "revenue_growth_pct": _subject_rev_growth(),
            "operating_income_growth_pct": _pct(av("operatingIncomeGrowth", latest_year)),
            "eps_diluted_growth_pct": _pct(av("epsdilutedGrowth", latest_year)),
            "fcf_growth_pct": _pct(av("freeCashFlowGrowth", latest_year)) or _fcf_growth_fallback(),
            "net_debt_to_ebitda": _round(av("netDebtToEBITDA", latest_year), 2),
            "debt_to_equity": _round(av("debtToEquityRatio", latest_year), 2),
            "interest_coverage": _round(av("interestCoverageRatio", latest_year), 2) if av("interestCoverageRatio", latest_year) else None,
            "current_ratio": _round(av("currentRatio", latest_year), 2),
            "rd_to_revenue_pct": _pct(av("researchAndDevelopementToRevenue", latest_year)),
            "sbc_to_revenue_pct": _pct(av("stockBasedCompensationToRevenue", latest_year)),
            "capex_to_revenue_pct": _pct(av("capexToRevenue", latest_year)),
            "dso_days": None if _skip_cogs else _round(av("daysOfSalesOutstanding", latest_year), 1),
            "ccc_days": None if _skip_cogs else _round(av("cashConversionCycle", latest_year), 1),
            "segment_concentration_pct": _subject_seg_concentration(),
        }

        def build_table(peer_mapper):
            """Build a comp table: [subject, ...peers, median]."""
            peer_rows = [peer_mapper(p) for p in lp]
            subject_row = peer_mapper(subject_financials)
            subject_row["company"] = subject_symbol

            # NM-cap extreme outliers in all rows
            for row in [subject_row] + peer_rows:
                for field in list(row.keys()):
                    if field == "company":
                        continue
                    row[field] = _nm_cap(row[field], field)

            # Median row (computed AFTER NM capping so outliers don't skew)
            median_row = {"company": "Peer Median"}
            if peer_rows:
                numeric_keys = [k for k in peer_rows[0] if k != "company"]
                for k in numeric_keys:
                    vals = sorted(
                        v for v in (r.get(k) for r in peer_rows)
                        if v is not None
                    )
                    if not vals:
                        median_row[k] = None
                        continue
                    mid = len(vals) // 2
                    median_row[k] = (
                        _round(vals[mid], 2) if len(vals) % 2
                        else _round((vals[mid - 1] + vals[mid]) / 2, 2)
                    )

            return [subject_row] + peer_rows + [median_row]

        # Individual comp tables
        competitive_landscape = build_table(lambda p: {
            "company": p.get("symbol", ""),
            "revenue_usd_b": p.get("revenue_usd_b"),
            "revenue_growth_pct": p.get("revenue_growth_pct"),
            "operating_margin_pct": p.get("operating_margin_pct"),
            "net_margin_pct": p.get("net_margin_pct"),
        })

        profitability_comps = build_table(lambda p: {
            "company": p.get("symbol", ""),
            "gross_margin_pct": p.get("gross_margin_pct"),
            "operating_margin_pct": p.get("operating_margin_pct"),
            "ebitda_margin_pct": p.get("ebitda_margin_pct"),
            "net_margin_pct": p.get("net_margin_pct"),
            "roic_pct": p.get("roic_pct"),
            "roe_pct": p.get("roe_pct"),
        })

        growth_comps = build_table(lambda p: {
            "company": p.get("symbol", ""),
            "revenue_growth_pct": p.get("revenue_growth_pct"),
            "operating_income_growth_pct": p.get("operating_income_growth_pct"),
            "eps_diluted_growth_pct": p.get("eps_diluted_growth_pct"),
            "fcf_growth_pct": p.get("fcf_growth_pct"),
        })

        valuation_comps = build_table(lambda p: {
            "company": p.get("symbol", ""),
            "market_cap_usd_b": p.get("market_cap_usd_b"),
            "ev_to_sales": p.get("ev_to_sales"),
            "ev_to_ebitda": p.get("ev_to_ebitda"),
            "price_to_earnings": p.get("price_to_earnings"),
            "price_to_fcf": p.get("price_to_fcf"),
            "fcf_yield_pct": p.get("fcf_yield_pct"),
            "dividend_yield_pct": p.get("dividend_yield_pct"),
        })

        leverage_comps = build_table(lambda p: {
            "company": p.get("symbol", ""),
            "net_debt_to_ebitda": p.get("net_debt_to_ebitda"),
            "debt_to_equity": p.get("debt_to_equity"),
            "interest_coverage": p.get("interest_coverage"),
            "current_ratio": p.get("current_ratio"),
        })

        efficiency_comps = build_table(lambda p: {
            "company": p.get("symbol", ""),
            "rd_to_revenue_pct": p.get("rd_to_revenue_pct"),
            "sbc_to_revenue_pct": p.get("sbc_to_revenue_pct"),
            "capex_to_revenue_pct": p.get("capex_to_revenue_pct"),
            "dso_days": p.get("dso_days"),
            "ccc_days": p.get("ccc_days"),
        })

        returns_comps = build_table(lambda p: {
            "company": p.get("symbol", ""),
            "roic_pct": p.get("roic_pct"),
            "roe_pct": p.get("roe_pct"),
            "roa_pct": p.get("roa_pct"),
        })

        # Geographic comps (special — no median row, different logic)
        def _build_geo_comps() -> list[dict]:
            def _classify_us_intl(geo_dict: dict) -> tuple[float, float]:
                # Collect all US-matching segments; use the LARGEST one
                # to avoid double-counting overlapping hierarchies
                # (e.g., "North America" 64% + "United States" 60%).
                us_candidates: list[float] = []
                intl_pct = 0.0
                for region, val in geo_dict.items():
                    lower = region.lower()
                    if any(kw in lower for kw in ("united states", "north america", "domestic")) or lower in ("us", "u.s."):
                        us_candidates.append(val or 0)
                    elif lower in ("international", "non us", "non-us"):
                        # Skip aggregate international row if granular intl exists
                        continue
                    else:
                        intl_pct += val or 0
                us_pct = max(us_candidates) if us_candidates else 0.0
                # If us + intl > 110%, likely still overlapping — cap intl
                if us_pct + intl_pct > 110:
                    intl_pct = max(0, 100 - us_pct)
                return us_pct, intl_pct

            rows: list[dict] = []
            for p in lp:
                geo = p.get("geographic_revenue_pct")
                if not geo:
                    continue
                us, intl = _classify_us_intl(geo)
                rows.append({
                    "company": p.get("symbol", ""),
                    "revenue_usd_b": p.get("revenue_usd_b"),
                    "us_pct": _round(us, 1),
                    "international_pct": _round(intl, 1),
                })

            # Insert subject at the front
            subject_geo = geo_pcts.get(latest_year)
            if subject_geo:
                us, intl = _classify_us_intl(subject_geo)
                rows.insert(0, {
                    "company": subject_symbol,
                    "revenue_usd_b": _to_b(get_revenue(latest_year)),
                    "us_pct": _round(us, 1),
                    "international_pct": _round(intl, 1),
                })

            return rows

        geographic_comps = _build_geo_comps()

        return {
            "competitive_landscape": competitive_landscape,
            "profitability_comps": profitability_comps,
            "growth_comps": growth_comps,
            "valuation_comps": valuation_comps,
            "leverage_comps": leverage_comps,
            "efficiency_comps": efficiency_comps,
            "returns_comps": returns_comps,
            "geographic_comps": geographic_comps,
            "peer_medians": peers_grouped["peer_medians"],
        }

    peer_comp_tables = _build_peer_comp_tables()

    # ══════════════════════════════════════════════════════════════
    # ASSEMBLE OUTPUT
    # ══════════════════════════════════════════════════════════════

    # Helper lambdas for inline series
    def _shares_diluted_millions_5() -> dict:
        out = {}
        for y in last5:
            v = av("weightedAverageShsOutDil", y)
            out[y] = _round(v / 1_000_000, 2) if v is not None else None
        return out

    def _revenue_usd_m_5() -> dict:
        return {y: _to_m(get_revenue(y)) for y in last5}

    def _revenue_growth_pct_5() -> dict:
        out: dict = {}
        for i in range(1, len(last5)):
            curr = get_revenue(last5[i])
            prev = get_revenue(last5[i - 1])
            out[last5[i]] = (
                _pct((curr - prev) / abs(prev))
                if (curr is not None and prev is not None and prev != 0)
                else None
            )
        # First year: use stored metric
        if last5:
            out[last5[0]] = _pct(av("revenueGrowth", last5[0]))
        return out

    def _eps_diluted_5() -> dict:
        return {y: _round(av("epsDiluted", y), 4) for y in last5}

    def _income_quality_5() -> dict:
        return {y: _round(av("incomeQuality", y), 3) for y in last5}

    def _market_cap_usd_b_5() -> dict:
        return {y: _to_b(av("marketCap", y)) for y in last5}

    def _enterprise_value_usd_b_5() -> dict:
        return {y: _to_b(av("enterpriseValue", y)) for y in last5}

    def _shares_diluted_millions_5_with_fallback() -> dict:
        out = {}
        for y in last5:
            v = av("weightedAverageShsOutDil", y)
            out[y] = _round((v or 0) / 1_000_000, 2)
        return out

    def _dividend_per_share_3() -> dict:
        return {y: _round(av("dividendPerShare", y), 4) for y in last3}

    def _dividend_yield_pct_3() -> dict:
        return {y: _pct(av("dividendYield", y)) for y in last3}

    def _dividend_payout_ratio_pct_3() -> dict:
        return {y: _pct(av("dividendPayoutRatio", y)) for y in last3}

    def _fiscal_periods_string() -> str:
        subject_symbol = _rev_row.get("symbol", "")
        parts = [f"{subject_symbol}: {latest_year}"]
        for p in peers_grouped["latest"]:
            parts.append(f"{p['symbol']}: {p.get('fiscal_year', 'N/A')}")
        return " | ".join(parts)

    output = {
        "_meta": {
            "symbol": _rev_row.get("symbol", ""),
            "is_bank": is_bank,
            "is_insurance": is_insurance,
            "annual_years": last5,
            "quarterly_periods": last8q,
            "latest_annual_year": latest_year,
            "data_as_of": date.today().isoformat(),
            "extraction_note": (
                "All $ values in USD millions unless labeled _usd_b. "
                "Percentages are decimal x 100 (e.g. 41.77 = 41.77%). "
                "Negative capex/buyback values reflect cash outflows."
            ),
            "section_map": (
                "S1-S10 unchanged | S11 Financial Analysis | "
                "S12 Peer Benchmarking | S13 Valuation | S14 Risk"
            ),
        },

        "s2_capital_structure": {
            "total_debt_usd_m": {
                y: _to_m(av("totalDebt", y))
                for y in (latest_year, prior_year) if y
            },
            "short_term_debt_usd_m": {
                y: _to_m(av("shortTermDebt", y))
                for y in (latest_year,) if y
            },
            "long_term_debt_usd_m": {
                y: _to_m(av("longTermDebt", y))
                for y in (latest_year,) if y
            },
            "net_debt_usd_m": {
                y: _to_m(av("netDebt", y))
                for y in (latest_year, prior_year) if y
            },
            "total_equity_usd_m": {
                y: _to_m(av("totalStockholdersEquity", y))
                for y in (latest_year,) if y
            },
            "debt_to_equity_ratio": {
                y: _round(av("debtToEquityRatio", y), 2)
                for y in (latest_year, prior_year) if y
            },
            "cash_and_equivalents_usd_m": {
                y: _to_m(av("cashAndCashEquivalents", y))
                for y in (latest_year,) if y
            },
        },

        "s2_s4_revenue_splits": {
            "segment_revenue_usd_m": segment_splits,
            "segment_revenue_pct_of_total": segment_pcts,
            "segment_yoy_growth_pct": segment_growth,
            "geographic_revenue_usd_m": geo_splits,
            "geographic_revenue_pct_of_total": geo_pcts,
            "geographic_yoy_growth_pct": geo_growth,
        },

        "s4_rd": None if _skip_cogs else {
            "rd_expense_usd_m": {
                latest_year: _to_m(av("researchAndDevelopmentExpenses", latest_year)),
                prior_year: _to_m(av("researchAndDevelopmentExpenses", prior_year)),
                year2ago: _to_m(av("researchAndDevelopmentExpenses", year2ago)),
            },
            "rd_pct_of_revenue": {
                latest_year: _pct(av("researchAndDevelopementToRevenue", latest_year)),
                prior_year: _pct(av("researchAndDevelopementToRevenue", prior_year)),
                year2ago: _pct(av("researchAndDevelopementToRevenue", year2ago)),
            },
            "rd_growth_pct": {
                latest_year: _pct(av("rdexpenseGrowth", latest_year)),
                prior_year: _pct(av("rdexpenseGrowth", prior_year)),
            },
        },

        "s5_subject_margins": {
            "gross_margin_pct": None if _skip_cogs else pct_series_5("grossProfitMargin"),
            "operating_margin_pct": pct_series_5("operatingProfitMargin"),
            "net_margin_pct": pct_series_5("netProfitMargin"),
            "capex_pct_of_revenue": capex_pct_rev,
            "roic_pct": None if _skip_cogs else pct_series_5("returnOnInvestedCapital"),
        },

        "s5_share_data": {
            "shares_diluted_millions": _shares_diluted_millions_5(),
            "shares_diluted_latest_q_millions": (
                _round(qv("weightedAverageShsOutDil", last8q[0]) / 1_000_000, 2)
                if (last8q and qv("weightedAverageShsOutDil", last8q[0]) is not None)
                else None
            ),
            "latest_quarter_period": last8q[0] if last8q else None,
            "net_share_count_change_pct": share_count_change,
            "sbc_usd_m": to_m_series_5("stockBasedCompensation"),
            "sbc_pct_of_revenue": sbc_pct_rev,
        },

        "s6_competitive_landscape": peer_comp_tables["competitive_landscape"],

        # Working capital metrics are meaningless for banks/insurance
        "s7_working_capital": None if _skip_cogs else {
            "dso_days": round_series_5("daysOfSalesOutstanding", 1),
            "dpo_days": round_series_5("daysOfPayablesOutstanding", 1),
            "dio_days": round_series_5("daysOfInventoryOutstanding", 1),
            "cash_conversion_cycle_days": round_series_5("cashConversionCycle", 1),
            "deferred_revenue_usd_m": to_m_series_5("deferredRevenue"),
            "deferred_revenue_noncurrent_usd_m": to_m_series_5("deferredRevenueNonCurrent"),
            "accounts_receivable_usd_m": to_m_series_5("accountsReceivables"),
            "ar_vs_revenue_growth": ar_vs_rev_growth,
        },

        "s9_guidance_beat_miss": beat_miss_data,

        "s9_capital_allocation": {
            "rd_usd_m": None if _skip_cogs else to_m_series_3("researchAndDevelopmentExpenses"),
            "capex_usd_m": to_abs_m_series_3("capitalExpenditure"),
            "acquisitions_net_usd_m": to_abs_m_series_3("acquisitionsNet"),
            "dividends_paid_usd_m": to_abs_m_series_3("commonDividendsPaid"),
            "buybacks_usd_m": to_abs_m_series_3("commonStockRepurchased"),
            "dividend_per_share": _dividend_per_share_3(),
            "dividend_yield_pct": _dividend_yield_pct_3(),
            "dividend_payout_ratio_pct": _dividend_payout_ratio_pct_3(),
            "roic_pct": None if _skip_cogs else pct_series_5("returnOnInvestedCapital"),
            "owner_earnings": owner_earnings,
        },

        "s10_s13_forward_estimates": forward_estimates,

        # ── SECTION 11 ──────────────────────────────────────────

        "s11_income_statement": {
            "revenue_usd_m": _revenue_usd_m_5(),
            "revenue_growth_pct": _revenue_growth_pct_5(),
            "revenue_cagr_5yr_pct": rev_cagr_5yr,
            "revenue_cagr_3yr_pct": rev_cagr_3yr,
            "cost_of_revenue_usd_m": None if _skip_cogs else to_m_series_5("costOfRevenue"),
            "gross_profit_usd_m": None if _skip_cogs else to_m_series_5("grossProfit"),
            "gross_margin_pct": None if _skip_cogs else pct_series_5("grossProfitMargin"),
            "rd_expense_usd_m": None if _skip_cogs else to_m_series_5("researchAndDevelopmentExpenses"),
            "sga_expense_usd_m": to_m_series_5("sellingGeneralAndAdministrativeExpenses"),
            "operating_income_usd_m": to_m_series_5("operatingIncome"),
            "operating_margin_pct": pct_series_5("operatingProfitMargin"),
            "ebitda_usd_m": to_m_series_5("ebitda"),
            "ebitda_margin_pct": pct_series_5("ebitdaMargin"),
            "net_income_usd_m": to_m_series_5("netIncome"),
            "net_margin_pct": pct_series_5("netProfitMargin"),
            "net_income_growth_pct": pct_series_5("netIncomeGrowth"),
            "eps_diluted": _eps_diluted_5(),
            "eps_diluted_growth_pct": pct_series_5("epsdilutedGrowth"),
            "sbc_usd_m": to_m_series_5("stockBasedCompensation"),
            "sbc_pct_of_revenue": sbc_pct_rev,
            "interest_expense_usd_m": to_m_series_5("interestExpense"),
            "income_tax_expense_usd_m": to_m_series_5("incomeTaxExpense"),
            "effective_tax_rate_pct": pct_series_5("effectiveTaxRate"),
            "quarterly_income": quarterly_income,
        },

        "s11_cash_flow": {
            "operating_cash_flow_usd_m": to_m_series_5("operatingCashFlow"),
            "ocf_growth_pct": pct_series_5("operatingCashFlowGrowth"),
            "ocf_margin_pct": ocf_margin,
            "capex_usd_m": to_m_series_5("capitalExpenditure"),
            "capex_pct_of_revenue": capex_pct_rev,
            "free_cash_flow_usd_m": None if _skip_cogs else to_m_series_5("freeCashFlow"),
            "fcf_margin_pct": None if _skip_cogs else fcf_margin,
            "fcf_growth_pct": None if _skip_cogs else _fcf_growth_series(),
            "fcf_conversion_pct": None if _skip_cogs else fcf_conversion,
            "da_usd_m": to_m_series_5("depreciationAndAmortization"),
            "change_in_working_capital_usd_m": to_m_series_5("changeInWorkingCapital"),
            "dividends_paid_usd_m": to_m_series_5("dividendsPaid"),
            "share_repurchases_usd_m": to_m_series_5("commonStockRepurchased"),
            "owner_earnings": owner_earnings,
            "quarterly_cash_flow": quarterly_cash_flow,
        },

        "s11_balance_sheet": {
            "cash_and_equivalents_usd_m": to_m_series_5("cashAndCashEquivalents"),
            "total_current_assets_usd_m": to_m_series_5("totalCurrentAssets"),
            "pp_and_e_usd_m": to_m_series_5("propertyPlantAndEquipment"),
            "goodwill_usd_m": to_m_series_5("goodwill"),
            "intangible_assets_usd_m": to_m_series_5("intangibleAssets"),
            "total_assets_usd_m": to_m_series_5("totalAssets"),
            "total_current_liabilities_usd_m": to_m_series_5("totalCurrentLiabilities"),
            "total_debt_usd_m": to_m_series_5("totalDebt"),
            "total_liabilities_usd_m": to_m_series_5("totalLiabilities"),
            "retained_earnings_usd_m": to_m_series_5("retainedEarnings"),
            "total_equity_usd_m": to_m_series_5("totalStockholdersEquity"),
            "net_debt_usd_m": to_m_series_5("netDebt"),
            "net_working_capital_usd_m": {
                y: (
                    _to_m(av("totalCurrentAssets", y)) - _to_m(av("totalCurrentLiabilities", y))
                    if av("totalCurrentAssets", y) is not None and av("totalCurrentLiabilities", y) is not None
                    else None
                )
                for y in last5
            },
            "net_debt_to_ebitda": round_series_5("netDebtToEBITDA", 2),
            "interest_coverage_ratio": round_series_5("interestCoverageRatio", 2),
            "debt_to_equity_ratio": round_series_5("debtToEquityRatio", 2),
            "current_ratio": round_series_5("currentRatio", 2),
            "goodwill_pct_total_assets": goodwill_pct_assets,
            "goodwill_and_intangibles_usd_m": to_m_series_5("goodwillAndIntangibleAssets"),
        },

        "s11_returns": {
            "roic_pct": None if _skip_cogs else pct_series_5("returnOnInvestedCapital"),
            "roe_pct": pct_series_5("returnOnEquity"),
            "roa_pct": pct_series_5("returnOnAssets"),
            "roce_pct": pct_series_5("returnOnCapitalEmployed"),
            "income_quality": _income_quality_5(),
            "ar_vs_revenue_growth": ar_vs_rev_growth,
        },

        "s12_peer_benchmarking": {
            "profitability_comps": peer_comp_tables["profitability_comps"],
            "growth_comps": peer_comp_tables["growth_comps"],
            "valuation_comps": peer_comp_tables["valuation_comps"],
            "leverage_comps": peer_comp_tables["leverage_comps"],
            "efficiency_comps": peer_comp_tables["efficiency_comps"],
            "returns_comps": peer_comp_tables["returns_comps"],
            "geographic_comps": peer_comp_tables["geographic_comps"],
            "peer_medians": peer_comp_tables["peer_medians"],
            "fiscal_periods": _fiscal_periods_string(),
            "peers_full": peers_grouped,
        },

        "s13_valuation": {
            "market_cap_usd_b": _market_cap_usd_b_5(),
            "enterprise_value_usd_b": _enterprise_value_usd_b_5(),
            "ev_to_sales": round_series_5("evToSales", 2),
            "ev_to_ebitda": round_series_5("evToEBITDA", 2),
            "ev_to_fcf": round_series_5("evToFreeCashFlow", 2),
            "price_to_earnings": round_series_5("priceToEarningsRatio", 2),
            "price_to_fcf": round_series_5("priceToFreeCashFlowRatio", 2),
            "price_to_sales": round_series_5("priceToSalesRatio", 2),
            "price_to_book": round_series_5("priceToBookRatio", 2),
            "earnings_yield_pct": pct_series_5("earningsYield"),
            "fcf_yield_pct": pct_series_5("freeCashFlowYield"),
            "shares_diluted_millions": _shares_diluted_millions_5_with_fallback(),
            "net_debt_usd_m": to_m_series_5("netDebt"),
            "peer_valuation_medians": {
                "ev_to_sales": peers_grouped["peer_medians"].get("ev_to_sales"),
                "ev_to_ebitda": peers_grouped["peer_medians"].get("ev_to_ebitda"),
                "ev_to_fcf": peers_grouped["peer_medians"].get("ev_to_fcf"),
                "price_to_earnings": peers_grouped["peer_medians"].get("price_to_earnings"),
                "price_to_fcf": peers_grouped["peer_medians"].get("price_to_fcf"),
                "fcf_yield_pct": peers_grouped["peer_medians"].get("fcf_yield_pct"),
            },
            "forward_estimates": forward_estimates,
        },
    }

    # ── POST-PROCESSING: Scrub incomplete-year zeros -> null ─────

    rev_for_scrub = output["s11_income_statement"].get("revenue_usd_m", {})
    incomplete_years = [
        y for y, v in rev_for_scrub.items()
        if v is None or v == 0
    ]

    if incomplete_years:
        def scrub_zeros(obj: Any) -> None:
            if not obj or not isinstance(obj, dict):
                return
            for key, val in obj.items():
                if isinstance(val, list):
                    continue
                if isinstance(val, dict):
                    keys = list(val.keys())
                    is_year_keyed = len(keys) > 0 and any(
                        re.match(r"^\d{4}", str(k)) for k in keys
                    )
                    if is_year_keyed:
                        for yr in incomplete_years:
                            if yr in val and val[yr] == 0:
                                val[yr] = None
                    else:
                        scrub_zeros(val)

        scrub_zeros(output)

    # ── POST-PROCESSING: Financial Health Flags ───────────────────
    output["s10_financial_flags"] = _build_financial_health_flags(output)

    return output


# ══════════════════════════════════════════════════════════════════════
# FINANCIAL HEALTH FLAGS — Pre-computed diagnostic anomalies for S10
# ══════════════════════════════════════════════════════════════════════

def _build_financial_health_flags(facts: dict) -> list[dict]:
    """Detect financial quality anomalies from the computed facts.

    Returns a list of flag dicts:
        {"flag": "Net Debt Spike", "severity": "HIGH",
         "detail": "ND/EBITDA jumped to 5.3x from 1.9x YoY (+179%)"}

    Skips flags where underlying data is None/NM.  For banks, skips
    Net Debt Spike and Interest Coverage (meaningless for bank balance sheets).
    """
    flags: list[dict] = []
    ticker = facts.get("_meta", {}).get("symbol", "")
    _is_bank = ticker in BANK_TICKERS
    _is_insurance = facts.get("_meta", {}).get("is_insurance", False)
    _is_financial = _is_bank or _is_insurance
    years = facts.get("_meta", {}).get("annual_years", [])
    if len(years) < 2:
        return flags
    latest = years[-1]
    prev = years[-2]

    def _v(section: str, metric: str, year: str):
        """Safely get a value from facts[section][metric][year]."""
        s = facts.get(section)
        if not s:
            return None
        m = s.get(metric)
        if not m or not isinstance(m, dict):
            return None
        v = m.get(year)
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _avg(section: str, metric: str, year_list: list[str]):
        """Average of non-None values over given years."""
        vals = [_v(section, metric, y) for y in year_list]
        vals = [x for x in vals if x is not None]
        return sum(vals) / len(vals) if vals else None

    # ── 1. Net Debt Spike ────────────────────────────────────────
    if not _is_financial:
        nd_latest = _v("s11_balance_sheet", "net_debt_to_ebitda", latest)
        nd_prev = _v("s11_balance_sheet", "net_debt_to_ebitda", prev)
        if nd_latest is not None and nd_prev is not None and nd_prev > 0:
            yoy_ratio = nd_latest / nd_prev
            if yoy_ratio > 1.5:
                flags.append({
                    "flag": "Net Debt Spike",
                    "severity": "HIGH",
                    "detail": (
                        f"ND/EBITDA jumped to {nd_latest:.1f}x from {nd_prev:.1f}x YoY "
                        f"(+{(yoy_ratio - 1) * 100:.0f}%)"
                    ),
                })
        # Also check vs 3yr avg
        if nd_latest is not None and len(years) >= 4:
            avg_3yr = _avg("s11_balance_sheet", "net_debt_to_ebitda",
                           years[-4:-1])  # 3 years before latest
            if avg_3yr is not None and avg_3yr > 0 and nd_latest / avg_3yr > 2.0:
                # Don't double-flag if YoY already triggered
                if not any(f["flag"] == "Net Debt Spike" for f in flags):
                    flags.append({
                        "flag": "Net Debt Spike",
                        "severity": "HIGH",
                        "detail": (
                            f"ND/EBITDA at {nd_latest:.1f}x vs 3-year avg of "
                            f"{avg_3yr:.1f}x (+{(nd_latest / avg_3yr - 1) * 100:.0f}%)"
                        ),
                    })

    # ── 2. ROIC Decline ──────────────────────────────────────────
    # Skip for financials — ROIC formula (NOPAT / invested capital) is
    # meaningless for banks/insurers where policyholder liabilities and
    # deposit funding aren't captured in the denominator.
    if _is_financial:
        roic_latest = None
    else:
        roic_latest = _v("s11_returns", "roic_pct", latest)
    if roic_latest is not None and len(years) >= 4:
        roic_3yr_avg = _avg("s11_returns", "roic_pct", years[-4:-1])
        peer_roic = facts.get("s12_peer_benchmarking", {}).get(
            "peer_medians", {}).get("roic_pct")
        detail_parts = []
        if roic_3yr_avg is not None and (roic_3yr_avg - roic_latest) > 5:
            detail_parts.append(
                f"ROIC dropped to {roic_latest:.1f}% from 3-year avg of "
                f"{roic_3yr_avg:.1f}% (−{roic_3yr_avg - roic_latest:.1f}pp)"
            )
        if peer_roic is not None and roic_latest < peer_roic:
            detail_parts.append(
                f"below peer median of {peer_roic:.1f}%"
            )
        if detail_parts:
            flags.append({
                "flag": "ROIC Decline",
                "severity": "HIGH",
                "detail": "; ".join(detail_parts),
            })

    # ── 3. Margin Compression ────────────────────────────────────
    op_latest = _v("s11_income_statement", "operating_margin_pct", latest)
    op_prev = _v("s11_income_statement", "operating_margin_pct", prev)
    gm_latest = _v("s11_income_statement", "gross_margin_pct", latest)
    gm_prev = _v("s11_income_statement", "gross_margin_pct", prev)
    if (op_latest is not None and op_prev is not None
            and gm_latest is not None and gm_prev is not None):
        op_delta = op_latest - op_prev
        gm_delta = gm_latest - gm_prev
        if op_delta < -3 and abs(gm_delta) <= 1:
            flags.append({
                "flag": "Margin Compression",
                "severity": "MEDIUM",
                "detail": (
                    f"Operating margin declined {op_delta:+.1f}pp to {op_latest:.1f}% "
                    f"while gross margin stable at {gm_latest:.1f}% "
                    f"({gm_delta:+.1f}pp) — suggests opex/SGA inflation"
                ),
            })

    # ── 4. FCF Conversion Volatility ─────────────────────────────
    if not _is_financial:
        conv_vals = []
        for y in years:
            c = _v("s11_cash_flow", "fcf_conversion_pct", y)
            if c is not None:
                conv_vals.append((y, c))
        if len(conv_vals) >= 2:
            max_swing = 0
            swing_years = ("", "")
            for i in range(1, len(conv_vals)):
                swing = abs(conv_vals[i][1] - conv_vals[i - 1][1])
                if swing > max_swing:
                    max_swing = swing
                    swing_years = (conv_vals[i - 1][0], conv_vals[i][0])
            if max_swing > 50:
                flags.append({
                    "flag": "FCF Conversion Volatility",
                    "severity": "MEDIUM",
                    "detail": (
                        f"FCF/Net Income swung {max_swing:.0f}pp between "
                        f"{swing_years[0]} and {swing_years[1]}"
                    ),
                })

    # ── 5. Interest Coverage Deterioration ───────────────────────
    if not _is_financial:
        ic_latest = _v("s11_balance_sheet", "interest_coverage_ratio", latest)
        ic_prev = _v("s11_balance_sheet", "interest_coverage_ratio", prev)
        if ic_latest is not None:
            detail = None
            if ic_latest < 3:
                detail = f"Interest coverage at {ic_latest:.1f}x (below 3x threshold)"
            elif ic_prev is not None and ic_prev > 0 and ic_latest / ic_prev < 0.5:
                detail = (
                    f"Interest coverage fell {(1 - ic_latest / ic_prev) * 100:.0f}% "
                    f"to {ic_latest:.1f}x from {ic_prev:.1f}x"
                )
            if detail:
                flags.append({
                    "flag": "Interest Coverage Deterioration",
                    "severity": "HIGH",
                    "detail": detail,
                })

    # ── 6. Revenue Deceleration ──────────────────────────────────
    growth_vals = []
    for y in years:
        g = _v("s11_income_statement", "revenue_growth_pct", y)
        if g is not None:
            growth_vals.append((y, g))
    if len(growth_vals) >= 3:
        # Check for 2+ consecutive declining growth rates
        declines = 0
        for i in range(1, len(growth_vals)):
            if growth_vals[i][1] < growth_vals[i - 1][1]:
                declines += 1
            else:
                declines = 0
            if declines >= 2:
                flags.append({
                    "flag": "Revenue Deceleration",
                    "severity": "MEDIUM",
                    "detail": (
                        f"Revenue growth declined for {declines + 1} consecutive years: "
                        f"{growth_vals[i - declines][1]:.1f}% → {growth_vals[i][1]:.1f}%"
                    ),
                })
                break

    # ── 7. Goodwill Concentration ────────────────────────────────
    gw = _v("s11_balance_sheet", "goodwill_pct_total_assets", latest)
    if gw is not None and gw > 25:
        flags.append({
            "flag": "Goodwill Concentration",
            "severity": "LOW",
            "detail": f"Goodwill is {gw:.1f}% of total assets (>25% threshold)",
        })

    # ── 8. SBC Dilution ──────────────────────────────────────────
    sbc_pct = _v("s11_income_statement", "sbc_pct_of_revenue", latest)
    share_chg = _v("s5_share_data", "net_share_count_change_pct", latest)
    if sbc_pct is not None and share_chg is not None:
        if sbc_pct > 5 and share_chg > 2:
            flags.append({
                "flag": "SBC Dilution",
                "severity": "MEDIUM",
                "detail": (
                    f"SBC at {sbc_pct:.1f}% of revenue with share count "
                    f"growing {share_chg:+.1f}%/yr"
                ),
            })

    # ── 9½. Operating Margin Distortion (one-time items) ─────────
    #    Detects when EBITDA margin is significantly higher than operating
    #    margin, suggesting large non-recurring charges (e.g., merger
    #    termination fees, restructuring, impairments) distort GAAP operating
    #    income. Also triggered if the op-margin-to-EBITDA-margin gap widened
    #    significantly YoY.
    if not _is_financial:
        ebitda_m_latest = _v("s11_income_statement", "ebitda_margin_pct", latest)
        ebitda_m_prev = _v("s11_income_statement", "ebitda_margin_pct", prev)
        if (op_latest is not None and ebitda_m_latest is not None
                and ebitda_m_latest > 0):
            gap_latest = ebitda_m_latest - op_latest
            gap_prev = (
                (ebitda_m_prev - op_prev)
                if (ebitda_m_prev is not None and op_prev is not None)
                else None
            )
            # Flag 1: Current gap > 12pp (large non-recurring charges)
            # Flag 2: Gap widened > 8pp YoY (sudden one-time hit)
            triggered = False
            detail_parts = []
            if gap_latest > 12:
                detail_parts.append(
                    f"EBITDA margin ({ebitda_m_latest:.1f}%) is {gap_latest:.0f}pp above "
                    f"operating margin ({op_latest:.1f}%), suggesting large non-recurring "
                    f"charges depress GAAP operating income"
                )
                triggered = True
            if gap_prev is not None and (gap_latest - gap_prev) > 8:
                detail_parts.append(
                    f"EBITDA-to-operating margin gap widened from {gap_prev:.0f}pp "
                    f"to {gap_latest:.0f}pp YoY (+{gap_latest - gap_prev:.0f}pp), "
                    f"indicating new one-time charges in the latest period"
                )
                triggered = True
            if triggered:
                flags.append({
                    "flag": "Operating Margin Distortion",
                    "severity": "HIGH",
                    "detail": "; ".join(detail_parts),
                })

    # ── 10. Share Count Step Change ────────────────────────────────
    #    Detects significant YoY share count changes (>5%) that signal
    #    major buybacks or dilution. Also compares annual weighted average
    #    to the latest quarterly figure when available (post-ASR detection).
    shares_latest = _v("s5_share_data", "shares_diluted_millions", latest)
    shares_prev = _v("s5_share_data", "shares_diluted_millions", prev)
    if shares_latest is not None and shares_prev is not None and shares_prev > 0:
        share_delta_pct = (shares_latest - shares_prev) / shares_prev * 100
        if abs(share_delta_pct) > 5:
            direction = "increased" if share_delta_pct > 0 else "decreased"
            flags.append({
                "flag": "Share Count Step Change",
                "severity": "MEDIUM",
                "detail": (
                    f"Diluted share count {direction} {abs(share_delta_pct):.1f}% YoY "
                    f"from {shares_prev:.1f}M to {shares_latest:.1f}M"
                ),
            })
    # Also check latest quarterly vs annual weighted average (post-ASR detection)
    latest_q_shares = facts.get("s5_share_data", {}).get(
        "shares_diluted_latest_q_millions")
    if (shares_latest is not None and latest_q_shares is not None
            and shares_latest > 0):
        q_delta_pct = (latest_q_shares - shares_latest) / shares_latest * 100
        if abs(q_delta_pct) > 5:
            latest_q_period = facts.get("s5_share_data", {}).get(
                "latest_quarter_period", "latest quarter")
            direction = "higher" if q_delta_pct > 0 else "lower"
            flags.append({
                "flag": "Share Count Step Change",
                "severity": "HIGH" if abs(q_delta_pct) > 10 else "MEDIUM",
                "detail": (
                    f"Latest quarterly share count ({latest_q_shares:.1f}M as of "
                    f"{latest_q_period}) is {abs(q_delta_pct):.1f}% {direction} than "
                    f"annual weighted average ({shares_latest:.1f}M), suggesting "
                    f"{'significant recent buybacks' if q_delta_pct < 0 else 'recent dilution'}"
                ),
            })

    # ── 11. Earnings Quality Gap ──────────────────────────────────
    if not _is_financial:
        ocf = _v("s11_cash_flow", "operating_cash_flow_usd_m", latest)
        ni = _v("s11_income_statement", "net_income_usd_m", latest)
        if ocf is not None and ni is not None and ni > 0:
            ratio = ocf / ni
            if ratio < 0.7:
                flags.append({
                    "flag": "Earnings Quality Gap",
                    "severity": "MEDIUM",
                    "detail": (
                        f"OCF/Net Income at {ratio:.2f}x (<0.7x threshold) — "
                        f"cash earnings lag reported earnings"
                    ),
                })

    return flags


# ══════════════════════════════════════════════════════════════════════
# STANDALONE PEER COMP TABLE BUILDER
# ══════════════════════════════════════════════════════════════════════
#
# Module-level function that can be called from the orchestrator with
# the LLM-curated peer data from peer_selection.py.
# Unlike the nested _build_peer_comp_tables() above, this takes
# peer_data dict + subject_ticker directly instead of relying on
# closure variables from build_quantitative_facts().

def build_peer_comp_tables(
    peer_data: dict,
    subject_ticker: str,
    subject_overrides: dict | None = None,
) -> dict:
    """Build peer comparison tables from peer pipeline data.

    Args:
        peer_data: Dict with keys ``latest`` (list of peer records),
            ``by_symbol`` (dict of symbol → year → record),
            ``peer_medians`` (dict of metric → median value).
        subject_ticker: Subject company's ticker symbol.
        subject_overrides: Optional dict of metric values (from SEC XBRL)
            to overlay on the subject's FMP peer-API row so that the
            subject's own ratios (ROIC, ROE, margins, etc.) are
            consistent with the rest of the memo.

    Returns:
        Dict with keys: profitability_comps, growth_comps,
        valuation_comps, leverage_comps, efficiency_comps,
        competitive_landscape, geographic_comps, peer_medians.
    """
    all_latest = peer_data.get("latest") or []
    medians = peer_data.get("peer_medians") or {}

    if not all_latest:
        return {"peer_medians": medians}

    # Separate subject from peers
    subject_data = _find_subject_in_peers(all_latest, subject_ticker)

    # Overlay SEC XBRL-derived values so the subject row is consistent
    # with the rest of the memo (FMP peer API computes ratios differently).
    if subject_overrides:
        subject_data = {**subject_data, **subject_overrides}
    latest_peers = [
        p for p in all_latest
        if (p.get("symbol") or "").upper() != subject_ticker.upper()
    ]

    def _r2(val, ndigits=2):
        if val is None:
            return None
        try:
            return round(float(val), ndigits)
        except (TypeError, ValueError):
            return None

    def _build_table(peer_mapper, subject_mapper=None):
        """Build a comp table: [subject, ...peers, median]."""
        peer_rows = [peer_mapper(p) for p in latest_peers]
        # Subject row — from subject data fetched alongside peers
        subject_row = (subject_mapper or peer_mapper)(subject_data)
        subject_row["company"] = subject_ticker

        # NM-cap extreme outliers in all rows
        for row in [subject_row] + peer_rows:
            for field in list(row.keys()):
                if field == "company":
                    continue
                row[field] = _nm_cap(row[field], field)

        # Median row (computed AFTER NM capping so outliers don't skew)
        median_row = {"company": "Peer Median"}
        if peer_rows:
            numeric_keys = [k for k in peer_rows[0] if k != "company"]
            for k in numeric_keys:
                vals = sorted(
                    v for v in (r.get(k) for r in peer_rows)
                    if v is not None
                )
                if not vals:
                    median_row[k] = None
                    continue
                mid = len(vals) // 2
                median_row[k] = (
                    _r2(vals[mid]) if len(vals) % 2
                    else _r2((vals[mid - 1] + vals[mid]) / 2)
                )

        return [subject_row] + peer_rows + [median_row]

    competitive_landscape = _build_table(lambda p: {
        "company": p.get("symbol", ""),
        "revenue_usd_b": p.get("revenue_usd_b"),
        "revenue_growth_pct": p.get("revenue_growth_pct"),
        "operating_margin_pct": p.get("operating_margin_pct"),
        "net_margin_pct": p.get("net_margin_pct"),
    })

    profitability_comps = _build_table(lambda p: {
        "company": p.get("symbol", ""),
        "gross_margin_pct": p.get("gross_margin_pct"),
        "operating_margin_pct": p.get("operating_margin_pct"),
        "ebitda_margin_pct": p.get("ebitda_margin_pct"),
        "net_margin_pct": p.get("net_margin_pct"),
        "roic_pct": p.get("roic_pct"),
        "roe_pct": p.get("roe_pct"),
    })

    growth_comps = _build_table(lambda p: {
        "company": p.get("symbol", ""),
        "revenue_growth_pct": p.get("revenue_growth_pct"),
        "eps_diluted_growth_pct": p.get("eps_diluted_growth_pct"),
        "fcf_growth_pct": p.get("fcf_growth_pct"),
    })

    valuation_comps = _build_table(lambda p: {
        "company": p.get("symbol", ""),
        "market_cap_usd_b": p.get("market_cap_usd_b"),
        "ev_to_sales": p.get("ev_to_sales"),
        "ev_to_ebitda": p.get("ev_to_ebitda"),
        "price_to_earnings": p.get("price_to_earnings"),
        "price_to_fcf": p.get("price_to_fcf"),
        "price_to_book": p.get("price_to_book"),
        "fcf_yield_pct": p.get("fcf_yield_pct"),
        "dividend_yield_pct": p.get("dividend_yield_pct"),
    })

    leverage_comps = _build_table(lambda p: {
        "company": p.get("symbol", ""),
        "net_debt_to_ebitda": p.get("net_debt_to_ebitda"),
        "debt_to_equity": p.get("debt_to_equity"),
        "interest_coverage": p.get("interest_coverage"),
        "current_ratio": p.get("current_ratio"),
    })

    efficiency_comps = _build_table(lambda p: {
        "company": p.get("symbol", ""),
        "rd_to_revenue_pct": p.get("rd_to_revenue_pct"),
        "sbc_to_revenue_pct": p.get("sbc_to_revenue_pct"),
        "capex_to_revenue_pct": p.get("capex_to_revenue_pct"),
    })

    returns_comps = _build_table(lambda p: {
        "company": p.get("symbol", ""),
        "roic_pct": p.get("roic_pct"),
        "roe_pct": p.get("roe_pct"),
        "roa_pct": p.get("roa_pct"),
    })

    # Financial-sector valuation comps (P/B, P/TBV focus for banks/insurance)
    valuation_comps_financial = _build_table(lambda p: {
        "company": p.get("symbol", ""),
        "market_cap_usd_b": p.get("market_cap_usd_b"),
        "price_to_earnings": p.get("price_to_earnings"),
        "price_to_book": p.get("price_to_book"),
        "price_to_tangible_book": p.get("price_to_tangible_book"),
        "dividend_yield_pct": p.get("dividend_yield_pct"),
        "roe_pct": p.get("roe_pct"),
    })

    return {
        "competitive_landscape": competitive_landscape,
        "profitability_comps": profitability_comps,
        "growth_comps": growth_comps,
        "valuation_comps": valuation_comps,
        "valuation_comps_financial": valuation_comps_financial,
        "leverage_comps": leverage_comps,
        "efficiency_comps": efficiency_comps,
        "returns_comps": returns_comps,
        "geographic_comps": [],  # Requires geo data not in standard peer records
        "peer_medians": medians,
    }


def _find_subject_in_peers(
    latest_peers: list[dict],
    subject_ticker: str,
) -> dict:
    """Find the subject company's data within the peer list, or return empty dict."""
    for p in latest_peers:
        if (p.get("symbol") or "").upper() == subject_ticker.upper():
            return p
    return {}
