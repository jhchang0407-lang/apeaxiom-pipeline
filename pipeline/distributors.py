"""
Section Distributor — Python port of Section_Distributor.js (v10.1) and
Section_Distributor_2.js (v11.1).

Builds writer inputs for all memo sections (2-14) from a fact sheet and
source registry.  Each section gets: schema, facts/context, template, and
pre-computed table data.

Public entry point: distribute_sections()
"""

from __future__ import annotations

import math
import re
from copy import deepcopy
from typing import Any

# ═══════════════════════════════════════════════════════════════
# 1. NM (NOT MEANINGFUL) SANITIZATION
# ═══════════════════════════════════════════════════════════════

NM_THRESHOLDS: dict[str, dict] = {
    # Growth rates (YoY %)
    "revenue_growth_pct":          {"absMax": 500},
    "operating_income_growth_pct": {"absMax": 500},
    "eps_diluted_growth_pct":      {"absMax": 500},
    "fcf_growth_pct":              {"absMax": 500},
    "ebitda_growth_pct":           {"absMax": 500},
    # Return metrics
    "roe_pct":                     {"absMax": 200},
    "roic_pct":                    {"absMax": 200},
    # Valuation multiples
    "price_to_earnings":           {"max": 150, "min": 0},
    "price_to_fcf":                {"max": 150, "min": 0},
    "ev_to_ebitda":                {"max": 100, "min": 0},
    "ev_to_sales":                 {"max": 50,  "min": 0},
    # Working capital (days)
    "dso_days":                    {"max": 365, "min": 0},
    "dpo_days":                    {"max": 365, "min": 0},
    "dio_days":                    {"max": 365, "min": 0},
    "cash_conversion_cycle_days":  {"absMax": 365},
    # Leverage
    "debt_to_equity":              {"max": 20, "min": -0.01},
    "debt_to_equity_ratio":        {"max": 20, "min": -0.01},
    "net_debt_to_ebitda":          {"max": 30, "min": -5},
    "interest_coverage_ratio":     {"max": 200, "min": -200},
}

NM_FIELD_ALIASES: dict[str, str] = {
    "revenue_growth":          "revenue_growth_pct",
    "operating_income_growth": "operating_income_growth_pct",
    "eps_growth":              "eps_diluted_growth_pct",
    "eps_diluted_growth":      "eps_diluted_growth_pct",
    "fcf_growth":              "fcf_growth_pct",
    "ebitda_growth":           "ebitda_growth_pct",
    "roe":                     "roe_pct",
    "roic":                    "roic_pct",
    "pe":                      "price_to_earnings",
    "p_e":                     "price_to_earnings",
    "pe_ratio":                "price_to_earnings",
    "p_fcf":                   "price_to_fcf",
    "ev_ebitda":               "ev_to_ebitda",
    "ev_sales":                "ev_to_sales",
    "dso":                     "dso_days",
    "dpo":                     "dpo_days",
    "dio":                     "dio_days",
    "ccc":                     "cash_conversion_cycle_days",
    "ccc_days":                "cash_conversion_cycle_days",
    "de_ratio":                "debt_to_equity",
    "net_debt_ebitda":         "net_debt_to_ebitda",
}


def _get_threshold(field_name: str) -> dict | None:
    if field_name in NM_THRESHOLDS:
        return NM_THRESHOLDS[field_name]
    alias = NM_FIELD_ALIASES.get(field_name)
    if alias and alias in NM_THRESHOLDS:
        return NM_THRESHOLDS[alias]
    return None


def nm_check(value: Any, field_name: str) -> Any:
    """Return *value* unchanged if within thresholds, else ``"NM"``."""
    threshold = _get_threshold(field_name)
    if threshold is None:
        return value
    if value is None or value == "" or value == "NM":
        return "NM"
    if isinstance(value, str):
        cleaned = re.sub(r"[%,x$]", "", value).strip()
        try:
            num = float(cleaned)
        except (ValueError, TypeError):
            return "NM"
    elif isinstance(value, (int, float)):
        num = value
    else:
        return "NM"
    if math.isnan(num):
        return "NM"
    if "absMax" in threshold and abs(num) > threshold["absMax"]:
        return "NM"
    if "max" in threshold and num > threshold["max"]:
        return "NM"
    if "min" in threshold and num < threshold["min"]:
        return "NM"
    return value


def nm_sanitize_year_obj(obj: dict | None, field_name: str) -> dict | None:
    """NM-sanitize every year-keyed value in *obj* in-place."""
    if not isinstance(obj, dict):
        return obj
    threshold = _get_threshold(field_name)
    if threshold is None:
        return obj
    for yr in list(obj.keys()):
        obj[yr] = nm_check(obj[yr], field_name)
    return obj


def nm_sanitize_peer_table(rows: list[dict], field_aliases: dict | None = None) -> list[dict]:
    """NM-sanitize peer rows (skip Peer Median row), then recalc median."""
    if not isinstance(rows, list) or len(rows) == 0:
        return rows
    # Sanitize individual peer rows
    for row in rows:
        if not row or row.get("company") == "Peer Median":
            continue
        for field in list(row.keys()):
            if field == "company":
                continue
            thr = _get_threshold(field)
            if thr:
                row[field] = nm_check(row[field], field)
    # Recalculate median row
    median_row = None
    for row in rows:
        if row and row.get("company") == "Peer Median":
            median_row = row
            break
    if median_row is not None:
        _nm_recalc_median_row(rows, median_row)
    return rows


def _nm_recalc_median_row(rows: list[dict], median_row: dict) -> dict:
    data_rows = [
        r for r in rows
        if r and r.get("company") != "Peer Median"
        and "\u2605" not in (r.get("company") or "")
    ]
    for field in list(median_row.keys()):
        if field == "company":
            continue
        vals = []
        for r in data_rows:
            v = r.get(field)
            if v == "NM" or v is None:
                continue
            try:
                vals.append(float(v))
            except (ValueError, TypeError):
                continue
        vals.sort()
        if len(vals) == 0:
            median_row[field] = "NM"
        elif len(vals) == 1:
            median_row[field] = vals[0]
        else:
            mid = len(vals) // 2
            median_row[field] = (
                (vals[mid - 1] + vals[mid]) / 2 if len(vals) % 2 == 0 else vals[mid]
            )
    return median_row


# ═══════════════════════════════════════════════════════════════
# 2. NUMBER FORMATTING
# ═══════════════════════════════════════════════════════════════

def _trim_zeros(s: str) -> str:
    return s[:-3] if s.endswith(".00") else s


def fmt_num(val: Any, unit: str = "") -> str | None:
    if val is None:
        return None
    if isinstance(val, str):
        return val

    a = abs(val)
    sign = "-" if val < 0 else ""

    if unit == "usd_m":
        if a >= 1_000_000:  # $1T+
            return f"{sign}${_trim_zeros(f'{a / 1_000_000:.2f}')}T"
        if a >= 1000:
            return f"{sign}${_trim_zeros(f'{a / 1000:.2f}')}B"
        if a > 0:
            return f"{sign}${_trim_zeros(f'{a:.2f}')}M"
        return "$0"

    if unit == "usd_b":
        if a >= 1000:  # $1T+
            return f"{sign}${_trim_zeros(f'{a / 1000:.2f}')}T"
        if a >= 1:
            return f"{sign}${_trim_zeros(f'{a:.2f}')}B"
        return f"{sign}${_trim_zeros(f'{a * 1000:.2f}')}M"

    if unit == "usd_raw":
        if a >= 1e12:
            return f"{sign}${_trim_zeros(f'{a / 1e12:.2f}')}T"
        if a >= 1e9:
            return f"{sign}${_trim_zeros(f'{a / 1e9:.2f}')}B"
        if a >= 1e6:
            return f"{sign}${_trim_zeros(f'{a / 1e6:.2f}')}M"
        if a >= 1000:
            return f"{sign}${round(a):,}"
        return f"{sign}${a:.0f}"

    if unit == "pct":
        return f"{sign}{a:.1f}%"

    if unit in ("ratio", "x"):
        return f"{sign}{a:.1f}x"

    if unit == "millions":
        if a >= 1_000_000:  # 1T+ shares
            return f"{sign}{_trim_zeros(f'{a / 1_000_000:.2f}')}T"
        if a >= 1000:
            return f"{sign}{_trim_zeros(f'{a / 1000:.2f}')}B"
        return f"{sign}{round(a):,}M"

    if unit == "count":
        if a >= 1e9:
            return f"{sign}{a / 1e9:.1f}B"
        if a >= 1e6:
            return f"{sign}{a / 1e6:.1f}M"
        if a >= 1000:
            return f"{sign}{round(a):,}"
        return f"{sign}{val}"

    if unit == "days":
        if isinstance(val, int) or (isinstance(val, float) and val == int(val)):
            return f"{sign}{int(a)}"
        return f"{sign}{a:.1f}"

    # default
    if isinstance(val, int):
        return f"{val:,}"
    return f"{val:.1f}"


def detect_unit(key: str) -> str:
    if not key:
        return ""
    k = key.lower()
    if any(t in k for t in ("dso", "dpo", "dio", "days_sales", "days_payable",
                             "days_inventory", "cash_conversion_cycle", "_days")) or k == "ccc":
        return "days"
    if k.endswith("_usd_m") or k.endswith("_usd_millions"):
        return "usd_m"
    if k.endswith("_usd_b") or k.endswith("_usd_billions"):
        return "usd_b"
    if any(k.endswith(s) for s in ("_pct", "_percent", "_margin")) or \
       "yield_pct" in k or "_growth" in k or k == "surprise_pct" or "cagr" in k:
        return "pct"
    if k.startswith("ev_to_") or k.startswith("price_to_") or k.startswith("net_debt_to_") or \
       "_ratio" in k or k in ("interest_coverage_ratio", "current_ratio",
                               "quick_ratio", "debt_to_equity"):
        return "x"
    if "shares_" in k and "million" in k:
        return "millions"
    if k in ("shares_diluted_millions", "shares_basic_millions"):
        return "millions"
    if k in ("employee_count", "fulltimeemployees", "full_time_employees"):
        return "count"
    if k in ("mktcap", "market_cap"):
        return "usd_raw"
    if k in ("price", "current_price", "stock_price", "target_price") or "_per_share" in k:
        return ""
    if k in ("revenue", "netincome", "ebitda", "operatingincome"):
        return "usd_raw"
    return ""


_YEAR_KEY_RE = re.compile(r"^\d{4}|^Q\d|FY")


def fmt_obj(obj: Any, parent_key: str | None = None) -> Any:
    if obj is None:
        return obj
    if isinstance(obj, str):
        return obj
    if isinstance(obj, (int, float)):
        return fmt_num(obj, detect_unit(parent_key or ""))
    if isinstance(obj, list):
        return [fmt_obj(item, parent_key) for item in obj]
    if isinstance(obj, dict):
        result = {}
        for key, val in obj.items():
            if not isinstance(key, str):
                continue
            if _YEAR_KEY_RE.match(key):
                result[key] = fmt_obj(val, parent_key)
            else:
                result[key] = fmt_obj(val, key)
        return result
    return obj


# ═══════════════════════════════════════════════════════════════
# 3. TEXT SANITIZATION
# ═══════════════════════════════════════════════════════════════

_SANITIZE_MAP = [
    (re.compile(r"[\u2018\u2019\u201A\u2039\u203A]"), "'"),
    (re.compile(r"[\u201C\u201D\u201E\u00AB\u00BB]"), '"'),
    (re.compile(r"\u2013"), "-"),
    (re.compile(r"\u2014"), "--"),
    (re.compile(r"\u2026"), "..."),
    (re.compile(r"\u00A0"), " "),
    (re.compile(r"[\u200B\u200C\u200D\uFEFF]"), ""),
]


def sanitize_text(s: Any) -> Any:
    if not isinstance(s, str):
        return s
    for pat, repl in _SANITIZE_MAP:
        s = pat.sub(repl, s)
    return s


def sanitize_obj(obj: Any) -> Any:
    if obj is None:
        return obj
    if isinstance(obj, str):
        return sanitize_text(obj)
    if isinstance(obj, (int, float)):
        return obj
    if isinstance(obj, list):
        return [sanitize_obj(item) for item in obj]
    if isinstance(obj, dict):
        return {k: sanitize_obj(v) for k, v in obj.items()}
    return obj


# ═══════════════════════════════════════════════════════════════
# 4. DATA TAG → HUMAN-READABLE NAME TRANSLATION
# ═══════════════════════════════════════════════════════════════
#
# Internal dict keys (e.g. "operating_margin", "net_debt_to_ebitda")
# get serialized as JSON and shown to LLM writers.  Without translation
# the writer references these tags verbatim in prose ("operating_margin
# of 32%") instead of proper names ("Operating Margin of 32%").
# This mapping converts dict keys to human-readable names before the
# context is passed to writers.

_TAG_TO_HUMAN: dict[str, str] = {
    # ── Income Statement ──
    "revenue": "Revenue",
    "revenue_usd_m": "Revenue ($M)",
    "revenue_usd_b": "Revenue ($B)",
    "cost_of_revenue_usd_m": "Cost of Revenue ($M)",
    "revenue_growth": "Revenue Growth",
    "revenue_growth_pct": "Revenue Growth (%)",
    "revenue_growth_yoy": "Revenue Growth YoY",
    "revenue_5yr_cagr": "Revenue 5-Year CAGR",
    "revenue_3yr_cagr": "Revenue 3-Year CAGR",
    "revenue_cagr_5yr_pct": "Revenue 5-Year CAGR (%)",
    "revenue_cagr_3yr_pct": "Revenue 3-Year CAGR (%)",
    "revenue_latest": "Revenue (Latest Year)",
    "revenue_growth_latest": "Revenue Growth (Latest Year)",
    "gross_profit": "Gross Profit",
    "gross_profit_usd_m": "Gross Profit ($M)",
    "gross_profit_usd_b": "Gross Profit ($B)",
    "operating_income": "Operating Income",
    "operating_income_usd_m": "Operating Income ($M)",
    "operating_income_usd_b": "Operating Income ($B)",
    "net_income": "Net Income",
    "net_income_usd_m": "Net Income ($M)",
    "net_income_usd_b": "Net Income ($B)",
    "ebitda": "EBITDA",
    "ebitda_usd_m": "EBITDA ($M)",
    "ebitda_usd_b": "EBITDA ($B)",
    "eps_diluted": "Diluted EPS",
    "eps_basic": "Basic EPS",
    "da_usd_m": "Depreciation & Amortization ($M)",
    "da_pct_of_revenue": "D&A as % of Revenue",
    "share_repurchases_usd_m": "Share Repurchases ($M)",
    "interest_expense_usd_m": "Interest Expense ($M)",
    "income_tax_expense_usd_m": "Income Tax Expense ($M)",
    "effective_tax_rate_pct": "Effective Tax Rate (%)",
    "rd_expense_usd_m": "R&D Expense ($M)",
    "rd_expense_usd_b": "R&D Expense ($B)",
    "sga_expense_usd_m": "SG&A Expense ($M)",
    "sga_expense_usd_b": "SG&A Expense ($B)",

    # ── Margins ──
    "gross_margin": "Gross Margin",
    "gross_margin_pct": "Gross Margin (%)",
    "operating_margin": "Operating Margin",
    "operating_margin_pct": "Operating Margin (%)",
    "ebitda_margin": "EBITDA Margin",
    "ebitda_margin_pct": "EBITDA Margin (%)",
    "net_margin": "Net Margin",
    "net_margin_pct": "Net Margin (%)",
    "fcf_margin": "Free Cash Flow Margin",
    "fcf_margin_pct": "Free Cash Flow Margin (%)",
    "ocf_margin_pct": "Operating Cash Flow Margin (%)",

    # ── Returns ──
    "roe_pct": "Return on Equity (%)",
    "roic_pct": "Return on Invested Capital (%)",
    "roa_pct": "Return on Assets (%)",
    "roce_pct": "Return on Capital Employed (%)",

    # ── Cash Flow ──
    "ocf": "Operating Cash Flow",
    "operating_cash_flow_usd_m": "Operating Cash Flow ($M)",
    "free_cash_flow_usd_m": "Free Cash Flow ($M)",
    "fcf": "Free Cash Flow",
    "capex_usd_m": "Capital Expenditure ($M)",
    "capex_pct_rev": "CapEx as % of Revenue",
    "capex_pct_of_revenue": "CapEx as % of Revenue (%)",
    "capex_to_revenue_pct": "CapEx as % of Revenue (%)",
    "fcf_conversion_pct": "FCF Conversion (%)",
    "buybacks_usd_m": "Share Buybacks ($M)",
    "dividends_paid_usd_m": "Dividends Paid ($M)",
    "acquisitions_net_usd_m": "Net Acquisitions ($M)",
    "rd_usd_m": "R&D Spending ($M)",

    # ── Balance Sheet ──
    "net_debt": "Net Debt",
    "net_debt_ebitda": "Net Debt / EBITDA",
    "net_debt_to_ebitda": "Net Debt / EBITDA",
    "net_debt_usd_m": "Net Debt ($M)",
    "total_debt": "Total Debt",
    "total_debt_usd_m": "Total Debt ($M)",
    "cash": "Cash & Equivalents",
    "total_assets_usd_m": "Total Assets ($M)",
    "total_equity_usd_m": "Total Equity ($M)",
    "total_current_assets_usd_m": "Total Current Assets ($M)",
    "total_current_liabilities_usd_m": "Total Current Liabilities ($M)",
    "total_liabilities_usd_m": "Total Liabilities ($M)",
    "pp_and_e_usd_m": "PP&E ($M)",
    "net_working_capital_usd_m": "Net Working Capital ($M)",
    "goodwill": "Goodwill",
    "goodwill_usd_m": "Goodwill ($M)",
    "goodwill_pct_assets": "Goodwill as % of Total Assets",
    "goodwill_pct_total_assets": "Goodwill as % of Total Assets (%)",
    "interest_coverage": "Interest Coverage Ratio",
    "interest_coverage_ratio": "Interest Coverage Ratio",
    "current_ratio": "Current Ratio",
    "debt_to_equity": "Debt-to-Equity Ratio",
    "debt_to_equity_ratio": "Debt-to-Equity Ratio",
    "book_value_per_share": "Book Value per Share",
    "tangible_bv_per_share": "Tangible Book Value per Share",

    # ── Share Data ──
    "sbc": "Stock-Based Compensation",
    "sbc_usd_m": "Stock-Based Compensation ($M)",
    "sbc_pct_of_revenue": "SBC as % of Revenue (%)",
    "sbc_pct_revenue": "SBC as % of Revenue (%)",
    "sbc_to_revenue_pct": "SBC as % of Revenue (%)",
    "shares_diluted_m": "Diluted Shares (M)",
    "shares_diluted_millions": "Diluted Shares (M)",
    "net_share_count_change_pct": "Net Share Count Change (%)",

    # ── Valuation Multiples ──
    "ev_to_ebitda": "EV / EBITDA",
    "ev_to_fcf": "EV / Free Cash Flow",
    "ev_to_ocf": "EV / Operating Cash Flow",
    "price_to_earnings": "P/E Ratio",
    "price_to_fcf": "Price / Free Cash Flow",
    "price_to_sales": "Price / Sales",
    "price_to_book": "Price / Book Value",
    "earnings_yield_pct": "Earnings Yield (%)",
    "fcf_yield_pct": "Free Cash Flow Yield (%)",
    "dividend_yield_pct": "Dividend Yield (%)",
    "dividend_payout_ratio_pct": "Dividend Payout Ratio (%)",

    # ── Working Capital ──
    "dso_days": "Days Sales Outstanding",
    "dpo_days": "Days Payable Outstanding",
    "dio_days": "Days Inventory Outstanding",
    "ccc": "Cash Conversion Cycle",
    "ccc_days": "Cash Conversion Cycle (Days)",
    "cash_conversion_cycle_days": "Cash Conversion Cycle (Days)",
    "inventory_turnover": "Inventory Turnover",

    # ── Growth Rates ──
    "gross_profit_growth_pct": "Gross Profit Growth (%)",
    "operating_income_growth_pct": "Operating Income Growth (%)",
    "net_income_growth_pct": "Net Income Growth (%)",
    "eps_diluted_growth_pct": "EPS Growth (%)",
    "fcf_growth_pct": "Free Cash Flow Growth (%)",
    "ebitda_growth_pct": "EBITDA Growth (%)",

    # ── Peer Data ──
    "rd_to_revenue_pct": "R&D as % of Revenue",
    "rd_pct_revenue": "R&D as % of Revenue",
    "rd_pct_of_revenue": "R&D as % of Revenue (%)",
    "rd_intensity_pct": "R&D Intensity (%)",
    "sga_to_revenue_pct": "SG&A as % of Revenue (%)",
    "income_quality": "Income Quality (OCF/Net Income)",

    # ── Banking KPIs ──
    "nim_pct": "Net Interest Margin (%)",
    "efficiency_ratio_pct": "Efficiency Ratio (%)",
    "fee_income_ratio_pct": "Fee Income Ratio (%)",
    "npl_ratio_pct": "Non-Performing Loan Ratio (%)",
    "nco_rate_pct": "Net Charge-Off Rate (%)",
    "reserve_coverage_pct": "Reserve Coverage (%)",
    "provision_to_loans_pct": "Provision to Loans (%)",
    "cet1_ratio_pct": "CET1 Capital Ratio (%)",
    "loan_to_deposit_pct": "Loan-to-Deposit Ratio (%)",
    "tbv_per_share": "Tangible Book Value per Share",
    "cost_of_deposits_pct": "Cost of Deposits (%)",

    # ── Insurance KPIs ──
    "combined_ratio_pct": "Combined Ratio (%)",
    "loss_ratio_pct": "Loss Ratio (%)",
    "expense_ratio_pct": "Expense Ratio (%)",

    # ── REIT KPIs ──
    "ffo_per_share": "FFO per Share",
    "affo_per_share": "AFFO per Share",
    "noi_margin_pct": "Net Operating Income Margin (%)",
    "debt_to_assets_pct": "Debt-to-Assets (%)",

    # ── Energy KPIs ──
    "production_mboed": "Production (MBoe/d)",
    "reserve_replacement_pct": "Reserve Replacement (%)",
    "finding_cost": "Finding Cost ($/Boe)",
    "lifting_cost": "Lifting Cost ($/Boe)",

    # ── Tech / SaaS KPIs ──
    "rule_of_40": "Rule of 40 Score",
    "nrr_proxy_pct": "Revenue Retention Proxy (%)",
    "sbc_pct_rev": "SBC as % of Revenue",

    # ── Industrials KPIs ──
    "book_to_bill": "Book-to-Bill Ratio",
    "backlog_to_revenue": "Backlog-to-Revenue Ratio",

    # ── Utilities KPIs ──
    "debt_to_ebitda": "Debt / EBITDA",

    # ── Container / structural keys ──
    "financials_5yr": "5-Year Financials",
    "capital_allocation_3yr": "3-Year Capital Allocation",
    "subject_metrics": "Company Metrics",
    "peer_medians": "Peer Medians",
    "sector_kpis": "Sector KPIs",
    "sector_analysis_guidance": "Sector Analysis Guidance",
    "s10_financial_flags": "Financial Quality Flags",
    "peer_emphasis": "Peer Comparison Emphasis",
    "peer_count": "Number of Peers",

    # ── Other commonly seen keys ──
    "market_cap_usd_b": "Market Cap ($B)",
    "enterprise_value_usd_b": "Enterprise Value ($B)",
    "ev_to_sales": "EV / Sales",
    "revenue_avg_usd_m": "Revenue Estimate ($M)",
    "revenue_low_usd_m": "Revenue Estimate Low ($M)",
    "revenue_high_usd_m": "Revenue Estimate High ($M)",
    "ebitda_avg_usd_m": "EBITDA Estimate ($M)",
    "eps_avg": "EPS Estimate",
    "eps_low": "EPS Estimate Low",
    "eps_high": "EPS Estimate High",
    "num_analysts": "Number of Analysts",
    "segment_concentration_pct": "Segment Concentration (%)",

    # ── Cash Flow (additional) ──
    "ocf_growth_pct": "Operating Cash Flow Growth (%)",
    "ocf_usd_m": "Operating Cash Flow ($M)",
    "fcf_usd_m": "Free Cash Flow ($M)",
    "change_in_working_capital_usd_m": "Change in Working Capital ($M)",
    "acquisitions_usd_m": "Acquisitions ($M)",
    "quarterly_cash_flow": "Quarterly Cash Flow",
    "quarterly_income": "Quarterly Income Statement",

    # ── Balance Sheet (additional) ──
    "cash_and_equivalents_usd_m": "Cash & Equivalents ($M)",
    "intangible_assets_usd_m": "Intangible Assets ($M)",
    "retained_earnings_usd_m": "Retained Earnings ($M)",
    "goodwill_and_intangibles_usd_m": "Goodwill & Intangibles ($M)",
    "short_term_debt_usd_m": "Short-Term Debt ($M)",
    "long_term_debt_usd_m": "Long-Term Debt ($M)",

    # ── Capital Allocation (short-form keys from 3yr summaries) ──
    "rd": "R&D",
    "capex": "Capital Expenditure",
    "acquisitions": "Acquisitions",
    "acquisitions_net": "Net Acquisitions",
    "dividends": "Dividends",
    "dividends_paid": "Dividends Paid",
    "buybacks": "Share Buybacks",
    "dividend_per_share": "Dividend per Share",
    "owner_earnings": "Owner Earnings",
    "owners_earnings_usd_m": "Owner's Earnings ($M)",
    "owners_earnings_per_share": "Owner's Earnings per Share",
    "maintenance_capex_usd_m": "Maintenance CapEx ($M)",
    "growth_capex_usd_m": "Growth CapEx ($M)",

    # ── Segment / Geographic ──
    "segment": "Segment",
    "segment_revenue_usd_m": "Segment Revenue ($M)",
    "segment_revenue_pct_of_total": "Segment Revenue (% of Total)",
    "segment_yoy_growth_pct": "Segment YoY Growth (%)",
    "segment_revenue_usd_b": "Segment Revenue ($B)",
    "segment_pct": "Segment (%)",
    "segment_growth": "Segment Growth",
    "segment_summary": "Segment Summary",
    "segment_concentration": "Segment Concentration",
    "geographic_revenue_usd_m": "Geographic Revenue ($M)",
    "geographic_revenue_pct_of_total": "Geographic Revenue (% of Total)",
    "geographic_yoy_growth_pct": "Geographic YoY Growth (%)",
    "geographic_revenue_usd_b": "Geographic Revenue ($B)",
    "geographic_revenue_pct": "Geographic Revenue (%)",
    "geographic_concentration": "Geographic Concentration",
    "geographic_revenue": "Geographic Revenue",
    "consolidated_revenue": "Consolidated Revenue",

    # ── Working Capital (additional) ──
    "dso": "Days Sales Outstanding",
    "dpo": "Days Payable Outstanding",
    "dio": "Days Inventory Outstanding",
    "deferred_revenue": "Deferred Revenue",
    "deferred_revenue_usd_m": "Deferred Revenue ($M)",
    "deferred_revenue_noncurrent_usd_m": "Non-Current Deferred Revenue ($M)",
    "accounts_receivable_usd_m": "Accounts Receivable ($M)",
    "ar_vs_revenue_growth": "AR vs. Revenue Growth",
    "ar_growth_pct": "Accounts Receivable Growth (%)",
    "divergence_pct": "AR-Revenue Divergence (%)",

    # ── Beat/Miss ──
    "guidance_beat_miss": "Guidance Beat/Miss",
    "guidance_quarters": "Guidance Quarters",
    "total_quarters_analyzed": "Total Quarters Analyzed",
    "eps_beat_count": "EPS Beat Count",
    "eps_beat_pct": "EPS Beat Rate (%)",
    "revenue_beat_count": "Revenue Beat Count",
    "revenue_beat_pct": "Revenue Beat Rate (%)",
    "avg_eps_surprise_pct": "Average EPS Surprise (%)",
    "avg_revenue_surprise_pct": "Average Revenue Surprise (%)",
    "eps_actual": "EPS Actual",
    "eps_estimated": "EPS Estimated",
    "eps_beat": "EPS Beat",
    "eps_surprise": "EPS Surprise",
    "eps_surprise_pct": "EPS Surprise (%)",
    "revenue_actual_usd_m": "Revenue Actual ($M)",
    "revenue_estimated_usd_m": "Revenue Estimated ($M)",
    "revenue_beat": "Revenue Beat",
    "revenue_surprise_usd_m": "Revenue Surprise ($M)",
    "revenue_surprise_pct": "Revenue Surprise (%)",

    # ── Valuation (additional) ──
    "price_to_tangible_book": "Price / Tangible Book Value",
    "market_cap": "Market Capitalization",
    "enterprise_value": "Enterprise Value",
    "current_price": "Current Price",
    "price_52wk_high": "52-Week High",
    "price_52wk_low": "52-Week Low",
    "forward_estimates": "Forward Estimates",
    "peer_valuation_medians": "Peer Valuation Medians",
    "subject_multiples": "Company Multiples",
    "shares_diluted": "Diluted Shares",
    "shares_diluted_latest_q_m": "Diluted Shares Latest Quarter (M)",
    "shares_diluted_latest_q_millions": "Diluted Shares Latest Quarter (M)",

    # ── Peer Benchmarking (structural) ──
    "profitability_comps": "Profitability Comparisons",
    "growth_comps": "Growth Comparisons",
    "valuation_comps": "Valuation Comparisons",
    "leverage_comps": "Leverage Comparisons",
    "efficiency_comps": "Efficiency Comparisons",
    "geographic_comps": "Geographic Comparisons",
    "competitive_landscape": "Competitive Landscape",
    "fiscal_periods": "Fiscal Periods",
    "peers_full": "Full Peer Data",
    "peer_latest": "Peer Latest Data",
    "company": "Company",
    "company_name": "Company Name",
    "us_pct": "US Revenue (%)",
    "international_pct": "International Revenue (%)",
    "da_usd_b": "Depreciation & Amortization ($B)",
    "interest_expense_usd_b": "Interest Expense ($B)",

    # ── Section context keys ──
    "revenue_m": "Revenue ($M)",
    "revenue_b": "Revenue ($B)",
    "pct_of_total": "% of Total",
    "yoy_growth": "YoY Growth",
    "rd_expense": "R&D Expense",
    "rd_growth": "R&D Growth",
    "revenue_5yr": "Revenue (5-Year)",
    "gross_margin_5yr": "Gross Margin (5-Year)",
    "operating_margin_5yr": "Operating Margin (5-Year)",
    "fcf_margin_5yr": "Free Cash Flow Margin (5-Year)",
    "roic_5yr": "ROIC (5-Year)",
    "peer_revenue_growth_median": "Peer Revenue Growth Median",
    "peer_rd_median_pct": "Peer R&D Median (%)",
    "peer_gross_margin_median": "Peer Gross Margin Median",
    "peer_operating_margin_median": "Peer Operating Margin Median",
    "peer_dso_median": "Peer DSO Median",
    "market_cap_latest": "Market Capitalization (Latest)",
    "seg_data_quality": "Segment Data Quality",
    "precomputed_geo_rows": "Geographic Breakdown",
    "geo_data_quality": "Geographic Data Quality",
    "working_capital": "Working Capital",
    "sector_risk_guidance": "Sector Risk Guidance",

    # ── Identity / Metadata ──
    "ticker": "Ticker",
    "exchange": "Exchange",
    "sector": "Sector",
    "industry": "Industry",
    "country": "Country",
    "headquarters": "Headquarters",
    "ceo": "CEO",
    "employee_count": "Employee Count",
    "ipo_date": "IPO Date",
    "website": "Website",
    "description": "Description",
    "fiscal_year_end": "Fiscal Year End",
    "founding_year": "Founding Year",
    "fiscal_year": "Fiscal Year",
    "reported_currency": "Reported Currency",
    "period": "Period",
    "date": "Date",
    "flag": "Quality Flag",
    "latest_quarter_period": "Latest Quarter Period",

    # ── Valuation model keys ──
    "valuation_model": "Valuation Model",
    "valuation_model_note": "Valuation Model Note",
    "alt_valuation_method": "Alternative Valuation Method",
    "alt_valuation_rationale": "Alternative Valuation Rationale",
    "pe_implied_fair_value": "P/E Implied Fair Value",
    "pe_implied_method_note": "P/E Implied Method Note",
    "alt_implied_fair_value": "Alternative Implied Fair Value",
    "alt_implied_method_note": "Alternative Implied Method Note",
    "alt_implied_method_used": "Alternative Implied Method Used",
    "dividend_per_share_history": "Dividend per Share History",
    "revenue_5yr_cagr_pct": "Revenue 5-Year CAGR (%)",
    "revenue_3yr_cagr_pct": "Revenue 3-Year CAGR (%)",
    "net_capex_pct_of_revenue": "Net CapEx as % of Revenue",
    "terminal_tax_rate_pct": "Terminal Tax Rate (%)",
    "avg_ppe_ratio": "Average PP&E Ratio",

    # ── Precomputed peer table shorthands ──
    "p_e": "P/E",
    "p_b": "P/B",
    "p_tbv": "P/TBV",
    "p_fcf": "P/FCF",
    "div_yield": "Dividend Yield",
    "ev_revenue": "EV/Revenue",
    "ev_ebitda": "EV/EBITDA",
    "is_financial": "Financial Sector",
    "subject_company": "Subject Company",
    "peer_median": "Peer Median",

    # ── Qualitative context keys ──
    "one_sentence_description": "One-Sentence Description",
    "revenue_model_type": "Revenue Model Type",
    "recurring_vs_nonrecurring": "Recurring vs. Non-Recurring",
    "core_value_proposition": "Core Value Proposition",
    "pricing_structure": "Pricing Structure",
    "primary_financing_method": "Primary Financing Method",
    "life_cycle_classification": "Life Cycle Classification",
    "life_cycle_evidence": "Life Cycle Evidence",
    "competitive_advantages": "Competitive Advantages",
    "segments_qualitative": "Segments (Qualitative)",
    "founding": "Founding",
    "milestones": "Milestones",
    "strategic_evolution": "Strategic Evolution",
    "recent_developments": "Recent Developments",
    "product_portfolio": "Product Portfolio",
    "rd_qualitative": "R&D (Qualitative)",
    "technology_initiatives": "Technology Initiatives",
    "patents": "Patents",
    "technology_partnerships": "Technology Partnerships",
    "product_pipeline": "Product Pipeline",
    "competitive_tech_position": "Competitive Technology Position",
    "brand_intangibles": "Brand & Intangibles",
    "switching_costs": "Switching Costs",
    "network_effects": "Network Effects",
    "cost_advantages": "Cost Advantages",
    "efficient_scale": "Efficient Scale",
    "moat_summary": "Moat Summary",
    "industry_overview": "Industry Overview",
    "addressable_market": "Addressable Market",
    "market_structure": "Market Structure",
    "competitors": "Competitors",
    "subject_positioning": "Subject Positioning",
    "competitive_intensity": "Competitive Intensity",
    "five_forces": "Five Forces Analysis",
    "composition": "Customer Composition",
    "value_proposition": "Value Proposition",
    "acquisition_retention": "Acquisition & Retention",
    "unit_economics": "Unit Economics",
    "working_capital_qualitative": "Working Capital (Qualitative)",
    "leadership_team": "Leadership Team",
    "founder_context": "Founder Context",
    "management_quality": "Management Quality",
    "capital_allocation_qualitative": "Capital Allocation (Qualitative)",
    "revenue_drivers": "Revenue Drivers",
    "near_term_catalysts": "Near-Term Catalysts",
    "medium_term_drivers": "Medium-Term Drivers",
    "medium_term_driver_topics": "Medium-Term Driver Topics",
    "long_term_position": "Long-Term Position",
    "margin_cashflow_evolution": "Margin & Cash Flow Evolution",
    "margin_evolution": "Margin Evolution",
    "tam_usd_b": "Total Addressable Market ($B)",
    "tam_cagr_pct": "TAM CAGR (%)",
    "current_penetration_pct": "Current Penetration (%)",
    "regulatory_framework": "Regulatory Framework",
    "top_risks": "Top Risks",
    "litigation": "Litigation",
    "bear_case_triggers": "Bear Case Triggers",
    "key_assumption_sensitivities": "Key Assumption Sensitivities",
    "bear_case_conclusion": "Bear Case Conclusion",
    "base_case_condition": "Base Case Condition",
    "tailwinds": "Industry Tailwinds",
    "headwinds": "Industry Headwinds",

    # ── Sector KPIs (camelCase from SEC modules) ──
    # Banking
    "efficiencyRatio": "Efficiency Ratio",
    "netInterestMargin": "Net Interest Margin",
    "loanToDepositRatio": "Loan-to-Deposit Ratio",
    "nplRatio": "Non-Performing Loan Ratio",
    "netChargeOffRate": "Net Charge-Off Rate",
    "reserveCoverage": "Reserve Coverage",
    "allowanceToLoans": "Allowance-to-Loans Ratio",
    "provisionToLoans": "Provision-to-Loans Ratio",
    "tangibleBookValuePerShare": "Tangible Book Value per Share",
    "feeIncomeRatio": "Fee Income Ratio",
    "compensationRatio": "Compensation Ratio",
    "nonInterestDepositRatio": "Non-Interest Deposit Ratio",
    "costOfDeposits": "Cost of Deposits",
    "yieldOnEarningAssets": "Yield on Earning Assets",
    "netInterestSpread": "Net Interest Spread",
    # Insurance
    "lossRatio": "Loss Ratio",
    "expenseRatio": "Expense Ratio",
    "combinedRatio": "Combined Ratio",
    "investmentYield": "Investment Yield",
    "premiumsEarnedGrowth": "Premiums Earned Growth",
    "premiumsWrittenGrowth": "Premiums Written Growth",
    "premiumsToEquity": "Premiums-to-Equity",
    "reserveToPremium": "Reserve-to-Premium",
    "floatLeverage": "Float Leverage",
    # REITs
    "ffo": "Funds from Operations",
    "affo": "Adjusted FFO",
    "ffoPerShare": "FFO per Share",
    "affoPerShare": "AFFO per Share",
    "noi": "Net Operating Income",
    "noiMargin": "NOI Margin",
    "noiGrowth": "NOI Growth",
    "ffoPayoutRatio": "FFO Payout Ratio",
    "affoPayoutRatio": "AFFO Payout Ratio",
    "debtToEbitda": "Debt / EBITDA",
    "debtToAssets": "Debt / Assets",
    "interestCoverage": "Interest Coverage",
    "capRateProxy": "Cap Rate Proxy",
    # Energy
    "ddaToRevenue": "DD&A as % of Revenue",
    "explorationPctOfRevenue": "Exploration as % of Revenue",
    "productionCostPctOfRevenue": "Production Cost as % of Revenue",
    "reinvestmentRate": "Reinvestment Rate",
    "shareholderReturnRatio": "Shareholder Return Ratio",
    "capitalEfficiency": "Capital Efficiency",
    "fcfPerShare": "Free Cash Flow per Share",
    # Tech / SaaS
    "deferredRevenueGrowth": "Deferred Revenue Growth",
    "rpoGrowth": "RPO Growth",
    "billingsProxy": "Billings Proxy",
    "totalDeferredRevenue": "Total Deferred Revenue",
    "magicNumber": "Magic Number",
    "rdIntensity": "R&D Intensity",
    "sbcAsPercentOfRevenue": "SBC as % of Revenue",
    "sgaAsPercentOfRevenue": "SG&A as % of Revenue",
    "sbcDilution": "SBC Dilution",
    "fcfAdjForSbcMargin": "FCF Adj. for SBC Margin",
    # Industrials
    "backlogToRevenue": "Backlog-to-Revenue",
    "backlogGrowth": "Backlog Growth",
    "bookToBill": "Book-to-Bill",
    "capexIntensity": "CapEx Intensity",
    "assetTurnover": "Asset Turnover",
    "ocfToNetIncome": "OCF / Net Income",
    "payoutRatio": "Payout Ratio",
    "goodwillIntensity": "Goodwill Intensity",
    # Healthcare
    "rdToGrossProfit": "R&D / Gross Profit",
    "intangibleAssetIntensity": "Intangible Asset Intensity",
    "netDebtToEbitda": "Net Debt / EBITDA",
    # Retail
    "inventoryTurnover": "Inventory Turnover",
    "daysInventory": "Days Inventory",
    "revenuePerStore": "Revenue per Store",
    "storeGrowth": "Store Growth",
    "sameStoreSalesProxy": "Same-Store Sales Proxy",
    "cashConversionCycle": "Cash Conversion Cycle",
    # Shared across sectors (camelCase)
    "grossMargin": "Gross Margin",
    "operatingMargin": "Operating Margin",
    "ebitdaMargin": "EBITDA Margin",
    "netMargin": "Net Margin",
    "fcfMargin": "Free Cash Flow Margin",
    "revenueGrowth": "Revenue Growth",
    "capexToRevenue": "CapEx as % of Revenue",
    "debtToEquity": "Debt / Equity",
    "roce": "Return on Capital Employed",
    "roa": "Return on Assets",
    "roe": "Return on Equity",
    "roic": "Return on Invested Capital",
}


# ═══════════════════════════════════════════════════════════════
# HELPERS (shared across section builders)
# ═══════════════════════════════════════════════════════════════

def _safe_num(val: Any, fallback: Any = None) -> Any:
    """NM-safe numeric guard -- prevents 'NM' strings from entering DCF anchors."""
    if val == "NM" or val is None:
        return fallback
    if isinstance(val, (int, float)):
        return val if not math.isnan(val) else fallback
    try:
        n = float(val)
        return fallback if math.isnan(n) else n
    except (ValueError, TypeError):
        return fallback


def _safe_get(d: dict | None, *keys: str, default: Any = None) -> Any:
    """Nested safe dict access: _safe_get(d, 'a', 'b') -> d['a']['b']."""
    current = d
    for k in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(k)
    return current if current is not None else default


def _yr5f(obj: dict | None, parent_key: str, annual_years: list[str]) -> list[dict] | None:
    if not obj:
        return None
    unit = detect_unit(parent_key)
    return [
        {"year": yr, "value": fmt_num(obj[yr], unit) if obj.get(yr) is not None else None}
        for yr in annual_years
    ]


def _get_cite(source_str: str | None, url_to_id: dict, fc: str) -> str:
    if not source_str:
        return fc
    urls = re.findall(r"https?://[^\s,;\"')\]]+", source_str)
    if not urls:
        return fc
    tags: set[str] = set()
    for raw_url in urls:
        clean = re.sub(r"\?utm_source=\w+$", "", raw_url).rstrip(". ")
        for reg_url, cite_id in url_to_id.items():
            if clean in reg_url or reg_url in clean:
                tags.add(cite_id)
                break
    return "".join(sorted(tags)) if tags else fc


def _cite(obj: Any, url_to_id: dict, fc: str) -> Any:
    if obj is None:
        return obj
    if isinstance(obj, list):
        return [_cite(item, url_to_id, fc) for item in obj]
    if isinstance(obj, dict):
        result = dict(obj)
        if obj.get("source"):
            result["_cite"] = _get_cite(obj["source"], url_to_id, fc)
        if obj.get("segment_source"):
            result["_cite"] = _get_cite(obj["segment_source"], url_to_id, fc)
        return result
    return obj


def _compute_median(arr: list) -> float | None:
    valid = sorted(v for v in arr if v is not None and not (isinstance(v, float) and math.isnan(v)))
    if not valid:
        return None
    mid = len(valid) // 2
    return valid[mid] if len(valid) % 2 else (valid[mid - 1] + valid[mid]) / 2


# ═══════════════════════════════════════════════════════════════
# 4. TABLE BUILDERS
# ═══════════════════════════════════════════════════════════════

def build_segment_rows(raw_rev_splits: dict, latest_year: str,
                       consolidated_revenue: float,
                       country: str = "") -> tuple[list[dict], str]:
    """Build segment rows.  Returns (rows, data_quality)."""
    seg_rev = _safe_get(raw_rev_splits, "segment_revenue_usd_m", latest_year, default={})
    seg_yoy = _safe_get(raw_rev_splits, "segment_yoy_growth_pct", latest_year, default={})
    tagged_total = sum(v or 0 for v in seg_rev.values())

    rows = [
        {
            "segment": name,
            "revenue_m": round(seg_rev[name]),
            "pct_of_total": None,
            "yoy_growth": round(seg_yoy.get(name, 0) * 10) / 10 if seg_yoy.get(name) is not None else None,
        }
        for name in seg_rev
        if seg_rev[name] is not None and seg_rev[name] > 0
    ]

    data_quality = "complete"

    if consolidated_revenue > 0 and tagged_total > 0:
        coverage = tagged_total / consolidated_revenue
        if coverage > 1.10:
            # Segments use a different revenue definition than consolidated.
            # Data is unusable — don't show it.
            data_quality = "unusable"
            rows = []
        elif coverage < 0.10:
            data_quality = "unusable"
            rows = []
        elif coverage < 0.70:
            data_quality = "residual_inferred"
            rows.append({
                "segment": "Other / Unclassified (Inferred)",
                "revenue_m": round(consolidated_revenue - tagged_total),
                "pct_of_total": None,
                "yoy_growth": None,
            })

    base = max(consolidated_revenue, tagged_total) or tagged_total
    for row in rows:
        row["pct_of_total"] = round((row["revenue_m"] / base) * 1000) / 10 if base > 0 else None
    rows.sort(key=lambda r: r.get("revenue_m") or 0, reverse=True)
    return rows, data_quality


def _build_table_rows(series: list[dict], annual_years: list[str]) -> list[dict]:
    """Generic 5-year time-series table builder.

    *series* is a list of ``{"key": str, "data": dict_or_None}`` where
    data is a year-keyed dict.
    """
    rows = []
    for yr in annual_years:
        row: dict[str, Any] = {"year": yr}
        for s in series:
            d = s.get("data")
            row[s["key"]] = d.get(yr) if isinstance(d, dict) else None
        rows.append(row)
    return rows


def build_financial_table_rows(raw_data: dict, annual_years: list[str]) -> dict:
    """Build all S10 financial table row sets from raw numeric data."""
    ri = raw_data.get("incStmt", {})
    rm = raw_data.get("margins", {})
    rc = raw_data.get("cfStmt", {})
    rb = raw_data.get("balSheet", {})
    rr = raw_data.get("returns", {})
    rw = raw_data.get("wc", {})
    rca = raw_data.get("capAlloc", {})
    rrd = raw_data.get("rd", {})
    rsd = raw_data.get("shareD", {})
    rcf = raw_data.get("cfStmt", {})

    revenue_growth = _build_table_rows([
        {"key": "revenue_m", "data": ri.get("revenue_usd_m", {})},
        {"key": "yoy_growth", "data": ri.get("revenue_growth_pct", {})},
    ], annual_years)

    margins = _build_table_rows([
        {"key": "gross_margin", "data": rm.get("gross_margin_pct", {})},
        {"key": "operating_margin", "data": rm.get("operating_margin_pct", {})},
        {"key": "net_margin", "data": rm.get("net_margin_pct", {})},
        {"key": "eps_diluted", "data": ri.get("eps_diluted", {})},
    ], annual_years)

    cash_flow = _build_table_rows([
        {"key": "ocf_m", "data": rc.get("operating_cash_flow_usd_m", {})},
        {"key": "capex_pct_rev", "data": rc.get("capex_pct_of_revenue", {})},
        {"key": "fcf_m", "data": rc.get("free_cash_flow_usd_m", {})},
        {"key": "fcf_margin", "data": rc.get("fcf_margin_pct", {})},
    ], annual_years)

    returns = _build_table_rows([
        {"key": "roe", "data": rr.get("roe_pct", {})},
        {"key": "roic", "data": rr.get("roic_pct", {})},
        {"key": "operating_margin", "data": rm.get("operating_margin_pct", {})},
    ], annual_years)

    leverage = _build_table_rows([
        {"key": "net_debt_ebitda", "data": rb.get("net_debt_to_ebitda", {})},
        {"key": "interest_coverage", "data": rb.get("interest_coverage_ratio", {})},
        {"key": "dso_days", "data": rw.get("dso_days", {})},
    ], annual_years)

    capital_alloc = _build_table_rows([
        {"key": "rd_m", "data": rca.get("rd_usd_m") or (rrd.get("rd_expense_usd_m", {}) if rrd else {})},
        {"key": "capex_m", "data": rca.get("capex_usd_m") or rcf.get("capex_usd_m", {})},
        {"key": "ma_m", "data": rca.get("acquisitions_net_usd_m", {})},
        {"key": "dividends_m", "data": rca.get("dividends_paid_usd_m") or rsd.get("dividends_paid_usd_m", {})},
        {"key": "buybacks_m", "data": rca.get("buybacks_usd_m") or rsd.get("share_repurchases_usd_m", {})},
    ], annual_years)

    return {
        "revenue_growth": revenue_growth,
        "margins": margins,
        "cash_flow": cash_flow,
        "returns": returns,
        "leverage": leverage,
        "capital_allocation": capital_alloc,
    }


def build_peer_comp_tables(peer_benchmarking: dict) -> dict:
    """Extract and NM-sanitize all peer comparison tables."""
    tables = {}
    for key in ("profitability_comps", "growth_comps", "valuation_comps",
                "valuation_comps_financial", "valuation_comps_reit",
                "leverage_comps", "efficiency_comps", "geographic_comps"):
        t = peer_benchmarking.get(key, [])
        if isinstance(t, list) and len(t) > 0 and key != "geographic_comps":
            nm_sanitize_peer_table(t)
        tables[key] = t
    return tables


# ═══════════════════════════════════════════════════════════════
# 5. OPENAI STRUCTURED OUTPUT CONVERTER
# ═══════════════════════════════════════════════════════════════

_SKIP_KEYS = frozenset(["minItems", "maxItems", "minimum", "maximum",
                         "exclusiveMinimum", "exclusiveMaximum"])


def to_openai(s: Any) -> Any:
    """Convert a JSON Schema dict to OpenAI structured-output format.

    Adds ``required`` and ``additionalProperties: false`` to every object,
    strips unsupported numeric constraints, and normalises nullable types
    to ``anyOf`` unions.
    """
    if not s or not isinstance(s, dict):
        return s
    if isinstance(s, list):
        return [to_openai(item) for item in s]
    r: dict[str, Any] = {}
    for k, v in s.items():
        if k in _SKIP_KEYS:
            continue
        if k == "enum" and s.get("type") == "integer":
            r["description"] = f"Must be: {', '.join(str(x) for x in v)}"
            continue
        if k in ("required", "additionalProperties"):
            continue
        if k == "type" and isinstance(v, list):
            if len(v) == 2 and "null" in v:
                real_type = next(t for t in v if t != "null")
                if real_type == "object" and "properties" in s:
                    inner = to_openai({**s, "type": "object"})
                    r["anyOf"] = [inner, {"type": "null"}]
                    return r
                if real_type == "array" and "items" in s:
                    inner = to_openai({**s, "type": "array"})
                    r["anyOf"] = [inner, {"type": "null"}]
                    return r
                r["anyOf"] = [{"type": real_type}, {"type": "null"}]
                if s.get("description"):
                    r["anyOf"][0]["description"] = s["description"]
                return r
            r[k] = v
            continue
        if k == "properties" and isinstance(v, dict):
            r["properties"] = {pn: to_openai(ps) for pn, ps in v.items()}
            r["required"] = list(v.keys())
            r["additionalProperties"] = False
        elif k == "items":
            r["items"] = to_openai(v)
        elif isinstance(v, dict) and not isinstance(v, list):
            r[k] = to_openai(v)
        else:
            r[k] = v
    if r.get("type") == "object" and r.get("properties"):
        if "required" not in r:
            r["required"] = list(r["properties"].keys())
        r["additionalProperties"] = False
    return r


# ── Analytical stance block (appended to every body-section schema) ──
# Forces each section to commit to a debate, a steelman, and a falsifier.
# Schema-level enforcement: unlike prompt instructions, required fields
# cannot be skipped by the writer.

_ANALYTICAL_STANCE_PROP = {
    "analytical_stance": {
        "type": "object",
        "properties": {
            "key_debate": {
                "type": "string",
                "description": (
                    "2-4 sentences. The genuine point of disagreement between bulls "
                    "and bears on THIS section's topic — what informed investors "
                    "actually argue about, grounded in the data provided. Not a "
                    "generic risk statement."
                ),
            },
            "strongest_counterargument": {
                "type": "string",
                "description": (
                    "2-4 sentences. The single strongest evidence-based argument "
                    "AGAINST this section's assessment, stated the way a skeptic "
                    "would state it, citing specific figures from the data."
                ),
            },
            "what_would_change_this_view": {
                "type": "string",
                "description": (
                    "1-3 sentences. The specific observable evidence — a metric "
                    "crossing a threshold, an event, a disclosure — that would "
                    "force a revision of this section's assessment."
                ),
            },
        },
        "required": ["key_debate", "strongest_counterargument", "what_would_change_this_view"],
        "additionalProperties": False,
    },
}


def _with_stance(schema: dict) -> dict:
    """Return a copy of *schema* with the analytical_stance block appended."""
    s = deepcopy(schema)
    props = s.get("properties")
    if not isinstance(props, dict) or "analytical_stance" in props:
        return s
    props.update(deepcopy(_ANALYTICAL_STANCE_PROP))
    if isinstance(s.get("required"), list):
        s["required"] = s["required"] + ["analytical_stance"]
    return s


# ═══════════════════════════════════════════════════════════════
# 6. SECTION SCHEMAS
# ═══════════════════════════════════════════════════════════════

_LABELED_BLOCK = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "description": "Short topic name, 2-6 words. Formatter will bold this."},
        "paragraph": {"type": "string", "description": "4-6 detailed analytical sentences about this topic. Substantive analysis with specific data points and comparisons."},
    },
    "required": ["label", "paragraph"],
}


def _table_wrapper(intro_desc: str | None = None, analysis_desc: str | None = None) -> dict:
    return {
        "type": "object",
        "properties": {
            "intro": {"type": "string", "description": intro_desc or "3-5 sentences introducing the data series with context and key highlights."},
            "analysis": {"type": "string", "description": analysis_desc or "3-5 sentences interpreting the table data with peer comparisons and implications."},
        },
        "required": ["intro", "analysis"],
    }


SECTION_SCHEMAS: dict[int, dict] = {}

# --- S2: Company Overview ---
SECTION_SCHEMAS[2] = {
    "type": "object",
    "properties": {
        "section_number": {"type": "integer", "enum": [2]},
        "section_thesis": {"type": "string", "description": "1-2 sentences. The single argument this section makes about the company. HIDDEN."},
        "opening_paragraph": {"type": "string", "description": "5-8 sentences. What the company does, scale, life-cycle stage, capital structure summary. Include specific revenue figures, employee count, market position."},
        "core_products_services": {
            "type": "object",
            "properties": {
                "intro": {"type": "string", "description": "4-6 sentences introducing the segment structure, revenue model, and relative contribution of each segment."},
                "segments": {"type": "array", "items": _LABELED_BLOCK, "description": "One labeled block per major segment."},
            },
            "required": ["intro", "segments"],
        },
        "industry_ecosystem": {
            "type": "object",
            "properties": {
                "value_chain": {"type": "string", "description": "4-6 sentences. Where the company sits in the value chain, upstream/downstream dependencies, and integration level."},
                "defensibility": {"type": "string", "description": "4-6 sentences. Structural reason the position is defensible, with specific evidence and competitive comparisons."},
            },
            "required": ["value_chain", "defensibility"],
        },
    },
    "required": ["section_number", "section_thesis", "opening_paragraph", "core_products_services", "industry_ecosystem"],
    "additionalProperties": False,
}

# --- S3: Company History ---
SECTION_SCHEMAS[3] = {
    "type": "object",
    "properties": {
        "section_number": {"type": "integer", "enum": [3]},
        "section_thesis": {"type": "string", "description": "1-2 sentences. The defining narrative arc. HIDDEN."},
        "opening_paragraph": {"type": "string", "description": "4-6 sentences. The defining arc, origin story, and how it shaped today's company."},
        "early_history": {"type": ["string", "null"], "description": "6-10 sentences (at least 100 words). Founding basics, early business model, and initial competitive positioning. Null if company is young."},
        "phase_blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "Phase title with years, e.g. '2010-2015: The Pivot to Cloud'"},
                    "paragraph": {"type": "string", "description": "5-8 sentences per phase. Include specific events, financial milestones, strategic decisions, and their impact."},
                },
                "required": ["label", "paragraph"],
            },
            "description": "4-6 phase blocks covering the company's major strategic eras.",
        },
    },
    "required": ["section_number", "section_thesis", "opening_paragraph", "phase_blocks"],
    "additionalProperties": False,
}

# --- S4: Product & Technology (sector-aware) ---
# Common S4 prefix shared by all variants
_S4_COMMON_PREFIX = {
    "section_number": {"type": "integer", "enum": [4]},
    "section_thesis": {"type": "string", "description": "1-2 sentences. The single argument about the company's products/services and their strategic positioning. HIDDEN."},
    "opening_paragraph": {"type": "string", "description": "5-8 sentences. Overview of the company's product/service lineup, market positioning, and strategic direction."},
    "revenue_by_segment": {
        "type": ["object", "null"],
        "properties": {
            "intro": {"type": "string"},
            "analysis": {"type": "string"},
        },
        "description": "CONDITIONAL: Include ONLY if segment data is available. Table injected by formatter.",
    },
}

_S4_DEFAULT = {
    "type": "object",
    "properties": {
        **_S4_COMMON_PREFIX,
        "product_portfolio": {
            "type": "object",
            "properties": {
                "intro": {"type": "string"},
                "products": {"type": "array", "items": _LABELED_BLOCK},
            },
            "required": ["intro", "products"],
        },
        "rd_and_technology": {"type": "string", "description": "4-6 sentences. R&D spending, efficiency, pipeline, and strategic priorities."},
        "technology_initiatives": {"type": ["array", "null"], "items": _LABELED_BLOCK},
        "competitive_tech_position": {"type": "string", "description": "4-6 sentences. Technology differentiation vs. competitors."},
    },
    "required": ["section_number", "section_thesis", "opening_paragraph",
                  "product_portfolio", "rd_and_technology", "competitive_tech_position"],
    "additionalProperties": False,
}

_S4_BANKING = {
    "type": "object",
    "properties": {
        **_S4_COMMON_PREFIX,
        "lending_portfolio": {"type": "string", "description": "5-8 sentences. Loan book composition by type (commercial, CRE, consumer, mortgage), credit quality metrics, underwriting standards, and growth strategy."},
        "deposit_and_funding": {"type": "string", "description": "5-8 sentences. Deposit mix (demand, savings, time), cost of deposits, core deposit ratio, funding strategy, and liquidity management."},
        "fee_based_services": {"type": "string", "description": "5-8 sentences. Wealth management, investment banking, trading, card services, treasury services — contribution and growth trajectory."},
        "digital_and_technology": {"type": "string", "description": "4-6 sentences. Digital banking adoption, mobile/online penetration, technology investments, and fintech competitive response."},
    },
    "required": ["section_number", "section_thesis", "opening_paragraph",
                  "lending_portfolio", "deposit_and_funding", "fee_based_services", "digital_and_technology"],
    "additionalProperties": False,
}

_S4_INSURANCE = {
    "type": "object",
    "properties": {
        **_S4_COMMON_PREFIX,
        "product_lines": {"type": "string", "description": "5-8 sentences. Insurance products by line (life, P&C, reinsurance, specialty), distribution channels, geographic mix, and product strategy."},
        "underwriting_and_pricing": {"type": "string", "description": "5-8 sentences. Underwriting approach, risk selection methodology, pricing discipline, loss ratio management, and cycle positioning."},
        "investment_portfolio": {"type": "string", "description": "5-8 sentences. Fixed income strategy, asset allocation, duration management, yield optimization, and credit quality."},
        "distribution_and_technology": {"type": "string", "description": "4-6 sentences. Agency vs. direct distribution, digital capabilities, InsurTech positioning, and claims technology."},
    },
    "required": ["section_number", "section_thesis", "opening_paragraph",
                  "product_lines", "underwriting_and_pricing", "investment_portfolio", "distribution_and_technology"],
    "additionalProperties": False,
}

_S4_REITS = {
    "type": "object",
    "properties": {
        **_S4_COMMON_PREFIX,
        "property_portfolio": {"type": "string", "description": "5-8 sentences. Property types, geographic locations, quality/age, concentration, and portfolio composition strategy."},
        "development_pipeline": {"type": "string", "description": "5-8 sentences. Active development projects, land bank, expected deliveries and yields, construction costs, and pre-leasing activity."},
        "acquisition_strategy": {"type": "string", "description": "5-8 sentences. Acquisition criteria, cap rate targets, recent deal activity, disposition strategy, and recycling of capital."},
        "property_technology": {"type": "string", "description": "4-6 sentences. PropTech adoption, smart building features, sustainability/ESG initiatives, and energy efficiency investments."},
    },
    "required": ["section_number", "section_thesis", "opening_paragraph",
                  "property_portfolio", "development_pipeline", "acquisition_strategy", "property_technology"],
    "additionalProperties": False,
}

_S4_ENERGY = {
    "type": "object",
    "properties": {
        **_S4_COMMON_PREFIX,
        "upstream_operations": {"type": "string", "description": "5-8 sentences. Proved/probable reserves, production volumes (BOE/d), exploration activity, resource basin quality, and reserve life."},
        "downstream_and_midstream": {"type": "string", "description": "5-8 sentences. Refining capacity/utilization, midstream pipeline/gathering assets, chemicals and specialties businesses."},
        "commodity_and_hedging": {"type": "string", "description": "5-8 sentences. Commodity price exposure, hedging strategy and coverage, breakeven economics, and price sensitivity analysis."},
        "energy_transition": {"type": "string", "description": "4-6 sentences. Low-carbon investments, renewable portfolio, carbon capture, emissions reduction targets, and strategic positioning."},
    },
    "required": ["section_number", "section_thesis", "opening_paragraph",
                  "upstream_operations", "downstream_and_midstream", "commodity_and_hedging", "energy_transition"],
    "additionalProperties": False,
}

_S4_UTILITIES = {
    "type": "object",
    "properties": {
        **_S4_COMMON_PREFIX,
        "generation_portfolio": {"type": "string", "description": "5-8 sentences. Fuel mix (nuclear, gas, coal, wind, solar), total capacity (MW), dispatch economics, capacity factors, and fleet age."},
        "transmission_and_distribution": {"type": "string", "description": "5-8 sentences. T&D infrastructure scale, grid modernization investments, reliability metrics, storm hardening, and smart grid adoption."},
        "regulatory_rate_base": {"type": "string", "description": "5-8 sentences. Authorized rate base, pending/recent rate cases, capex recovery mechanism, regulatory lag, and authorized vs. earned ROE."},
        "clean_energy_transition": {"type": "string", "description": "4-6 sentences. Renewable buildout plan, battery storage, decarbonization timeline, IRA/tax incentive benefits, and state clean energy mandates."},
    },
    "required": ["section_number", "section_thesis", "opening_paragraph",
                  "generation_portfolio", "transmission_and_distribution", "regulatory_rate_base", "clean_energy_transition"],
    "additionalProperties": False,
}

# Subsector → S4 schema dispatch
_S4_SECTOR_SCHEMAS: dict[str, dict] = {
    "banking": _S4_BANKING,
    "insurance": _S4_INSURANCE,
    "reits": _S4_REITS,
    "energy": _S4_ENERGY,
    "utilities": _S4_UTILITIES,
}


def _get_section_4_schema(subsector: str) -> dict:
    """Return the Section 4 schema variant appropriate for this subsector."""
    return _S4_SECTOR_SCHEMAS.get(subsector, _S4_DEFAULT)


# Keep SECTION_SCHEMAS[4] as default for backward compat
SECTION_SCHEMAS[4] = _S4_DEFAULT

# --- S5: Competitive Moats ---
SECTION_SCHEMAS[5] = {
    "type": "object",
    "properties": {
        "section_number": {"type": "integer", "enum": [5]},
        "section_thesis": {"type": "string"},
        "opening_paragraph": {"type": "string"},
        "moat_blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "evidence": {"type": "string", "description": "4-6 sentences. Specific evidence of this moat source with data, examples, and competitive comparisons."},
                    "mechanism": {"type": "string", "description": "4-6 sentences. How this moat creates durable competitive advantage, with analysis of sustainability and threats."},
                },
                "required": ["label", "evidence", "mechanism"],
            },
        },
        "overall_assessment": {
            "type": "object",
            "properties": {
                "classification": {"type": "string", "enum": ["WIDE", "NARROW", "NONE"]},
                "paragraph": {"type": "string", "description": "5-8 sentences. Synthesize all moat sources into a unified assessment with clear reasoning for the classification."},
            },
            "required": ["classification", "paragraph"],
        },
        "moat_score": {"type": "integer", "description": (
            "Moat strength 0-100. Calibrate: 85-100 = multiple reinforcing moats with direct "
            "pricing-power evidence (realized price increases without share loss); 65-84 = one "
            "clear durable moat with quantified switching costs or network effects; 40-64 = real "
            "but replicable advantages (scale or brand without pricing evidence); 20-39 = weak or "
            "eroding advantages; 0-19 = commodity competition. Most companies score below 65 — "
            "reserve 80+ for demonstrated, not asserted, power."
        )},
    },
    "required": ["section_number", "section_thesis", "opening_paragraph",
                  "moat_blocks", "overall_assessment", "moat_score"],
    "additionalProperties": False,
}

# --- S6: Industry & Competitive ---
SECTION_SCHEMAS[6] = {
    "type": "object",
    "properties": {
        "section_number": {"type": "integer", "enum": [6]},
        "section_thesis": {"type": "string"},
        "opening_paragraph": {"type": "string"},
        "market_structure": {
            "type": "object",
            "properties": {
                "broad_market": {"type": "string", "description": "4-6 sentences. Market size, structure, key players, and concentration."},
                "key_segment": {"type": "string", "description": "4-6 sentences. The company's primary segment, positioning, and share."},
            },
            "required": ["broad_market", "key_segment"],
        },
        "competitive_landscape": {
            "type": ["object", "null"],
            "properties": {
                "intro": {"type": "string", "description": "3-5 sentences introducing the competitive landscape with specific competitor names and positioning."},
                "analysis": {"type": "string", "description": "3-5 sentences analyzing competitive positioning, market share shifts, and strategic differentiation."},
            },
        },
        "competitive_dynamics": {
            "type": "object",
            "properties": {
                "competition": {"type": "string", "description": "4-6 sentences. How companies compete, pricing dynamics, differentiation strategies."},
                "barriers": {"type": "string", "description": "4-6 sentences. Entry barriers, regulatory moats, capital requirements, and switching costs."},
            },
            "required": ["competition", "barriers"],
        },
        "industry_forces": {"type": "array", "items": _LABELED_BLOCK, "description": "4-6 key industry forces with detailed analysis."},
        "tailwinds": {"type": "string", "description": "4-6 sentences. Secular and cyclical tailwinds with specific evidence and magnitude of impact."},
        "headwinds": {"type": "string", "description": "4-6 sentences. Secular and cyclical headwinds with specific evidence and magnitude of impact."},
    },
    "required": ["section_number", "section_thesis", "opening_paragraph",
                  "market_structure", "competitive_landscape", "competitive_dynamics",
                  "industry_forces", "tailwinds", "headwinds"],
    "additionalProperties": False,
}

# --- S7: Customer Analysis (sector-aware) ---
_S7_COMMON_PREFIX = {
    "section_number": {"type": "integer", "enum": [7]},
    "section_thesis": {"type": "string", "description": "1-2 sentences. The key insight about the company's customer/counterparty dynamics. HIDDEN."},
    "opening_paragraph": {"type": "string", "description": "5-8 sentences. Overview of the customer/counterparty base, key dynamics, and their implications for the business."},
}

_S7_DEFAULT = {
    "type": "object",
    "properties": {
        **_S7_COMMON_PREFIX,
        "customer_composition": {"type": "string", "description": "4-6 sentences. Customer mix, concentration risk, key accounts, and segment breakdown."},
        "stickiness_and_retention": {"type": "string", "description": "4-6 sentences. Customer retention rates, switching costs, contract structures, and churn dynamics."},
        "unit_economics": {"type": "string", "description": "4-6 sentences. Revenue per customer, CAC, LTV, and unit economics trends."},
        "working_capital": {"type": "string", "description": "4-6 sentences. Working capital cycle, DSO/DPO/DIO trends, and cash conversion."},
    },
    "required": ["section_number", "section_thesis", "opening_paragraph",
                  "customer_composition", "stickiness_and_retention", "unit_economics", "working_capital"],
    "additionalProperties": False,
}

_S7_BANKING = {
    "type": "object",
    "properties": {
        **_S7_COMMON_PREFIX,
        "deposit_franchise": {"type": "string", "description": "5-8 sentences. Core deposit ratio, deposit cost vs. peers, mix by type (demand/savings/time), deposit stability, growth trajectory, and franchise stickiness."},
        "loan_book_composition": {"type": "string", "description": "5-8 sentences. Loan mix by type, credit quality (NPL/NPA ratios, NCO rates), concentration risk (CRE, C&I), underwriting trends, and reserve coverage."},
        "interest_rate_sensitivity": {"type": "string", "description": "5-8 sentences. NIM trajectory, asset-liability duration gap, rate cycle positioning, deposit beta, and earnings sensitivity to rate changes."},
        "fee_income_analysis": {"type": "string", "description": "4-6 sentences. Fee revenue composition, recurring vs. transactional mix, cross-sell penetration, and fee income as percentage of total revenue."},
    },
    "required": ["section_number", "section_thesis", "opening_paragraph",
                  "deposit_franchise", "loan_book_composition", "interest_rate_sensitivity", "fee_income_analysis"],
    "additionalProperties": False,
}

_S7_INSURANCE = {
    "type": "object",
    "properties": {
        **_S7_COMMON_PREFIX,
        "policyholder_base": {"type": "string", "description": "5-8 sentences. Policyholder demographics, retention/persistency rates, distribution of risk, lapse rates, and geographic/line diversification."},
        "underwriting_cycle": {"type": "string", "description": "5-8 sentences. Hard/soft market positioning, pricing power, competitive dynamics, premium growth trends, and cycle management."},
        "claims_and_reserves": {"type": "string", "description": "5-8 sentences. Loss development patterns, reserve adequacy, prior-year development, catastrophe exposure, and reinsurance protection."},
        "distribution_economics": {"type": "string", "description": "4-6 sentences. Channel costs (agency vs. direct vs. broker), agent retention, commission structures, and distribution efficiency."},
    },
    "required": ["section_number", "section_thesis", "opening_paragraph",
                  "policyholder_base", "underwriting_cycle", "claims_and_reserves", "distribution_economics"],
    "additionalProperties": False,
}

_S7_REITS = {
    "type": "object",
    "properties": {
        **_S7_COMMON_PREFIX,
        "tenant_mix_and_quality": {"type": "string", "description": "5-8 sentences. Tenant industry diversification, credit quality of top tenants, concentration (top 10 tenants as % of rent), weighted average lease term."},
        "lease_structure": {"type": "string", "description": "5-8 sentences. Lease types (NNN, gross, modified gross), built-in rent escalators, CPI linkages, TI/LC obligations, and lease renewal economics."},
        "occupancy_and_retention": {"type": "string", "description": "5-8 sentences. Historical occupancy rates, tenant retention/renewal rates, downtime between tenants, and absorption trends."},
        "rent_dynamics": {"type": "string", "description": "4-6 sentences. Mark-to-market rent opportunity, same-store NOI growth, rent spreads on renewals vs. new leases, and releasing spread trends."},
    },
    "required": ["section_number", "section_thesis", "opening_paragraph",
                  "tenant_mix_and_quality", "lease_structure", "occupancy_and_retention", "rent_dynamics"],
    "additionalProperties": False,
}

_S7_ENERGY = {
    "type": "object",
    "properties": {
        **_S7_COMMON_PREFIX,
        "offtake_and_contracts": {"type": "string", "description": "5-8 sentences. Off-take contract structure, take-or-pay agreements, contract duration and renewal profile, and counterparty credit quality."},
        "commodity_customer_mix": {"type": "string", "description": "5-8 sentences. End-market customer exposure, geographic concentration, domestic vs. export split, and customer industry diversification."},
        "production_economics": {"type": "string", "description": "5-8 sentences. Per-BOE or per-MCF cost structure, lifting/operating cost, breakeven price, cash margin sensitivity to commodity prices."},
        "hedging_and_risk_management": {"type": "string", "description": "4-6 sentences. Hedge book coverage and duration, hedging instruments used, basis risk, and financial risk management philosophy."},
    },
    "required": ["section_number", "section_thesis", "opening_paragraph",
                  "offtake_and_contracts", "commodity_customer_mix", "production_economics", "hedging_and_risk_management"],
    "additionalProperties": False,
}

_S7_UTILITIES = {
    "type": "object",
    "properties": {
        **_S7_COMMON_PREFIX,
        "ratepayer_base": {"type": "string", "description": "5-8 sentences. Customer counts and growth, residential/commercial/industrial mix, service territory demographics, and load characteristics."},
        "regulatory_relationships": {"type": "string", "description": "5-8 sentences. State commission dynamics, regulatory philosophy (constructive vs. adversarial), historical rate case outcomes, and political environment."},
        "rate_case_dynamics": {"type": "string", "description": "5-8 sentences. Pending and recent rate cases, authorized ROE vs. earned ROE, test year methodology, formula rate plans, and regulatory lag."},
        "demand_and_load_patterns": {"type": "string", "description": "4-6 sentences. Load growth trends, electrification impacts (EV, data centers), peak demand management, weather sensitivity, and energy efficiency effects."},
    },
    "required": ["section_number", "section_thesis", "opening_paragraph",
                  "ratepayer_base", "regulatory_relationships", "rate_case_dynamics", "demand_and_load_patterns"],
    "additionalProperties": False,
}

# Subsector → S7 schema dispatch
_S7_SECTOR_SCHEMAS: dict[str, dict] = {
    "banking": _S7_BANKING,
    "insurance": _S7_INSURANCE,
    "reits": _S7_REITS,
    "energy": _S7_ENERGY,
    "utilities": _S7_UTILITIES,
}


def _get_section_7_schema(subsector: str) -> dict:
    """Return the Section 7 schema variant appropriate for this subsector."""
    return _S7_SECTOR_SCHEMAS.get(subsector, _S7_DEFAULT)


# Keep SECTION_SCHEMAS[7] as default for backward compat
SECTION_SCHEMAS[7] = _S7_DEFAULT

# ── Sector-specific section titles ───────────────────────────
_S4_TITLES: dict[str, str] = {
    "banking": "Banking Products & Services",
    "insurance": "Insurance Products & Strategy",
    "reits": "Property Portfolio & Strategy",
    "energy": "Operations & Asset Base",
    "utilities": "Generation & Regulatory Strategy",
}

_S7_TITLES: dict[str, str] = {
    "banking": "Deposit & Lending Analysis",
    "insurance": "Policyholder & Claims Analysis",
    "reits": "Tenant & Lease Analysis",
    "energy": "Production & Commodity Analysis",
    "utilities": "Regulatory & Demand Analysis",
}


def _get_section_4_title(subsector: str) -> str:
    return _S4_TITLES.get(subsector, "Product & Technology Strategy")


def _get_section_7_title(subsector: str) -> str:
    return _S7_TITLES.get(subsector, "Customer Analysis")


def _get_sector_family(subsector: str, sector: str = "", industry: str = "") -> str:
    """Return the canonical sector name (13) used across the pipeline.

    Canonical set (matches your screenshot):
      technology, banking, insurance, reits, retail, energy, healthcare,
      industrials, consumer_staples, utilities, telecom, materials, consumer_disc

    Priority:
    1) SEC sector KPI module label (subsector) for the financial split + retail/staples.
    2) FMP sector string (GICS-ish) for the rest.
    3) Industry heuristics as a last resort.
    """
    s = (subsector or "").lower().strip()
    sec = (sector or "").lower().strip()
    ind = (industry or "").lower().strip()

    # 1) SEC sector module label → canonical sector
    # (subsector is usually fs["_sec_sector_kpis"]["sector"])
    if s == "banking" or "bank" in s:
        return "banking"
    if s == "insurance" or "insurance" in s:
        # Health insurers can show up under the insurance KPI module but still belong
        # to the Healthcare sector in the canonical 13-sector taxonomy.
        if sec == "healthcare":
            return "healthcare"
        return "insurance"
    if s == "reits" or "reit" in s or "real estate investment trust" in ind:
        return "reits"
    if s == "retail":
        return "retail"
    if s in {"consumer_staples", "staples"}:
        return "consumer_staples"

    # 2) FMP sector string → canonical sector
    # (we keep this mapping tight and explicit)
    if sec in {"technology"}:
        return "technology"
    if sec in {"energy"}:
        return "energy"
    if sec in {"healthcare"}:
        return "healthcare"
    if sec in {"industrials"}:
        return "industrials"
    if sec in {"utilities"}:
        return "utilities"
    if sec in {"communication services", "telecom"}:
        return "telecom"
    if sec in {"consumer defensive", "consumer staples"}:
        return "consumer_staples"
    if sec in {"consumer cyclical", "consumer discretionary", "consumer disc"}:
        return "consumer_disc"
    if sec in {"materials", "basic materials"}:
        return "materials"
    if sec in {"real estate"}:
        return "reits"
    if sec in {"financial services", "financial"}:
        # Any non-bank financials that slip through still live under the banking/insurance split.
        return "insurance" if "insurance" in ind else "banking"

    # 3) Industry heuristics
    if "telecom" in ind or "wireless" in ind:
        return "telecom"

    # Last resort: most things that aren't explicitly classified behave closest to Consumer Disc.
    return "consumer_disc"


# If the SUBJECT itself has no sector KPI coverage for its sector, fall back to a
# generic (old-model) financial analysis and peer comp.
_SUBJECT_REQUIRED_KPIS: dict[str, list[str]] = {
    "banking": ["netInterestMargin", "efficiencyRatio", "nplRatio"],
    "insurance": ["combinedRatio", "investmentYield"],
    "reits": ["ffoPerShare", "affoPerShare"],
    "technology": ["ruleOf40", "rdIntensity", "sbcAsPercentOfRevenue"],
    "retail": ["inventoryTurnover", "sameStoreSalesProxy"],
    "energy": ["fcfPerShare", "capexToRevenue"],
    "healthcare": ["rdIntensity"],
    "industrials": ["bookToBill", "backlogToRevenue"],
    "consumer_staples": ["fcfConversion"],
    "utilities": ["rateBaseGrowth", "capexIntensity"],
    "telecom": [],  # no dedicated SEC KPI module — do not force fallback
    "materials": ["capexIntensity", "debtToEbitda"],
    "consumer_disc": [],
    "generic": [],
}


def _latest_sector_kpi_row(sector_kpis: dict | None) -> dict | None:
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


def _subject_kpi_coverage(sector_kpis: dict | None, sector_family: str) -> dict:
    req = _SUBJECT_REQUIRED_KPIS.get(sector_family, [])
    row = _latest_sector_kpi_row(sector_kpis)
    present = 0
    missing: list[str] = []
    for k in req:
        if row and row.get(k) is not None:
            present += 1
        else:
            missing.append(k)
    return {"present": present, "required": len(req), "missing": missing}


_S10_TITLES = {
    "technology": "Growth Efficiency & Profitability Analysis",
    "banking": "Financial Strength & Credit Analysis",
    "insurance": "Underwriting & Capital Analysis",
    "reits": "Property Cash Flow & Balance Sheet Analysis",
    "retail": "Store Economics & Cash Generation Analysis",
    "energy": "Commodity Economics & Capital Discipline Analysis",
    "healthcare": "R&D Productivity & Margin Profile Analysis",
    "industrials": "Through-Cycle Returns & Operating Efficiency Analysis",
    "consumer_staples": "Pricing Power & Cash Generation Analysis",
    "utilities": "Regulated Earnings & Capital Analysis",
    "telecom": "Network Economics & Capital Intensity Analysis",
    "materials": "Cost Curve & Cycle-Adjusted Profitability Analysis",
    "consumer_disc": "Demand Cyclicality & Margin Leverage Analysis",
    "generic": "Financial Analysis",
}

_S11_TITLES = {
    "technology": "Peer Technology Benchmarking",
    "banking": "Peer Banking Benchmarking",
    "insurance": "Peer Insurance Benchmarking",
    "reits": "Peer REIT Benchmarking",
    "retail": "Peer Retail Benchmarking",
    "energy": "Peer Energy Benchmarking",
    "healthcare": "Peer Healthcare Benchmarking",
    "industrials": "Peer Industrials Benchmarking",
    "consumer_staples": "Peer Consumer Staples Benchmarking",
    "utilities": "Peer Utility Benchmarking",
    "telecom": "Peer Telecom Benchmarking",
    "materials": "Peer Materials Benchmarking",
    "consumer_disc": "Peer Consumer Discretionary Benchmarking",
    "generic": "Peer Financial Benchmarking",
}

# Generic S10 precomputed tables to SUPPRESS by sector family.
# Banking / insurance / REITs have fundamentally different financial frameworks;
# generic gross-margin / FCF / net-debt-to-EBITDA tables would mislead the writer.
# Sectors NOT listed here keep all generic tables (they supplement sector KPI tables).
_S10_GENERIC_TABLE_SUPPRESSIONS: dict[str, set[str]] = {
    "banking":   {"margins", "cash_flow", "leverage"},   # uses NIM, efficiency ratio, CET1 instead
    "insurance": {"margins", "cash_flow", "leverage"},   # uses combined ratio, reserves, investment income
    "reits":     {"margins", "cash_flow"},               # uses FFO/AFFO, NOI; leverage tables still useful
}


def _clone_schema(obj: Any) -> Any:
    return deepcopy(obj)


def _get_section_10_schema(family: str) -> dict:
    schema = _clone_schema(SECTION_SCHEMAS[10])
    props = schema.get("properties", {})

    # Sector-family-specific S10 schemas: each sector covers all 5 financial analysis
    # dimensions (growth, margins, efficiency, returns, leverage) but framed through
    # the lens of that sector's key metrics and operating model.
    if family in {"technology", "banking", "insurance", "reits", "retail", "energy", "healthcare",
                  "industrials", "consumer_staples", "utilities", "telecom", "materials", "consumer_disc"}:
        base_props = schema.get("properties", {})
        # Split common fields: header goes first, footer (flags/synthesis/score)
        # goes AFTER all sector-specific subsections.
        common_top = {
            "section_number": _clone_schema(base_props.get("section_number")),
            "section_thesis": _clone_schema(base_props.get("section_thesis")),
            "opening_paragraph": _clone_schema(base_props.get("opening_paragraph")),
        }
        common_bottom = {
            "financial_quality_flags": _clone_schema(base_props.get("financial_quality_flags")),
            "synthesis": _clone_schema(base_props.get("synthesis")),
            "quality_score": _clone_schema(base_props.get("quality_score")),
        }
        # Ensure opening paragraph always has explicit prose expectations.
        if isinstance(common_top.get("opening_paragraph"), dict):
            common_top["opening_paragraph"]["description"] = (
                "4-6 sentences. Lead with the 2-3 key financial takeaways, then preview the subsections below."
            )

        if family == "banking":
            common_top["opening_paragraph"]["description"] = (
                "4-6 sentences. Frame earnings power (NIM + fees), operating efficiency, credit costs, and capital/funding strength."
            )
            schema["properties"] = {
                **common_top,
                # Growth
                "earnings_power_and_nim": _table_wrapper(
                    "3-5 sentences. Focus on NIM drivers, deposit beta/funding costs, and fee income mix.",
                    "4-6 sentences. Explain how spread income and fees translate into pre-provision profitability versus peers."
                ),
                # Efficiency
                "efficiency_and_costs": _table_wrapper(
                    "3-5 sentences. Focus on efficiency ratio, scale benefits, and expense discipline.",
                    "4-6 sentences. Explain whether cost structure supports durable profitability (and where efficiency can improve)."
                ),
                # Margins / Asset Quality
                "credit_quality": _table_wrapper(
                    "3-5 sentences. Focus on NPLs, net charge-offs, reserve coverage, and portfolio concentration risks.",
                    "4-6 sentences. Explain how credit costs may evolve across the cycle and how the bank compares to peers."
                ),
                # Returns
                "returns_capital_efficiency": _table_wrapper(
                    "3-5 sentences. Focus on ROE, ROTCE, and how capital deployment (buybacks/dividends) translates into shareholder returns.",
                    "4-6 sentences. Explain whether the bank earns above its cost of equity and how returns compare to peers."
                ),
                # Leverage / Balance Sheet
                "capital_and_funding": _table_wrapper(
                    "3-5 sentences. Focus on CET1/Tier 1/Leverage, deposit franchise stability, and liquidity.",
                    "4-6 sentences. Explain whether capital levels are conservative and how the funding mix supports resilience."
                ),
                **common_bottom,
            }
            return schema

        if family == "insurance":
            schema["properties"] = {
                **common_top,
                # Growth
                "investment_income": _table_wrapper(
                    "3-5 sentences. Focus on premium growth trends, pricing power, line-of-business mix, and how investment income supports total earnings.",
                    "4-6 sentences. Explain sensitivity to rates/credit spreads and whether total revenue growth is sustainable."
                ),
                # Margins
                "underwriting_profitability": _table_wrapper(
                    "3-5 sentences. Focus on combined ratio, pricing vs. loss trends, and underwriting discipline.",
                    "4-6 sentences. Explain whether underwriting profitability is structural or cyclical, and how it compares to peers."
                ),
                # Efficiency
                "cash_flow": _table_wrapper(
                    "3-5 sentences. Focus on operating cash flow quality, float generation, and expense ratio as a measure of operating efficiency.",
                    "4-6 sentences. Explain cash conversion from underwriting and investments and how it compares to peers."
                ),
                # Returns
                "returns_capital_efficiency": _table_wrapper(
                    "3-5 sentences. Focus on ROE, underwriting return on allocated capital, and how capital deployment creates shareholder value.",
                    "4-6 sentences. Explain whether returns exceed cost of equity and how capital allocation compares to peers."
                ),
                # Leverage / Balance Sheet
                "reserves_and_capital": _table_wrapper(
                    "3-5 sentences. Focus on reserve adequacy signals, catastrophe exposure, and capital strength (D/E, leverage).",
                    "4-6 sentences. Explain how capital supports growth and shareholder returns without compromising solvency."
                ),
                **common_bottom,
            }
            return schema

        if family == "reits":
            schema["properties"] = {
                **common_top,
                # Growth
                "portfolio_and_noi": _table_wrapper(
                    "3-5 sentences. Focus on same-store NOI growth, occupancy/leasing trends (if available), and portfolio expansion.",
                    "4-6 sentences. Explain portfolio quality and whether property-level economics are improving versus peers."
                ),
                # Margins / Cash Flow
                "ffo_affo_and_dividend": _table_wrapper(
                    "3-5 sentences. Focus on FFO/AFFO per share, NOI margin, payout coverage, and dividend safety.",
                    "4-6 sentences. Explain the drivers of AFFO growth and whether the dividend is supported across the cycle."
                ),
                # Efficiency
                "cash_flow": _table_wrapper(
                    "3-5 sentences. Focus on G&A efficiency, property operating costs, and capital recycling discipline (dispositions vs acquisitions).",
                    "4-6 sentences. Explain whether the platform operates efficiently relative to AUM and how it compares to peers."
                ),
                # Returns
                "returns_capital_efficiency": _table_wrapper(
                    "3-5 sentences. Focus on implied cap rates, development/acquisition spreads, and ROE relative to cost of equity.",
                    "4-6 sentences. Explain whether capital deployment is value-accretive and how returns compare to REIT peers."
                ),
                # Leverage
                "leverage_and_financing": _table_wrapper(
                    "3-5 sentences. Focus on debt load, maturity ladder/refi risk (if available), and balance-sheet flexibility.",
                    "4-6 sentences. Explain whether financing discipline supports accretive growth and protects equity holders."
                ),
                **common_bottom,
            }
            return schema

        if family == "utilities":
            schema["properties"] = {
                **common_top,
                # Growth
                "regulated_earnings_and_rate_base": _table_wrapper(
                    "3-5 sentences. Focus on rate-base growth, regulatory construct, and earnings growth visibility.",
                    "4-6 sentences. Explain the drivers of rate-base expansion and key regulatory sensitivities."
                ),
                # Margins / Efficiency
                "cash_flow": _table_wrapper(
                    "3-5 sentences. Focus on allowed vs earned ROE (if available), O&M efficiency, and operating cost discipline.",
                    "4-6 sentences. Explain whether the utility operates efficiently within its regulatory framework and how it compares to peers."
                ),
                # Cash Flow / Capital Deployment
                "capex_and_payout": _table_wrapper(
                    "3-5 sentences. Focus on capex intensity, FCF after investment, financing plan, and dividend support.",
                    "4-6 sentences. Explain whether the capex program is value-accretive and the dividend is sustainable."
                ),
                # Returns
                "returns_capital_efficiency": _table_wrapper(
                    "3-5 sentences. Focus on earned ROE vs allowed ROE, regulatory lag, and whether rate-base investment earns adequate returns.",
                    "4-6 sentences. Explain whether returns justify the capital program and how they compare to utility peers."
                ),
                # Leverage
                "balance_sheet_and_funding": _table_wrapper(
                    "3-5 sentences. Focus on leverage (D/E, debt/rate base), interest coverage, and refinancing flexibility.",
                    "4-6 sentences. Explain whether funding costs and leverage are appropriate for a regulated profile."
                ),
                **common_bottom,
            }
            return schema

        if family == "energy":
            schema["properties"] = {
                **common_top,
                # Growth
                "production_and_reserves": _table_wrapper(
                    "3-5 sentences. Focus on production trends, reserve replacement proxies, and mix (if available).",
                    "4-6 sentences. Explain sustainability of volumes and whether growth is value-accretive."
                ),
                # Margins
                "unit_economics_and_costs": _table_wrapper(
                    "3-5 sentences. Focus on per-unit economics (lifting cost, finding cost) and breakeven sensitivity.",
                    "4-6 sentences. Explain whether cost position is advantaged and how margins behave across commodity cycles."
                ),
                # Efficiency / Cash Flow
                "cash_flow": _table_wrapper(
                    "3-5 sentences. Focus on FCF generation across commodity cycles, capex efficiency, and cash conversion quality.",
                    "4-6 sentences. Explain whether cash flow is resilient at mid-cycle prices and how reinvestment competes with shareholder returns."
                ),
                # Returns
                "returns_capital_efficiency": _table_wrapper(
                    "3-5 sentences. Focus on ROIC/ROCE through the cycle, capital allocation discipline, and returns on reinvestment.",
                    "4-6 sentences. Explain whether the company earns above its cost of capital and how returns compare to E&P/integrated peers."
                ),
                # Leverage
                "capital_discipline": _table_wrapper(
                    "3-5 sentences. Focus on D/E, net debt/EBITDA, balance-sheet resiliency, and ability to self-fund through downturns.",
                    "4-6 sentences. Explain whether leverage is appropriate for the commodity exposure and how it compares to peers."
                ),
                **common_bottom,
            }
            return schema

        if family == "technology":
            schema["properties"] = {
                **common_top,
                # Growth
                "growth_and_retention": _table_wrapper(
                    "3-5 sentences. Focus on revenue growth, retention/NRR proxies (if available), and durability of demand.",
                    "4-6 sentences. Explain growth drivers, churn/expansion dynamics, and what would cause deceleration."
                ),
                # Margins
                "profitability_and_efficiency": _table_wrapper(
                    "3-5 sentences. Focus on gross margin, operating leverage, and rule-of-40 style efficiency.",
                    "4-6 sentences. Explain the path to scale and whether profitability is structurally improving."
                ),
                # Efficiency (R&D + SBC as capital efficiency)
                "r_and_d_and_sbc": _table_wrapper(
                    "3-5 sentences. Focus on R&D intensity, SBC burden, and how these investments translate into competitive positioning.",
                    "4-6 sentences. Explain whether R&D ROI is attractive, how SBC dilution affects owner returns, and efficiency vs peers."
                ),
                # Returns / Cash Flow
                "returns_capital_efficiency": _table_wrapper(
                    "3-5 sentences. Focus on ROIC, FCF conversion, and how capital-light models translate into shareholder returns.",
                    "4-6 sentences. Explain whether returns justify the R&D/SBC reinvestment and how FCF yield compares to tech peers."
                ),
                # Leverage
                "leverage_balance_sheet": _table_wrapper(
                    "3-5 sentences. Focus on D/E, net cash/debt position, and capital structure choices (buybacks vs growth investment).",
                    "4-6 sentences. Explain whether the balance sheet supports the growth strategy and how leverage compares to tech peers."
                ),
                **common_bottom,
            }
            return schema

        if family == "healthcare":
            schema["properties"] = {
                **common_top,
                # Growth
                "revenue_mix_and_volume": _table_wrapper(
                    "3-5 sentences. Focus on revenue mix, volume trends, and pricing/reimbursement sensitivity (as applicable).",
                    "4-6 sentences. Explain the durability of demand and key drivers of growth/pressure."
                ),
                # Margins / Cash Flow
                "margins_and_cash_generation": _table_wrapper(
                    "3-5 sentences. Focus on margin profile, cash conversion/FCF consistency, and operating leverage.",
                    "4-6 sentences. Explain whether profitability is defensible and how it compares to peers."
                ),
                # Efficiency (R&D as capital efficiency)
                "r_and_d_productivity": _table_wrapper(
                    "3-5 sentences. Focus on R&D intensity/productivity, pipeline ROI where relevant (biopharma/medtech), and operational efficiency.",
                    "4-6 sentences. Explain innovation ROI signals, SG&A efficiency, and risk to future growth."
                ),
                # Returns
                "returns_capital_efficiency": _table_wrapper(
                    "3-5 sentences. Focus on ROIC, ROE, and whether M&A/R&D reinvestment earns attractive returns.",
                    "4-6 sentences. Explain capital allocation discipline and how returns compare to healthcare peers."
                ),
                # Leverage
                "leverage_balance_sheet": _table_wrapper(
                    "3-5 sentences. Focus on D/E, net debt/EBITDA, and acquisition-related leverage risk.",
                    "4-6 sentences. Explain whether balance sheet supports the pipeline/growth strategy and how leverage compares to peers."
                ),
                **common_bottom,
            }
            return schema

        if family == "industrials":
            schema["properties"] = {
                **common_top,
                # Growth
                "orders_backlog_and_cycle": _table_wrapper(
                    "3-5 sentences. Focus on backlog/book-to-bill proxies, cycle position (if available), and organic growth.",
                    "4-6 sentences. Explain visibility of demand and sensitivity to industrial cycles."
                ),
                # Margins
                "operations_and_margins": _table_wrapper(
                    "3-5 sentences. Focus on utilization/throughput, margin leverage, and operating cost structure.",
                    "4-6 sentences. Explain whether margins are structurally improving or merely cyclical."
                ),
                # Efficiency / Cash Flow
                "cash_flow": _table_wrapper(
                    "3-5 sentences. Focus on FCF conversion, capex efficiency (maintenance vs growth), and working capital discipline.",
                    "4-6 sentences. Explain cash generation quality through the cycle and how it compares to industrial peers."
                ),
                # Returns
                "returns_and_capital_intensity": _table_wrapper(
                    "3-5 sentences. Focus on ROIC/ROCE, capital intensity (capex + R&D burden), and incremental returns on investment.",
                    "4-6 sentences. Explain through-cycle returns and whether reinvestment earns attractive spreads vs peers."
                ),
                # Leverage
                "leverage_balance_sheet": _table_wrapper(
                    "3-5 sentences. Focus on D/E, net debt/EBITDA, and balance-sheet flexibility for acquisitions and downturns.",
                    "4-6 sentences. Explain whether leverage is appropriate for the cyclicality of the business."
                ),
                **common_bottom,
            }
            return schema

        if family == "retail":
            schema["properties"] = {
                **common_top,
                # Growth
                "demand_and_unit_economics": _table_wrapper(
                    "3-5 sentences. Focus on comp sales, traffic/ticket trends, pricing, and unit/store economics where applicable.",
                    "4-6 sentences. Explain what drives comp sales/traffic and the risk of demand normalization."
                ),
                # Margins
                "gross_margin_and_cost_structure": _table_wrapper(
                    "3-5 sentences. Focus on gross margin drivers (mix, markdowns, shrink) and operating costs.",
                    "4-6 sentences. Explain operating leverage and the sustainability of margin levels."
                ),
                # Efficiency / Cash Flow
                "working_capital_and_cash": _table_wrapper(
                    "3-5 sentences. Focus on inventory discipline, working capital efficiency, cash conversion, and store-level productivity.",
                    "4-6 sentences. Explain whether cash generation is resilient and how it supports shareholder returns."
                ),
                # Returns
                "returns_capital_efficiency": _table_wrapper(
                    "3-5 sentences. Focus on ROIC, new-store returns, and whether expansion/remodel capex earns attractive returns.",
                    "4-6 sentences. Explain capital allocation discipline and how returns compare to retail peers."
                ),
                # Leverage
                "leverage_balance_sheet": _table_wrapper(
                    "3-5 sentences. Focus on D/E, lease-adjusted leverage, and balance-sheet flexibility.",
                    "4-6 sentences. Explain whether the capital structure supports growth and shareholder returns through cycles."
                ),
                **common_bottom,
            }
            return schema

        if family == "consumer_staples":
            schema["properties"] = {
                **common_top,
                # Growth
                "price_mix_vs_volume": _table_wrapper(
                    "3-5 sentences. Focus on price/mix versus volume dynamics and elasticity.",
                    "4-6 sentences. Explain whether growth is sustainable without eroding brand equity/volumes."
                ),
                # Margins
                "margin_resilience": _table_wrapper(
                    "3-5 sentences. Focus on gross margin resilience, input cost pass-through, and operating leverage.",
                    "4-6 sentences. Explain whether profitability is structurally defended and how it compares to peers."
                ),
                # Efficiency / Cash Flow
                "cash_generation_and_payout": _table_wrapper(
                    "3-5 sentences. Focus on cash conversion, capex efficiency, working capital discipline, and dividend/buyback sustainability.",
                    "4-6 sentences. Explain capital allocation discipline and downside protection."
                ),
                # Returns
                "returns_capital_efficiency": _table_wrapper(
                    "3-5 sentences. Focus on ROIC, brand investment ROI, and whether M&A/marketing spend earns attractive returns.",
                    "4-6 sentences. Explain whether returns are structurally high due to brand moat and how they compare to staples peers."
                ),
                # Leverage
                "leverage_balance_sheet": _table_wrapper(
                    "3-5 sentences. Focus on D/E, acquisition-related leverage, and balance-sheet capacity for M&A and buybacks.",
                    "4-6 sentences. Explain whether leverage is appropriate for the defensive earnings profile."
                ),
                **common_bottom,
            }
            return schema

        if family == "materials":
            schema["properties"] = {
                **common_top,
                # Growth
                "price_sensitivity_and_cycle": _table_wrapper(
                    "3-5 sentences. Focus on commodity price sensitivity, volume exposure, and cycle position.",
                    "4-6 sentences. Explain the key cycle risks and what a normalized earnings/growth view should look like."
                ),
                # Margins / Efficiency
                "cost_curve_and_competitiveness": _table_wrapper(
                    "3-5 sentences. Focus on cost curve position, unit costs, and operational efficiency.",
                    "4-6 sentences. Explain whether the company is advantaged in a downcycle and what drives relative margins."
                ),
                # Cash Flow
                "cash_flow": _table_wrapper(
                    "3-5 sentences. Focus on FCF generation across commodity cycles, sustaining vs growth capex, and cash conversion quality.",
                    "4-6 sentences. Explain whether cash flow is resilient at mid-cycle prices and how it funds shareholder returns."
                ),
                # Returns
                "returns_capital_efficiency": _table_wrapper(
                    "3-5 sentences. Focus on ROIC/ROCE through the cycle and whether reinvestment earns attractive returns.",
                    "4-6 sentences. Explain capital allocation discipline and how returns compare to materials peers."
                ),
                # Leverage
                "capital_intensity_and_balance_sheet": _table_wrapper(
                    "3-5 sentences. Focus on D/E, capex burden, sustaining vs growth spend, and balance sheet risk.",
                    "4-6 sentences. Explain whether the company can self-fund and survive adverse pricing environments."
                ),
                **common_bottom,
            }
            return schema

        if family == "telecom":
            schema["properties"] = {
                **common_top,
                # Growth
                "network_economics_and_arpu": _table_wrapper(
                    "3-5 sentences. Focus on subscriber growth, ARPU trends, pricing power, and customer mix.",
                    "4-6 sentences. Explain competitive intensity, churn dynamics (if available), and the path to durable revenue growth."
                ),
                # Margins / Efficiency
                "cash_flow": _table_wrapper(
                    "3-5 sentences. Focus on EBITDA margin, operating efficiency, and cost structure (network vs SG&A).",
                    "4-6 sentences. Explain whether efficiency is improving with scale and how margins compare to telecom peers."
                ),
                # Cash Flow / Capital Deployment
                "capex_and_fcf": _table_wrapper(
                    "3-5 sentences. Focus on capex intensity, spectrum/network reinvestment needs, and FCF conversion.",
                    "4-6 sentences. Explain whether free cash flow is sustainable after maintenance capex and what drives upside/downside."
                ),
                # Returns
                "returns_capital_efficiency": _table_wrapper(
                    "3-5 sentences. Focus on ROIC, return on network investment, and whether spectrum/infrastructure capex earns adequate returns.",
                    "4-6 sentences. Explain capital allocation discipline and how returns compare to telecom peers."
                ),
                # Leverage
                "leverage_and_regulatory": _table_wrapper(
                    "3-5 sentences. Focus on D/E, net debt/EBITDA, funding costs, and regulatory/spectrum risk.",
                    "4-6 sentences. Explain balance-sheet flexibility and how regulation impacts returns."
                ),
                **common_bottom,
            }
            return schema

        if family == "consumer_disc":
            schema["properties"] = {
                **common_top,
                # Growth
                "demand_and_cycle": _table_wrapper(
                    "3-5 sentences. Focus on demand cyclicality, discretionary exposure, and volume sensitivity.",
                    "4-6 sentences. Explain what drives upside in a strong cycle and what breaks in a weak macro environment."
                ),
                # Margins
                "margin_leverage": _table_wrapper(
                    "3-5 sentences. Focus on gross/operating margin leverage, cost structure flexibility, and operating efficiency.",
                    "4-6 sentences. Explain whether margins are structurally improving or just cyclical."
                ),
                # Efficiency / Cash Flow
                "cash_and_balance_sheet": _table_wrapper(
                    "3-5 sentences. Focus on cash conversion, working capital efficiency, capex burden, and inventory management.",
                    "4-6 sentences. Explain downside protection and whether the company can self-fund through downturns."
                ),
                # Returns
                "returns_capital_efficiency": _table_wrapper(
                    "3-5 sentences. Focus on ROIC, returns on store/brand investment, and capital allocation discipline.",
                    "4-6 sentences. Explain whether growth capex earns attractive returns and how they compare to consumer disc peers."
                ),
                # Leverage
                "leverage_balance_sheet": _table_wrapper(
                    "3-5 sentences. Focus on D/E, lease-adjusted leverage, and balance-sheet flexibility for cyclical downturns.",
                    "4-6 sentences. Explain whether leverage is appropriate for the cyclicality and how it compares to peers."
                ),
                **common_bottom,
            }
            return schema

    # Default / generic fallback: all 13 named families returned above.
    # Only "generic" (and any unknown family) reaches here.
    if isinstance(props.get("opening_paragraph"), dict) and not props["opening_paragraph"].get("description"):
        props["opening_paragraph"]["description"] = "4-6 sentences. Frame the financial profile and the 2-3 key takeaways before the table-driven subsections."

    # Keep generic S10 lean: omit sector_specific_analysis block to avoid forcing
    # a generic filler subsection when no sector KPIs are meaningful.
    props.pop("sector_specific_analysis", None)

    return schema


def _get_section_11_schema(family: str) -> dict:
    """Build sector-specific S11 schema covering all 5 peer comparison dimensions:
    profitability, growth, leverage, efficiency, and returns."""
    schema = _clone_schema(SECTION_SCHEMAS[11])
    props = schema.get("properties", {})

    if family == "banking":
        props["profitability_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around NIM, pre-provision profitability, ROE/ROTCE, and credit costs.",
            "4-6 sentences. Explain how the bank's earnings mix and funding franchise compare with peers."
        )
        props["growth_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around loan/deposit growth, fee income growth, and balance-sheet expansion.",
            "4-6 sentences. Explain whether growth is organic or acquisition-driven and how it compares to peers."
        )
        props["leverage_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around CET1, Tier 1 leverage, and deposit funding stability vs peers.",
            "4-6 sentences. Explain whether capital adequacy is conservative and how funding mix compares."
        )
        props["efficiency_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around efficiency ratio, cost/income, and operating leverage vs peers.",
            "4-6 sentences. Explain what drives efficiency gaps and whether scale benefits are structural."
        )
        props["returns_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around ROTCE, ROA, and pre-provision return on assets vs banking peers.",
            "4-6 sentences. Explain what drives returns differences — franchise quality, credit discipline, or capital deployment."
        )
    elif family == "insurance":
        props["profitability_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around combined ratio, underwriting margin, and ROE.",
            "4-6 sentences. Explain how reserve discipline and investment income support peer positioning."
        )
        props["growth_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around premium growth, line-of-business mix, and market share trends.",
            "4-6 sentences. Explain growth drivers relative to peers and whether pricing discipline is maintained."
        )
        props["leverage_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around reserve adequacy, capital ratios, and catastrophe exposure vs peers.",
            "4-6 sentences. Explain whether capital strength supports growth without compromising solvency."
        )
        props["efficiency_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around expense ratio, claims management efficiency, and operational scale.",
            "4-6 sentences. Explain operational efficiency gaps and how they affect underwriting profitability."
        )
        props["returns_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around ROE, operating ROE, and book value growth vs insurance peers.",
            "4-6 sentences. Explain whether returns reflect underwriting discipline, investment quality, or capital management."
        )
    elif family == "reits":
        props["profitability_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around NOI margin, occupancy, and FFO/AFFO margins.",
            "4-6 sentences. Explain how operating quality and portfolio composition compare with peers."
        )
        props["growth_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around same-store NOI growth, acquisition pipeline, and development activity.",
            "4-6 sentences. Explain growth drivers relative to REIT peers and portfolio quality differences."
        )
        props["leverage_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around debt/EBITDA, maturity profile, and funding costs vs REIT peers.",
            "4-6 sentences. Explain whether leverage supports accretive growth without excessive risk."
        )
        props["efficiency_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around G&A as % of revenue, capital recycling, and platform operating efficiency.",
            "4-6 sentences. Explain how platform scale and management costs compare to peers."
        )
        props["returns_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around AFFO yield, return on invested capital, and capital recycling returns vs REIT peers.",
            "4-6 sentences. Explain whether returns justify the capital deployed and how development/acquisition returns compare."
        )
    elif family == "utilities":
        props["leverage_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around debt/rate base, interest coverage, and funding costs vs utility peers.",
            "4-6 sentences. Explain whether leverage is appropriate for the regulated earnings profile."
        )
        props["efficiency_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around O&M efficiency, allowed vs earned ROE, and regulatory execution.",
            "4-6 sentences. Explain what drives efficiency gaps among utility peers."
        )
        props["returns_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around earned ROE vs allowed ROE, rate base growth returns, and regulatory lag vs utility peers.",
            "4-6 sentences. Explain whether returns are attractive given the regulatory construct and capex runway."
        )
    elif family == "energy":
        props["leverage_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around net debt/EBITDA, balance-sheet resiliency at trough pricing, and self-funding capacity.",
            "4-6 sentences. Explain how leverage discipline compares across E&P and integrated peers."
        )
        props["efficiency_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around cost curve position, per-unit costs, and capital efficiency vs energy peers.",
            "4-6 sentences. Explain what drives operational efficiency gaps and implications for through-cycle margins."
        )
        props["returns_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around ROIC, return on capital employed, and FCF yield vs energy peers.",
            "4-6 sentences. Explain whether returns justify reinvestment, how capital discipline compares, and through-cycle return sustainability."
        )
    elif family == "technology":
        props["leverage_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around net cash/debt position, capital structure, and buyback capacity vs tech peers.",
            "4-6 sentences. Explain how balance-sheet strength supports or constrains the growth strategy."
        )
        props["efficiency_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around R&D efficiency, SBC burden, and rule-of-40 style metrics vs tech peers.",
            "4-6 sentences. Explain how R&D productivity and operational efficiency compare."
        )
        props["returns_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around ROIC, FCF conversion, and capital allocation (buybacks vs reinvestment) vs tech peers.",
            "4-6 sentences. Explain whether returns justify the R&D/SBC reinvestment and how FCF yield compares."
        )
    elif family == "healthcare":
        props["leverage_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around net debt/EBITDA, acquisition-related leverage, and interest coverage vs peers.",
            "4-6 sentences. Explain whether leverage supports pipeline investment and M&A strategy."
        )
        props["efficiency_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around R&D productivity, SG&A efficiency, and cash conversion vs healthcare peers.",
            "4-6 sentences. Explain operational efficiency gaps and their implications for profitability."
        )
        props["returns_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around ROIC, ROCE, and FCF conversion vs healthcare peers.",
            "4-6 sentences. Explain whether returns reflect pipeline productivity, pricing power, or capital discipline."
        )
    elif family == "industrials":
        props["leverage_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around net debt/EBITDA, interest coverage, and balance-sheet flexibility for M&A and downturns.",
            "4-6 sentences. Explain how leverage discipline compares across industrial peers."
        )
        props["efficiency_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around asset turnover, capex efficiency, and working capital management vs peers.",
            "4-6 sentences. Explain how operational efficiency drives through-cycle margin differences."
        )
        props["returns_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around ROIC, ROCE, and FCF conversion vs industrial peers.",
            "4-6 sentences. Explain whether returns reflect asset quality, through-cycle resilience, or M&A discipline."
        )
    elif family == "retail":
        props["leverage_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around D/E, lease-adjusted leverage, and balance-sheet flexibility vs retail peers.",
            "4-6 sentences. Explain how capital structure supports growth and shareholder returns."
        )
        props["efficiency_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around inventory turnover, store productivity, and working capital efficiency vs peers.",
            "4-6 sentences. Explain what drives operational efficiency gaps among retail peers."
        )
        props["returns_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around ROIC, return on store investment, and FCF conversion vs retail peers.",
            "4-6 sentences. Explain whether returns justify store expansion and how capital allocation compares."
        )
    elif family == "consumer_staples":
        props["leverage_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around D/E, acquisition leverage, and balance-sheet capacity for M&A vs staples peers.",
            "4-6 sentences. Explain whether leverage is appropriate for the defensive earnings profile."
        )
        props["efficiency_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around cash conversion, marketing/brand investment efficiency, and SG&A discipline vs peers.",
            "4-6 sentences. Explain how operational efficiency supports margin resilience."
        )
        props["returns_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around ROIC, FCF conversion, and dividend payout sustainability vs staples peers.",
            "4-6 sentences. Explain whether returns reflect brand strength, pricing power, or capital discipline."
        )
    elif family == "materials":
        props["leverage_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around net debt/EBITDA, balance-sheet resilience at trough pricing, and self-funding capacity.",
            "4-6 sentences. Explain how leverage discipline compares across materials peers."
        )
        props["efficiency_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around cost curve position, asset utilization, and capital efficiency vs materials peers.",
            "4-6 sentences. Explain what drives cost-structure advantages and implications for through-cycle margins."
        )
        props["returns_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around ROIC, return on capital employed, and FCF yield vs materials peers.",
            "4-6 sentences. Explain whether returns justify reinvestment and how through-cycle return stability compares."
        )
    elif family == "telecom":
        props["leverage_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around net debt/EBITDA, spectrum obligations, and funding costs vs telecom peers.",
            "4-6 sentences. Explain how leverage constrains or enables network investment and shareholder returns."
        )
        props["efficiency_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around network cost per subscriber, capex efficiency, and EBITDA margin vs peers.",
            "4-6 sentences. Explain what drives efficiency gaps and how scale benefits compare."
        )
        props["returns_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around ROIC, return on network investment, and FCF yield vs telecom peers.",
            "4-6 sentences. Explain whether returns justify the capex intensity and how capital allocation discipline compares."
        )
    elif family == "consumer_disc":
        props["leverage_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around D/E, lease-adjusted leverage, and cyclical resilience vs consumer disc peers.",
            "4-6 sentences. Explain whether leverage is appropriate for the demand cyclicality."
        )
        props["efficiency_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around inventory management, working capital efficiency, and asset turnover vs peers.",
            "4-6 sentences. Explain how operational efficiency compares and what drives the gaps."
        )
        props["returns_comparison"] = _table_wrapper(
            "3-5 sentences. Frame around ROIC, FCF conversion, and capital allocation effectiveness vs consumer disc peers.",
            "4-6 sentences. Explain whether returns justify growth investment and how through-cycle return stability compares."
        )
    return schema


def _get_section_10_title(family: str) -> str:
    return _S10_TITLES.get(family, "Financial Analysis")


def _get_section_11_title(family: str) -> str:
    return _S11_TITLES.get(family, "Peer Financial Benchmarking")


# ── RESEARCH AGENT 1 SECTOR-SPECIFIC GUIDANCE ───────────────────────
# Maps subsector → analysis focus text injected into research_agent_1.jinja2
# so Agent 1 produces sector-appropriate qualitative analysis.

_SECTOR_AGENT1_GUIDANCE: dict[str, str] = {
    "banking": (
        "Focus your analysis on: deposit franchise quality and stability, lending portfolio "
        "composition and credit quality, NIM drivers and interest rate sensitivity, fee income "
        "diversification, and capital adequacy (CET1/Tier 1 ratios). "
        "Do NOT discuss R&D spending or traditional product portfolios — banks don't have "
        "'products' in the traditional sense. Instead analyze: lending products (commercial, "
        "CRE, consumer, mortgage), deposit products, wealth management, investment banking, "
        "and treasury services. For customer analysis, focus on depositor base stability, "
        "borrower credit quality, and relationship banking depth rather than traditional "
        "customer retention metrics like churn or CAC/LTV."
    ),
    "insurance": (
        "Focus on: underwriting profitability and combined ratio trends, loss ratio and reserve "
        "adequacy, investment portfolio strategy and yield, distribution channel economics, "
        "and policyholder persistency. Replace traditional product/R&D discussion with insurance "
        "product lines (life, P&C, reinsurance, specialty) and underwriting approach. "
        "For customer analysis, focus on policyholder retention, lapse rates, claims experience, "
        "and distribution channel mix (agency vs. direct vs. broker) rather than traditional "
        "customer metrics."
    ),
    "reits": (
        "Focus on: property portfolio quality, location, and diversification; tenant mix and "
        "credit quality; lease structure (NNN, gross, modified gross) and built-in escalators; "
        "occupancy trends and renewal rates; development pipeline and acquisition strategy. "
        "Replace traditional product/R&D with property-level analysis. Use FFO/AFFO instead "
        "of EPS/FCF as primary profitability measures. For customer analysis, focus on tenant "
        "industry diversification, weighted average lease term (WALT), same-store NOI growth, "
        "and rent dynamics rather than traditional customer retention metrics."
    ),
    "energy": (
        "Focus on: reserve base quality (proved/probable), production economics (per-BOE/MCF "
        "costs), upstream/downstream balance, commodity exposure and hedging strategy, breakeven "
        "economics, and energy transition positioning. Replace R&D discussion with exploration "
        "and development spending analysis. For customer analysis, focus on offtake agreements, "
        "contract structure (take-or-pay), counterparty credit quality, and commodity customer "
        "mix rather than traditional customer retention metrics."
    ),
    "utilities": (
        "Focus on: generation portfolio and fuel mix (nuclear, gas, renewables), rate base growth "
        "and regulatory recovery mechanisms, regulatory relationships and rate case outcomes, "
        "T&D infrastructure and grid modernization, clean energy transition progress. "
        "Do NOT discuss R&D — instead discuss authorized capex, rate case outcomes, and "
        "regulatory lag. For customer analysis, focus on ratepayer base composition "
        "(residential/commercial/industrial), regulatory commission dynamics, rate case history, "
        "load growth trends, and electrification impacts rather than traditional customer metrics."
    ),
}


def _get_sector_agent1_guidance(subsector: str) -> str:
    """Return sector-specific analysis guidance for Research Agent 1."""
    return _SECTOR_AGENT1_GUIDANCE.get(subsector, "")


# --- S8: Management & Capital Allocation ---
SECTION_SCHEMAS[8] = {
    "type": "object",
    "properties": {
        "section_number": {"type": "integer", "enum": [8]},
        "section_thesis": {"type": "string"},
        "opening_paragraph": {"type": "string"},
        "leadership_team": {"type": "array", "items": _LABELED_BLOCK},
        "execution_track_record": {
            "type": "object",
            "properties": {
                "guidance_accuracy": {"type": "string", "description": "4-6 sentences. Track record of meeting/beating guidance, specific examples."},
                "strategic_execution": {"type": "string", "description": "4-6 sentences. Major strategic initiatives and their execution outcomes."},
            },
            "required": ["guidance_accuracy", "strategic_execution"],
        },
        "capital_allocation": _table_wrapper(
            "3-5 sentences introducing the 3-year capital allocation pattern with specific figures.",
            "4-6 sentences. ROIC trend, R&D effectiveness, M&A track record, buyback quality, TSR decomposition.",
        ),
        "board_governance": {"type": "string", "description": "4-6 sentences. Board composition, independence, expertise, and governance quality."},
        "overall_assessment": {"type": "string", "description": "4-6 sentences. Synthesize management quality with specific strengths and concerns."},
    },
    "required": ["section_number", "section_thesis", "opening_paragraph", "leadership_team",
                  "execution_track_record", "capital_allocation",
                  "board_governance", "overall_assessment"],
    "additionalProperties": False,
}

# --- S9: Growth Prospects ---
SECTION_SCHEMAS[9] = {
    "type": "object",
    "properties": {
        "section_number": {"type": "integer", "enum": [9]},
        "section_thesis": {"type": "string"},
        "opening_paragraph": {"type": "string"},
        "market_opportunity": {
            "type": "object",
            "properties": {
                "tam_and_penetration": {"type": "string", "description": "4-6 sentences. TAM size, current penetration rate, and expansion potential with specific figures."},
                "demand_drivers": {"type": "string", "description": "4-6 sentences. Key demand drivers, secular trends, and cyclical factors."},
            },
            "required": ["tam_and_penetration", "demand_drivers"],
        },
        "near_term_catalysts": {"type": "array", "items": _LABELED_BLOCK, "description": "3-4 near-term catalysts with detailed analysis."},
        "medium_term_drivers": {"type": "array", "items": _LABELED_BLOCK, "description": "3-4 medium-term growth drivers with detailed analysis."},
        "long_term_position": {
            "type": "object",
            "properties": {
                "runway": {"type": "string", "description": "4-6 sentences. Long-term growth runway, end-market maturity, and optionality."},
                "at_scale": {"type": "string", "description": "4-6 sentences. What the business looks like at scale, margin structure, and competitive positioning."},
            },
            "required": ["runway", "at_scale"],
        },
        "margin_evolution": {"type": "string", "description": "4-6 sentences. Expected margin trajectory, operating leverage, and mix shift impact."},
        "growth_score": {"type": "integer", "description": (
            "Growth quality 0-100. Calibrate: 85-100 = double-digit organic revenue CAGR with "
            "stable or expanding margins and a quantified runway; 65-84 = high-single-digit "
            "durable growth, or faster but visibly decelerating; 40-64 = GDP-plus or mid-cycle "
            "cyclical growth; 20-39 = flat-to-low growth with mix headwinds; 0-19 = structural "
            "decline. Score the trajectory shown in the data, not the company narrative."
        )},
    },
    "required": ["section_number", "section_thesis", "opening_paragraph", "market_opportunity",
                  "near_term_catalysts", "medium_term_drivers", "long_term_position",
                  "margin_evolution", "growth_score"],
    "additionalProperties": False,
}

# --- S10: Financial Analysis ---
SECTION_SCHEMAS[10] = {
    "type": "object",
    "properties": {
        "section_number": {"type": "integer", "enum": [10]},
        "section_thesis": {"type": "string"},
        "opening_paragraph": {"type": "string"},
        "revenue_growth": _table_wrapper(
            "3-5 sentences. 5yr CAGR, acceleration/deceleration, primary drivers, organic vs inorganic growth, and revenue mix evolution.",
            "3-5 sentences. Key insight about revenue quality, sustainability of growth rate, comparison to peers and industry growth, and forward trajectory.",
        ),
        "margins": _table_wrapper(
            "3-5 sentences. Gross margin, operating margin, expansion/contraction trend, operating leverage, and mix effects.",
            "3-5 sentences. Key margin trend, sustainability, peer comparison, and what drives margin variability.",
        ),
        "cash_flow": _table_wrapper(
            "3-5 sentences. FCF level and margin, conversion quality, capex intensity, and working capital dynamics.",
            "3-5 sentences. Earnings quality assessment, capex trend, FCF yield, and capital allocation implications.",
        ),
        "returns_capital_efficiency": _table_wrapper(
            "3-5 sentences. ROIC level and trend, comparison to WACC, incremental returns, and peer benchmarking.",
            "3-5 sentences. Value creation assessment, ROE decomposition, moat connection, and capital efficiency trajectory.",
        ),
        "leverage_balance_sheet": {
            "type": ["object", "null"],
            "properties": {"intro": {"type": "string"}, "analysis": {"type": "string"}},
            "description": "CONDITIONAL: include only if Net Debt/EBITDA > 1.0x.",
        },
        "sector_specific_analysis": {
            "type": ["object", "null"],
            "properties": {
                "intro": {"type": "string"},
                "analysis": {"type": "string"},
            },
            "description": (
                "CONDITIONAL but STRONGLY RECOMMENDED: Include this subsection when "
                "sector_analysis_guidance is provided in the facts. For banks: discuss "
                "NIM trend, efficiency ratio, CET1 adequacy, credit quality (NPL/NCO), "
                "and loan-to-deposit ratio using the specific numbers from sector_kpis. "
                "For REITs: discuss FFO/AFFO, occupancy, NOI growth. For insurance: "
                "combined ratio, loss ratio. For energy: production, reserves. "
                "3-5 sentences. Reference actual metric values."
            ),
        },
        "financial_quality_flags": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {
                        "type": "string",
                        "description": (
                            "Short flag name matching a pre-computed flag "
                            "(e.g. 'Net Debt Spike', 'ROIC Decline', "
                            "'Earnings Quality Gap', 'FCF Conversion Volatility')."
                        ),
                    },
                    "paragraph": {
                        "type": "string",
                        "description": (
                            "4-8 sentences. Diagnose the flag: (1) cite the specific "
                            "numbers that triggered it, (2) explain the cause from "
                            "business context/filings, (3) compare to peers where "
                            "relevant, (4) state what it signals for the investment case."
                        ),
                    },
                },
                "required": ["label", "paragraph"],
            },
            "description": (
                "3-7 labeled blocks. Pre-computed flags are provided in "
                "facts.s10_financial_flags — diagnose each triggered flag. "
                "Only include TRIGGERED flags — skip clean metrics. "
                "If no flags triggered, write 1-2 blocks on the cleanest quality signals."
            ),
        },
        "synthesis": {"type": "string"},
        "quality_score": {"type": "integer", "description": (
            "Financial quality 0-100. Calibrate: 85-100 = returns on capital well above cost of "
            "capital with high FCF conversion and conservative leverage; 65-84 = solid returns "
            "with one blemish (leverage, conversion, or volatility); 40-64 = average returns or "
            "inconsistent FCF; 20-39 = returns below cost of capital or a strained balance "
            "sheet; 0-19 = value-destructive economics. Anchor to the computed ratios provided."
        )},
    },
    # NOTE: "required" is intentionally omitted here. The to_openai()
    # converter auto-generates it from properties.keys(), which is
    # essential because _get_section_10_schema() replaces properties
    # with sector-specific keys.
    "additionalProperties": False,
}

# --- S11: Peer Financial Benchmarking ---
SECTION_SCHEMAS[11] = {
    "type": "object",
    "properties": {
        "section_number": {"type": "integer", "enum": [11]},
        "section_thesis": {"type": "string"},
        "opening_paragraph": {"type": "string"},
        "profitability_comparison": _table_wrapper(
            "3-5 sentences. Frame the profitability comparison with context on why these metrics matter for this industry.",
            "4-6 sentences. Subject margin profile vs peers, specific gaps, what drives differences, and implications for competitive positioning.",
        ),
        "growth_comparison": _table_wrapper(
            "3-5 sentences. Frame the growth comparison with context on industry growth rates and cycle position.",
            "4-6 sentences. Subject growth vs peers, market share trends, organic vs inorganic drivers, and sustainability assessment.",
        ),
        "leverage_comparison": _table_wrapper(
            "3-5 sentences. Frame the leverage comparison — D/E, net debt/EBITDA, interest coverage — and why balance-sheet strength matters for this industry.",
            "4-6 sentences. Explain how leverage compares to peers, whether it constrains or enables growth, and implications for risk.",
        ),
        "efficiency_comparison": _table_wrapper(
            "3-5 sentences. Frame the efficiency comparison — asset turnover, capex efficiency, working capital, or operating efficiency — for this industry.",
            "4-6 sentences. Explain how operational efficiency compares to peers and what drives the gaps.",
        ),
        "returns_comparison": _table_wrapper(
            "3-5 sentences. Frame the returns comparison — ROIC, ROCE, FCF conversion, and capital allocation effectiveness — for this industry.",
            "4-6 sentences. Explain how returns on invested capital compare to peers, whether capital allocation is disciplined, and what drives the gaps.",
        ),
        "synthesis": {"type": "string"},
    },
    "required": ["section_number", "section_thesis", "opening_paragraph",
                  "profitability_comparison", "growth_comparison",
                  "leverage_comparison", "efficiency_comparison",
                  "returns_comparison", "synthesis"],
    "additionalProperties": False,
}

# --- S13: Risk Assessment ---
SECTION_SCHEMAS[13] = {
    "type": "object",
    "properties": {
        "section_number": {"type": "integer", "enum": [13]},
        "section_thesis": {"type": "string"},
        "opening_paragraph": {"type": "string"},
        "regulatory_structural": {"type": ["string", "null"], "description": "4-6 sentences. Regulatory environment and structural risks specific to this company."},
        "key_risks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "transmission": {"type": "string", "description": "4-6 sentences. How this risk transmits to financials, with specific scenario analysis and magnitude."},
                    "probability_and_monitoring": {"type": "string", "description": "4-6 sentences. Probability assessment and key metrics to monitor."},
                },
                "required": ["label", "transmission", "probability_and_monitoring"],
            },
        },
        "bear_case_triggers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "trigger": {"type": "string"},
                    "probability": {"type": "string"},
                    "monitoring_metric": {"type": "string"},
                },
                "required": ["trigger", "probability", "monitoring_metric"],
            },
        },
        "sensitivity_assumptions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "assumption": {"type": "string"},
                    "downside_scenario": {"type": "string"},
                    "monitoring_metric": {"type": "string"},
                },
                "required": ["assumption", "downside_scenario", "monitoring_metric"],
            },
        },
        "litigation": {"type": ["string", "null"]},
        "closing_paragraph": {"type": "string"},
        "mandatory_final_sentence": {"type": "string"},
    },
    "required": ["section_number", "section_thesis", "opening_paragraph",
                  "key_risks", "bear_case_triggers", "sensitivity_assumptions",
                  "closing_paragraph", "mandatory_final_sentence"],
    "additionalProperties": False,
}


# --- S12 schemas are mode-dependent (built at runtime) ---
# --- S1 and S14 schemas are also mode-dependent ---
# See _build_s12_schema(), _build_s1_schema(), _build_s14_schema() below.


def _dcf_scenario_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "revenue_cagr":        {"type": "number", "description": "Revenue CAGR as decimal. Projection horizon is dynamic: >20% CAGR → 10yr, 10-20% → 7yr, <10% → 5yr. For the BASE case, anchor to the company's trailing 3yr CAGR adjusted for maturation. Do NOT arbitrarily compress growth."},
            "terminal_op_margin":  {"type": "number", "description": "Terminal operating margin as decimal."},
            "terminal_growth":     {"type": "number", "description": "Terminal perpetuity growth rate as decimal. Typical range: 2.5-3% for mature companies (anchored to nominal GDP growth ~4-5% minus 1-2% real fade), 3-4% for companies with durable pricing power or secular tailwinds. Below 2% only if facing structural decline. Most quality tech/software companies warrant at least 2.5%."},
            "wacc":                {"type": "number", "description": "WACC as decimal. Anchor to: risk-free rate (~4.2% US 10Y) + equity risk premium (4-5%) × beta. For high-quality software/tech, typical range is 8-10%. For stable large-caps, 7-9%. Do not reflexively use 10%+ unless justified by high beta or business risk."},
            "annual_dilution_pct": {"type": "number", "description": "Annual share dilution as decimal."},
            "probability_pct":     {"type": "number", "description": "Probability weight as integer (e.g. 50 for 50%). Bull+Base+Bear MUST sum to 100."},
            "narrative":           {"type": "string", "description": "3-5 sentences: what real-world conditions produce this scenario. Link to OPERATIONAL DRIVERS (capacity additions, subscriber growth, market share %) not just abstract financial metrics. Reference the operational_drivers from qualitative research if available."},
            "key_triggers":        {"type": "array", "items": {"type": "string"}, "description": "2-3 observable, date-anchored triggers that would signal this scenario is playing out."},
            "scenario_risk":       {"type": "string", "description": "1-2 sentences: primary risk specific to THIS scenario."},
        },
        "required": ["revenue_cagr", "terminal_op_margin", "terminal_growth", "wacc",
                      "annual_dilution_pct", "probability_pct", "narrative", "key_triggers", "scenario_risk"],
    }


def _scenario_case_schema(valuation_mode: str = "industry_peer",
                          metric_label: str = "") -> dict:
    """Schema for a single bull/base/bear scenario in non-DCF valuation modes."""
    if valuation_mode == "bank_equity":
        implied_desc = (
            "SHORT STRING. EXACT FORMAT: 'Justified P/B of X.Xx → $YYY/share'. "
            "CORRECT EXAMPLES: 'Justified P/B of 1.6x → $215/share', 'Justified P/B of 2.1x → $276/share'. "
            "WRONG: 'Justified P/B of 2.8x 106 $365/share' — missing arrow, has stray numbers. "
            "WRONG: 'Justified P/B of 2.21x 1x 1 1 x 1 1 79.88 /share' — garbled. "
            "ONLY output: the P/B multiple, then →, then the dollar price. Nothing else."
        )
    elif valuation_mode == "ddm":
        implied_desc = (
            "SHORT STRING. EXACT FORMAT: 'DDM: X% dividend growth, Y% CoE → $ZZ/share'. "
            "CORRECT EXAMPLE: 'DDM: 6% dividend growth, 8.5% CoE → $72/share'. "
            "ONLY output: the growth rate, cost of equity, then →, then the dollar price. Nothing else."
        )
    else:
        _lbl = metric_label or "[Metric]"
        _examples = (f"'{_lbl} of 15x → $52/share'" if metric_label
                     else "'P/E of 15x → $52/share', 'EV/EBITDA of 10x → $85/share'")
        implied_desc = (
            f"SHORT STRING. EXACT FORMAT: '{_lbl} of X.Xx → $YY/share'. "
            f"CORRECT EXAMPLES: {_examples}. "
            "ONLY output: the metric and multiple, then →, then the dollar price. Nothing else."
        )
    return {
        "type": "object",
        "properties": {
            "narrative": {
                "type": "string",
                "description": (
                    "3-5 sentences describing what real-world conditions produce this scenario. "
                    "Reference OPERATIONAL DRIVERS (capacity additions, loan growth, subscriber adds) "
                    "not just abstract financial metrics."
                ),
            },
            "implied_multiple": {
                "type": "string",
                "description": implied_desc,
            },
            "probability_pct": {
                "type": "number",
                "description": "Probability weight as integer (e.g. 50 for 50%). Bull+Base+Bear MUST sum to 100.",
            },
            "key_triggers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "2-3 observable triggers that would signal this scenario is playing out.",
            },
            "scenario_risk": {
                "type": "string",
                "description": "1-2 sentences: primary risk specific to THIS scenario.",
            },
        },
        "required": ["narrative", "implied_multiple", "probability_pct", "key_triggers", "scenario_risk"],
    }


def _build_s12_schema(valuation_mode: str, industry: str = "",
                      alt_config: dict | None = None) -> dict:
    _metric_label = (alt_config or {}).get("metric_label", "")
    _sc = _scenario_case_schema(valuation_mode, metric_label=_metric_label)
    _scenario_analysis = {
        "type": "object",
        "properties": {
            "bull_case": _sc,
            "base_case": _sc,
            "bear_case": _sc,
        },
        "required": ["bull_case", "base_case", "bear_case"],
        "description": (
            "Bull/Base/Bear scenario analysis. Each scenario should reference "
            "operational drivers and catalysts. Probability weights MUST sum to 100."
        ),
    }

    _fv_conclusion = {
        "type": "object",
        "properties": {
            "valuation_synthesis": {
                "type": "string",
                "description": (
                    "4-6 sentences synthesizing the valuation case. DO NOT state specific "
                    "dollar per-share fair values — the valuation engine computes exact "
                    "values from your scenario assumptions. Focus on: (1) which valuation "
                    "approach is most reliable and why, (2) what drives the range between "
                    "scenarios, (3) key assumptions creating the most sensitivity, "
                    "(4) what operational outcomes would shift fair value materially. "
                    "DO NOT make buy/sell recommendations, state margin of safety, or "
                    "compare the current price to fair value. The memo presents the "
                    "mechanical model output and lets readers draw their own conclusions."
                ),
            },
        },
        "required": ["valuation_synthesis"],
    }

    if valuation_mode == "bank_equity":
        return {
            "type": "object",
            "properties": {
                "section_number": {"type": "integer", "enum": [12]},
                "section_thesis": {"type": "string"},
                "opening_paragraph": {"type": "string"},
                "dcf_not_applicable": {
                    "type": "string",
                    "description": (
                        "3-5 sentences. Explain why standard FCFF DCF is not applicable for this "
                        "financial institution and why the bank equity model (justified P/B, excess "
                        "return on equity) is preferred. Reference the regulatory capital framework."
                    ),
                },
                "scenario_analysis": _scenario_analysis,
                "peer_valuation": _table_wrapper(
                    "3-4 sentences. Introduce the peer comparison table as a CROSS-CHECK to the "
                    "bank equity model above. Justify why P/E, P/B, and P/TBV multiples are the "
                    "key cross-check metrics for this financial sector company.",
                    "4-6 sentences. Analyze where the subject trades versus peers on P/E, P/B, and "
                    "ROE. Discuss whether premium/discount is justified by profitability and capital adequacy.",
                ),
                "fair_value_conclusion": _fv_conclusion,
            },
            "required": ["section_number", "section_thesis", "opening_paragraph",
                          "dcf_not_applicable", "scenario_analysis", "peer_valuation",
                          "fair_value_conclusion"],
            "additionalProperties": False,
        }
    elif valuation_mode == "ddm":
        return {
            "type": "object",
            "properties": {
                "section_number": {"type": "integer", "enum": [12]},
                "section_thesis": {"type": "string"},
                "opening_paragraph": {"type": "string"},
                "dcf_limitations": {
                    "type": "string",
                    "description": (
                        "3-5 sentences. Explain why standard FCFF DCF is unreliable for this regulated "
                        "utility (project-level financing, tax-equity monetization, lumpy development "
                        "cash flows). Explain why a Dividend Discount Model (DDM) is the primary "
                        "valuation approach — stable regulated earnings, high payout ratios, and "
                        "predictable dividend growth make DDM the most appropriate model."
                    ),
                },
                "scenario_analysis": _scenario_analysis,
                "peer_valuation": _table_wrapper(
                    "3-4 sentences. Introduce the peer multiple comparison as a CROSS-CHECK to "
                    "the DDM above. Explain why P/E is a useful secondary metric for regulated utilities.",
                    "4-6 sentences. Analyze where the subject trades versus peers. Discuss whether "
                    "any premium/discount is justified by growth, regulatory environment, and dividend yield.",
                ),
                "fair_value_conclusion": _fv_conclusion,
            },
            "required": ["section_number", "section_thesis", "opening_paragraph",
                          "dcf_limitations", "scenario_analysis", "peer_valuation",
                          "fair_value_conclusion"],
            "additionalProperties": False,
        }
    elif valuation_mode == "industry_peer":
        _method = (alt_config or {}).get("method", "ev_ebitda")
        _method_upper = _metric_label or _method.replace("_", "/").upper()
        _is_peer_primary = industry in _PEER_PRIMARY_INDUSTRIES

        if _is_peer_primary:
            # Peer comp is the PRIMARY model — it comes before scenarios
            _dcf_desc = (
                f"3-5 sentences. Explain why standard FCFF DCF is not the primary model for this "
                f"sector and why {_method_upper} peer multiples are preferred. "
                f"For REITs, reference GAAP depreciation on long-lived assets depressing "
                f"FCFF and book value. For energy/mining, reference commodity cyclicality."
            )
            _peer_intro = (
                f"3-4 sentences. Why {_method_upper} is the PRIMARY valuation metric for this "
                f"company and sector. Explain what makes this multiple the most appropriate measure "
                f"of value (e.g., for REITs: GAAP depreciation understates true profitability, "
                f"FFO better captures cash-generating ability)."
            )
            _peer_analysis = (
                f"4-6 sentences. Analyze where the subject trades versus peers on {_method_upper}. "
                f"Discuss whether the premium/discount is justified by growth, asset quality, "
                f"balance sheet strength, or management quality."
            )
            return {
                "type": "object",
                "properties": {
                    "section_number": {"type": "integer", "enum": [12]},
                    "section_thesis": {"type": "string"},
                    "opening_paragraph": {"type": "string"},
                    "dcf_limitations": {"type": "string", "description": _dcf_desc},
                    "peer_valuation": _table_wrapper(_peer_intro, _peer_analysis),
                    "scenario_analysis": _scenario_analysis,
                    "fair_value_conclusion": _fv_conclusion,
                },
                "required": ["section_number", "section_thesis", "opening_paragraph",
                              "dcf_limitations", "peer_valuation", "scenario_analysis",
                              "fair_value_conclusion"],
                "additionalProperties": False,
            }
        else:
            # Scenarios first, peer as cross-check (current behavior)
            return {
                "type": "object",
                "properties": {
                    "section_number": {"type": "integer", "enum": [12]},
                    "section_thesis": {"type": "string"},
                    "opening_paragraph": {"type": "string"},
                    "dcf_limitations": {"type": "string"},
                    "scenario_analysis": _scenario_analysis,
                    "peer_valuation": _table_wrapper(
                        f"3-4 sentences. Why {_method_upper} is the primary cross-check metric.",
                        "4-6 sentences. Where subject trades vs peers.",
                    ),
                    "fair_value_conclusion": _fv_conclusion,
                },
                "required": ["section_number", "section_thesis", "opening_paragraph",
                              "dcf_limitations", "scenario_analysis", "peer_valuation",
                              "fair_value_conclusion"],
                "additionalProperties": False,
            }
    else:  # dcf
        dcf_sc = _dcf_scenario_schema()
        return {
            "type": "object",
            "properties": {
                "section_number": {"type": "integer", "enum": [12]},
                "section_thesis": {"type": "string"},
                "opening_paragraph": {"type": "string"},
                "dcf_analysis": {
                    "type": "object",
                    "properties": {
                        "intro": {"type": "string"},
                        "dcf_table": {
                            "type": "object",
                            "properties": {"bull": dcf_sc, "base": dcf_sc, "bear": dcf_sc},
                            "required": ["bull", "base", "bear"],
                        },
                        "assumption_blocks": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {"label": {"type": "string"}, "paragraph": {"type": "string"}},
                                "required": ["label", "paragraph"],
                            },
                        },
                        "closing": {"type": "string"},
                    },
                    "required": ["intro", "dcf_table", "assumption_blocks", "closing"],
                },
                "sensitivity_analysis": {
                    "type": "object",
                    "properties": {
                        "intro": {"type": "string"},
                        "sensitivity_table": {
                            "type": "object",
                            "properties": {
                                "wacc_values": {"type": "array", "items": {"type": "number"}},
                                "growth_values": {"type": "array", "items": {"type": "number"}},
                                "matrix": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
                            },
                            "required": ["wacc_values", "growth_values", "matrix"],
                        },
                        "analysis": {"type": "string"},
                    },
                    "required": ["intro", "sensitivity_table", "analysis"],
                },
                "peer_valuation": _table_wrapper(
                    "3-4 sentences. Peer selection rationale.",
                    "4-6 sentences. Premium/discount, implied value.",
                ),
                "fair_value_conclusion": {
                    "type": "object",
                    "properties": {
                        "valuation_synthesis": {
                            "type": "string",
                            "description": (
                                "4-6 sentences synthesizing the valuation case. DO NOT state specific "
                                "dollar per-share fair values — the DCF engine computes exact per-share "
                                "values from your scenario assumptions above. Focus on: (1) which "
                                "valuation approach is most reliable and why, (2) what drives the range "
                                "between scenarios, (3) key assumptions creating the most sensitivity, "
                                "(4) what operational outcomes would shift fair value materially. "
                                "DO NOT make buy/sell recommendations, state margin of safety, or "
                                "compare the current price to fair value. The memo presents the "
                                "mechanical model output and lets readers draw their own conclusions."
                            ),
                        },
                    },
                    "required": ["valuation_synthesis"],
                },
            },
            "required": ["section_number", "section_thesis", "opening_paragraph",
                          "dcf_analysis", "sensitivity_analysis", "peer_valuation",
                          "fair_value_conclusion"],
            "additionalProperties": False,
        }


def _build_s1_schema(valuation_mode: str, industry: str = "",
                     alt_config: dict | None = None) -> dict:
    if valuation_mode == "bank_equity":
        val_desc = (
            "4-5 sentences. Reference the bank equity (justified P/B) valuation approach "
            "from Section 12 and the key assumptions driving the scenario range. "
            "DO NOT make buy/sell recommendations, state margin of safety, or compare "
            "the current price to fair value. Present the model mechanics only."
        )
    elif valuation_mode == "ddm":
        val_desc = (
            "4-5 sentences. Reference the DDM (dividend discount model) approach from "
            "Section 12 and the key assumptions driving the scenario range. "
            "DO NOT make buy/sell recommendations, state margin of safety, or compare "
            "the current price to fair value. Present the model mechanics only."
        )
    elif valuation_mode == "industry_peer":
        _metric_label = (alt_config or {}).get("metric_label", "")
        method_label = _metric_label or ("EV/EBITDA" if (alt_config or {}).get("method") == "ev_ebitda" else "P/E")
        val_desc = (
            f"4-5 sentences. Reference the {method_label} peer valuation approach from "
            "Section 12 and the key assumptions driving the scenario range. "
            "DO NOT make buy/sell recommendations, state margin of safety, or compare "
            "the current price to fair value. Present the model mechanics only."
        )
    else:
        val_desc = (
            "4-5 sentences. Describe the DCF valuation approach used in Section 12 and "
            "the key assumptions that drive the scenario range (bull/base/bear). "
            "DO NOT state specific dollar fair values — the engine computes exact figures. "
            "DO NOT make buy/sell recommendations, state margin of safety, or compare "
            "the current price to fair value. Present the model mechanics only."
        )

    return {
        "type": "object",
        "properties": {
            "section_number": {"type": "integer", "enum": [1]},
            "section_thesis": {"type": "string"},
            "the_company": {"type": "string"},
            "investment_thesis": {
                "type": "string",
                "description": (
                    "3-5 sentences. Summarize the core investment thesis: what makes this "
                    "business durable, what drives value creation, and what key conditions "
                    "must hold. DO NOT make buy/sell recommendations or give financial advice."
                ),
            },
            "growth_in_words": {"type": "string"},
            "valuation_and_fair_value": {"type": "string", "description": val_desc},
            "primary_risk": {"type": "string"},
            "verdict": {
                "type": "string",
                "description": (
                    "3-5 sentences. Summarize the company's overall quality — moat strength, "
                    "financial profile, management execution, and key risks to monitor. "
                    "DO NOT make buy/sell/hold recommendations, state margin of safety, or "
                    "advise readers on whether to own the stock. Present an objective "
                    "quality assessment only."
                ),
            },
            "company_description": {"type": "string"},
        },
        "required": ["section_number", "section_thesis", "the_company", "investment_thesis",
                      "growth_in_words", "valuation_and_fair_value", "primary_risk",
                      "verdict", "company_description"],
        "additionalProperties": False,
    }


def _build_s14_schema(valuation_mode: str, industry: str = "",
                      alt_config: dict | None = None) -> dict:
    verdict_desc = (
        "4-5 sentences. Summarize the overall quality assessment: moat durability, "
        "financial strength, management execution, and key conditions to monitor. "
        "DO NOT make buy/sell/hold recommendations, state margin of safety, "
        "compare the current price to fair value, or give any investment advice. "
        "Present an objective summary of business quality and the key thesis risks."
    )

    return {
        "type": "object",
        "properties": {
            "section_number": {"type": "integer", "enum": [14]},
            "section_thesis": {"type": "string"},
            "the_verdict": {"type": "string", "description": verdict_desc},
            "what_must_be_true": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "condition": {
                            "type": "string",
                            "description": (
                                "A specific, falsifiable condition. "
                                "GOOD: 'Azure consumption growth must sustain >25% YoY through FY2027'. "
                                "BAD: 'Cloud must keep growing'. "
                                "Be company-specific and quantitative."
                            ),
                        },
                        "rationale": {
                            "type": "string",
                            "description": "1-2 sentences: why this condition matters for the thesis.",
                        },
                        "monitoring_metric": {
                            "type": "string",
                            "description": "The observable metric to track (e.g., 'Azure revenue growth in quarterly earnings').",
                        },
                        "timeframe": {
                            "type": "string",
                            "description": "When this must hold (e.g., 'Through FY2027', 'Next 12 months').",
                        },
                    },
                    "required": ["condition", "rationale", "monitoring_metric"],
                },
                "minItems": 3,
                "maxItems": 5,
                "description": (
                    "3-5 structured conviction items. Each must be COMPANY-SPECIFIC and "
                    "FALSIFIABLE — an analyst should be able to monitor each condition and "
                    "know when the thesis breaks."
                ),
            },
            "the_primary_risk": {
                "type": "string",
                "description": (
                    "3-5 sentences. Identify the single biggest risk to the thesis and "
                    "how it would affect the business fundamentally. DO NOT frame as "
                    "investment advice — describe the operational/financial impact only."
                ),
            },
            "final_statement": {
                "type": "string",
                "description": (
                    "2-4 sentences. Closing summary of the company's quality and key "
                    "monitoring conditions. DO NOT make buy/sell/hold recommendations "
                    "or give investment advice. End with the conditions that would cause "
                    "a reassessment of the thesis."
                ),
            },
        },
        "required": ["section_number", "section_thesis", "the_verdict",
                      "what_must_be_true", "the_primary_risk", "final_statement"],
        "additionalProperties": False,
    }


# ═══════════════════════════════════════════════════════════════
# 7. SECTION TEMPLATES
# ═══════════════════════════════════════════════════════════════

GLOBAL_RULES = """
WRITING RULES -- READ BEFORE EVERY SECTION

YOU ARE A WRITER, NOT A FORMATTER.
Your job is to write rich, detailed analytical prose. You never write markdown.
No bold, no italic, no bullet points, no numbered lists, no headers.
The formatter handles all visual structure from the schema shape.

LENGTH & DEPTH GUIDANCE
- HARD CAP: The full memo must be 15,000-18,000 words. Do NOT exceed 18,000 words.
- Your section has a word target. STAY WITHIN IT. This is a binding constraint, not a suggestion.
- Short 1-2 sentence paragraphs are UNACCEPTABLE. Each paragraph should be 4-6 sentences.
- Be concise but substantive. Prioritize insight density over word count. Cut filler and repetition.
- Use concrete data, comparisons, and forward-looking analysis. Every sentence must earn its place.

NARRATIVE DISCIPLINE
- Before writing any paragraph, first fill section_thesis.
- Opening paragraph: state the thesis/verdict with supporting evidence.
- Body paragraphs: build the evidence with multiple data points per paragraph.
- Synthesis paragraph (where present): resolve with clear conclusions.

REPETITION AVOIDANCE -- CRITICAL
- This memo has 15 sections. Each headline metric (revenue, gross margin, Services revenue, etc.)
  should be cited in FULL at most ONCE across the memo, in the section where it is most relevant.
- If you need to reference a metric covered elsewhere, use a SHORT reference (e.g., "the 46.9% gross
  margin discussed in Section 7") rather than re-stating the full context.
- Each section must add NEW analytical depth, not re-summarize the same metrics with different wording.
- Focus on metrics UNIQUE to your section's topic. Do not repeat the company description boilerplate.

PARAGRAPH RULES
- Target 4-6 sentences for most fields. Maximum 8 sentences for major synthesis fields.
- Every sentence must contain a fact AND connect logically.
- Never write transition filler or restate what was already said in another field.
- Never begin two consecutive sentences with "The company."
- Paragraphs should flow as cohesive analytical arguments, not disconnected observations.
- DO NOT pad paragraphs to hit a word count. If 4 sentences cover the point, stop.

NUMBER FORMAT RULES -- MANDATORY
All financial figures are pre-formatted -- USE THEM AS-IS.
  Dollar amounts: $X.XXB / $XXXM   Percentages: X.X%
  Multiples: X.Xx   Days: plain number
If you cite a number from the facts, copy the exact formatted value.
NEVER write "$394,328 million" -- write "$394.33M" or "$X.XXB".
NEVER wrap numbers in quotation marks. Write 33.8% not "33.8%". Write 22.4x not "22.4x".
Numbers appear as PLAIN TEXT in prose -- no quotes, no backticks.

LABEL + PARAGRAPH FIELDS
- {label, paragraph}: label is 2-6 words. paragraph is 4-6 detailed analytical sentences.

TABLE WRAPPER FIELDS
- {intro, analysis}: Tables are auto-injected by formatter. Write rich prose AROUND the tables.

NULL HANDLING
- If a data field is null, missing, or zero, skip it entirely.
- Never write "data not available."

NM (NOT MEANINGFUL) HANDLING
- Do NOT narrate "NM" values. Skip them in prose.

SCORE DISCIPLINE
- Fill score fields as integers. NEVER mention scores in prose.

CITATION RULES
- Maximum 2-3 citation tags per paragraph.
- [F] for financial data (subject company financials AND peer comparison data): cite ONCE per paragraph.
- Peer benchmark data (profitability, valuation multiples, medians) is also sourced from [F].

ANTI-HALLUCINATION RULES -- CRITICAL
- NEVER invent, estimate, or compute financial figures not in the provided data.
- NEVER present a valuation multiple (P/E, P/CF, EV/EBITDA ratio) as a per-share fair value.
  A multiple is a RATIO (e.g., "15.2x"). A fair value is a DOLLAR AMOUNT (e.g., "$150/share").
- NEVER sum individual segment or geographic figures to create your own total.
  Use the provided consolidated revenue or precomputed totals only.
- If data appears inconsistent across different breakdowns, note the discrepancy explicitly.
  Do NOT silently reconcile or invent corrections.
- When citing financial quality flag data, use EXACTLY the numbers from the pre-computed flag detail.
  Do NOT substitute related but different metrics.
- GAAP vs NON-GAAP: If a financial quality flag indicates margin distortion from one-time items,
  always note the EBITDA margin alongside the GAAP operating margin so readers can distinguish
  recurring profitability from GAAP-reported figures.
"""

_SECTION_TEMPLATE_STUBS: dict[int, str] = {
    2: "\nSECTION 2: COMPANY OVERVIEW -- 400-600 words\nSECTION THESIS: What is the one-sentence verdict about this company's business model?\nOPENING PARAGRAPH: Dense overview. CORE PRODUCTS & SERVICES: intro + segments.\nINDUSTRY ECOSYSTEM: value_chain + defensibility.\nTotal memo HARD CAP is 18,000 words. Stay within your section word target.",
    3: "\nSECTION 3: COMPANY HISTORY & KEY MILESTONES -- 500-700 words\nSECTION THESIS: What is the defining narrative arc?\nOPENING PARAGRAPH. EARLY HISTORY (if applicable). PHASE BLOCKS: 3-5 phases.\nTotal memo HARD CAP is 18,000 words. Stay within your section word target.",
    4: ("\nSECTION 4: PRODUCT & TECHNOLOGY STRATEGY -- 500-800 words\n"
        "SECTION THESIS: Is the company investing in the right things?\n"
        "REVENUE BY SEGMENT: Check seg_data_quality. PRODUCT PORTFOLIO. R&D. TECHNOLOGY INITIATIVES.\n"
        "SEGMENT DATA DISCIPLINE: If the company reports revenue by multiple dimensions\n"
        "(e.g., by product category AND by brand), these may not sum to the same total.\n"
        "Use segment data as-is — do NOT invent reconciliation or force segments to sum to consolidated revenue.\n"
        "If totals differ across segment taxonomies, note the discrepancy factually.\n"
        "Total memo HARD CAP is 18,000 words. Stay within your section word target."),
    5: "\nSECTION 5: COMPETITIVE MOATS -- 600-900 words\nSECTION THESIS: What is the moat verdict?\nMOAT BLOCKS: One per source. OVERALL ASSESSMENT. SCORE TAG: MOAT.\nTotal memo HARD CAP is 18,000 words. Stay within your section word target.",
    6: "\nSECTION 6: INDUSTRY & COMPETITIVE DYNAMICS -- 700-1000 words\nMARKET STRUCTURE. COMPETITIVE LANDSCAPE. DYNAMICS. INDUSTRY FORCES. TAILWINDS. HEADWINDS.\nTotal memo HARD CAP is 18,000 words. Stay within your section word target.",
    7: ("\nSECTION 7: CUSTOMER ANALYSIS -- 500-700 words\n"
        "CUSTOMER COMPOSITION. GEOGRAPHIC SPLIT. STICKINESS. UNIT ECONOMICS. WORKING CAPITAL.\n"
        "GEOGRAPHIC DATA DISCIPLINE: No precomputed geographic revenue tables are provided.\n"
        "Do NOT compute your own geographic revenue totals by summing individual regions.\n"
        "Do NOT cite geographic revenue figures that are not in the provided facts.\n"
        "If geographic data is sparse or missing regions, say so — do NOT fill gaps with estimates.\n"
        "Total memo HARD CAP is 18,000 words. Stay within your section word target."),
    8: "\nSECTION 8: MANAGEMENT & CAPITAL ALLOCATION -- 600-900 words\nLEADERSHIP TEAM. EXECUTION. CAPITAL ALLOCATION. GOVERNANCE.\nTotal memo HARD CAP is 18,000 words. Stay within your section word target.",
    9: ("\nSECTION 9: GROWTH PROSPECTS -- 600-900 words\n"
        "MARKET OPPORTUNITY. NEAR-TERM CATALYSTS. MEDIUM-TERM DRIVERS. LONG-TERM POSITION. MARGIN EVOLUTION.\n"
        "MEDIUM-TERM DRIVERS: If 'medium_term_driver_topics' is provided in the qualitative data,\n"
        "use those topics as labels for your medium_term_drivers blocks. Write a substantive\n"
        "analytical paragraph (4-6 sentences) for EACH topic explaining its mechanics, magnitude,\n"
        "timeline, and what must be true for the driver to materialise.\n"
        "Total memo HARD CAP is 18,000 words. Stay within your section word target."),
    10: (
        "\nSECTION 10: FINANCIAL ANALYSIS -- 800-1100 words\n"
        "Tables are auto-injected (including sector KPI tables when available). Provide detailed narrative analysis around the data.\n"
        "IMPORTANT: The exact subsection set in Section 10 is SECTOR-FAMILY-SPECIFIC (banks/REITs/insurers do NOT use generic revenue/margins/FCF framing).\n"
        "Follow the provided JSON schema exactly — fill every required field with 4-6 sentence analysis (unless the field is explicitly a table wrapper).\n"
        "SECTOR KPIs: Use sector_kpi_recent and sector_analysis_guidance to ground your analysis in the correct sector metrics (e.g., NIM/CET1 for banks; FFO/AFFO/NOI for REITs).\n"
        "LEVERAGE: Include only if material (ND/EBITDA > 1x or leverage is a thesis driver).\n"
        "FINANCIAL QUALITY FLAGS: Pre-computed diagnostic flags are provided in the facts payload as s10_financial_flags.\n"
        "For each triggered flag, write a labeled block diagnosing: WHAT happened (cite EXACT numbers from the flag detail), WHY it happened, WHAT IT SIGNALS, and PEER CONTEXT (if relevant).\n"
        "CRITICAL: Use the EXACT numbers from the flag detail. Do NOT swap in related but different metrics.\n"
        "If no flags are triggered, you MUST still write 2-3 blocks highlighting the company's STRONGEST quality signals. The financial_quality_flags array must NEVER be empty.\n"
        "SYNTHESIS: Bring together the section's subsections and the strongest quality signals into a single, investment-relevant conclusion.\n"
        "Total memo HARD CAP is 18,000 words. Stay within your section word target."
    ),
    11: ("\nSECTION 11: PEER FINANCIAL BENCHMARKING -- 800-1100 words\n"
        "Purely quantitative. All tables auto-injected.\n"
        "PROFITABILITY, GROWTH, VALUATION (required). LEVERAGE, EFFICIENCY (conditional). SYNTHESIS.\n"
        "Total memo HARD CAP is 18,000 words. Stay within your section word target."),
    13: ("\nSECTION 13: RISK ASSESSMENT -- 700-1000 words\n"
        "REGULATORY. KEY RISKS. BEAR CASE TRIGGERS. SENSITIVITY. LITIGATION. CLOSING.\n"
        "Total memo HARD CAP is 18,000 words. Stay within your section word target."),
}


def _build_section_template(section_num: int) -> str:
    stub = _SECTION_TEMPLATE_STUBS.get(section_num, "")
    return GLOBAL_RULES + stub


# ═══════════════════════════════════════════════════════════════
# INDUSTRY DETECTION
# ═══════════════════════════════════════════════════════════════

SKIP_DCF_INDUSTRIES = frozenset([
    "Banks - Diversified", "Banks - Regional",
    "Insurance - Diversified", "Insurance - Life",
    "Insurance - Property & Casualty", "Insurance - Specialty", "Insurance - Reinsurance",
    "Mortgage Finance", "Thrifts & Mortgage Finance", "Credit Services",
])

INDUSTRY_VALUATION_CONFIG: dict[str, dict] = {
    "Oil & Gas E&P":                     {"method": "ev_ebitda", "secondary": "pe", "rationale": "Commodity price cyclicality and depletion charges make FCF projections unreliable", "sector_note": ""},
    "Oil & Gas Integrated":              {"method": "ev_ebitda", "secondary": "pe", "rationale": "Integrated O&G capex cycles span decades", "sector_note": ""},
    "Oil & Gas Midstream":               {"method": "ev_ebitda", "secondary": "pe", "rationale": "Pipeline depreciation distorts GAAP FCF", "sector_note": ""},
    "Oil & Gas Refining & Marketing":    {"method": "ev_ebitda", "secondary": "pe", "rationale": "Crack spread cyclicality makes FCF lumpy", "sector_note": ""},
    "Oil & Gas Equipment & Services":    {"method": "ev_ebitda", "secondary": "pe", "rationale": "Revenue tied to commodity-driven drilling cycles", "sector_note": ""},
    "Oil & Gas Drilling":                {"method": "ev_ebitda", "secondary": "pe", "rationale": "Dayrate cyclicality makes GAAP FCF unreliable", "sector_note": ""},
    "Gold":                              {"method": "ev_ebitda", "secondary": "pe", "rationale": "Gold price cyclicality and mine depletion", "sector_note": ""},
    "Silver":                            {"method": "ev_ebitda", "secondary": "pe", "rationale": "Silver price cyclicality", "sector_note": ""},
    "Copper":                            {"method": "ev_ebitda", "secondary": "pe", "rationale": "Copper price cycles and mine development capex", "sector_note": ""},
    "Other Industrial Metals & Mining":  {"method": "ev_ebitda", "secondary": "pe", "rationale": "Commodity cyclicality", "sector_note": ""},
    "Other Precious Metals & Mining":    {"method": "ev_ebitda", "secondary": "pe", "rationale": "Precious metals price cyclicality", "sector_note": ""},
    "Coking Coal":                       {"method": "ev_ebitda", "secondary": "pe", "rationale": "Coking coal tied to steel production cycles", "sector_note": ""},
    "Thermal Coal":                      {"method": "ev_ebitda", "secondary": "pe", "rationale": "Commodity price cycles", "sector_note": ""},
    "Steel":                             {"method": "ev_ebitda", "secondary": "pe", "rationale": "Steel price cyclicality", "sector_note": ""},
    "Aluminum":                          {"method": "ev_ebitda", "secondary": "pe", "rationale": "Aluminum price cycles", "sector_note": ""},
    "Uranium":                           {"method": "ev_ebitda", "secondary": "pe", "rationale": "Uranium contract pricing cycles", "sector_note": ""},
    "Marine Shipping":                   {"method": "ev_ebitda", "secondary": "pe", "rationale": "Charter rate cyclicality", "sector_note": ""},
    "Airlines":                          {"method": "ev_ebitda", "secondary": "pe", "rationale": "Fleet depreciation and fuel cost volatility", "sector_note": ""},
    "Utilities - Regulated Electric":    {"method": "pe", "secondary": "ev_ebitda", "rationale": "Regulated utility earnings reflect allowed ROE on rate base", "sector_note": ""},
    "Utilities - Regulated Gas":         {"method": "pe", "secondary": "ev_ebitda", "rationale": "Same regulated rate base model", "sector_note": ""},
    "Utilities - Regulated Water":       {"method": "pe", "secondary": "ev_ebitda", "rationale": "Regulated water utilities earn allowed ROE", "sector_note": ""},
    "Utilities - Diversified":           {"method": "pe", "secondary": "ev_ebitda", "rationale": "Mix of regulated and unregulated", "sector_note": ""},
    "Utilities - Independent Power Producers": {"method": "ev_ebitda", "secondary": "pe", "rationale": "Merchant power commodity exposure", "sector_note": ""},
    "Utilities - Renewable":             {"method": "ev_ebitda", "secondary": "pe", "rationale": "Tax credit structures distort GAAP", "sector_note": ""},
    # REITs — P/FFO is the industry standard; GAAP depreciation crushes ROE/BVPS
    "REIT - Diversified":                {"method": "pe", "secondary": "ev_ebitda", "rationale": "REIT GAAP depreciation depresses book value and ROE; P/FFO is the industry standard", "sector_note": "", "metric_label": "P/FFO", "secondary_label": "P/AFFO"},
    "REIT - Office":                     {"method": "pe", "secondary": "ev_ebitda", "rationale": "REIT GAAP depreciation depresses book value and ROE", "sector_note": "", "metric_label": "P/FFO", "secondary_label": "P/AFFO"},
    "REIT - Retail":                     {"method": "pe", "secondary": "ev_ebitda", "rationale": "REIT GAAP depreciation depresses book value and ROE", "sector_note": "", "metric_label": "P/FFO", "secondary_label": "P/AFFO"},
    "REIT - Residential":               {"method": "pe", "secondary": "ev_ebitda", "rationale": "REIT GAAP depreciation depresses book value and ROE", "sector_note": "", "metric_label": "P/FFO", "secondary_label": "P/AFFO"},
    "REIT - Industrial":                 {"method": "pe", "secondary": "ev_ebitda", "rationale": "REIT GAAP depreciation depresses book value and ROE", "sector_note": "", "metric_label": "P/FFO", "secondary_label": "P/AFFO"},
    "REIT - Healthcare Facilities":      {"method": "pe", "secondary": "ev_ebitda", "rationale": "REIT GAAP depreciation depresses book value and ROE", "sector_note": "", "metric_label": "P/FFO", "secondary_label": "P/AFFO"},
    "REIT - Hotel & Motel":              {"method": "pe", "secondary": "ev_ebitda", "rationale": "REIT GAAP depreciation depresses book value and ROE", "sector_note": "", "metric_label": "P/FFO", "secondary_label": "P/AFFO"},
    "REIT - Mortgage":                   {"method": "pe", "secondary": "ev_ebitda", "rationale": "Mortgage REITs are interest-rate sensitive; P/E captures spread income", "sector_note": "", "metric_label": "P/FFO", "secondary_label": "P/AFFO"},
    "REIT - Specialty":                  {"method": "pe", "secondary": "ev_ebitda", "rationale": "REIT GAAP depreciation depresses book value and ROE", "sector_note": "", "metric_label": "P/FFO", "secondary_label": "P/AFFO"},
    # Catch-all for VICI-style FMP classification
    "Real Estate Investment Trust":      {"method": "pe", "secondary": "ev_ebitda", "rationale": "REIT GAAP depreciation depresses book value and ROE; P/FFO is the industry standard", "sector_note": "", "metric_label": "P/FFO", "secondary_label": "P/AFFO"},
}

# Industries where peer multiples are the PRIMARY valuation model (peer comp comes
# BEFORE bull/base/bear scenarios in S12).  All other industry_peer sectors put
# scenarios first and peer comp as a cross-check.
_PEER_PRIMARY_INDUSTRIES = frozenset({
    # REITs — P/FFO is the primary model
    "REIT - Diversified", "REIT - Office", "REIT - Retail",
    "REIT - Residential", "REIT - Industrial",
    "REIT - Healthcare Facilities", "REIT - Hotel & Motel",
    "REIT - Mortgage", "REIT - Specialty",
    "Real Estate Investment Trust",
    # Energy — EV/EBITDA
    "Oil & Gas E&P", "Oil & Gas Integrated", "Oil & Gas Midstream",
    "Oil & Gas Refining & Marketing", "Oil & Gas Equipment & Services",
    "Oil & Gas Drilling",
    # Mining/Metals
    "Gold", "Silver", "Copper", "Steel", "Aluminum", "Uranium",
    "Other Industrial Metals & Mining", "Other Precious Metals & Mining",
    "Coking Coal", "Thermal Coal",
    # Transport
    "Marine Shipping", "Airlines",
})


def _normalize_industry(s: str, sector: str = "") -> str:
    if not s:
        return ""
    s = re.sub(r"[\u2014\u2013]", " - ", s)
    s = re.sub(r"\s+", " ", s).strip()
    for p in ("Banks", "Insurance", "REIT", "Thrifts", "Utilities"):
        if s.startswith(p + " ") and " - " not in s:
            s = p + " - " + s[len(p) + 1:]
            break
    s = re.sub(r"^(Oil & Gas)\s+-\s+", r"\1 ", s)

    # If the industry doesn't match any config key but the sector prefix
    # would create a match, prepend it.  Example: sector="Utilities",
    # industry="Regulated Electric" → "Utilities - Regulated Electric".
    if sector and s not in SKIP_DCF_INDUSTRIES and s not in INDUSTRY_VALUATION_CONFIG:
        sector_norm = sector.strip()
        candidate = f"{sector_norm} - {s}"
        if candidate in SKIP_DCF_INDUSTRIES or candidate in INDUSTRY_VALUATION_CONFIG:
            s = candidate
    return s


# ═══════════════════════════════════════════════════════════════
# 8. SECTION CONTEXT BUILDERS
# ═══════════════════════════════════════════════════════════════

def _build_identity(ident: dict, fc: str) -> dict:
    return sanitize_obj({
        "company_name": ident.get("company_name", ""),
        "ticker": ident.get("ticker", ""),
        "exchange": ident.get("exchange", ""),
        "sector": ident.get("sector", ""),
        "industry": ident.get("industry", ""),
        "country": ident.get("country", ""),
        "headquarters": (f"{ident['city']}, {ident['state']}" if ident.get("city") and ident.get("state") else ident.get("city", "")),
        "ceo": ident.get("ceo", ""),
        "employee_count": fmt_num(ident.get("employee_count"), "count"),
        "ipo_date": ident.get("ipo_date"),
        "website": ident.get("website", ""),
        "description": ident.get("description", ""),
        "_cite": fc,
    })


# ── Section context builders ─────────────────────────────────

def build_section_2_context(
    *,
    fs: dict, F: dict, identity: dict, latest_year: str,
    fc: str, url_to_id: dict, annual_years: list[str],
    seg: dict, raw_seg: dict, comp_overview: dict, ident: dict,
) -> dict:
    """Build facts dict for Section 2: Company Overview."""
    return {
        **identity,
        "fiscal_year_end": latest_year,
        "financials": {
            "revenue_latest": _safe_get(F, "incStmt", "revenue_usd_m", latest_year),
            "revenue_growth_yoy": _safe_get(F, "incStmt", "revenue_growth_pct", latest_year),
            "gross_margin": _safe_get(F, "margins", "gross_margin_pct", latest_year),
            "operating_margin": _safe_get(F, "margins", "operating_margin_pct", latest_year),
            "total_debt": _safe_get(F, "cap", "total_debt_usd_m", latest_year),
            "net_debt": _safe_get(F, "cap", "net_debt_usd_m", latest_year),
            "debt_to_equity": _safe_get(F, "cap", "debt_to_equity_ratio", latest_year),
            "cash": _safe_get(F, "cap", "cash_and_equivalents_usd_m", latest_year),
            "market_cap": _safe_get(F, "valuation", "market_cap_usd_b", latest_year),
        },
        "segment_revenue": fmt_obj(
            _safe_get(seg, "segment_revenue_usd_m", latest_year), "segment_revenue_usd_m"),
        "segment_pct": _safe_get(seg, "segment_revenue_pct_of_total", latest_year),
        "segment_yoy": _safe_get(seg, "segment_yoy_growth_pct", latest_year),
        "_cite_financials": fc,
        "qualitative": {
            "one_sentence_description": comp_overview.get("one_sentence_description"),
            "revenue_model_type": comp_overview.get("revenue_model_type"),
            "recurring_vs_nonrecurring": comp_overview.get("recurring_vs_nonrecurring_note"),
            "core_value_proposition": comp_overview.get("core_value_proposition"),
            "pricing_structure": comp_overview.get("pricing_structure_description"),
            "primary_financing_method": comp_overview.get("primary_financing_method"),
            "life_cycle_classification": comp_overview.get("life_cycle_classification"),
            "life_cycle_evidence": comp_overview.get("life_cycle_evidence_narrative"),
            "competitive_advantages": _cite(comp_overview.get("competitive_advantages", []), url_to_id, fc),
            "segments_qualitative": _cite(comp_overview.get("segments_qualitative", []), url_to_id, fc),
        },
    }


def build_section_3_context(
    *,
    fs: dict, F: dict, identity: dict, latest_year: str,
    fc: str, url_to_id: dict, annual_years: list[str],
    ident: dict, raw_inc: dict, hist_mile: dict,
) -> dict:
    """Build facts dict for Section 3: Company History & Key Milestones."""
    ipo = ident.get("ipo_date")
    founding_year = None
    if ipo:
        try:
            founding_year = int(str(ipo)[:4])
        except (ValueError, TypeError):
            pass
    return {
        **identity,
        "founding_year": founding_year,
        "revenue_5yr": _yr5f(raw_inc.get("revenue_usd_m", {}), "revenue_usd_m", annual_years),
        "market_cap_latest": _safe_get(F, "valuation", "market_cap_usd_b", latest_year),
        "_cite_financials": fc,
        "qualitative": {
            "founding": _cite(hist_mile.get("founding"), url_to_id, fc),
            "milestones": _cite(hist_mile.get("milestones", []), url_to_id, fc),
            "strategic_evolution": _cite(hist_mile.get("strategic_evolution"), url_to_id, fc),
            "recent_developments": _cite(hist_mile.get("recent_developments", []), url_to_id, fc),
        },
    }


def build_section_4_context(
    *,
    fs: dict, F: dict, identity: dict, latest_year: str,
    fc: str, url_to_id: dict, annual_years: list[str],
    seg: dict, raw_seg: dict, prod_tech: dict,
    consolidated_revenue: float, seg_data_quality: str, ident: dict,
) -> dict:
    """Build facts dict for Section 4: Product & Technology Strategy."""
    return {
        **identity,
        "seg_data_quality": seg_data_quality,
        "segment_summary": fmt_obj(
            _safe_get(seg, "segment_revenue_usd_m", latest_year), "segment_revenue_usd_m"),
        "segment_pct": _safe_get(seg, "segment_revenue_pct_of_total", latest_year),
        "segment_growth": _safe_get(seg, "segment_yoy_growth_pct", latest_year),
        "consolidated_revenue": fmt_num(consolidated_revenue, "usd_m"),
        "rd_expense": _safe_get(F, "rd", "rd_expense_usd_m", latest_year),
        "rd_pct_of_revenue": _safe_get(F, "rd", "rd_pct_of_revenue", latest_year),
        "rd_growth": _safe_get(F, "rd", "rd_growth_pct", latest_year),
        "peer_rd_median_pct": _safe_get(F, "peers", "peer_medians", "rd_to_revenue_pct"),
        "_cite_financials": fc,
        "qualitative": {
            "product_portfolio": _cite(prod_tech.get("product_portfolio", []), url_to_id, fc),
            "rd_qualitative": _cite(prod_tech.get("rd_qualitative"), url_to_id, fc),
            "technology_initiatives": _cite(prod_tech.get("technology_initiatives", []), url_to_id, fc),
            "patents": _cite(prod_tech.get("patents"), url_to_id, fc),
            "technology_partnerships": _cite(prod_tech.get("technology_partnerships", []), url_to_id, fc),
            "product_pipeline": _cite(prod_tech.get("product_pipeline", []), url_to_id, fc),
            "competitive_tech_position": _cite(prod_tech.get("competitive_tech_position"), url_to_id, fc),
        },
    }


def build_section_5_context(
    *,
    fs: dict, F: dict, identity: dict, latest_year: str,
    fc: str, url_to_id: dict, annual_years: list[str],
    comp_moats: dict, ident: dict,
) -> dict:
    """Build facts dict for Section 5: Competitive Moats."""
    return {
        **identity,
        "gross_margin": _safe_get(F, "margins", "gross_margin_pct", latest_year),
        "operating_margin": _safe_get(F, "margins", "operating_margin_pct", latest_year),
        "roic": _safe_get(F, "margins", "roic_pct", latest_year),
        "peer_gross_margin_median": _safe_get(F, "peers", "peer_medians", "gross_margin_pct"),
        "peer_operating_margin_median": _safe_get(F, "peers", "peer_medians", "operating_margin_pct"),
        "_cite_financials": fc,
        "qualitative": {
            "brand_intangibles": _cite(comp_moats.get("brand_intangibles"), url_to_id, fc),
            "switching_costs": _cite(comp_moats.get("switching_costs"), url_to_id, fc),
            "network_effects": _cite(comp_moats.get("network_effects"), url_to_id, fc),
            "cost_advantages": _cite(comp_moats.get("cost_advantages"), url_to_id, fc),
            "efficient_scale": _cite(comp_moats.get("efficient_scale"), url_to_id, fc),
            "moat_summary": _cite(comp_moats.get("moat_summary"), url_to_id, fc),
        },
    }


def build_section_6_context(
    *,
    fs: dict, F: dict, identity: dict, latest_year: str,
    fc: str, url_to_id: dict, annual_years: list[str],
    ind_comp: dict, peers_obj: dict, ident: dict,
) -> dict:
    """Build facts dict for Section 6: Industry & Competitive Dynamics."""
    return {
        **identity,
        "revenue_latest": _safe_get(F, "incStmt", "revenue_usd_m", latest_year),
        "peer_medians": fmt_obj(_safe_get(F, "peers", "peer_medians")),
        "peer_latest": fmt_obj(peers_obj.get("latest")),
        "_cite_financials": fc,
        "qualitative": {
            "industry_overview": _cite(ind_comp.get("industry_overview"), url_to_id, fc),
            "addressable_market": _cite(ind_comp.get("addressable_market"), url_to_id, fc),
            "market_structure": _cite(ind_comp.get("market_structure"), url_to_id, fc),
            "competitors": _cite(ind_comp.get("competitors", []), url_to_id, fc),
            "subject_positioning": _cite(ind_comp.get("subject_positioning"), url_to_id, fc),
            "competitive_intensity": _cite(ind_comp.get("competitive_intensity"), url_to_id, fc),
            "five_forces": _cite(ind_comp.get("five_forces"), url_to_id, fc),
        },
    }


def build_section_7_context(
    *,
    fs: dict, F: dict, identity: dict, latest_year: str,
    fc: str, url_to_id: dict, annual_years: list[str],
    cust_analysis: dict, geo_rows: list, geo_data_quality: str,
    consolidated_revenue: float, ident: dict,
) -> dict:
    """Build facts dict for Section 7: Customer Analysis."""
    return {
        **identity,
        "precomputed_geo_rows": geo_rows,
        "geo_data_quality": geo_data_quality,
        "consolidated_revenue": fmt_num(consolidated_revenue, "usd_m"),
        "working_capital": {
            "dso": _safe_get(F, "wc", "dso_days", latest_year),
            "dpo": _safe_get(F, "wc", "dpo_days", latest_year),
            "ccc": _safe_get(F, "wc", "cash_conversion_cycle_days", latest_year),
            "deferred_revenue": _safe_get(F, "wc", "deferred_revenue_usd_m", latest_year),
        },
        "peer_dso_median": _safe_get(F, "peers", "peer_medians", "dso_days"),
        "_cite_financials": fc,
        "qualitative": {
            "composition": _cite(cust_analysis.get("composition"), url_to_id, fc),
            "geographic_revenue": _cite(cust_analysis.get("geographic_revenue"), url_to_id, fc),
            "value_proposition": _cite(cust_analysis.get("value_proposition"), url_to_id, fc),
            "acquisition_retention": _cite(cust_analysis.get("acquisition_retention"), url_to_id, fc),
            "unit_economics": _cite(cust_analysis.get("unit_economics"), url_to_id, fc),
            "working_capital_qualitative": _cite(cust_analysis.get("working_capital_qualitative"), url_to_id, fc),
        },
    }


def build_section_8_context(
    *,
    fs: dict, F: dict, identity: dict, latest_year: str,
    fc: str, url_to_id: dict, annual_years: list[str],
    mgmt_cap: dict, beat_miss: dict, cap_alloc: dict,
    raw_margins: dict, raw_share_d: dict, ident: dict,
) -> dict:
    """Build facts dict for Section 8: Management & Capital Allocation."""
    return {
        **identity,
        "guidance_beat_miss": sanitize_obj(beat_miss.get("summary")),
        "guidance_quarters": sanitize_obj(beat_miss.get("quarters")),
        "capital_allocation_3yr": {
            "rd": fmt_obj(cap_alloc.get("rd_usd_m"), "rd_usd_m"),
            "capex": fmt_obj(cap_alloc.get("capex_usd_m"), "capex_usd_m"),
            "acquisitions_net": fmt_obj(cap_alloc.get("acquisitions_net_usd_m"), "acquisitions_net_usd_m"),
            "dividends_paid": fmt_obj(cap_alloc.get("dividends_paid_usd_m"), "dividends_paid_usd_m"),
            "buybacks": fmt_obj(cap_alloc.get("buybacks_usd_m"), "buybacks_usd_m"),
        },
        "roic_5yr": _yr5f(
            cap_alloc.get("roic_pct") or raw_margins.get("roic_pct", {}),
            "roic_pct", annual_years),
        "shares_diluted": _yr5f(raw_share_d.get("shares_diluted_millions", {}),
                                "shares_diluted_millions", annual_years),
        "sbc_pct_of_revenue": _yr5f(raw_share_d.get("sbc_pct_of_revenue", {}),
                                    "sbc_pct_of_revenue", annual_years),
        "_cite_financials": fc,
        "qualitative": {
            "leadership_team": _cite(mgmt_cap.get("leadership_team", []), url_to_id, fc),
            "founder_context": _cite(mgmt_cap.get("founder_context"), url_to_id, fc),
            "management_quality": _cite(mgmt_cap.get("management_quality"), url_to_id, fc),
            "capital_allocation_qualitative": _cite(
                mgmt_cap.get("capital_allocation_qualitative"), url_to_id, fc),
        },
    }


def build_section_9_context(
    *,
    fs: dict, F: dict, identity: dict, latest_year: str,
    fc: str, url_to_id: dict, annual_years: list[str],
    growth_prosp: dict, ind_comp: dict, seg: dict,
    raw_margins: dict, raw_cf: dict, fwd_est: Any, ident: dict,
) -> dict:
    """Build facts dict for Section 9: Growth Prospects & Catalysts."""
    # TAM / penetration from ind_comp qualitative
    ind_overview = ind_comp.get("industry_overview") or {}
    addr_market = ind_comp.get("addressable_market") or {}
    if isinstance(ind_overview, list):
        ind_overview = ind_overview[0] if ind_overview else {}
    if isinstance(addr_market, list):
        addr_market = addr_market[0] if addr_market else {}

    tam_usd_b = ind_overview.get("tam_usd_b")
    tam_cagr_pct = ind_overview.get("tam_cagr_pct")
    penetration = addr_market.get("current_penetration_pct")

    return {
        **identity,
        "revenue_5yr_cagr": _safe_get(F, "incStmt", "revenue_cagr_5yr_pct"),
        "revenue_growth_latest": _safe_get(F, "incStmt", "revenue_growth_pct", latest_year),
        "gross_margin_5yr": _yr5f(raw_margins.get("gross_margin_pct", {}),
                                  "gross_margin_pct", annual_years),
        "operating_margin_5yr": _yr5f(raw_margins.get("operating_margin_pct", {}),
                                      "operating_margin_pct", annual_years),
        "fcf_margin_5yr": _yr5f(raw_cf.get("fcf_margin_pct", {}),
                                "fcf_margin_pct", annual_years),
        "forward_estimates": sanitize_obj(fwd_est),
        "segment_growth": fmt_obj(
            _safe_get(seg, "segment_yoy_growth_pct", latest_year), "segment_yoy_growth_pct"),
        "peer_revenue_growth_median": _safe_get(F, "peers", "peer_medians", "revenue_growth_pct"),
        "_cite_financials": fc,
        "qualitative": {
            "revenue_drivers": _cite(growth_prosp.get("revenue_drivers", []), url_to_id, fc),
            "near_term_catalysts": _cite(growth_prosp.get("near_term_catalysts", []), url_to_id, fc),
            "medium_term_drivers": _cite(growth_prosp.get("medium_term_drivers", []), url_to_id, fc),
            "long_term_position": _cite(growth_prosp.get("long_term_position"), url_to_id, fc),
            "margin_cashflow_evolution": _cite(
                growth_prosp.get("margin_cashflow_evolution"), url_to_id, fc),
            "tam_usd_b": fmt_num(tam_usd_b, "usd_b") if tam_usd_b is not None else None,
            "tam_cagr_pct": fmt_num(tam_cagr_pct, "pct") if tam_cagr_pct is not None else None,
            "current_penetration_pct": (
                fmt_num(penetration, "pct") if penetration is not None else None),
        },
    }


# ── SECTOR-SPECIFIC GUIDANCE BUILDERS ─────────────────────────────
# These inject targeted analysis instructions based on sector/industry.

_SECTOR_ANALYSIS_MAP: dict[str, dict] = {
    # Banks & Financial Services
    "Banks—Diversified": {
        "focus_metrics": ["NIM (net interest margin)", "efficiency ratio", "CET1 ratio",
                          "provisions for credit losses", "NPA ratio", "loan growth", "deposit growth"],
        "guidance": "Focus on net interest income trends, credit quality (NPA/NCO ratios), "
                    "capital adequacy (CET1 > 10.5%), and efficiency ratio improvement. "
                    "Revenue = net interest income + noninterest income (not gross interest income).",
    },
    "Banks—Regional": {
        "focus_metrics": ["NIM", "efficiency ratio", "CET1 ratio", "NPA ratio",
                          "loan-to-deposit ratio", "provision expense"],
        "guidance": "Regional banks: emphasize NIM sensitivity to rate cycle, "
                    "commercial real estate exposure, and deposit base stability.",
    },
    "Insurance—Diversified": {
        "focus_metrics": ["combined ratio", "loss ratio", "expense ratio",
                          "investment income yield", "book value growth", "ROE"],
        "guidance": "Focus on underwriting profitability (combined ratio < 100%), "
                    "reserve adequacy, and investment portfolio returns.",
    },
    "Insurance—Life": {
        "focus_metrics": ["embedded value", "new business margin", "persistency ratio",
                          "investment income", "solvency ratio"],
        "guidance": "Life insurance: focus on embedded value growth, new business margins, "
                    "policy persistency, and interest rate sensitivity.",
    },
    # REITs
    "REIT—Diversified": {
        "focus_metrics": ["FFO/share", "AFFO/share", "NOI growth", "occupancy rate",
                          "cap rate", "same-store NOI growth", "dividend payout ratio (of FFO)"],
        "guidance": "Use FFO and AFFO (not net income or FCF) as primary profitability measures. "
                    "Analyze occupancy trends, rent escalators, and lease maturity schedule.",
    },
    # Energy
    "Oil & Gas E&P": {
        "focus_metrics": ["production (BOE/d)", "reserve replacement ratio", "finding cost/BOE",
                          "lifting cost/BOE", "netback/BOE", "reserve life"],
        "guidance": "Focus on per-BOE economics, reserve replacement, and breakeven price. "
                    "Use SEC standardized measure for reserve valuation.",
    },
    "Oil & Gas Integrated": {
        "focus_metrics": ["upstream production", "downstream throughput", "refining margin",
                          "finding & development cost", "reserve replacement"],
        "guidance": "Analyze upstream/downstream balance, refining crack spreads, "
                    "and integrated margin stability through commodity cycles.",
    },
    # Utilities
    "Utilities—Regulated Electric": {
        "focus_metrics": ["rate base growth", "allowed ROE", "earned ROE", "regulatory lag",
                          "capex/depreciation ratio", "dividend payout ratio", "FFO/debt"],
        "guidance": "Focus on rate base growth trajectory, regulatory relationships, "
                    "and balance between capex investment and regulatory recovery.",
    },
    # Tech
    "Software—Application": {
        "focus_metrics": ["ARR", "NRR (net revenue retention)", "RPO (remaining performance obligations)",
                          "rule of 40", "CAC payback", "gross margin", "magic number"],
        "guidance": "SaaS: focus on ARR growth, NRR > 110%, RPO growth, and Rule of 40 score. "
                    "Analyze land-and-expand motion and customer cohort behavior.",
    },
    "Software—Infrastructure": {
        "focus_metrics": ["ARR", "NRR", "consumption growth", "gross margin",
                          "RPO", "DBNRR", "rule of 40"],
        "guidance": "Infra software: consumption vs. subscription model matters. "
                    "Focus on platform adoption, multi-product attach rates, and NRR.",
    },
    "Semiconductors": {
        "focus_metrics": ["design wins", "wafer ASP", "fab utilization", "inventory days",
                          "book-to-bill ratio", "R&D intensity"],
        "guidance": "Semiconductors are cyclical. Focus on inventory cycle position, "
                    "design win pipeline, and technology node transitions.",
    },
}

# Map broader sectors to fallback guidance
_SECTOR_FALLBACK: dict[str, dict] = {
    "Financial Services": {
        "focus_metrics": ["ROE", "efficiency ratio", "capital ratios", "credit quality"],
        "guidance": "Financial sector: focus on ROE vs. cost of equity, "
                    "capital adequacy, and credit cycle positioning.",
    },
    "Real Estate": {
        "focus_metrics": ["FFO", "AFFO", "NOI growth", "occupancy", "cap rate"],
        "guidance": "Real estate: use FFO (not net income) as primary earnings measure. "
                    "Analyze NAV discount/premium and same-store NOI growth.",
    },
    "Energy": {
        "focus_metrics": ["production volumes", "per-unit economics", "reserve metrics", "breakeven price"],
        "guidance": "Energy: focus on per-unit economics, breakeven prices, "
                    "and reserve replacement to assess long-term value.",
    },
    "Utilities": {
        "focus_metrics": ["rate base", "allowed ROE", "earned ROE", "FFO/debt", "dividend yield"],
        "guidance": "Utilities: regulated earnings model — focus on rate base growth, "
                    "regulatory lag, and dividend sustainability.",
    },
    "Technology": {
        "focus_metrics": ["revenue growth", "gross margin", "R&D intensity", "FCF margin", "rule of 40"],
        "guidance": "Tech: prioritize growth-profitability balance (Rule of 40), "
                    "R&D efficiency, and TAM penetration.",
    },
    "Industrials": {
        "focus_metrics": ["operating margin", "ROIC", "book-to-bill", "backlog",
                          "capex intensity", "FCF conversion"],
        "guidance": "Industrials: analyze cycle position via book-to-bill and backlog, "
                    "operating leverage through the cycle, and ROIC vs. WACC.",
    },
    "Healthcare": {
        "focus_metrics": ["gross margin", "R&D intensity", "R&D pipeline value",
                          "SGA efficiency", "FCF margin", "patent cliff exposure"],
        "guidance": "Healthcare: focus on R&D productivity, pipeline optionality, "
                    "patent expiry risk, and pricing power sustainability.",
    },
    "Consumer Cyclical": {
        "focus_metrics": ["same-store sales", "inventory turnover", "gross margin",
                          "SGA/revenue", "ROIC", "FCF conversion"],
        "guidance": "Consumer: analyze brand health via pricing power, "
                    "inventory management efficiency, and unit economics.",
    },
    "Consumer Defensive": {
        "focus_metrics": ["organic revenue growth", "gross margin", "operating margin",
                          "ROIC", "dividend payout ratio", "FCF yield"],
        "guidance": "Staples: focus on organic growth, pricing vs. volume mix, "
                    "brand portfolio strength, and dividend sustainability.",
    },
}


def _normalize_industry_lookup(industry: str) -> str:
    """Normalize industry string for map lookup (FMP uses ' - ', our map uses '—')."""
    return industry.replace(" - ", "—").replace(" – ", "—")


def _fmt_dollar_val(v: float | int | None) -> str | None:
    """Format raw dollar values for human readability. 49779000000 → '$49.8B'."""
    if v is None:
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    neg = n < 0
    n = abs(n)
    if n >= 1_000_000_000:
        s = f"${n / 1_000_000_000:.1f}B"
    elif n >= 1_000_000:
        s = f"${n / 1_000_000:.0f}M"
    elif n >= 1_000:
        s = f"${n / 1_000:.0f}K"
    else:
        s = f"${n:.0f}"
    return f"-{s}" if neg else s


def _format_sector_kpis(kpis: dict) -> dict:
    """Format raw dollar values in sector KPI data for LLM consumption.

    Converts raw numbers (e.g., 49779000000) to human-readable strings
    (e.g., '$49.8B') for all absolute dollar value arrays.
    Leaves computed ratios untouched (they're already decimal percentages).
    """
    if not kpis or not isinstance(kpis, dict):
        return kpis

    inner = kpis.get("kpis", kpis) if isinstance(kpis, dict) else kpis
    if not isinstance(inner, dict):
        return kpis

    # Keys that contain absolute dollar values (not ratios/percentages)
    _DOLLAR_ARRAY_KEYS = {
        # Insurance
        "premiumsEarned", "premiumsWritten", "premiumsCeded", "claims",
        "underwritingExpense", "investmentIncome", "realizedGains",
        "lossReserves", "unearnedPremiums", "totalInvestments",
        # Banking
        "netInterestIncome", "nonInterestIncome", "provisionForCreditLosses",
        "totalLoans", "totalDeposits", "totalAssets", "totalEquity",
        "netIncome", "tier1Capital",
        # REITs
        "revenue", "noi", "ffo", "affo", "totalDebt",
        # Energy
        "capex", "explorationExpense", "depletion",
        # Generic
        "totalRevenue",
    }

    formatted = deepcopy(kpis)
    fmt_inner = formatted.get("kpis", formatted) if isinstance(formatted, dict) else formatted

    for key in list(fmt_inner.keys()):
        val = fmt_inner[key]
        if key in _DOLLAR_ARRAY_KEYS and isinstance(val, list):
            for item in val:
                if isinstance(item, dict) and "val" in item:
                    raw = item["val"]
                    fmt_str = _fmt_dollar_val(raw)
                    if fmt_str is not None:
                        item["formatted"] = fmt_str
                        item["val"] = fmt_str  # Replace raw with formatted

    return formatted


def _build_sector_analysis_guidance(sector: str, industry: str, sector_kpis: dict) -> str:
    """Build sector-specific analysis guidance for Section 10 writers."""
    norm = _normalize_industry_lookup(industry)
    cfg = _SECTOR_ANALYSIS_MAP.get(norm) or _SECTOR_ANALYSIS_MAP.get(industry) or _SECTOR_FALLBACK.get(sector)
    if not cfg:
        return ""
    lines = [
        f"REQUIRED — SECTOR-SPECIFIC ANALYSIS ({industry or sector}):",
        f"You MUST include a 'sector_specific_analysis' subsection discussing: {', '.join(cfg['focus_metrics'])}",
        cfg["guidance"],
        "",
    ]
    # Include computed ratios/metrics so the writer can cite real numbers.
    # Banking uses "computedRatios", all other sectors use "computedMetrics".
    kpis_inner = sector_kpis.get("kpis", sector_kpis) if isinstance(sector_kpis, dict) else {}
    computed_ratios = kpis_inner.get("computedRatios") or kpis_inner.get("computedMetrics") or []
    if computed_ratios:
        lines.append("ACTUAL METRIC VALUES (from SEC filings — use these in your analysis):")
        # Show the last 3 years of computed ratios
        recent = sorted(computed_ratios, key=lambda r: r.get("date", ""), reverse=True)[:3]
        _DOLLAR_KEYS = {"tangibleBookValuePerShare", "ffoPerShare", "affoPerShare",
                        "findingCostPerBoe", "liftingCostPerBoe", "fcfPerShare"}
        _ALL_METRIC_LABELS = [
            # Banking
            ("netInterestMargin", "NIM"), ("efficiencyRatio", "Effic. Ratio"),
            ("roa", "ROA"), ("roe", "ROE"), ("loanToDepositRatio", "L/D Ratio"),
            ("nplRatio", "NPL Ratio"), ("netChargeOffRate", "NCO Rate"),
            ("reserveCoverage", "Reserve Cov."), ("provisionToLoans", "PCL/Loans"),
            ("feeIncomeRatio", "Fee Income %"), ("costOfDeposits", "Cost of Deps"),
            ("tangibleBookValuePerShare", "TBV/Share"),
            # Insurance
            ("combinedRatio", "Combined Ratio"), ("lossRatio", "Loss Ratio"),
            ("expenseRatio", "Expense Ratio"), ("investmentYield", "Inv. Yield"),
            # REITs
            ("ffoPerShare", "FFO/Share"), ("affoPerShare", "AFFO/Share"),
            ("noiMargin", "NOI Margin"), ("debtToAssets", "Debt/Assets"),
            # Energy
            ("operatingMargin", "Op. Margin"), ("netMargin", "Net Margin"),
            ("fcfMargin", "FCF Margin"), ("roce", "ROCE"),
            ("capexToRevenue", "Capex/Rev"),
            # Tech / SaaS
            ("grossMargin", "Gross Margin"), ("rdIntensity", "R&D %"),
            ("sbcAsPercentOfRevenue", "SBC/Rev"), ("ruleOf40", "Rule of 40"),
            ("nrrProxy", "NRR Proxy"), ("revenueGrowth", "Rev. Growth"),
            # Healthcare
            ("rdToGrossProfit", "R&D/GP"),
            # Industrials
            ("roic", "ROIC"), ("bookToBill", "Book/Bill"),
            ("backlogToRevenue", "Backlog/Rev"), ("ebitdaMargin", "EBITDA Margin"),
        ]
        for row in reversed(recent):  # oldest to newest
            year = row.get("date", "")[:4]
            parts = [f"FY{year}"]
            for key, label in _ALL_METRIC_LABELS:
                val = row.get(key)
                if val is not None:
                    if key in _DOLLAR_KEYS:
                        parts.append(f"{label}: ${val:.2f}")
                    elif key == "ruleOf40":
                        parts.append(f"{label}: {val:.1f}")
                    elif key == "bookToBill":
                        parts.append(f"{label}: {val:.2f}x")
                    elif key == "backlogToRevenue":
                        parts.append(f"{label}: {val:.2f}x")
                    elif abs(val) < 1:
                        parts.append(f"{label}: {val*100:.1f}%")
                    else:
                        parts.append(f"{label}: {val:.1f}%")
            if len(parts) > 1:
                lines.append("  " + " | ".join(parts))

    # Also show raw capital ratios if present
    for raw_key, label in [("cet1Ratio", "CET1"), ("tier1Ratio", "Tier 1"),
                            ("totalCapitalRatio", "Total Capital"), ("leverageRatio", "Leverage")]:
        raw_series = kpis_inner.get(raw_key, [])
        if raw_series and isinstance(raw_series, list):
            latest = raw_series[0] if raw_series else {}
            val = latest.get("val")
            year = latest.get("date", "")[:4]
            if val is not None:
                if abs(val) < 1:
                    lines.append(f"  {label} Ratio ({year}): {val*100:.1f}%")
                else:
                    lines.append(f"  {label} Ratio ({year}): {val:.1f}%")

    return "\n".join(lines)


def _build_peer_emphasis(sector: str, industry: str) -> str:
    """Build guidance on which peer comparison multiples to emphasize per sector."""
    # Industry-specific emphasis
    norm_industry = _normalize_industry_lookup(industry)
    emphasis_map = {
        # Financials — P/B and P/E, not EV/EBITDA
        "Banks—Diversified": "PRIMARY: P/B (tangible), P/E. AVOID: EV/EBITDA, EV/Sales (meaningless for banks). "
                             "Key: ROE vs cost of equity justifies P/B premium/discount.",
        "Banks—Regional": "PRIMARY: P/B (tangible), P/E. AVOID: EV/EBITDA. "
                          "Key: compare NIM, efficiency ratio, and credit quality across peers.",
        "Insurance—Diversified": "PRIMARY: P/B, P/E. SECONDARY: combined ratio comparison. "
                                 "Key: underwriting profitability and investment returns vs peers.",
        "Insurance—Life": "PRIMARY: P/EV (price to embedded value), P/B. "
                          "Key: compare new business margins and persistency.",
        # REITs — FFO multiples
        "REIT—Diversified": "PRIMARY: P/FFO, P/AFFO, dividend yield. AVOID: P/E, FCF. "
                            "Key: compare cap rates, occupancy, same-store NOI growth.",
        # Energy — EV/EBITDA, per-unit metrics
        "Oil & Gas E&P": "PRIMARY: EV/EBITDA, EV/proved reserves, EV/production. "
                         "Key: compare per-BOE economics and reserve replacement.",
        "Oil & Gas Integrated": "PRIMARY: EV/EBITDA, EV/production. "
                                "Key: compare upstream/downstream mix and refining margins.",
        # Utilities — P/E and dividend yield
        "Utilities—Regulated Electric": "PRIMARY: P/E, dividend yield, EV/rate base. "
                                        "Key: compare allowed vs earned ROE, rate base growth.",
        # Tech — growth-adjusted multiples
        "Software—Application": "PRIMARY: EV/Sales, EV/NTM revenue, rule of 40. "
                                "Key: growth-adjusted comparison (PEG, EV/Sales ÷ growth).",
        "Software—Infrastructure": "PRIMARY: EV/Sales, EV/NTM revenue. "
                                   "Key: platform vs. point solution positioning, NRR comparison.",
        "Semiconductors": "PRIMARY: EV/EBITDA, P/E (normalized for cycle). "
                          "Key: compare through-cycle margins and R&D productivity.",
    }

    # Sector-level fallback
    sector_emphasis = {
        "Financial Services": "PRIMARY: P/B, P/E, ROE. AVOID: EV/EBITDA for banks/insurance.",
        "Real Estate": "PRIMARY: P/FFO, P/AFFO, dividend yield, NAV premium/discount.",
        "Energy": "PRIMARY: EV/EBITDA, EV/reserves. Key: breakeven price comparison.",
        "Utilities": "PRIMARY: P/E, dividend yield. Key: rate base growth comparison.",
        "Technology": "PRIMARY: EV/Sales (high-growth), EV/EBITDA (mature). Key: Rule of 40.",
        "Healthcare": "PRIMARY: EV/EBITDA, P/E. Key: pipeline value for pharma, volume trends for providers.",
        "Industrials": "PRIMARY: EV/EBITDA. Key: cycle position and margin expansion potential.",
        "Consumer Cyclical": "PRIMARY: EV/EBITDA, P/E. Key: comparable same-store metrics.",
        "Consumer Defensive": "PRIMARY: P/E, EV/EBITDA. Key: volume/price mix and private label exposure.",
    }

    return emphasis_map.get(norm_industry, emphasis_map.get(industry, sector_emphasis.get(sector, "")))


def _build_sector_risk_guidance(sector: str, industry: str) -> str:
    """Build sector-specific risk assessment guidance for Section 13."""
    risk_map = {
        "Banks—Diversified": "SECTOR RISKS: Credit cycle (NPA trends, provision trajectory), "
                             "interest rate sensitivity (NIM compression in falling rate environment), "
                             "regulatory capital requirements (stress test results), "
                             "commercial real estate exposure, counterparty risk.",
        "Banks—Regional": "SECTOR RISKS: CRE concentration, deposit flight to money markets, "
                          "NIM compression, regulatory scrutiny post-SVB, loan portfolio quality.",
        "Insurance—Diversified": "SECTOR RISKS: Reserve adequacy (prior year development), "
                                 "catastrophe exposure, investment portfolio losses, regulatory changes, "
                                 "social inflation trends increasing loss severity.",
        "REIT—Diversified": "SECTOR RISKS: Interest rate sensitivity (cost of debt, cap rate expansion), "
                            "occupancy risk (especially office/retail post-COVID), "
                            "tenant credit quality, development pipeline risk, refinancing risk.",
        "Oil & Gas E&P": "SECTOR RISKS: Commodity price volatility, reserve depletion, "
                         "regulatory/environmental risk, stranded asset risk from energy transition, "
                         "geopolitical supply disruptions.",
        "Oil & Gas Integrated": "SECTOR RISKS: Commodity cycle, refining margin volatility, "
                                "energy transition/stranded assets, environmental liabilities, "
                                "capital allocation between upstream investment and shareholder returns.",
        "Utilities—Regulated Electric": "SECTOR RISKS: Regulatory risk (rate case outcomes), "
                                        "wildfire liability (western utilities), renewable transition costs, "
                                        "interest rate sensitivity on financing costs, political intervention.",
        "Software—Application": "SECTOR RISKS: Customer churn acceleration, competition from platforms, "
                                "AI disruption potential, elongated sales cycles in downturn, "
                                "key customer concentration.",
        "Semiconductors": "SECTOR RISKS: Inventory cycle (destocking), demand cyclicality, "
                          "geopolitical supply chain risk (China/Taiwan), technology disruption, "
                          "customer concentration, export controls.",
    }

    sector_fallback = {
        "Financial Services": "SECTOR RISKS: Credit cycle, rate sensitivity, regulatory change, capital requirements.",
        "Real Estate": "SECTOR RISKS: Rate sensitivity, occupancy risk, refinancing risk, tenant credit.",
        "Energy": "SECTOR RISKS: Commodity price, energy transition, environmental liability, geopolitical.",
        "Utilities": "SECTOR RISKS: Regulatory outcomes, rate sensitivity, infrastructure risk, climate.",
        "Technology": "SECTOR RISKS: Growth deceleration, competition, regulatory, talent retention.",
        "Healthcare": "SECTOR RISKS: Drug pricing regulation, patent cliffs, FDA approvals, reimbursement.",
        "Industrials": "SECTOR RISKS: Cyclicality, input costs, supply chain, trade policy.",
    }

    norm = _normalize_industry_lookup(industry)
    return risk_map.get(norm, risk_map.get(industry, sector_fallback.get(sector, "")))


def build_section_10_context(
    *,
    fs: dict, F: dict, identity: dict, latest_year: str,
    fc: str, url_to_id: dict, annual_years: list[str],
    fcf_conversion_5yr: Any, ident: dict,
    family: str = "generic",
    sector_kpis: dict | None = None,
) -> dict:
    """Build facts dict for Section 10: Financial Analysis.

    Includes sector-specific KPIs (NIM for banks, FFO for REITs, RPO for SaaS, etc.)
    when available from the SEC sector modules.
    """
    ctx = {
        **identity,
        "sector_family": family,
        "section_family": family,
        "sector_kpi_coverage": fs.get("_sector_kpi_coverage"),
        "financials_5yr": {
            "revenue": _safe_get(F, "incStmt", "revenue_usd_m"),
            "revenue_growth": _safe_get(F, "incStmt", "revenue_growth_pct"),
            "gross_margin": _safe_get(F, "margins", "gross_margin_pct"),
            "operating_margin": _safe_get(F, "margins", "operating_margin_pct"),
            "ebitda_margin": _safe_get(F, "incStmt", "ebitda_margin_pct"),
            "net_margin": _safe_get(F, "margins", "net_margin_pct"),
            "eps_diluted": _safe_get(F, "incStmt", "eps_diluted"),
            "ocf": _safe_get(F, "cfStmt", "operating_cash_flow_usd_m"),
            "capex_pct_rev": _safe_get(F, "cfStmt", "capex_pct_of_revenue"),
            "fcf": _safe_get(F, "cfStmt", "free_cash_flow_usd_m"),
            "fcf_margin": _safe_get(F, "cfStmt", "fcf_margin_pct"),
            "roe": _safe_get(F, "returns", "roe_pct"),
            "roic": _safe_get(F, "returns", "roic_pct"),
            "net_debt_ebitda": _safe_get(F, "balSheet", "net_debt_to_ebitda"),
            "interest_coverage": _safe_get(F, "balSheet", "interest_coverage_ratio"),
            "dso_days": _safe_get(F, "wc", "dso_days"),
        },
        "capital_allocation_3yr": {
            "rd": _safe_get(F, "capAlloc", "rd_usd_m"),
            "capex": _safe_get(F, "capAlloc", "capex_usd_m"),
            "acquisitions": _safe_get(F, "capAlloc", "acquisitions_net_usd_m"),
            "dividends": _safe_get(F, "capAlloc", "dividends_paid_usd_m"),
            "buybacks": _safe_get(F, "capAlloc", "buybacks_usd_m"),
        },
        "revenue_5yr_cagr": _safe_get(F, "incStmt", "revenue_cagr_5yr_pct"),
        "peer_medians": _safe_get(F, "peers", "peer_medians"),
        "fcf_conversion_pct": fcf_conversion_5yr,
        "goodwill": _safe_get(F, "balSheet", "goodwill_usd_m", latest_year),
        "goodwill_pct_assets": _safe_get(F, "balSheet", "goodwill_pct_total_assets", latest_year),
        "sbc": _safe_get(F, "shareD", "sbc_usd_m", latest_year),
        "sbc_pct_rev": _safe_get(F, "shareD", "sbc_pct_of_revenue", latest_year),
        "ccc": _safe_get(F, "wc", "cash_conversion_cycle_days", latest_year),
        "_cite_financials": fc,
    }

    # ── Inject sector-specific KPIs + recent computed metrics ─
    if sector_kpis:
        ctx["sector_kpis"] = _format_sector_kpis(sector_kpis)
        kpis_inner = sector_kpis.get("kpis", sector_kpis) if isinstance(sector_kpis, dict) else {}
        computed = kpis_inner.get("computedRatios") or kpis_inner.get("computedMetrics") or []
        if isinstance(computed, list) and computed:
            recent = sorted(
                [r for r in computed if isinstance(r, dict) and r.get("date")],
                key=lambda r: r.get("date", ""),
                reverse=True,
            )[:3]
            ctx["sector_kpi_recent"] = list(reversed(recent))
    # Sector guidance (only when we are not in Generic fallback mode)
    if family != "generic":
        sector = ident.get("sector", "")
        industry = ident.get("industry", "")
        guidance = _build_sector_analysis_guidance(sector, industry, sector_kpis or {})
        if guidance:
            ctx["sector_analysis_guidance"] = guidance

    # ── Inject pre-computed financial health flags ─────────────
    financial_flags = fs.get("s10_financial_flags", [])
    if financial_flags:
        ctx["s10_financial_flags"] = financial_flags

    return ctx


def build_section_11_context(
    *,
    fs: dict, F: dict, identity: dict, latest_year: str,
    fc: str, url_to_id: dict, annual_years: list[str],
    peer_medians_formatted: dict,
    raw_peer_bench: dict, peer_bench: dict,
    raw_bal_sheet: dict, ident: dict,
    family: str = "generic",
    sector_kpis: dict | None = None,
) -> dict:
    """Build facts dict for Section 11: Peer Financial Benchmarking.

    Includes sector-specific emphasis on which multiples matter most
    (e.g., P/B for banks, EV/EBITDA for industrials, P/E for utilities).
    """
    # Count real peers (exclude "Peer Median" and subject rows)
    prof_comps = (raw_peer_bench.get("profitability_comps")
                  or peer_bench.get("profitability_comps") or [])
    peer_count = sum(
        1 for r in prof_comps
        if r.get("company") and r["company"] != "Peer Median"
        and "\u2605" not in r.get("company", "")
    )

    ctx = {
        **identity,
        "sector_family": family,
        "section_family": family,
        "sector_kpi_coverage": fs.get("_sector_kpi_coverage"),
        "peer_medians": peer_medians_formatted,
        "subject_metrics": {
            "revenue": _safe_get(F, "incStmt", "revenue_usd_m", latest_year),
            "revenue_growth": _safe_get(F, "incStmt", "revenue_growth_pct", latest_year),
            "gross_margin": _safe_get(F, "margins", "gross_margin_pct", latest_year),
            "operating_margin": _safe_get(F, "margins", "operating_margin_pct", latest_year),
            "net_margin": _safe_get(F, "margins", "net_margin_pct", latest_year),
            "roic": _safe_get(F, "returns", "roic_pct", latest_year),
            "roe": _safe_get(F, "returns", "roe_pct", latest_year),
            "fcf_margin": _safe_get(F, "cfStmt", "fcf_margin_pct", latest_year),
            "ev_to_ebitda": _safe_get(F, "valuation", "ev_to_ebitda", latest_year),
            "ev_to_sales": _safe_get(F, "valuation", "ev_to_sales", latest_year),
            "price_to_earnings": _safe_get(F, "valuation", "price_to_earnings", latest_year),
            "net_debt_to_ebitda": _safe_get(F, "balSheet", "net_debt_to_ebitda", latest_year),
            "rd_pct_revenue": _safe_get(F, "rd", "rd_pct_of_revenue", latest_year),
            "sbc_pct_revenue": _safe_get(F, "shareD", "sbc_pct_of_revenue", latest_year),
        },
        "peer_count": peer_count,
        "_cite_financials": fc,
    }

    # ── Inject sector-specific peer comparison guidance ──────────
    if sector_kpis:
        ctx["sector_kpis"] = _format_sector_kpis(sector_kpis)
        kpis_inner = sector_kpis.get("kpis", sector_kpis) if isinstance(sector_kpis, dict) else {}
        computed = kpis_inner.get("computedRatios") or kpis_inner.get("computedMetrics") or []
        if isinstance(computed, list) and computed:
            recent = sorted(
                [r for r in computed if isinstance(r, dict) and r.get("date")],
                key=lambda r: r.get("date", ""),
                reverse=True,
            )[:3]
            ctx["sector_kpi_recent"] = list(reversed(recent))

    if family != "generic":
        sector = ident.get("sector", "")
        industry = ident.get("industry", "")
        ctx["peer_emphasis"] = _build_peer_emphasis(sector, industry)

    # Family-specific subject metric focus
    if family == "banking":
        # Banks: EV-based multiples are not meaningful. Emphasize P/B and P/TBV.
        sm = ctx.get("subject_metrics", {})
        sm.pop("ev_to_ebitda", None)
        sm.pop("ev_to_sales", None)
        sm.pop("net_debt_to_ebitda", None)
        sm["price_to_book"] = _safe_get(F, "valuation", "price_to_book", latest_year)
        sm["price_to_tangible_book"] = _safe_get(F, "valuation", "price_to_tangible_book", latest_year)
        ctx["subject_metrics"] = sm
    elif family == "reits":
        # REITs: emphasize P/FFO and P/AFFO when sector KPIs provide per-share metrics.
        sm = ctx.get("subject_metrics", {})
        price = ident.get("price")
        rec = (ctx.get("sector_kpi_recent") or [])[-1] if isinstance(ctx.get("sector_kpi_recent"), list) and ctx.get("sector_kpi_recent") else {}
        try:
            ffo_ps = float(rec.get("ffoPerShare")) if rec and rec.get("ffoPerShare") is not None else None
        except Exception:
            ffo_ps = None
        try:
            affo_ps = float(rec.get("affoPerShare")) if rec and rec.get("affoPerShare") is not None else None
        except Exception:
            affo_ps = None
        if price and ffo_ps and ffo_ps > 0:
            sm["p_ffo"] = round(float(price) / ffo_ps, 2)
        if price and affo_ps and affo_ps > 0:
            sm["p_affo"] = round(float(price) / affo_ps, 2)
        ctx["subject_metrics"] = sm

    return ctx


def build_section_13_context(
    *,
    fs: dict, F: dict, identity: dict, latest_year: str,
    fc: str, url_to_id: dict, annual_years: list[str],
    risk_assess: dict, seg: dict, inc_stmt: dict,
    fwd_est: Any, ident: dict,
    sector_kpis: dict | None = None,
) -> dict:
    """Build facts dict for Section 13: Risk Assessment.

    Includes sector-specific risk metrics (NPA for banks, loss ratio for
    insurance, regulatory capital for financials) when available.
    """
    ctx = {
        **identity,
        "operating_margin": _safe_get(F, "margins", "operating_margin_pct", latest_year),
        "revenue_growth": _safe_get(F, "incStmt", "revenue_growth_pct", latest_year),
        "net_debt_to_ebitda": _safe_get(F, "balSheet", "net_debt_to_ebitda", latest_year),
        "fcf_margin": _safe_get(F, "cfStmt", "fcf_margin_pct", latest_year),
        "eps_diluted": _safe_get(inc_stmt, "eps_diluted", latest_year),
        "geographic_concentration": _safe_get(
            seg, "geographic_revenue_pct_of_total", latest_year),
        "segment_concentration": _safe_get(
            seg, "segment_revenue_pct_of_total", latest_year),
        "forward_estimates": sanitize_obj(fwd_est),
        "_cite_financials": fc,
        "qualitative": {
            "regulatory_framework": _cite(risk_assess.get("regulatory_framework"), url_to_id, fc),
            "top_risks": _cite(risk_assess.get("top_risks", []), url_to_id, fc),
            "litigation": _cite(risk_assess.get("litigation"), url_to_id, fc),
            "bear_case_triggers": _cite(risk_assess.get("bear_case_triggers", []), url_to_id, fc),
            "key_assumption_sensitivities": _cite(
                risk_assess.get("key_assumption_sensitivities", []), url_to_id, fc),
            "bear_case_conclusion": risk_assess.get("bear_case_conclusion"),
            "base_case_condition": risk_assess.get("base_case_condition"),
        },
    }

    # ── Inject sector-specific risk guidance ─────────────────────
    if sector_kpis:
        ctx["sector_kpis"] = _format_sector_kpis(sector_kpis)
    sector = ident.get("sector", "")
    industry = ident.get("industry", "")
    ctx["sector_risk_guidance"] = _build_sector_risk_guidance(sector, industry)

    return ctx


# ═══════════════════════════════════════════════════════════════
# 9. MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════

def distribute_sections(
    fact_sheet: dict,
    source_registry: dict,
    qualitative_data: dict | None = None,
    include_pricing: bool = True,
    memo_body: str = "",
    *,
    section_map: dict | None = None,
    scores: dict | None = None,
    company_name: str = "",
    ticker: str = "",
    industry_profile: dict | None = None,
) -> dict:
    """Build writer inputs for all sections (2-14).

    Returns a dict with keys ``"section_2"`` through ``"section_14"`` --
    each a dict ready for ``write_section()`` -- plus ``"agent_prompts"``
    with agent_1 / agent_2 / agent_3 prompt strings.
    """
    fs = deepcopy(fact_sheet)
    url_to_id = source_registry.get("url_to_id", {})
    fc = source_registry.get("financial_cite", "[F]")
    sources_appendix = source_registry.get("sources_appendix", "")

    section_map = section_map or {}
    scores = scores or {}
    industry_profile = industry_profile or {}

    # ── Determine latest year ───────────────────────────────────
    meta = fs.get("_meta", {})
    meta_latest = meta.get("latest_annual_year", "2025 FY")
    annual_years: list[str] = meta.get("annual_years", [])
    raw_root = meta.get("_raw", {})
    rev_check = (raw_root.get("s11_income_statement") or fs.get("s11_income_statement") or {}).get("revenue_usd_m", {})

    latest_year = meta_latest
    if rev_check.get(latest_year) is None or rev_check.get(latest_year) == 0:
        sorted_yrs = sorted(y for y in rev_check if rev_check[y] is not None and rev_check[y] != 0)
        if sorted_yrs:
            latest_year = sorted_yrs[-1]

    # ── Backward-compatible s6_peers shim ───────────────────────
    if not fs.get("s6_peers") and fs.get("s12_peer_benchmarking"):
        pb = fs["s12_peer_benchmarking"]
        fs["s6_peers"] = {
            "latest": _safe_get(pb, "peers_full", "latest", default=[]),
            "peer_medians": pb.get("peer_medians", {}),
            "by_symbol": _safe_get(pb, "peers_full", "by_symbol", default={}),
        }

    # ── Quantitative shorthands ─────────────────────────────────
    ident = fs.get("s1_identity", {})
    seg = fs.get("s2_s4_revenue_splits", {})
    rd = fs.get("s4_rd", {})
    margins = fs.get("s5_subject_margins", {})
    share_d = fs.get("s5_share_data", {})
    cap = fs.get("s2_capital_structure", {})
    wc = fs.get("s7_working_capital") or {}
    beat_miss = fs.get("s9_guidance_beat_miss", {})
    cap_alloc = fs.get("s9_capital_allocation", {})
    inc_stmt = fs.get("s11_income_statement", {})
    cf_stmt = fs.get("s11_cash_flow", {})
    bal_sheet = fs.get("s11_balance_sheet", {})
    returns = fs.get("s11_returns", {})
    valuation = fs.get("s13_valuation") or fs.get("s12_valuation", {})
    peers_obj = fs.get("s6_peers", {})
    peer_bench = fs.get("s12_peer_benchmarking", {})
    fwd_est = fs.get("s10_s13_forward_estimates") or fs.get("s10_s12_forward_estimates") or valuation.get("forward_estimates", [])

    # ── Raw numeric shorthands ──────────────────────────────────
    R = raw_root
    raw_seg = R.get("s2_s4_revenue_splits") or seg
    raw_rd = R.get("s4_rd") or rd or {}
    raw_margins = R.get("s5_subject_margins") or margins
    raw_share_d = R.get("s5_share_data") or share_d
    raw_cap = R.get("s2_capital_structure") or cap
    raw_wc = R.get("s7_working_capital") or wc
    raw_cap_alloc = R.get("s9_capital_allocation") or cap_alloc
    raw_inc = R.get("s11_income_statement") or inc_stmt
    raw_cf = R.get("s11_cash_flow") or cf_stmt
    raw_bal = R.get("s11_balance_sheet") or bal_sheet
    raw_returns = R.get("s11_returns") or returns
    # Prefer the enriched peer benchmarking block (peer selection can update it post-cleaning).
    raw_peer_bench = peer_bench or R.get("s12_peer_benchmarking") or {}

    # ── Sector/subsector detection for schema dispatch ────────
    sector_kpis_obj = fs.get("_sec_sector_kpis") or {}
    # Prefer SEC-derived sector module label (more reliable than FMP subsector strings)
    subsector = sector_kpis_obj.get("sector") or ident.get("subsector") or ""
    sector_family = _get_sector_family(subsector, ident.get("sector", ""), ident.get("industry", ""))

    # Some canonical sectors (Telecom / Consumer Disc) don't have dedicated SEC KPI modules.
    # If the SIC-based module label is misleading (e.g. "tech"), do not inject those tables.
    if sector_family in {"telecom", "consumer_disc"}:
        fs["_sec_sector_kpis"] = {}
        sector_kpis_obj = {}

    # Subject-side KPI coverage gating: if the subject itself doesn't report any of the
    # sector KPIs we rely on, fall back to the generic S10/S11 schemas + comps.
    sector_kpi_coverage = _subject_kpi_coverage(sector_kpis_obj, sector_family)
    if sector_kpi_coverage.get("required") and sector_kpi_coverage.get("present") == 0:
        sector_family = "generic"
        # Disable sector KPI tables/guidance downstream (subject is the culprit).
        fs["_sec_sector_kpis"] = {}
        sector_kpis_obj = {}

    # Persist for downstream (writers/debug/probes)
    fs["_sector_family"] = sector_family
    fs["_sector_kpi_coverage"] = sector_kpi_coverage

    raw_val = R.get("s13_valuation") or R.get("s12_valuation") or valuation

    # ── Qualitative shorthands (sanitized) ──────────────────────
    comp_overview = sanitize_obj(fs.get("s3_company_overview", {}))
    hist_mile = sanitize_obj(fs.get("s4_history_milestones", {}))
    comp_moats = sanitize_obj(fs.get("s5_competitive_moats", {}))
    prod_tech = sanitize_obj(fs.get("s5_product_technology", {}))
    ind_comp = sanitize_obj(fs.get("s6_industry_competitive", {}))
    cust_analysis = sanitize_obj(fs.get("s7_customer_analysis", {}))
    mgmt_cap = sanitize_obj(fs.get("s8_management_capital", {}))
    growth_prosp = sanitize_obj(fs.get("s9_growth_prospects", {}))
    risk_assess = sanitize_obj(fs.get("s13_risk_assessment") or fs.get("s12_risk_assessment", {}))

    # ── NM sanitization: subject time-series ────────────────────
    nm_sanitize_year_obj(raw_returns.get("roe_pct", {}), "roe_pct")
    nm_sanitize_year_obj(raw_returns.get("roic_pct", {}), "roic_pct")
    if raw_margins.get("roic_pct"):
        nm_sanitize_year_obj(raw_margins["roic_pct"], "roic_pct")
    if raw_margins.get("roe_pct"):
        nm_sanitize_year_obj(raw_margins["roe_pct"], "roe_pct")
    nm_sanitize_year_obj(raw_wc.get("dso_days", {}), "dso_days")
    nm_sanitize_year_obj(raw_wc.get("dpo_days", {}), "dpo_days")
    nm_sanitize_year_obj(raw_wc.get("dio_days", {}), "dio_days")
    nm_sanitize_year_obj(raw_wc.get("cash_conversion_cycle_days", {}), "cash_conversion_cycle_days")
    nm_sanitize_year_obj(raw_inc.get("revenue_growth_pct", {}), "revenue_growth_pct")
    if raw_inc.get("operating_income_growth_pct"):
        nm_sanitize_year_obj(raw_inc["operating_income_growth_pct"], "operating_income_growth_pct")
    if raw_inc.get("eps_diluted_growth_pct"):
        nm_sanitize_year_obj(raw_inc["eps_diluted_growth_pct"], "eps_diluted_growth_pct")
    nm_sanitize_year_obj(raw_bal.get("net_debt_to_ebitda", {}), "net_debt_to_ebitda")
    nm_sanitize_year_obj(raw_bal.get("interest_coverage_ratio", {}), "interest_coverage_ratio")
    if raw_cap.get("debt_to_equity_ratio"):
        nm_sanitize_year_obj(raw_cap["debt_to_equity_ratio"], "debt_to_equity_ratio")

    # ── NM sanitization: peer tables ────────────────────────────
    for tbl_key in ("profitability_comps", "growth_comps", "valuation_comps",
                    "leverage_comps", "efficiency_comps"):
        if raw_peer_bench.get(tbl_key):
            nm_sanitize_peer_table(raw_peer_bench[tbl_key])

    # ── NM sanitization: peer medians (prose) ───────────────────
    if peer_bench.get("peer_medians"):
        for pm_field in list(peer_bench["peer_medians"].keys()):
            thr = _get_threshold(pm_field)
            if thr:
                peer_bench["peer_medians"][pm_field] = nm_check(
                    peer_bench["peer_medians"][pm_field], pm_field)

    # Belt-and-suspenders: formatted counterparts
    nm_sanitize_year_obj(returns.get("roe_pct", {}), "roe_pct")
    nm_sanitize_year_obj(returns.get("roic_pct", {}), "roic_pct")
    nm_sanitize_year_obj(wc.get("dso_days", {}), "dso_days")
    nm_sanitize_year_obj(wc.get("dpo_days", {}), "dpo_days")
    nm_sanitize_year_obj(wc.get("dio_days", {}), "dio_days")
    nm_sanitize_year_obj(wc.get("cash_conversion_cycle_days", {}), "cash_conversion_cycle_days")

    # ── Pre-formatted quantitative for prose ────────────────────
    F = {
        "incStmt":    fmt_obj(inc_stmt),
        "margins":    fmt_obj(margins),
        "cap":        fmt_obj(cap),
        "valuation":  fmt_obj(valuation),
        "shareD":     fmt_obj(share_d),
        "cfStmt":     fmt_obj(cf_stmt),
        "balSheet":   fmt_obj(bal_sheet),
        "returns":    fmt_obj(returns),
        "peers":      fmt_obj(peers_obj),
        "rd":         fmt_obj(rd),
        "wc":         fmt_obj(wc),
        "capAlloc":   fmt_obj(cap_alloc),
        "beatMiss":   fmt_obj(beat_miss),
    }

    identity = _build_identity(ident, fc)

    # ── Consolidated revenue ────────────────────────────────────
    consolidated_revenue = _safe_get(raw_inc, "revenue_usd_m", latest_year, default=0) or 0

    # ── Pre-computed table data ─────────────────────────────────
    segment_rows, seg_data_quality = build_segment_rows(
        raw_seg, latest_year, consolidated_revenue, ident.get("country", ""))

    fin_tables = build_financial_table_rows({
        "incStmt": raw_inc, "margins": raw_margins, "cfStmt": raw_cf,
        "balSheet": raw_bal, "returns": raw_returns, "wc": raw_wc,
        "capAlloc": raw_cap_alloc, "rd": raw_rd, "shareD": raw_share_d,
    }, annual_years)

    # Sector KPI tables for Section 10/11 (writer inputs). These are derived
    # from `_sec_sector_kpis` and provide sector-appropriate metrics (FFO/AFFO,
    # NIM/CET1, combined ratio, etc.).
    sector_kpi_tables = {}
    if sector_kpis_obj:
        try:
            from pipeline.sector_tables import build_sector_kpi_tables
            sector_kpi_tables = build_sector_kpi_tables(sector_kpis_obj) or {}
        except Exception:
            sector_kpi_tables = {}

    peer_tables = build_peer_comp_tables(raw_peer_bench)
    peer_medians_formatted = fmt_obj(peer_bench.get("peer_medians", {}))

    # ── Industry detection ──────────────────────────────────────
    raw_industry = (
        industry_profile.get("industry")
        or meta.get("industry")
        or ident.get("industry")
        or ""
    )
    raw_sector = ident.get("sector", "")
    industry = _normalize_industry(raw_industry, sector=raw_sector)
    skip_dcf = industry in SKIP_DCF_INDUSTRIES
    alt_val_config = (INDUSTRY_VALUATION_CONFIG.get(industry) if not skip_dcf else None) or None
    use_alt_valuation = alt_val_config is not None

    # Specific DDM industries (regulated utilities with stable dividends)
    _DDM_INDUSTRY_SET = frozenset({
        "Utilities - Regulated Electric", "Utilities - Regulated Gas",
        "Utilities - Regulated Water", "Utilities - Diversified",
    })

    if skip_dcf:
        valuation_mode = "bank_equity"
    elif use_alt_valuation and industry in _DDM_INDUSTRY_SET:
        valuation_mode = "ddm"
    elif use_alt_valuation:
        valuation_mode = "industry_peer"
    else:
        valuation_mode = "dcf"

    # Sector-family fallback: if the SEC module detected REIT/energy but the
    # FMP industry string didn't match any INDUSTRY_VALUATION_CONFIG entry,
    # override to industry_peer with a sensible default config.
    if valuation_mode == "dcf" and sector_family == "reits":
        alt_val_config = INDUSTRY_VALUATION_CONFIG.get("REIT - Diversified")
        use_alt_valuation = True
        valuation_mode = "industry_peer"
    elif valuation_mode == "dcf" and sector_family == "energy":
        alt_val_config = INDUSTRY_VALUATION_CONFIG.get("Oil & Gas E&P")
        use_alt_valuation = True
        valuation_mode = "industry_peer"

    # ── Current price data ──────────────────────────────────────
    current_price = ident.get("price")
    current_price_fmt = f"${current_price:.2f}" if current_price is not None else None
    _range_parts = (ident.get("range") or "").split("-")
    price_52wk_low = None
    price_52wk_high = None
    if len(_range_parts) == 2:
        try:
            price_52wk_low = float(_range_parts[0])
            price_52wk_high = float(_range_parts[1])
        except ValueError:
            pass
    price_52wk_low_fmt = f"${price_52wk_low:.2f}" if price_52wk_low is not None else None
    price_52wk_high_fmt = f"${price_52wk_high:.2f}" if price_52wk_high is not None else None

    # ── Peer multiples for S12 ──────────────────────────────────
    peer_latest = [
        {
            "symbol": p.get("symbol"),
            "ev_to_sales": p.get("ev_to_sales"),
            "ev_to_ebitda": p.get("ev_to_ebitda"),
            "price_to_fcf": p.get("price_to_fcf"),
            "price_to_earnings": p.get("price_to_earnings"),
            "price_to_book": p.get("price_to_book"),
            "price_to_tangible_book": p.get("price_to_tangible_book"),
            "dividend_yield_pct": p.get("dividend_yield_pct"),
            "roe_pct": p.get("roe_pct"),
            "revenue_growth_pct": p.get("revenue_growth_pct"),
            "operating_margin_pct": p.get("operating_margin_pct"),
            "roic_pct": p.get("roic_pct"),
        }
        for p in (peers_obj.get("latest") or [])
    ]
    if not peer_latest and valuation.get("peers", {}).get("by_symbol"):
        for sym, year_data in valuation["peers"]["by_symbol"].items():
            years_sorted = sorted(year_data.keys())
            if years_sorted:
                d = year_data[years_sorted[-1]]
                peer_latest.append({
                    "symbol": sym,
                    "ev_to_sales": d.get("ev_to_sales"),
                    "ev_to_ebitda": d.get("ev_to_ebitda"),
                    "price_to_fcf": d.get("price_to_fcf"),
                    "price_to_earnings": d.get("price_to_earnings"),
                    "price_to_book": d.get("price_to_book"),
                    "price_to_tangible_book": d.get("price_to_tangible_book"),
                    "dividend_yield_pct": d.get("dividend_yield_pct"),
                    "roe_pct": d.get("roe_pct"),
                    "revenue_growth_pct": d.get("revenue_growth_pct"),
                    "operating_margin_pct": d.get("operating_margin_pct"),
                    "roic_pct": d.get("roic_pct"),
                })

    subject_multiples = {
        "ev_to_sales":       _safe_get(valuation, "ev_to_sales", latest_year),
        "ev_to_ebitda":      _safe_get(valuation, "ev_to_ebitda", latest_year),
        "ev_to_fcf":         _safe_get(valuation, "ev_to_fcf", latest_year),
        "price_to_fcf":      _safe_get(valuation, "price_to_fcf", latest_year),
        "price_to_earnings": _safe_get(valuation, "price_to_earnings", latest_year),
        "price_to_book":     _safe_get(valuation, "price_to_book", latest_year),
        "fcf_yield_pct":     _safe_get(valuation, "fcf_yield_pct", latest_year),
        "earnings_yield_pct": _safe_get(valuation, "earnings_yield_pct", latest_year),
    }
    subject_multiples_fmt = {k: fmt_num(v, "x") if k not in ("fcf_yield_pct", "earnings_yield_pct") else fmt_num(v, "pct") for k, v in subject_multiples.items()}

    # Exclude subject from peer list to avoid duplicate rows in tables
    _ticker_up = (ticker or "").upper()
    _subj_peer = next(
        (p for p in peer_latest if (p.get("symbol") or "").upper() == _ticker_up),
        {},
    )
    _peer_latest_excl = [
        p for p in peer_latest if (p.get("symbol") or "").upper() != _ticker_up
    ]

    peer_medians = peers_obj.get("peer_medians")
    if not peer_medians and _peer_latest_excl:
        peer_medians = {
            "ev_to_sales":       _compute_median([p.get("ev_to_sales") for p in _peer_latest_excl]),
            "ev_to_ebitda":      _compute_median([p.get("ev_to_ebitda") for p in _peer_latest_excl]),
            "price_to_fcf":      _compute_median([p.get("price_to_fcf") for p in _peer_latest_excl]),
            "price_to_earnings": _compute_median([p.get("price_to_earnings") for p in _peer_latest_excl]),
            "price_to_book":     _compute_median([p.get("price_to_book") for p in _peer_latest_excl]),
        }
    peer_medians_fmt = {k: fmt_num(v, "x") for k, v in (peer_medians or {}).items()} if peer_medians else None

    # NM thresholds for peer table outlier capping
    _NM_CAPS_PT = {
        "p_e": (0, 150), "p_fcf": (0, 150), "p_b": (0, 50), "p_tbv": (0, 50),
        "ev_revenue": (0, 50), "ev_ebitda": (0, 100), "roe": (-200, 200),
        "div_yield": (-50, 100),
    }

    def _nm_cap_pt(val, field):
        """Return None if val exceeds NM cap for field."""
        if val is None or field not in _NM_CAPS_PT:
            return val
        try:
            n = float(val)
        except (TypeError, ValueError):
            return val
        lo, hi = _NM_CAPS_PT[field]
        return None if n < lo or n > hi else val

    def _local_median(vals_list):
        _clean = sorted(x for x in vals_list if x is not None)
        if not _clean:
            return None
        _mid = len(_clean) // 2
        return round(_clean[_mid], 2) if len(_clean) % 2 else round((_clean[_mid - 1] + _clean[_mid]) / 2, 2)

    if skip_dcf:
        # Financial sector (banks, insurance): P/E, P/B, P/TBV, Div Yield, ROE
        _fin_peer_entries = [
            {"company_name": p.get("symbol"),
             "p_e": _nm_cap_pt(p.get("price_to_earnings"), "p_e"),
             "p_b": _nm_cap_pt(p.get("price_to_book"), "p_b"),
             "p_tbv": _nm_cap_pt(p.get("price_to_tangible_book"), "p_tbv"),
             "div_yield": _nm_cap_pt(p.get("dividend_yield_pct"), "div_yield"),
             "roe": _nm_cap_pt(p.get("roe_pct"), "roe")}
            for p in _peer_latest_excl
        ]
        precomputed_peer_table = {
            "subject_company": {
                "company_name": ticker or company_name,
                "p_e": _nm_cap_pt(subject_multiples.get("price_to_earnings") or _subj_peer.get("price_to_earnings"), "p_e"),
                "p_b": _nm_cap_pt(subject_multiples.get("price_to_book") or _subj_peer.get("price_to_book"), "p_b"),
                "p_tbv": _nm_cap_pt(_subj_peer.get("price_to_tangible_book") or _safe_get(valuation, "price_to_tangible_book", latest_year), "p_tbv"),
                "div_yield": _nm_cap_pt(_subj_peer.get("dividend_yield_pct") or _safe_get(valuation, "dividend_yield_pct", latest_year), "div_yield"),
                "roe": _nm_cap_pt(_subj_peer.get("roe_pct") or _safe_get(valuation, "roe_pct", latest_year), "roe"),
            },
            "peers": _fin_peer_entries,
            "peer_median": {
                "p_e": _local_median([p.get("p_e") for p in _fin_peer_entries]),
                "p_b": _local_median([p.get("p_b") for p in _fin_peer_entries]),
                "p_tbv": _local_median([p.get("p_tbv") for p in _fin_peer_entries]),
                "div_yield": _local_median([p.get("div_yield") for p in _fin_peer_entries]),
                "roe": _local_median([p.get("roe") for p in _fin_peer_entries]),
            },
            "is_financial": True,
        }
    else:
        _peer_entries_raw = [
            {"company_name": p.get("symbol"),
             "ev_revenue": _nm_cap_pt(p.get("ev_to_sales"), "ev_revenue"),
             "ev_ebitda": _nm_cap_pt(p.get("ev_to_ebitda"), "ev_ebitda"),
             "p_fcf": _nm_cap_pt(p.get("price_to_fcf"), "p_fcf"),
             "p_e": _nm_cap_pt(p.get("price_to_earnings"), "p_e"),
             "p_b": _nm_cap_pt(p.get("price_to_book"), "p_b")}
            for p in _peer_latest_excl
        ]

        precomputed_peer_table = {
            "subject_company": {
                "company_name": ticker or company_name,
                "ev_revenue": _nm_cap_pt(subject_multiples.get("ev_to_sales"), "ev_revenue"),
                "ev_ebitda": _nm_cap_pt(subject_multiples.get("ev_to_ebitda"), "ev_ebitda"),
                "p_fcf": _nm_cap_pt(subject_multiples.get("price_to_fcf"), "p_fcf"),
                "p_e": _nm_cap_pt(subject_multiples.get("price_to_earnings"), "p_e"),
                "p_b": _nm_cap_pt(subject_multiples.get("price_to_book"), "p_b"),
            },
            "peers": _peer_entries_raw,
            "peer_median": {
                "ev_revenue": _local_median([p.get("ev_revenue") for p in _peer_entries_raw]),
                "ev_ebitda": _local_median([p.get("ev_ebitda") for p in _peer_entries_raw]),
                "p_fcf": _local_median([p.get("p_fcf") for p in _peer_entries_raw]),
                "p_e": _local_median([p.get("p_e") for p in _peer_entries_raw]),
                "p_b": _local_median([p.get("p_b") for p in _peer_entries_raw]),
            },
        }

    # ── Implied fair value computations ─────────────────────────
    pe_implied_fair_value = None
    pe_implied_fair_value_fmt = None
    pe_implied_method_note = None
    alt_implied_fair_value = None
    alt_implied_fair_value_fmt = None
    alt_implied_method_note = None
    alt_implied_method_used = None

    def _to_float(val: Any) -> float | None:
        """Coerce a value to float, stripping currency/pct formatting."""
        if val is None or val == "" or val == "NM":
            return None
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            import re as _re
            cleaned = _re.sub(r"[,$%xX]", "", val).strip()
            try:
                return float(cleaned)
            except (ValueError, TypeError):
                return None
        return None

    is_reit = industry.startswith("REIT") or industry == "Real Estate Investment Trust"

    if skip_dcf:
        _eps = _to_float(_safe_get(raw_inc, "eps_diluted", latest_year))
        _bvps = _to_float(
            _safe_get(raw_bal, "book_value_per_share", latest_year)
            or _safe_get(raw_bal, "bvps", latest_year)
        )
        _pm_pe = _to_float(
            _safe_get(peers_obj, "peer_medians", "price_to_earnings")
            or _safe_get(fs, "s12_peer_benchmarking", "peer_medians", "price_to_earnings")
            or (_safe_get(peer_medians, "price_to_earnings") if peer_medians else None)
        )
        _pm_pb = _to_float(
            _safe_get(peers_obj, "peer_medians", "price_to_book")
            or _safe_get(fs, "s12_peer_benchmarking", "peer_medians", "price_to_book")
            or (_safe_get(peer_medians, "price_to_book") if peer_medians else None)
        )
        if is_reit:
            # Try P/FFO first from REIT valuation comps
            _reit_vc = _safe_get(fs, "s12_peer_benchmarking", "valuation_comps_reit") or []
            _reit_m = next((r for r in _reit_vc if (r.get("company") or "").lower() == "peer median"), {})
            _pm_pffo_sk = _to_float(_reit_m.get("p_ffo"))
            _sk_obj = fs.get("_sec_sector_kpis") or {}
            _sk_inner = _sk_obj.get("kpis") or _sk_obj
            _sk_rows = (
                _sk_inner.get("computedMetrics")
                or _sk_inner.get("computedRatios")
                or _sk_inner.get("computed")
                or []
            ) if isinstance(_sk_inner, dict) else []
            _ffo_ps_sk = None
            if isinstance(_sk_rows, list) and _sk_rows:
                _lk = max(_sk_rows, key=lambda r: r.get("date", ""))
                try:
                    _ffo_ps_sk = float(_lk.get("ffoPerShare")) if _lk.get("ffoPerShare") is not None else None
                except (TypeError, ValueError):
                    _ffo_ps_sk = None

            if _ffo_ps_sk and _ffo_ps_sk > 0 and _pm_pffo_sk and _pm_pffo_sk > 0:
                pe_implied_fair_value = round(_ffo_ps_sk * _pm_pffo_sk * 100) / 100
                pe_implied_fair_value_fmt = f"${pe_implied_fair_value:.2f}"
                pe_implied_method_note = f"Implied from FFO/share of ${_ffo_ps_sk:.2f} x peer median P/FFO of {_pm_pffo_sk:.1f}x"
            elif _bvps and _bvps > 0 and _pm_pb and _pm_pb > 0:
                pe_implied_fair_value = round(_bvps * _pm_pb * 100) / 100
                pe_implied_fair_value_fmt = f"${pe_implied_fair_value:.2f}"
                pe_implied_method_note = f"Implied from BVPS of ${_bvps:.2f} x peer median P/B of {_pm_pb:.1f}x"
            elif _eps and _eps > 0 and _pm_pe and _pm_pe > 0:
                pe_implied_fair_value = round(_eps * _pm_pe * 100) / 100
                pe_implied_fair_value_fmt = f"${pe_implied_fair_value:.2f}"
                pe_implied_method_note = f"Implied from EPS of ${_eps:.2f} x peer median P/E of {_pm_pe:.1f}x (P/FFO and P/B unavailable)"
            else:
                pe_implied_method_note = "Not available -- insufficient FFO, book value, or peer data"
        else:
            if _eps and _eps > 0 and _pm_pe and _pm_pe > 0:
                pe_implied_fair_value = round(_eps * _pm_pe * 100) / 100
                pe_implied_fair_value_fmt = f"${pe_implied_fair_value:.2f}"
                pe_implied_method_note = f"Implied from EPS of ${_eps:.2f} x peer median P/E of {_pm_pe:.1f}x"
            elif _bvps and _bvps > 0 and _pm_pb and _pm_pb > 0:
                pe_implied_fair_value = round(_bvps * _pm_pb * 100) / 100
                pe_implied_fair_value_fmt = f"${pe_implied_fair_value:.2f}"
                pe_implied_method_note = f"Implied from BVPS of ${_bvps:.2f} x peer median P/B of {_pm_pb:.1f}x (EPS negative, P/B fallback)"
            else:
                pe_implied_method_note = "Not available -- insufficient EPS or peer P/E data"

    if use_alt_valuation:
        _ebitda = _to_float(_safe_get(raw_inc, "ebitda_usd_m", latest_year))
        _net_debt = _to_float(_safe_get(raw_cap, "net_debt_usd_m", latest_year, default=0)) or 0
        _shares = _to_float(_safe_get(raw_share_d, "shares_diluted_millions", latest_year))
        _eps = _to_float(_safe_get(raw_inc, "eps_diluted", latest_year))
        _pm_ev_eb = _to_float(
            _safe_get(peers_obj, "peer_medians", "ev_to_ebitda")
            or _safe_get(fs, "s12_peer_benchmarking", "peer_medians", "ev_to_ebitda")
            or (_safe_get(peer_medians, "ev_to_ebitda") if peer_medians else None)
        )
        _pm_pe = _to_float(
            _safe_get(peers_obj, "peer_medians", "price_to_earnings")
            or _safe_get(fs, "s12_peer_benchmarking", "peer_medians", "price_to_earnings")
            or (_safe_get(peer_medians, "price_to_earnings") if peer_medians else None)
        )

        if alt_val_config["method"] == "ev_ebitda":
            if _ebitda and _ebitda > 0 and _pm_ev_eb and _pm_ev_eb > 0 and _shares and _shares > 0:
                implied_ev = _ebitda * _pm_ev_eb
                implied_equity = implied_ev - _net_debt
                if implied_equity > 0:
                    alt_implied_fair_value = round((implied_equity / _shares) * 100) / 100
                    alt_implied_fair_value_fmt = f"${alt_implied_fair_value:.2f}"
                    alt_implied_method_used = "ev_ebitda"
                    alt_implied_method_note = (
                        f"Implied from EBITDA of {fmt_num(_ebitda, 'usd_m')} x peer median EV/EBITDA of "
                        f"{_pm_ev_eb:.1f}x = implied EV of {fmt_num(implied_ev, 'usd_m')}, less net debt of "
                        f"{fmt_num(_net_debt, 'usd_m')}, over {fmt_num(_shares, 'millions')} diluted shares"
                    )
            # Fallback to P/E
            if alt_implied_fair_value is None:
                if _eps and _eps > 0 and _pm_pe and _pm_pe > 0:
                    alt_implied_fair_value = round(_eps * _pm_pe * 100) / 100
                    alt_implied_fair_value_fmt = f"${alt_implied_fair_value:.2f}"
                    alt_implied_method_used = "pe"
                    alt_implied_method_note = f"Implied from EPS of ${_eps:.2f} x peer median P/E of {_pm_pe:.1f}x (EV/EBITDA preferred but unavailable)"
                else:
                    alt_implied_method_note = "Not available -- EV/EBITDA and P/E fallback both unavailable"
        elif alt_val_config["method"] == "pe":
            # REITs: prefer FFO/share from XBRL over EPS
            _ffo_ps = None
            _affo_ps = None
            if is_reit and sector_kpis_obj:
                _kpis_inner = sector_kpis_obj.get("kpis", sector_kpis_obj) if isinstance(sector_kpis_obj, dict) else {}
                _kpi_rows = (_kpis_inner.get("computedMetrics") or _kpis_inner.get("computedRatios") or []) if isinstance(_kpis_inner, dict) else []
                # Sort by date descending and take the most recent
                _kpi_rows_sorted = sorted([r for r in _kpi_rows if isinstance(r, dict) and r.get("date")], key=lambda r: r["date"], reverse=True)
                _latest_kpi = _kpi_rows_sorted[0] if _kpi_rows_sorted else {}
                try:
                    _ffo_ps = float(_latest_kpi.get("ffoPerShare")) if _latest_kpi.get("ffoPerShare") is not None else None
                except (TypeError, ValueError):
                    _ffo_ps = None
                try:
                    _affo_ps = float(_latest_kpi.get("affoPerShare")) if _latest_kpi.get("affoPerShare") is not None else None
                except (TypeError, ValueError):
                    _affo_ps = None

            # REITs: get actual peer median P/FFO from REIT valuation comps
            _reit_val_comps = _safe_get(fs, "s12_peer_benchmarking", "valuation_comps_reit") or []
            _reit_med = next(
                (r for r in _reit_val_comps if (r.get("company") or "").lower() == "peer median"),
                {},
            )
            _pm_pffo = _to_float(_reit_med.get("p_ffo"))

            if is_reit and _ffo_ps and _ffo_ps > 0 and _pm_pffo and _pm_pffo > 0:
                # P/FFO: FFO/share × actual peer median P/FFO
                alt_implied_fair_value = round(_ffo_ps * _pm_pffo * 100) / 100
                alt_implied_fair_value_fmt = f"${alt_implied_fair_value:.2f}"
                alt_implied_method_used = "p_ffo"
                alt_implied_method_note = (
                    f"Implied from FFO/share of ${_ffo_ps:.2f} x peer median P/FFO of {_pm_pffo:.1f}x"
                )
            elif is_reit and _ffo_ps and _ffo_ps > 0 and _pm_pe and _pm_pe > 0:
                # Fallback: use P/E as proxy for P/FFO
                alt_implied_fair_value = round(_ffo_ps * _pm_pe * 100) / 100
                alt_implied_fair_value_fmt = f"${alt_implied_fair_value:.2f}"
                alt_implied_method_used = "p_ffo"
                alt_implied_method_note = (
                    f"Implied from FFO/share of ${_ffo_ps:.2f} x peer median P/E of {_pm_pe:.1f}x "
                    f"(P/E used as proxy for P/FFO)"
                )
            elif _eps and _eps > 0 and _pm_pe and _pm_pe > 0:
                alt_implied_fair_value = round(_eps * _pm_pe * 100) / 100
                alt_implied_fair_value_fmt = f"${alt_implied_fair_value:.2f}"
                alt_implied_method_used = "pe"
                _label = "P/FFO (EPS fallback)" if is_reit else "P/E"
                alt_implied_method_note = f"Implied from EPS of ${_eps:.2f} x peer median {_label} of {_pm_pe:.1f}x"
            # Fallback to EV/EBITDA
            if alt_implied_fair_value is None:
                if _ebitda and _ebitda > 0 and _pm_ev_eb and _pm_ev_eb > 0 and _shares and _shares > 0:
                    implied_ev = _ebitda * _pm_ev_eb
                    implied_equity = implied_ev - _net_debt
                    if implied_equity > 0:
                        alt_implied_fair_value = round((implied_equity / _shares) * 100) / 100
                        alt_implied_fair_value_fmt = f"${alt_implied_fair_value:.2f}"
                        alt_implied_method_used = "ev_ebitda"
                        alt_implied_method_note = f"Implied from EBITDA of {fmt_num(_ebitda, 'usd_m')} x peer median EV/EBITDA of {_pm_ev_eb:.1f}x (P/E preferred but EPS negative)"
                    else:
                        alt_implied_method_note = "Not available -- EPS negative and EV/EBITDA fallback implies negative equity"
                else:
                    alt_implied_method_note = "Not available -- insufficient EPS and EV/EBITDA data"

    # ── DCF anchors ─────────────────────────────────────────────
    if skip_dcf:
        dcf_anchors = {"valuation_method": "bank_equity", "industry": industry, "current_price": current_price}
    elif valuation_mode == "ddm":
        _dps_latest_anch = _safe_get(raw_cap_alloc, "dividend_per_share", latest_year)
        _payout_ratio_anch = _safe_get(raw_cap_alloc, "dividend_payout_ratio_pct", latest_year)
        _roe_anch = _safe_get(raw_returns, "roe_pct", latest_year) or _safe_get(raw_margins, "roe_pct", latest_year)
        dcf_anchors = {
            "valuation_method": "ddm", "industry": industry,
            "dividend_per_share": _dps_latest_anch,
            "dividend_payout_ratio_pct": _payout_ratio_anch,
            "roe_pct": _roe_anch,
            "eps_diluted": _safe_get(raw_inc, "eps_diluted", latest_year),
            "current_price": current_price,
        }
    elif use_alt_valuation:
        dcf_anchors = {
            "valuation_method": "industry_peer_comp", "industry": industry,
            "primary_metric": alt_val_config["method"], "current_price": current_price,
            "ebitda_usd_m": _safe_get(raw_inc, "ebitda_usd_m", latest_year),
            "net_debt_usd_m": _safe_get(raw_cap, "net_debt_usd_m", latest_year, default=0),
            "shares_diluted_m": _safe_get(raw_share_d, "shares_diluted_millions", latest_year, default=1),
            "eps_diluted": _safe_get(raw_inc, "eps_diluted", latest_year),
            "revenue_usd_m": _safe_get(raw_inc, "revenue_usd_m", latest_year),
            "operating_margin_pct": _safe_get(raw_margins, "operating_margin_pct", latest_year),
        }
    else:
        dcf_anchors = {
            "revenue_usd_m": _safe_get(raw_inc, "revenue_usd_m", latest_year, default=0),
            "fcf_margin_pct": _safe_get(raw_cf, "fcf_margin_pct", latest_year, default=20),
            "net_debt_usd_m": _safe_get(raw_cap, "net_debt_usd_m", latest_year, default=0),
            "shares_diluted_m": _safe_get(raw_share_d, "shares_diluted_millions", latest_year, default=1),
            "sbc_usd_m": _safe_get(raw_share_d, "sbc_usd_m", latest_year, default=0),
            "sbc_pct_of_revenue": _safe_get(raw_share_d, "sbc_pct_of_revenue", latest_year, default=0),
            "effective_tax_rate_pct": _safe_get(raw_margins, "effective_tax_rate_pct", latest_year, default=21),
            "terminal_tax_rate_pct": 21,
            "capex_pct_of_revenue": _safe_get(raw_cf, "capex_pct_of_revenue", latest_year, default=0),
            "da_pct_of_revenue": _safe_get(raw_inc, "da_pct_of_revenue", latest_year, default=0),
            "net_capex_pct_of_revenue": _safe_get(raw_cf, "capex_pct_of_revenue", latest_year, default=0),
            "roic_pct": _safe_num(_safe_get(raw_margins, "roic_pct", latest_year), 0),
            "operating_margin_pct": _safe_get(raw_margins, "operating_margin_pct", latest_year, default=0),
            "reported_currency": meta.get("reported_currency", "USD"),
            "current_price": current_price,
        }

    # ── Quant inputs formatted for prose ────────────────────────
    quant_inputs_formatted = {
        "revenue": fmt_num(_safe_get(raw_inc, "revenue_usd_m", latest_year), "usd_m"),
        "revenue_5yr_cagr": fmt_num(raw_inc.get("revenue_cagr_5yr_pct"), "pct"),
        "revenue_3yr_cagr": fmt_num(raw_inc.get("revenue_cagr_3yr_pct"), "pct"),
        "revenue_growth": fmt_num(_safe_get(raw_inc, "revenue_growth_pct", latest_year), "pct"),
        "operating_margin": fmt_num(_safe_get(raw_margins, "operating_margin_pct", latest_year), "pct"),
        "ebitda_margin": fmt_num(_safe_get(raw_inc, "ebitda_margin_pct", latest_year), "pct"),
        "ebitda": fmt_num(_safe_get(raw_inc, "ebitda_usd_m", latest_year), "usd_m"),
        "fcf": fmt_num(_safe_get(raw_cf, "free_cash_flow_usd_m", latest_year), "usd_m"),
        "fcf_margin": fmt_num(_safe_get(raw_cf, "fcf_margin_pct", latest_year), "pct"),
        "roic": fmt_num(_safe_num(_safe_get(raw_margins, "roic_pct", latest_year), None), "pct"),
        "net_debt": fmt_num(_safe_get(raw_cap, "net_debt_usd_m", latest_year), "usd_m"),
        # net_debt_ebitda removed from S12 context — leverage ratios are in
        # precomputed_leverage_comps; including ND/EBITDA alongside the current-price
        # EV/EBITDA in subject_multiples caused LLM writers to flag "conflicting data".
        "shares_diluted": fmt_num(_safe_get(raw_share_d, "shares_diluted_millions", latest_year), "millions"),
        "market_cap": fmt_num(_safe_get(raw_val, "market_cap_usd_b", latest_year), "usd_b"),
        "enterprise_value": fmt_num(_safe_get(raw_val, "enterprise_value_usd_b", latest_year), "usd_b"),
        "eps_diluted": (f"${raw_inc['eps_diluted'][latest_year]:.2f}" if _safe_get(raw_inc, "eps_diluted", latest_year) is not None else None),
        "sbc_pct_of_revenue": fmt_num(_safe_get(raw_share_d, "sbc_pct_of_revenue", latest_year), "pct"),
        "subject_multiples": subject_multiples_fmt,
        "peer_medians": peer_medians_fmt,
        "pe_implied_fair_value": pe_implied_fair_value_fmt,
        "pe_implied_method_note": pe_implied_method_note,
        "alt_implied_fair_value": alt_implied_fair_value_fmt,
        "alt_implied_method_note": alt_implied_method_note,
        "alt_implied_method_used": alt_implied_method_used,
        "current_price": current_price_fmt,
        "price_52wk_high": price_52wk_high_fmt,
        "price_52wk_low": price_52wk_low_fmt,
    }

    # ── Qualitative inputs for S12/S1/S14 ───────────────────────
    qualitative_inputs = sanitize_obj({
        "_cite_financials": fc,
        "company": {
            "one_sentence_description": comp_overview.get("one_sentence_description"),
            "life_cycle_classification": comp_overview.get("life_cycle_classification"),
            "core_value_proposition": comp_overview.get("core_value_proposition"),
            "competitive_advantages": _cite(comp_overview.get("competitive_advantages", []), url_to_id, fc),
        },
        "moat": {
            "moat_summary": _cite(comp_moats.get("moat_summary"), url_to_id, fc),
            "brand_intangibles": _cite(comp_moats.get("brand_intangibles"), url_to_id, fc),
            "switching_costs": _cite(comp_moats.get("switching_costs"), url_to_id, fc),
        },
        "industry": {
            "tam_usd_b": fmt_num(_safe_get(ind_comp, "industry_overview", "tam_usd_b"), "usd_b") if _safe_get(ind_comp, "industry_overview", "tam_usd_b") is not None else None,
            "tam_cagr_pct": fmt_num(_safe_get(ind_comp, "industry_overview", "tam_cagr_pct"), "pct") if _safe_get(ind_comp, "industry_overview", "tam_cagr_pct") is not None else None,
            "competitors": _cite(ind_comp.get("competitors", []), url_to_id, fc),
            "subject_positioning": _cite(ind_comp.get("subject_positioning"), url_to_id, fc),
            "tailwinds": _cite(_safe_get(ind_comp, "industry_overview", "tailwinds", default=[]), url_to_id, fc),
            "headwinds": _cite(_safe_get(ind_comp, "industry_overview", "headwinds", default=[]), url_to_id, fc),
        },
        "growth": {
            "revenue_drivers": _cite(growth_prosp.get("revenue_drivers", []), url_to_id, fc),
            "near_term_catalysts": _cite(growth_prosp.get("near_term_catalysts", []), url_to_id, fc),
            "medium_term_drivers": _cite(growth_prosp.get("medium_term_drivers", []), url_to_id, fc),
            "long_term_position": _cite(growth_prosp.get("long_term_position"), url_to_id, fc),
            "margin_evolution": _cite(growth_prosp.get("margin_cashflow_evolution"), url_to_id, fc),
            "revenue_5yr_cagr": fmt_num(raw_inc.get("revenue_cagr_5yr_pct"), "pct"),
        },
        "risk": {
            "top_risks": _cite(risk_assess.get("top_risks", []), url_to_id, fc),
            "bear_case_triggers": _cite(risk_assess.get("bear_case_triggers", []), url_to_id, fc),
            "key_assumption_sensitivities": _cite(risk_assess.get("key_assumption_sensitivities", []), url_to_id, fc),
            "base_case_condition": risk_assess.get("base_case_condition"),
            "bear_case_conclusion": risk_assess.get("bear_case_conclusion"),
            "regulatory_framework": _cite(risk_assess.get("regulatory_framework"), url_to_id, fc),
            "litigation": _cite(risk_assess.get("litigation"), url_to_id, fc),
        },
        "management": {
            "management_quality": _cite(mgmt_cap.get("management_quality"), url_to_id, fc),
            "capital_allocation_qualitative": _cite(mgmt_cap.get("capital_allocation_qualitative"), url_to_id, fc),
        },
    })

    # ── Valuation context for S12 ───────────────────────────────
    valuation_context = "\n\n---\n\n".join(filter(None, [
        section_map.get("section_9", ""),
        section_map.get("section_10", ""),
        section_map.get("section_11", ""),
        section_map.get("section_13", ""),
    ]))

    # ── S12 quant inputs (mode-dependent) ───────────────────────
    if valuation_mode == "bank_equity":
        # Bank-specific quant inputs for bank equity model narrative
        _bvps_val = _safe_get(raw_bal, "book_value_per_share", latest_year)
        _roe_val = _safe_get(raw_returns, "roe_pct", latest_year) or _safe_get(raw_margins, "roe_pct", latest_year)
        _div_yield_val = _safe_get(valuation, "dividend_yield_pct", latest_year)
        s12_quant_inputs = {
            "revenue_usd_m": _safe_get(raw_inc, "revenue_usd_m", latest_year),
            "operating_margin_pct": _safe_get(raw_margins, "operating_margin_pct", latest_year),
            "eps_diluted": _safe_get(raw_inc, "eps_diluted", latest_year),
            "book_value_per_share": _bvps_val,
            "roe_pct": _roe_val,
            "dividend_yield_pct": _div_yield_val,
            "valuation_model": "bank_equity",
            "valuation_model_note": (
                "Bank equity model: Justified P/B = (ROE - g) / (CoE - g). "
                "Fair Value = Justified P/B x BVPS. CoE typically 9-12% for large banks."
            ),
            "subject_multiples": subject_multiples,
            "peer_latest": peer_latest, "peer_medians": peer_medians,
            "forward_estimates": sanitize_obj(fwd_est),
            "pe_implied_fair_value": pe_implied_fair_value,
            "pe_implied_method_note": pe_implied_method_note,
            "current_price": current_price,
        }
    elif valuation_mode == "ddm":
        # DDM-specific quant inputs for dividend discount model narrative
        _div_yield_val = _safe_get(valuation, "dividend_yield_pct", latest_year)
        _dps_data = raw_cap_alloc.get("dividend_per_share", {})
        _dps_latest = _safe_get(raw_cap_alloc, "dividend_per_share", latest_year)
        _payout_ratio = _safe_get(raw_cap_alloc, "dividend_payout_ratio_pct", latest_year)
        _roe_val = _safe_get(raw_returns, "roe_pct", latest_year) or _safe_get(raw_margins, "roe_pct", latest_year)
        s12_quant_inputs = {
            "revenue_usd_m": _safe_get(raw_inc, "revenue_usd_m", latest_year),
            "ebitda_usd_m": _safe_get(raw_inc, "ebitda_usd_m", latest_year),
            "operating_margin_pct": _safe_get(raw_margins, "operating_margin_pct", latest_year),
            "eps_diluted": _safe_get(raw_inc, "eps_diluted", latest_year),
            "dividend_per_share": _dps_latest,
            "dividend_per_share_history": sanitize_obj(_dps_data),
            "dividend_yield_pct": _div_yield_val,
            "dividend_payout_ratio_pct": _payout_ratio,
            "roe_pct": _roe_val,
            "net_debt_usd_m": _safe_get(raw_cap, "net_debt_usd_m", latest_year),
            "shares_diluted_m": _safe_get(raw_share_d, "shares_diluted_millions", latest_year),
            "shares_diluted_latest_q_m": raw_share_d.get("shares_diluted_latest_q_millions"),
            "latest_quarter_period": raw_share_d.get("latest_quarter_period"),
            "market_cap_usd_b": _safe_get(raw_val, "market_cap_usd_b", latest_year),
            "enterprise_value_usd_b": _safe_get(raw_val, "enterprise_value_usd_b", latest_year),
            "valuation_model": "ddm",
            "valuation_model_note": (
                "DDM: FV = D1 / (r - g) where D1 = next year dividend, r = cost of equity, "
                "g = long-term dividend growth. Typical CoE for regulated utilities: 7-9%. "
                "Dividend growth for regulated utilities: 4-8% (tied to rate base growth)."
            ),
            "subject_multiples": subject_multiples, "peer_latest": peer_latest,
            "peer_medians": peer_medians,
            "forward_estimates": sanitize_obj(fwd_est),
            "alt_implied_fair_value": alt_implied_fair_value,
            "alt_implied_method_note": alt_implied_method_note,
            "alt_implied_method_used": alt_implied_method_used,
            "current_price": current_price,
        }
    elif use_alt_valuation:
        s12_quant_inputs = {
            "revenue_usd_m": _safe_get(raw_inc, "revenue_usd_m", latest_year),
            "ebitda_usd_m": _safe_get(raw_inc, "ebitda_usd_m", latest_year),
            "operating_margin_pct": _safe_get(raw_margins, "operating_margin_pct", latest_year),
            "ebitda_margin_pct": _safe_get(raw_inc, "ebitda_margin_pct", latest_year),
            "eps_diluted": _safe_get(raw_inc, "eps_diluted", latest_year),
            "net_debt_usd_m": _safe_get(raw_cap, "net_debt_usd_m", latest_year),
            "shares_diluted_m": _safe_get(raw_share_d, "shares_diluted_millions", latest_year),
            "shares_diluted_latest_q_m": raw_share_d.get("shares_diluted_latest_q_millions"),
            "latest_quarter_period": raw_share_d.get("latest_quarter_period"),
            "market_cap_usd_b": _safe_get(raw_val, "market_cap_usd_b", latest_year),
            "enterprise_value_usd_b": _safe_get(raw_val, "enterprise_value_usd_b", latest_year),
            "subject_multiples": subject_multiples, "peer_latest": peer_latest,
            "peer_medians": peer_medians,
            "forward_estimates": sanitize_obj(fwd_est),
            "alt_implied_fair_value": alt_implied_fair_value,
            "alt_implied_method_note": alt_implied_method_note,
            "alt_implied_method_used": alt_implied_method_used,
            "alt_valuation_method": alt_val_config["method"],
            "alt_valuation_rationale": alt_val_config["rationale"],
            "metric_label": alt_val_config.get("metric_label", ""),
            "current_price": current_price,
        }
        # REIT: inject FFO/AFFO per share + derived P/FFO & P/AFFO
        if is_reit:
            _kpis_inner_s12 = sector_kpis_obj.get("kpis", sector_kpis_obj) if isinstance(sector_kpis_obj, dict) else {}
            _kpi_rows_s12 = (_kpis_inner_s12.get("computedMetrics") or _kpis_inner_s12.get("computedRatios") or []) if isinstance(_kpis_inner_s12, dict) else []
            _kpi_rows_s12_sorted = sorted([r for r in _kpi_rows_s12 if isinstance(r, dict) and r.get("date")], key=lambda r: r["date"], reverse=True)
            _latest_kpi_s12 = _kpi_rows_s12_sorted[0] if _kpi_rows_s12_sorted else {}
            try:
                _ffo_val = float(_latest_kpi_s12.get("ffoPerShare")) if _latest_kpi_s12.get("ffoPerShare") is not None else None
            except (TypeError, ValueError):
                _ffo_val = None
            try:
                _affo_val = float(_latest_kpi_s12.get("affoPerShare")) if _latest_kpi_s12.get("affoPerShare") is not None else None
            except (TypeError, ValueError):
                _affo_val = None
            s12_quant_inputs["ffo_per_share"] = _ffo_val
            s12_quant_inputs["affo_per_share"] = _affo_val
            if current_price and _ffo_val and _ffo_val > 0:
                s12_quant_inputs["subject_p_ffo"] = round(float(current_price) / _ffo_val, 2)
            if current_price and _affo_val and _affo_val > 0:
                s12_quant_inputs["subject_p_affo"] = round(float(current_price) / _affo_val, 2)
    else:
        s12_quant_inputs = {
            "revenue_usd_m": _safe_get(raw_inc, "revenue_usd_m", latest_year),
            "revenue_5yr_cagr_pct": raw_inc.get("revenue_cagr_5yr_pct"),
            "revenue_3yr_cagr_pct": raw_inc.get("revenue_cagr_3yr_pct"),
            "revenue_growth_pct": _safe_get(raw_inc, "revenue_growth_pct", latest_year),
            "operating_margin_pct": _safe_get(raw_margins, "operating_margin_pct", latest_year),
            "ebitda_margin_pct": _safe_get(raw_inc, "ebitda_margin_pct", latest_year),
            "fcf_usd_m": _safe_get(raw_cf, "free_cash_flow_usd_m", latest_year),
            "fcf_margin_pct": _safe_get(raw_cf, "fcf_margin_pct", latest_year),
            "roic_pct": _safe_num(_safe_get(raw_margins, "roic_pct", latest_year), None),
            "net_debt_usd_m": _safe_get(raw_cap, "net_debt_usd_m", latest_year),
            # net_debt_ebitda removed — available in precomputed leverage tables;
            # passing it alongside current-price EV/EBITDA caused "conflicting data" in prose.
            "ebitda_usd_m": _safe_get(raw_inc, "ebitda_usd_m", latest_year),
            "shares_diluted_m": _safe_get(raw_share_d, "shares_diluted_millions", latest_year),
            "shares_diluted_latest_q_m": raw_share_d.get("shares_diluted_latest_q_millions"),
            "latest_quarter_period": raw_share_d.get("latest_quarter_period"),
            "market_cap_usd_b": _safe_get(raw_val, "market_cap_usd_b", latest_year),
            "enterprise_value_usd_b": _safe_get(raw_val, "enterprise_value_usd_b", latest_year),
            "subject_multiples": subject_multiples, "peer_latest": peer_latest,
            "peer_medians": peer_medians,
            "forward_estimates": sanitize_obj(fwd_est),
            "eps_diluted": _safe_get(raw_inc, "eps_diluted", latest_year),
            "current_price": current_price,
        }

    val_method_str = (
        "bank_equity_model" if valuation_mode == "bank_equity"
        else "ddm_dividend_discount" if valuation_mode == "ddm"
        else "industry_peer_comp" if use_alt_valuation
        else "dcf_and_peers"
    )

    # ── Build S12 template (mode-dependent) ─────────────────────
    s12_template = GLOBAL_RULES + f"\nSECTION 12: VALUATION ANALYSIS\nVALUATION MODE: {valuation_mode}\nINDUSTRY: {industry}\nCURRENT PRICE: {current_price_fmt or 'N/A'}\n52-Week Range: {price_52wk_low_fmt or 'N/A'} - {price_52wk_high_fmt or 'N/A'}\n"

    _scenario_guidance = (
        "\nSCENARIO ANALYSIS:\n"
        "For each scenario (bull/base/bear):\n"
        "  (1) Write a NARRATIVE grounding the scenario in operational drivers, not just percentages\n"
        "  (2) Reference specific operational metrics from the qualitative research if available\n"
        "  (3) Provide 2-3 observable KEY TRIGGERS that signal this scenario is playing out\n"
        "  (4) Assign PROBABILITY WEIGHTS (bull+base+bear MUST sum to 100)\n"
        "  (5) State the scenario-specific RISK\n"
    )

    if valuation_mode == "bank_equity":
        s12_template += (
            f"THIS IS A FINANCIAL SECTOR COMPANY ({industry}). DCF NOT APPLICABLE.\n"
            "PRIMARY VALUATION MODEL: Bank Equity (Justified P/B × Book Value per Share).\n"
            "The bank equity model calculates fair value from: (1) Justified P/B = (ROE - g) / (CoE - g), "
            "(2) Fair Value = Justified P/B × BVPS. Discuss ROE sustainability, cost of equity, "
            "and terminal growth assumptions.\n\n"
            "SECTION ORDERING: Write scenario_analysis FIRST (primary model), then peer_valuation as CROSS-CHECK.\n"
        )
        if pe_implied_fair_value_fmt:
            s12_template += f"P/E IMPLIED FAIR VALUE (cross-check): {pe_implied_fair_value_fmt}. {pe_implied_method_note}.\n"
        s12_template += _scenario_guidance
        s12_template += (
            "IMPLIED MULTIPLE FORMAT: The implied_multiple field for each scenario MUST be a SHORT string "
            "with EXACTLY this pattern: 'Justified P/B of [multiple]x → $[price]/share'\n"
            "CORRECT: 'Justified P/B of 1.6x → $215/share'\n"
            "CORRECT: 'Justified P/B of 2.1x → $276/share'\n"
            "WRONG: 'Justified P/B of 2.8x 106 $365/share' (missing arrow, has stray number)\n"
            "WRONG: 'Justified P/B of 2.21x × $130.36 = $292/share' (too much detail)\n"
            "DO NOT show the BVPS calculation in implied_multiple — just the final P/B and price.\n"
        )
    elif valuation_mode == "ddm":
        s12_template += (
            f"THIS IS A REGULATED UTILITY ({industry}). STANDARD FCFF DCF IS UNRELIABLE.\n"
            "PRIMARY VALUATION MODEL: Dividend Discount Model (DDM).\n"
            "The DDM calculates fair value as: FV = D₁ / (r - g) where D₁ = next year's dividend, "
            "r = required return (cost of equity), g = long-term dividend growth rate. "
            "Discuss dividend payout sustainability, dividend growth trajectory, regulatory "
            "rate-base dynamics, and appropriate cost of equity for a regulated utility.\n\n"
            "SECTION ORDERING: Write scenario_analysis FIRST (primary DDM scenarios), then peer_valuation as CROSS-CHECK.\n"
        )
        if alt_implied_fair_value_fmt:
            s12_template += f"PEER-IMPLIED FAIR VALUE (cross-check): {alt_implied_fair_value_fmt}. {alt_implied_method_note}.\n"
        s12_template += _scenario_guidance
        s12_template += (
            "IMPLIED MULTIPLE FORMAT: For each scenario, the implied_multiple field MUST follow this EXACT format:\n"
            "  'DDM: X% dividend growth, Y% CoE → $ZZ/share'\n"
            "Example: 'DDM: 6% dividend growth, 8.5% CoE → $72/share'\n"
            "WRONG: 'DDM 6 % 8.5 % 72 /share' — DO NOT produce garbled text.\n"
        )
    elif use_alt_valuation:
        _peer_primary = industry in _PEER_PRIMARY_INDUSTRIES
        if _peer_primary:
            s12_template += (
                f"STANDARD FCFF DCF IS UNRELIABLE FOR THIS INDUSTRY.\n"
                "SECTION ORDERING: Write peer_valuation FIRST (primary model), then scenario_analysis.\n"
            )
        else:
            s12_template += (
                f"STANDARD FCFF DCF IS UNRELIABLE FOR THIS INDUSTRY.\n"
                "SECTION ORDERING: Write scenario_analysis FIRST, then peer_valuation as CROSS-CHECK.\n"
            )
        if alt_implied_fair_value_fmt:
            s12_template += f"IMPLIED FAIR VALUE: {alt_implied_fair_value_fmt}. {alt_implied_method_note}.\n"
        s12_template += _scenario_guidance
        s12_template += (
            "IMPLIED MULTIPLE FORMAT: For each scenario, the implied_multiple field MUST follow this EXACT format:\n"
            "  '[Metric] of X.Xx → $YY/share'\n"
            "Example: 'EV/EBITDA of 12x → $85/share' or 'P/E of 15x → $52/share'\n"
        )
    else:
        s12_template += "STANDARD DCF MODE. Anchor DCF assumptions in quant_inputs data.\n"
        s12_template += _scenario_guidance
        s12_template += (
            "PROBABILITY-WEIGHTED FAIR VALUE: Assign probability weights to each DCF scenario.\n"
            "The fair value will be computed as: Bull × P(bull) + Base × P(base) + Bear × P(bear).\n"
            "WEIGHT GUIDANCE: Anchor weights to the company's DEMONSTRATED momentum and execution:\n"
            "- If trailing revenue growth EXCEEDS your base-case CAGR, the bull case is MORE LIKELY than bear → weight bull higher (30-40%).\n"
            "- If trailing revenue growth is BELOW your base-case CAGR, weight bear higher (30-40%).\n"
            "- Neutral/balanced: Bull 25-30%, Base 45-55%, Bear 20-25%.\n"
            "- Do NOT default to pessimistic weights (Bull 20%, Bear 25%) for companies with strong recent execution.\n"
        )

    # Anti-hallucination guidance for valuation
    s12_template += (
        "\nVALUATION DISCIPLINE -- READ CAREFULLY:\n"
        "- A valuation MULTIPLE (e.g., 19.3x P/CF, 15.2x P/E) is a RATIO, not a price.\n"
        "  NEVER cite a multiple as 'fair value of $19.31.' That is a multiple, not a share price.\n"
        "- Fair value = implied share price in DOLLARS (e.g., '$145/share').\n"
        "- Peer-implied fair value = (peer median multiple) × (subject's metric) ÷ shares.\n"
        "  Show the full calculation. The result is a dollar amount, not a ratio.\n"
        "- Subject multiples from quant_inputs show where the stock CURRENTLY trades.\n"
        "  They are descriptive facts, not prescriptive targets.\n"
        "- SHARE COUNT: If shares_diluted_latest_q_m is provided, it is MORE CURRENT than\n"
        "  shares_diluted_m (annual weighted average). Use the latest quarterly figure for\n"
        "  per-share calculations, and note significant share count changes (buybacks/ASR).\n"
        "\n"
        "FAIR VALUE CONCLUSION — CRITICAL INSTRUCTION:\n"
        "- The valuation_synthesis field describes the MODEL MECHANICS only.\n"
        "- DO NOT state specific dollar per-share fair values — the engine computes them.\n"
        "- DO NOT make buy/sell/hold recommendations or give investment advice.\n"
        "- DO NOT state 'margin of safety' — this is not an advisory memo.\n"
        "- DO NOT compare the current price to fair value or state upside/downside.\n"
        "- Focus on: which valuation approach is most reliable, what drives the range "
        "between scenarios, and which assumptions create the most sensitivity.\n"
        "- The memo presents the mechanical model output and lets readers draw their "
        "own conclusions.\n"
    )

    # ── Build S1 / S14 templates ────────────────────────────────
    s1_template = GLOBAL_RULES + (
        "\nSECTION 1: EXECUTIVE SUMMARY & INVESTMENT THESIS -- 500-700 words\n"
        "IMPORTANT: Write thorough, detailed prose. Be concise — every sentence must earn its place.\n"
        "CRITICAL: This memo is for a public website. DO NOT make buy/sell/hold recommendations, "
        "state margin of safety, compare the current price to fair value, or give investment advice. "
        "Present an objective quality assessment of the business.\n"
        "SYNTHESIS RULE: You have the full memo body as context. Your job is to SYNTHESIZE, "
        "not re-state. Use DIFFERENT wording and DIFFERENT data combinations than the body sections. "
        "Cite a headline metric only ONCE per subsection. If Services revenue, gross margin, or other "
        "key metrics already appear in your Executive Summary, do NOT repeat them in the Thesis or Verdict.\n"
    )
    if valuation_mode == "bank_equity":
        s1_template += f"NOTE: Financial sector company ({industry}). Valuation uses bank equity (justified P/B) model.\n"
    elif valuation_mode == "ddm":
        s1_template += f"NOTE: Regulated utility ({industry}). Valuation uses DDM (dividend discount model).\n"
    elif use_alt_valuation:
        method_label = "EV/EBITDA" if alt_val_config["method"] == "ev_ebitda" else "P/E"
        s1_template += f"NOTE: {industry} company. Valuation uses {method_label} peer framework.\n"

    s14_template = GLOBAL_RULES + (
        "\nSECTION 14: CONCLUSION -- 250-400 words\n"
        "CRITICAL: This memo is for a public website. DO NOT make buy/sell/hold recommendations, "
        "state margin of safety, compare the current price to fair value, or give investment advice. "
        "Present an objective summary of business quality and key monitoring conditions.\n"
        "SYNTHESIS RULE: The reader has already read 13 sections. Do NOT re-cite specific revenue "
        "figures, margin percentages, or other metrics from the body. Instead, reference them "
        "conceptually (e.g., 'industry-leading margins' not '46.9% gross margin'). "
        "Focus on the forward-looking assessment and monitoring framework.\n"
    )
    s14_template += (
        "\nWHAT MUST BE TRUE: Provide 3-5 STRUCTURED conviction items.\n"
        "Each item must be:\n"
        "  - COMPANY-SPECIFIC (not generic like 'revenue must grow')\n"
        "  - FALSIFIABLE (an analyst can objectively assess if it's true or false)\n"
        "  - MONITORABLE (tied to a specific metric that can be tracked quarterly)\n"
        "GOOD: 'Azure consumption growth must sustain >25% YoY' — 'Justifies $80B capex'\n"
        "BAD: 'Cloud business must continue performing well'\n"
    )

    # ── FCF conversion for S10 ──────────────────────────────────
    fcf_conv = _yr5f(raw_cf.get("fcf_conversion_pct", {}), "fcf_conversion_pct", annual_years)

    # ═══════════════════════════════════════════════════════════
    # ASSEMBLE ALL SECTION OUTPUTS
    # ═══════════════════════════════════════════════════════════
    result: dict[str, Any] = {}

    # Common kwargs for context builders
    _common = dict(
        fs=fs, F=F, identity=identity, latest_year=latest_year,
        fc=fc, url_to_id=url_to_id, annual_years=annual_years,
    )

    # ── Dist 1 sections (2-11, 13) ──────────────────────────────
    # Word targets are flexible guides; min_words is a hard floor that triggers retry.
    # Total memo target: 15,000-18,000 words (hard cap 18K).
    _s2_facts = build_section_2_context(**_common, seg=seg, raw_seg=raw_seg, comp_overview=comp_overview, ident=ident)
    if sector_kpis_obj:
        _s2_facts["sector_kpis"] = _format_sector_kpis(sector_kpis_obj)
    result["section_2"] = {
        "section_number": 2, "section_title": "Company Overview",
        "word_target": "400-600 words", "min_words": 300,
        "schema": to_openai(_with_stance(SECTION_SCHEMAS[2])),
        "facts": _s2_facts,
        "template": _build_section_template(2),
    }

    result["section_3"] = {
        "section_number": 3, "section_title": "Company History & Key Milestones",
        "word_target": "500-700 words", "min_words": 350,
        "schema": to_openai(_with_stance(SECTION_SCHEMAS[3])),
        "facts": build_section_3_context(**_common, ident=ident, raw_inc=raw_inc, hist_mile=hist_mile),
        "template": _build_section_template(3),
    }

    _s4_facts = build_section_4_context(**_common, seg=seg, raw_seg=raw_seg, prod_tech=prod_tech,
                                        consolidated_revenue=consolidated_revenue, seg_data_quality=seg_data_quality, ident=ident)
    if sector_kpis_obj:
        _s4_facts["sector_kpis"] = _format_sector_kpis(sector_kpis_obj)
    _s4_facts["subsector"] = subsector
    result["section_4"] = {
        "section_number": 4, "section_title": _get_section_4_title(subsector),
        "word_target": "500-800 words", "min_words": 400,
        "schema": to_openai(_with_stance(_get_section_4_schema(subsector))),
        "facts": _s4_facts,
        "precomputed_segment_rows": segment_rows,
        "template": _build_section_template(4),
    }

    _s5_facts = build_section_5_context(**_common, comp_moats=comp_moats, ident=ident)
    if sector_kpis_obj:
        _s5_facts["sector_kpis"] = _format_sector_kpis(sector_kpis_obj)
    result["section_5"] = {
        "section_number": 5, "section_title": "Competitive Moats",
        "word_target": "600-900 words", "min_words": 450,
        "schema": to_openai(_with_stance(SECTION_SCHEMAS[5])),
        "facts": _s5_facts,
        "template": _build_section_template(5),
    }

    _s6_facts = build_section_6_context(**_common, ind_comp=ind_comp, peers_obj=peers_obj, ident=ident)
    if sector_kpis_obj:
        _s6_facts["sector_kpis"] = _format_sector_kpis(sector_kpis_obj)
    result["section_6"] = {
        "section_number": 6, "section_title": "Industry & Competitive Dynamics",
        "word_target": "700-1000 words", "min_words": 500,
        "schema": to_openai(_with_stance(SECTION_SCHEMAS[6])),
        "facts": _s6_facts,
        "template": _build_section_template(6),
    }

    # Geographic segments skipped entirely — SEC XBRL geographic axes are too
    # inconsistent across companies, causing recurring rendering failures.

    _s7_facts = build_section_7_context(**_common, cust_analysis=cust_analysis,
                                        geo_rows=None, geo_data_quality="unusable",
                                        consolidated_revenue=consolidated_revenue, ident=ident)
    if sector_kpis_obj:
        _s7_facts["sector_kpis"] = _format_sector_kpis(sector_kpis_obj)
    _s7_facts["subsector"] = subsector
    result["section_7"] = {
        "section_number": 7, "section_title": _get_section_7_title(subsector),
        "word_target": "500-700 words", "min_words": 350,
        "schema": to_openai(_with_stance(_get_section_7_schema(subsector))),
        "facts": _s7_facts,
        "template": _build_section_template(7),
    }

    _s8_facts = build_section_8_context(**_common, mgmt_cap=mgmt_cap, beat_miss=beat_miss,
                                        cap_alloc=cap_alloc, raw_margins=raw_margins,
                                        raw_share_d=raw_share_d, ident=ident)
    if sector_kpis_obj:
        _s8_facts["sector_kpis"] = _format_sector_kpis(sector_kpis_obj)
    result["section_8"] = {
        "section_number": 8, "section_title": "Management & Capital Allocation",
        "word_target": "600-900 words", "min_words": 400,
        "schema": to_openai(_with_stance(SECTION_SCHEMAS[8])),
        "facts": _s8_facts,
        "precomputed_capital_allocation": fin_tables["capital_allocation"],
        "template": _build_section_template(8),
    }

    _s9_facts = build_section_9_context(**_common, growth_prosp=growth_prosp, ind_comp=ind_comp,
                                        seg=seg, raw_margins=raw_margins, raw_cf=raw_cf,
                                        fwd_est=fwd_est, ident=ident)
    if sector_kpis_obj:
        _s9_facts["sector_kpis"] = _format_sector_kpis(sector_kpis_obj)
    result["section_9"] = {
        "section_number": 9, "section_title": "Growth Prospects & Catalysts",
        "word_target": "600-900 words", "min_words": 400,
        "schema": to_openai(_with_stance(SECTION_SCHEMAS[9])),
        "facts": _s9_facts,
        "template": _build_section_template(9),
    }

    _s10_suppress = _S10_GENERIC_TABLE_SUPPRESSIONS.get(sector_family, set())
    _s10_entry: dict[str, Any] = {
        "section_number": 10, "section_title": _get_section_10_title(sector_family),
        "word_target": "800-1100 words", "min_words": 550,
        "schema": to_openai(_with_stance(_get_section_10_schema(sector_family))),
        "facts": build_section_10_context(**_common, fcf_conversion_5yr=fcf_conv, ident=ident,
                                         family=sector_family, sector_kpis=fs.get("_sec_sector_kpis")),
        "template": _build_section_template(10),
    }
    # Inject generic financial tables — skip any that clash with sector-specific framing.
    for _tbl_key in ("revenue_growth", "margins", "cash_flow", "returns", "leverage"):
        if _tbl_key not in _s10_suppress:
            _s10_entry[f"precomputed_{_tbl_key}"] = fin_tables[_tbl_key]
    # Overlay sector KPI tables (always injected — these are the primary data for specialized sectors).
    if sector_kpi_tables:
        for k, v in sector_kpi_tables.items():
            _s10_entry[f"precomputed_{k}"] = v
    result["section_10"] = _s10_entry

    _s11_template = _build_section_template(11)

    # Choose the sector-appropriate valuation comps table for writer prompts.
    _val_comps_primary = peer_tables.get("valuation_comps", [])
    if sector_family in {"banking", "insurance"}:
        _val_comps_primary = peer_tables.get("valuation_comps_financial") or _val_comps_primary
    elif sector_family == "reits":
        _val_comps_primary = peer_tables.get("valuation_comps_reit") or _val_comps_primary

    result["section_11"] = {
        "section_number": 11, "section_title": _get_section_11_title(sector_family),
        "word_target": "800-1100 words", "min_words": 550,
        "schema": to_openai(_with_stance(_get_section_11_schema(sector_family))),
        "facts": build_section_11_context(**_common, peer_medians_formatted=peer_medians_formatted,
                                          raw_peer_bench=raw_peer_bench, peer_bench=peer_bench,
                                          raw_bal_sheet=raw_bal, ident=ident,
                                          family=sector_family, sector_kpis=fs.get("_sec_sector_kpis")),
        "precomputed_profitability_comps": peer_tables.get("profitability_comps", []),
        "precomputed_growth_comps": peer_tables.get("growth_comps", []),
        "precomputed_valuation_comps": _val_comps_primary,
        "precomputed_valuation_comps_financial": peer_tables.get("valuation_comps_financial", []),
        "precomputed_valuation_comps_reit": peer_tables.get("valuation_comps_reit", []),
        "precomputed_leverage_comps": peer_tables.get("leverage_comps", []),
        "precomputed_efficiency_comps": peer_tables.get("efficiency_comps", []),
        "peer_medians_formatted": peer_medians_formatted,
        "template": _s11_template,
    }
    if sector_kpi_tables:
        for k, v in sector_kpi_tables.items():
            result["section_11"][f"precomputed_{k}"] = v

    result["section_13"] = {
        "section_number": 13, "section_title": "Risk Assessment",
        "word_target": "700-1000 words", "min_words": 450,
        "schema": to_openai(_with_stance(SECTION_SCHEMAS[13])),
        "facts": build_section_13_context(**_common, risk_assess=risk_assess, seg=seg,
                                          inc_stmt=inc_stmt, fwd_est=fwd_est, ident=ident,
                                          sector_kpis=fs.get("_sec_sector_kpis")),
        "template": _build_section_template(13),
    }

    # ── Dist 2 sections (12, 1, 14) ─────────────────────────────
    s12_schema = _build_s12_schema(valuation_mode, industry, alt_val_config)
    s1_schema = _build_s1_schema(valuation_mode, industry, alt_val_config)
    s14_schema = _build_s14_schema(valuation_mode, industry, alt_val_config)

    result["section_12"] = {
        "section_number": 12, "section_title": "Valuation Analysis",
        "company_name": company_name, "ticker": ticker,
        "word_target": "600-800 words" if (skip_dcf or use_alt_valuation) else "700-1000 words",
        "min_words": 400,
        "schema": to_openai(s12_schema),
        "context": valuation_context,
        "context_description": "Sections 9, 10, 11, 13 -- read all before writing",
        "qualitative_inputs": qualitative_inputs,
        "valuation_method": val_method_str,
        "industry": industry,
        "quant_inputs": s12_quant_inputs,
        "quant_inputs_formatted": quant_inputs_formatted,
        "subject_multiples_formatted": subject_multiples_fmt,
        "peer_medians_formatted": peer_medians_fmt,
        "precomputed_peer_table": precomputed_peer_table,
        "dcf_anchors": dcf_anchors,
        "scores": scores,
        "template": s12_template,
    }

    result["section_1"] = {
        "section_number": 1, "section_title": "Executive Summary & Investment Thesis",
        "company_name": company_name, "ticker": ticker,
        "word_target": "500-700 words", "min_words": 350,
        "schema": to_openai(s1_schema),
        "context": memo_body,
        "context_description": "Complete memo -- sections 2 through 14.",
        "qualitative_inputs": qualitative_inputs,
        "valuation_method": val_method_str,
        "dcf_anchors": dcf_anchors,
        "precomputed_peer_table": precomputed_peer_table,
        "scores": scores,
        "current_price": current_price_fmt,
        "sources_appendix": sources_appendix,
        "template": s1_template,
    }
    if skip_dcf:
        result["section_1"].update({
            "pe_implied_fair_value": pe_implied_fair_value,
            "pe_implied_fair_value_fmt": pe_implied_fair_value_fmt,
            "pe_implied_method_note": pe_implied_method_note,
        })
    elif use_alt_valuation:
        result["section_1"].update({
            "alt_implied_fair_value": alt_implied_fair_value,
            "alt_implied_fair_value_fmt": alt_implied_fair_value_fmt,
            "alt_implied_method_note": alt_implied_method_note,
            "alt_implied_method_used": alt_implied_method_used,
        })

    result["section_14"] = {
        "section_number": 14, "section_title": "Conclusion",
        "company_name": company_name, "ticker": ticker,
        "word_target": "250-400 words",
        "schema": to_openai(s14_schema),
        "context": memo_body,
        "context_description": "Complete memo including Section 12 (Valuation) and Section 1 (Executive Summary).",
        "qualitative_inputs": qualitative_inputs,
        "valuation_method": val_method_str,
        "dcf_anchors": dcf_anchors,
        "precomputed_peer_table": precomputed_peer_table,
        "scores": scores,
        "current_price": current_price_fmt,
        "template": s14_template,
    }
    if skip_dcf:
        result["section_14"].update({
            "pe_implied_fair_value": pe_implied_fair_value,
            "pe_implied_fair_value_fmt": pe_implied_fair_value_fmt,
            "pe_implied_method_note": pe_implied_method_note,
        })
    elif use_alt_valuation:
        result["section_14"].update({
            "alt_implied_fair_value": alt_implied_fair_value,
            "alt_implied_fair_value_fmt": alt_implied_fair_value_fmt,
            "alt_implied_method_note": alt_implied_method_note,
            "alt_implied_method_used": alt_implied_method_used,
        })

    # ── Agent prompts ───────────────────────────────────────────
    result["agent_prompts"] = {
        "agent_1": "Sections 2-7 (Company Overview through Customer Analysis)",
        "agent_2": "Sections 8-11, 13 (Management through Risk Assessment)",
        "agent_3": "Section 12 (Valuation), Section 1 (Executive Summary), Section 14 (Conclusion)",
    }

    return result