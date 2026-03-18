"""Final Assembly — merges aggregate outputs into formatted investment memo.

Ported from Final_Assembly.js v8.3 (n8n node).

Responsibilities:
  1. Merge Aggregate 1 (sections 2-11, 13) + Aggregate 2 (sections 12, 1, 14)
     + Source Registry into a unified section/structured map.
  2. DCF computation: ``compute_dcf_scenario()`` with bull/base/bear scenarios.
  3. Section formatting: renders structured JSON into markdown tables/prose.
  4. Score assembly: moat, growth, quality from sections + fair_value from DCF.
  5. Data block: scores, description, fair value details for Discord/PDF.
  6. Validation/warnings.

Exports:
  - ``assemble_memo(section_outputs, fact_sheet, source_registry) -> MemoAssembly``
  - ``compute_dcf_scenario(assumptions, anchors) -> DCFScenario``
  - ``extract_scores(structured_map) -> dict``
  - ``build_data_block(scores, fair_value, section_outputs) -> dict``
  - ``render_section_markdown(section_num, structured_output) -> str``
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Optional

# Valuation dispatcher — runs industry-appropriate model
try:
    from valuation import run_valuation as _run_valuation_dispatcher
except ImportError:
    _run_valuation_dispatcher = None

# ═══════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class DCFScenario:
    """Result of a single DCF scenario computation."""

    revenue_cagr: float = 0.0
    terminal_op_margin: float = 0.0
    terminal_nopat: float = 0.0
    terminal_fcff: float = 0.0
    terminal_growth: float = 0.0
    wacc: float = 0.0
    effective_tax_rate: float = 0.0
    terminal_tax_rate: float = 0.0
    reinvestment_rate: float = 0.0
    annual_dilution_pct: float = 0.0
    enterprise_value: float = 0.0
    net_debt: float = 0.0
    equity_value: float = 0.0
    shares_outstanding: float = 0.0
    fair_value_per_share: float = 0.0
    projection_years: int = 5
    dilution_applied: bool = False
    used_gwacc: bool = False
    used_min_floor: bool = False


@dataclass
class MemoAssembly:
    """Complete assembly output — consumed by Discord scorecard and PDF."""

    company_name: str = ""
    ticker: str = ""
    formatted_memo: str = ""
    data_block: dict = field(default_factory=dict)
    section_map: dict = field(default_factory=dict)
    sections_ordered: list = field(default_factory=list)
    word_count: int = 0
    tables_rendered: int = 0
    warnings: list = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════
# SECTOR CONSTANTS — mirrors Dist 2 v10
# ═══════════════════════════════════════════════════════════════════════

_SKIP_DCF_FINANCIAL: set[str] = {
    "Banks - Diversified",
    "Banks - Regional",
    "Insurance - Diversified",
    "Insurance - Life",
    "Insurance - Property & Casualty",
    "Insurance - Specialty",
    "Insurance - Reinsurance",
    "REIT - Diversified",
    "REIT - Office",
    "REIT - Retail",
    "REIT - Residential",
    "REIT - Industrial",
    "REIT - Healthcare Facilities",
    "REIT - Hotel & Motel",
    "REIT - Mortgage",
    "REIT - Specialty",
    "Mortgage Finance",
    "Thrifts & Mortgage Finance",
    "Credit Services",
}

_SKIP_DCF_ASSET_HEAVY: set[str] = {
    "Marine Shipping",
    "Shipping & Ports",
    "Oil & Gas E&P",
    "Oil & Gas Exploration & Production",
    "Oil & Gas Integrated",
    "Oil & Gas Midstream",
    "Oil & Gas Refining & Marketing",
    "Oil & Gas Equipment & Services",
    "Gold",
    "Silver",
    "Copper",
    "Steel",
    "Aluminum",
    "Coal",
    "Uranium",
    "Other Industrial Metals & Mining",
    "Other Precious Metals & Mining",
    "Diversified Metals & Mining",
    "Metals & Mining",
}

# ═══════════════════════════════════════════════════════════════════════
# SECTION FORMAT DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════

SECTION_FORMATS: dict[int, dict] = {
    1: {
        "title": "Executive Summary & Investment Thesis",
        "subsections": [
            "The Company",
            "Investment Thesis",
            "Growth Outlook",
            "Valuation & Fair Value",
            "Primary Risk",
            "Verdict",
        ],
        "style": "bold-lead-in",
    },
    2: {
        "title": "Company Overview",
        "subsections": [
            "Core Products & Services",
            "Industry Ecosystem & Positioning",
        ],
    },
    3: {
        "title": "Company History & Key Milestones",
        "subsections": ["Early History", "Key Phases"],
    },
    4: {
        "title": "Product & Technology Strategy",
        "subsections": [
            "Revenue by Segment",
            "Product Portfolio",
            "R&D & Technology Investment",
            "Technology Initiatives",
            "Competitive Technology Position",
        ],
    },
    5: {
        "title": "Competitive Moats",
        "subsections": ["Moat Sources", "Overall Moat Assessment"],
    },
    6: {
        "title": "Industry & Competitive Dynamics",
        "subsections": [
            "Market Structure",
            "Competitive Landscape",
            "Competitive Dynamics",
            "Key Industry Forces",
            "Tailwinds",
            "Headwinds",
        ],
    },
    7: {
        "title": "Customer Analysis",
        "subsections": [
            "Customer Composition",
            "Geographic Split",
            "Product Stickiness & Retention",
            "Unit Economics",
            "Working Capital",
        ],
    },
    8: {
        "title": "Management & Capital Allocation",
        "subsections": [
            "Leadership Team",
            "Execution Track Record",
            "Insider Ownership & Alignment",
            "Capital Allocation",
            "Board & Governance",
            "Overall Assessment",
        ],
    },
    9: {
        "title": "Growth Prospects & Catalysts",
        "subsections": [
            "Market Opportunity",
            "Near-Term Catalysts",
            "Medium-Term Drivers",
            "Long-Term Strategic Position",
            "Margin Evolution",
        ],
    },
    10: {
        "title": "Financial Analysis",
        "subsections": [
            "Revenue & Growth",
            "Margins",
            "Cash Flow",
            "Returns & Capital Efficiency",
            "Leverage & Balance Sheet",
            "Financial Quality Flags",
            "Synthesis",
            # Sector-specific subsections (only appear when sector schema is used)
            "Earnings Power & NIM",
            "Efficiency & Costs",
            "Credit Quality",
            "Capital & Funding",
            "Underwriting Profitability",
            "Reserves & Capital",
            "Investment Income",
            "FFO, AFFO & Dividend",
            "Portfolio & NOI",
            "Leverage & Financing",
            "Regulated Earnings & Rate Base",
            "Capex & Payout",
            "Balance Sheet & Funding",
            "Unit Economics & Costs",
            "Production & Reserves",
            "Capital Discipline",
            "Growth & Retention",
            "Profitability & Efficiency",
            "R&D & SBC",
            "Revenue Mix & Volume",
            "R&D Productivity",
            "Margins & Cash Generation",
            "Orders, Backlog & Cycle",
            "Operations & Margins",
            "Returns & Capital Intensity",
            "Demand & Unit Economics",
            "Gross Margin & Cost Structure",
            "Working Capital & Cash",
            "Price/Mix vs. Volume",
            "Margin Resilience",
            "Cash Generation & Payout",
            "Cost Curve & Competitiveness",
            "Price Sensitivity & Cycle",
            "Capital Intensity & Balance Sheet",
            "Network Economics & ARPU",
            "Capex & FCF",
            "Leverage & Regulatory",
            "Demand & Cycle",
            "Margin Leverage",
            "Cash & Balance Sheet",
        ],
    },
    11: {
        "title": "Peer Financial Benchmarking",
        "subsections": [
            "Profitability Comparison",
            "Growth Comparison",
            "Leverage Comparison",
            "Efficiency Comparison",
            "Returns Comparison",
            "Geographic Comparison",
            "Synthesis",
        ],
    },
    12: {
        "title": "Valuation Analysis",
        "subsections": [
            "DCF Analysis",
            "Sensitivity Analysis",
            "Scenario Analysis",
            "Peer Valuation",
            "Fair Value Conclusion",
        ],
    },
    13: {
        "title": "Risk Assessment",
        "subsections": [
            "Regulatory & Structural Risks",
            "Key Risks",
            "Bear Case Triggers",
            "Sensitivity Assumptions",
            "Litigation",
        ],
    },
    14: {
        "title": "Conclusion",
        "subsections": [
            "The Verdict",
            "What Must Be True",
            "The Primary Risk",
            "Final Statement",
        ],
        "style": "bold-lead-in",
    },
}

NESTED_BOLD_LABELS: dict[str, str] = {
    "guidance_accuracy": "Guidance Accuracy",
    "strategic_execution": "Strategic Execution",
    "broad_market": "Broad Market",
    "key_segment": "Key Segment",
    "strategic_positioning": "Strategic Positioning",
    "optionality": "Optionality",
    "valuation_synthesis": "Fair Value Conclusion",
    "runway": "Long-Term Runway",
    "at_scale": "Business at Scale",
}

KEY_TO_SUBSECTION: dict[str, Optional[str]] = {
    "the_company": "The Company",
    "investment_thesis": "Investment Thesis",
    "growth_in_words": "Growth Outlook",
    "valuation_and_fair_value": "Valuation & Fair Value",
    "primary_risk": "Primary Risk",
    "verdict": "Verdict",
    "core_products_services": "Core Products & Services",
    "industry_ecosystem": "Industry Ecosystem & Positioning",
    "early_history": "Early History",
    "phase_blocks": "Key Phases",
    "revenue_by_segment": "Revenue by Segment",
    "product_portfolio": "Product Portfolio",
    "rd_and_technology": "R&D & Technology Investment",
    "technology_initiatives": "Technology Initiatives",
    "competitive_tech_position": "Competitive Technology Position",
    "moat_blocks": "Moat Sources",
    "overall_assessment": "Overall Moat Assessment",
    "market_structure": "Market Structure",
    "competitive_landscape": "Competitive Landscape",
    "competitive_dynamics": "Competitive Dynamics",
    "industry_forces": "Key Industry Forces",
    "tailwinds": "Tailwinds",
    "headwinds": "Headwinds",
    "customer_composition": "Customer Composition",
    # geographic_split: skipped — SEC XBRL axes too inconsistent
    "stickiness_and_retention": "Product Stickiness & Retention",
    "unit_economics": "Unit Economics",
    "working_capital": "Working Capital",
    "leadership_team": "Leadership Team",
    "execution_track_record": "Execution Track Record",
    "insider_ownership": "Insider Ownership & Alignment",
    "capital_allocation": "Capital Allocation",
    "board_governance": "Board & Governance",
    "overall_assessment_s8": "Overall Assessment",
    "market_opportunity": "Market Opportunity",
    "near_term_catalysts": "Near-Term Catalysts",
    "medium_term_drivers": "Medium-Term Drivers",
    "long_term_position": "Long-Term Strategic Position",
    "margin_evolution": "Margin Evolution",
    "revenue_growth": "Revenue & Growth",
    "margins": "Margins",
    "cash_flow": "Cash Flow",
    "returns_capital_efficiency": "Returns & Capital Efficiency",
    "leverage_balance_sheet": "Leverage & Balance Sheet",
    "sector_specific_analysis": "Sector-Specific Analysis",
    "financial_quality_flags": "Financial Quality Flags",
    "synthesis": "Synthesis",
    "opening_paragraph": None,
    "profitability_comparison": "Profitability Comparison",
    "growth_comparison": "Growth Comparison",
    "leverage_comparison": "Leverage Comparison",
    "efficiency_comparison": "Efficiency Comparison",
    "returns_comparison": "Returns Comparison",
    # geographic_comparison: skipped — SEC XBRL axes too inconsistent
    "dcf_analysis": "DCF Analysis",
    "sensitivity_analysis": "Sensitivity Analysis",
    "scenario_analysis": "Scenario Analysis",
    "peer_valuation": "Peer Valuation",
    "fair_value_conclusion": "Fair Value Conclusion",
    "dcf_limitations": None,
    "regulatory_structural": "Regulatory & Structural Risks",
    "key_risks": "Key Risks",
    "bear_case_triggers": "Bear Case Triggers",
    "sensitivity_assumptions": "Sensitivity Assumptions",
    "litigation": "Litigation",
    "the_verdict": "The Verdict",
    "what_must_be_true": "What Must Be True",
    "the_primary_risk": "The Primary Risk",
    "final_statement": "Final Statement",
    # skipDCF S12 keys — no subsection header
    "dcf_not_applicable": None,
    # Sector-specific S10 subsection keys
    "earnings_power_and_nim": "Earnings Power & NIM",
    "efficiency_and_costs": "Efficiency & Costs",
    "credit_quality": "Credit Quality",
    "capital_and_funding": "Capital & Funding",
    "underwriting_profitability": "Underwriting Profitability",
    "reserves_and_capital": "Reserves & Capital",
    "investment_income": "Investment Income",
    "ffo_affo_and_dividend": "FFO, AFFO & Dividend",
    "portfolio_and_noi": "Portfolio & NOI",
    "leverage_and_financing": "Leverage & Financing",
    "regulated_earnings_and_rate_base": "Regulated Earnings & Rate Base",
    "capex_and_payout": "Capex & Payout",
    "balance_sheet_and_funding": "Balance Sheet & Funding",
    "unit_economics_and_costs": "Unit Economics & Costs",
    "production_and_reserves": "Production & Reserves",
    "capital_discipline": "Capital Discipline",
    "growth_and_retention": "Growth & Retention",
    "profitability_and_efficiency": "Profitability & Efficiency",
    "r_and_d_and_sbc": "R&D & SBC",
    "revenue_mix_and_volume": "Revenue Mix & Volume",
    "r_and_d_productivity": "R&D Productivity",
    "margins_and_cash_generation": "Margins & Cash Generation",
    "orders_backlog_and_cycle": "Orders, Backlog & Cycle",
    "operations_and_margins": "Operations & Margins",
    "returns_and_capital_intensity": "Returns & Capital Intensity",
    "demand_and_unit_economics": "Demand & Unit Economics",
    "gross_margin_and_cost_structure": "Gross Margin & Cost Structure",
    "working_capital_and_cash": "Working Capital & Cash",
    "price_mix_vs_volume": "Price/Mix vs. Volume",
    "margin_resilience": "Margin Resilience",
    "cash_generation_and_payout": "Cash Generation & Payout",
    "cost_curve_and_competitiveness": "Cost Curve & Competitiveness",
    "price_sensitivity_and_cycle": "Price Sensitivity & Cycle",
    "capital_intensity_and_balance_sheet": "Capital Intensity & Balance Sheet",
    "network_economics_and_arpu": "Network Economics & ARPU",
    "capex_and_fcf": "Capex & FCF",
    "leverage_and_regulatory": "Leverage & Regulatory",
    "demand_and_cycle": "Demand & Cycle",
    "margin_leverage": "Margin Leverage",
    "cash_and_balance_sheet": "Cash & Balance Sheet",
}

MOAT_CLASS_MAP: dict[str, int] = {"WIDE": 80, "NARROW": 50, "NONE": 15}

READING_ORDER: list[int] = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]

# ═══════════════════════════════════════════════════════════════════════
# TABLE COLUMN DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════

_Col = dict[str, str]  # keys: k, h, f, and optionally alt


def _col(k: str, h: str, f: str, alt: str | None = None) -> _Col:
    d: _Col = {"k": k, "h": h, "f": f}
    if alt is not None:
        d["alt"] = alt
    return d


COLS: dict[str, list[_Col]] = {
    "revenue_by_segment": [
        _col("segment", "Segment", "text"),
        _col("revenue_m", "Revenue", "$smart"),
        _col("pct_of_total", "% of Total", "pct1"),
        _col("yoy_growth", "YoY Growth", "pct1"),
    ],
    "competitive_landscape": [
        _col("company", "Company", "text"),
        _col("revenue_usd_b", "Revenue ($B)", "$dec1"),
        _col("revenue_growth_pct", "Rev. Growth", "pct1"),
        _col("operating_margin_pct", "Op. Margin", "pct1"),
        _col("net_margin_pct", "Net Margin", "pct1"),
    ],
    # geographic_split: removed — SEC XBRL geographic axes too inconsistent
    "capital_allocation": [
        _col("year", "Year", "year"),
        _col("rd_m", "R&D", "$smart", alt="rd_usd_m"),
        _col("capex_m", "Capex", "$abs_smart", alt="capex_usd_m"),
        _col("ma_m", "M&A", "$abs_smart", alt="acquisitions_net_usd_m"),
        _col("dividends_m", "Dividends", "$abs_smart", alt="dividends_paid_usd_m"),
        _col("buybacks_m", "Buybacks", "$abs_smart", alt="buybacks_usd_m"),
    ],
    "revenue_growth": [
        _col("year", "Year", "year"),
        _col("revenue_m", "Revenue", "$smart"),
        _col("yoy_growth", "YoY Growth", "pct1"),
    ],
    "margins": [
        _col("year", "Year", "year"),
        _col("gross_margin", "Gross Margin", "pct2"),
        _col("operating_margin", "Op. Margin", "pct2"),
        _col("net_margin", "Net Margin", "pct2"),
        _col("eps_diluted", "EPS", "$dec2"),
    ],
    "cash_flow": [
        _col("year", "Year", "year"),
        _col("ocf_m", "OCF", "$smart"),
        _col("capex_pct_rev", "Capex/Rev", "pct2"),
        _col("fcf_m", "FCF", "$smart"),
        _col("fcf_margin", "FCF Margin", "pct2"),
    ],
    "returns_capital_efficiency": [
        _col("year", "Year", "year"),
        _col("roe", "ROE", "pct2"),
        _col("roic", "ROIC", "pct2"),
        _col("operating_margin", "Op. Margin", "pct2"),
    ],
    "leverage_balance_sheet": [
        _col("year", "Year", "year"),
        _col("net_debt_ebitda", "Net Debt/EBITDA", "x2"),
        _col("interest_coverage", "Int. Coverage", "x1"),
        _col("dso_days", "DSO (Days)", "days"),
    ],
    "bear_case_triggers": [
        _col("trigger", "Trigger", "text"),
        _col("probability", "Probability", "text"),
        _col("monitoring_metric", "What to Watch", "text"),
    ],
    "sensitivity_assumptions": [
        _col("assumption", "Assumption", "text"),
        _col("downside_scenario", "Downside", "text"),
        _col("monitoring_metric", "What to Watch", "text"),
    ],
    "profitability_comps": [
        _col("company", "Company", "text"),
        _col("gross_margin_pct", "Gross Margin", "pct1"),
        _col("operating_margin_pct", "Op. Margin", "pct1"),
        _col("ebitda_margin_pct", "EBITDA Margin", "pct1"),
        _col("net_margin_pct", "Net Margin", "pct1"),
        _col("roic_pct", "ROIC", "pct1"),
        _col("roe_pct", "ROE", "pct1"),
    ],
    "growth_comps": [
        _col("company", "Company", "text"),
        _col("revenue_growth_pct", "Rev. Growth", "pct1"),
        _col("operating_income_growth_pct", "Op. Inc. Growth", "pct1"),
        _col("eps_diluted_growth_pct", "EPS Growth", "pct1"),
        _col("fcf_growth_pct", "FCF Growth", "pct1"),
    ],
    "valuation_comps": [
        _col("company", "Company", "text"),
        _col("market_cap_usd_b", "Mkt Cap ($B)", "$B_dec1"),
        _col("ev_to_sales", "EV/Sales", "x1"),
        _col("ev_to_ebitda", "EV/EBITDA", "x1"),
        _col("price_to_fcf", "P/FCF", "x1"),
        _col("price_to_earnings", "P/E", "x1"),
        _col("price_to_book", "P/B", "x1"),
        _col("fcf_yield_pct", "FCF Yield", "pct1"),
        _col("dividend_yield_pct", "Div. Yield", "pct1"),
    ],
    "leverage_comps": [
        _col("company", "Company", "text"),
        _col("net_debt_to_ebitda", "ND/EBITDA", "x1"),
        _col("debt_to_equity", "D/E", "x1"),
        _col("interest_coverage", "Int. Cov.", "x1"),
        _col("current_ratio", "Current Ratio", "x1"),
    ],
    "efficiency_comps": [
        _col("company", "Company", "text"),
        _col("rd_to_revenue_pct", "R&D/Rev", "pct1"),
        _col("sbc_to_revenue_pct", "SBC/Rev", "pct1"),
        _col("capex_to_revenue_pct", "Capex/Rev", "pct1"),
        _col("dso_days", "DSO (Days)", "days"),
        _col("ccc_days", "CCC (Days)", "days"),
    ],
    "returns_comps": [
        _col("company", "Company", "text"),
        _col("roic_pct", "ROIC", "pct1"),
        _col("roe_pct", "ROE", "pct1"),
        _col("roa_pct", "ROA", "pct1"),
    ],
    "geographic_comps": [
        _col("company", "Company", "text"),
        _col("revenue_usd_b", "Revenue ($B)", "$B_dec1"),
        _col("us_pct", "US %", "pct1"),
        _col("international_pct", "Int'l %", "pct1"),
    ],
    # Financial-sector valuation comps (P/B, P/TBV instead of EV-based)
    "valuation_comps_financial": [
        _col("company", "Company", "text"),
        _col("market_cap_usd_b", "Mkt Cap ($B)", "$B_dec1"),
        _col("price_to_earnings", "P/E", "x1"),
        _col("price_to_book", "P/B", "x1"),
        _col("price_to_tangible_book", "P/TBV", "x1"),
        _col("dividend_yield_pct", "Div. Yield", "pct1"),
        _col("roe_pct", "ROE", "pct1"),
    ],
    # REIT valuation comps (P/FFO, P/AFFO)
    "valuation_comps_reit": [
        _col("company", "Company", "text"),
        _col("market_cap_usd_b", "Mkt Cap ($B)", "$B_dec1"),
        _col("p_ffo", "P/FFO", "x1"),
        _col("p_affo", "P/AFFO", "x1"),
        _col("dividend_yield_pct", "Div. Yield", "pct1"),
        _col("cap_rate_proxy", "Cap Rate (proxy)", "pct1"),
    ],
    # ── Sector-specific KPI tables ───────────────────────────────
    "bank_core_metrics": [
        _col("year", "Year", "text"),
        _col("nim_pct", "NIM", "pct2"),
        _col("efficiency_ratio_pct", "Efficiency", "pct1"),
        _col("roa_pct", "ROA", "pct2"),
        _col("roe_pct", "ROE", "pct1"),
        _col("fee_income_ratio_pct", "Fee Income %", "pct1"),
    ],
    "bank_credit_quality": [
        _col("year", "Year", "text"),
        _col("npl_ratio_pct", "NPL Ratio", "pct2"),
        _col("nco_rate_pct", "NCO Rate", "pct2"),
        _col("reserve_coverage_pct", "Reserve Cov.", "pct1"),
        _col("provision_to_loans_pct", "PCL/Loans", "pct2"),
    ],
    "bank_capital_funding": [
        _col("year", "Year", "text"),
        _col("cet1_ratio_pct", "CET1", "pct1"),
        _col("loan_to_deposit_pct", "L/D Ratio", "pct1"),
        _col("tbv_per_share", "TBV/Share", "$dec2"),
        _col("cost_of_deposits_pct", "Cost of Deps", "pct2"),
    ],
    "insurance_underwriting": [
        _col("year", "Year", "text"),
        _col("combined_ratio_pct", "Combined Ratio", "pct1"),
        _col("loss_ratio_pct", "Loss Ratio", "pct1"),
        _col("expense_ratio_pct", "Expense Ratio", "pct1"),
        _col("roe_pct", "ROE", "pct1"),
    ],
    "reit_operations": [
        _col("year", "Year", "text"),
        _col("ffo_per_share", "FFO/Share", "$dec2"),
        _col("affo_per_share", "AFFO/Share", "$dec2"),
        _col("noi_margin_pct", "NOI Margin", "pct1"),
        _col("debt_to_assets_pct", "Debt/Assets", "pct1"),
    ],
    "energy_operations": [
        _col("year", "Year", "text"),
        _col("production_mboed", "Prod. (mboe/d)", "dec1"),
        _col("reserve_replacement_pct", "RR Ratio", "pct0"),
        _col("finding_cost", "F&D Cost", "$dec2"),
        _col("lifting_cost", "Lifting Cost", "$dec2"),
    ],
    "retail_operations": [
        _col("year", "Year", "text"),
        _col("inventory_turnover", "Inv. Turns", "x1"),
        _col("gross_margin_pct", "Gross Margin", "pct1"),
        _col("sga_to_revenue_pct", "SGA/Rev", "pct1"),
        _col("operating_margin_pct", "Op. Margin", "pct1"),
    ],
    # ── Energy financials (fallback when per-BOE data is sparse) ──
    "energy_financials": [
        _col("year", "Year", "text"),
        _col("operating_margin_pct", "Op. Margin", "pct1"),
        _col("net_margin_pct", "Net Margin", "pct1"),
        _col("fcf_margin_pct", "FCF Margin", "pct1"),
        _col("roce_pct", "ROCE", "pct1"),
    ],
    # ── Tech / SaaS ──────────────────────────────────────────────
    "tech_financials": [
        _col("year", "Year", "text"),
        _col("gross_margin_pct", "Gross Margin", "pct1"),
        _col("operating_margin_pct", "Op. Margin", "pct1"),
        _col("fcf_margin_pct", "FCF Margin", "pct1"),
        _col("rd_intensity_pct", "R&D %", "pct1"),
    ],
    "tech_growth_metrics": [
        _col("year", "Year", "text"),
        _col("revenue_growth_pct", "Rev. Growth", "pct1"),
        _col("rule_of_40", "Rule of 40", "dec1"),
        _col("nrr_proxy_pct", "Rev. Retention", "pct1"),
        _col("sbc_pct_rev", "SBC/Rev", "pct1"),
    ],
    # ── Healthcare / Pharma ──────────────────────────────────────
    "healthcare_financials": [
        _col("year", "Year", "text"),
        _col("gross_margin_pct", "Gross Margin", "pct1"),
        _col("rd_intensity_pct", "R&D %", "pct1"),
        _col("sga_to_revenue_pct", "SGA/Rev", "pct1"),
        _col("net_margin_pct", "Net Margin", "pct1"),
    ],
    # ── Industrials ──────────────────────────────────────────────
    "industrials_operations": [
        _col("year", "Year", "text"),
        _col("operating_margin_pct", "Op. Margin", "pct1"),
        _col("roic_pct", "ROIC", "pct1"),
        _col("book_to_bill", "Book/Bill", "x2"),
        _col("backlog_to_revenue", "Backlog/Rev", "x2"),
    ],
    # ── Utilities ────────────────────────────────────────────────
    "utilities_operations": [
        _col("year", "Year", "text"),
        _col("operating_margin_pct", "Op. Margin", "pct1"),
        _col("net_margin_pct", "Net Margin", "pct1"),
        _col("roe_pct", "ROE", "pct1"),
        _col("debt_to_ebitda", "Debt/EBITDA", "x1"),
    ],
}

_PEER_TABLE_KEYS: set[str] = {
    "competitive_landscape",
    "profitability_comps",
    "growth_comps",
    "valuation_comps",
    "valuation_comps_financial",
    "valuation_comps_reit",
    "leverage_comps",
    "efficiency_comps",
    "geographic_comps",
}

# Sector KPI table keys (auto-rendered if present in precomputed_tables)
_SECTOR_TABLE_KEYS: set[str] = {
    "bank_core_metrics", "bank_credit_quality", "bank_capital_funding",
    "insurance_underwriting",
    "reit_operations",
    "energy_operations", "energy_financials",
    "retail_operations",
    "tech_financials", "tech_growth_metrics",
    "healthcare_financials",
    "industrials_operations",
    "utilities_operations",
}

# Narrative subsection key → sector KPI table key
# Maps each S10 narrative subsection to the table that should render inline after it
_NARRATIVE_TO_SECTOR_TABLE: dict[str, str] = {
    # ── Sector-specific KPI tables ───────────────────────────────
    # Banking
    "earnings_power_and_nim": "bank_core_metrics",
    "credit_quality": "bank_credit_quality",
    "capital_and_funding": "bank_capital_funding",
    # Insurance
    "underwriting_profitability": "insurance_underwriting",
    # REITs
    "ffo_affo_and_dividend": "reit_operations",
    # Utilities
    "regulated_earnings_and_rate_base": "utilities_operations",
    # Energy
    "unit_economics_and_costs": "energy_operations",
    "capital_discipline": "energy_financials",
    # Technology
    "growth_and_retention": "tech_growth_metrics",
    "profitability_and_efficiency": "tech_financials",
    # Healthcare
    "margins_and_cash_generation": "healthcare_financials",
    # Industrials
    "operations_and_margins": "industrials_operations",
    # Retail
    "gross_margin_and_cost_structure": "retail_operations",

    # ── Generic financial tables ─────────────────────────────────
    # Revenue/Growth → revenue_growth table
    "price_mix_vs_volume": "revenue_growth",
    "demand_and_unit_economics": "revenue_growth",
    "production_and_reserves": "revenue_growth",
    "revenue_mix_and_volume": "revenue_growth",
    "orders_backlog_and_cycle": "revenue_growth",
    "network_economics_and_arpu": "revenue_growth",
    "price_sensitivity_and_cycle": "revenue_growth",
    "demand_and_cycle": "revenue_growth",
    "portfolio_and_noi": "revenue_growth",
    "investment_income": "revenue_growth",
    # Margins → margins table
    "margin_resilience": "margins",
    "margin_leverage": "margins",
    "efficiency_and_costs": "margins",
    "r_and_d_and_sbc": "margins",
    "r_and_d_productivity": "margins",
    # Cash flow → cash_flow table
    "cash_generation_and_payout": "cash_flow",
    "working_capital_and_cash": "cash_flow",
    "cash_and_balance_sheet": "cash_flow",
    "capex_and_payout": "cash_flow",
    "capex_and_fcf": "cash_flow",
    "cost_curve_and_competitiveness": "cash_flow",
    # Returns → returns_capital_efficiency table
    "returns_and_capital_intensity": "returns_capital_efficiency",
    # Leverage → leverage_balance_sheet table
    "leverage_and_financing": "leverage_balance_sheet",
    "balance_sheet_and_funding": "leverage_balance_sheet",
    "leverage_and_regulatory": "leverage_balance_sheet",
    "capital_intensity_and_balance_sheet": "leverage_balance_sheet",
    "reserves_and_capital": "leverage_balance_sheet",
}

_PEER_COMP_KEYS: set[str] = {
    "profitability_comparison",
    "growth_comparison",
    "leverage_comparison",
    "efficiency_comparison",
    "returns_comparison",
    # "geographic_comparison" — skipped
}

# ═══════════════════════════════════════════════════════════════════════
# HELPERS — NaN-safe numeric
# ═══════════════════════════════════════════════════════════════════════


def _safe_num(v: Any) -> Optional[float]:
    """Convert *v* to float, returning ``None`` for non-numeric values."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        if math.isnan(v) or math.isinf(v):
            return None
        return float(v)
    if isinstance(v, str):
        cleaned = v.replace("$", "").replace(",", "").replace("%", "").replace("x", "").strip()
        try:
            n = float(cleaned)
            return None if math.isnan(n) or math.isinf(n) else n
        except (ValueError, TypeError):
            return None
    return None


def _safe_get(d: Any, *keys: str, default: Any = None) -> Any:
    """Nested dict access with safe fallback."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


def _nn(v: Any, fallback: Any = 0) -> Any:
    """Return *v* if not None, else *fallback*."""
    return v if v is not None else fallback


def _to_num(v: Any) -> Optional[float]:
    """Convert value to float for score extraction; None if impossible."""
    if v is None or v == "":
        return None
    try:
        n = float(v)
        return None if math.isnan(n) else n
    except (ValueError, TypeError):
        return None


# ═══════════════════════════════════════════════════════════════════════
# INDUSTRY NORMALISATION & SECTOR DETECTION
# ═══════════════════════════════════════════════════════════════════════


def _normalize_industry(s: str) -> str:
    if not s:
        return ""
    # Replace em-dash / en-dash with hyphen
    s = re.sub(r"[\u2014\u2013]", " - ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # v8.2: added Oil, Gas to prefix list
    for prefix in ("Banks", "Insurance", "REIT", "Thrifts", "Oil", "Gas"):
        if s.startswith(prefix + " ") and " - " not in s:
            s = prefix + " - " + s[len(prefix) + 1 :]
            break
    return s


def _detect_sector(
    dcf_anchors: dict, industry_from_fs: str
) -> tuple[bool, bool, bool, str]:
    """Return (is_financial, is_asset_heavy, is_reit, normalized_industry)."""
    normalized = _normalize_industry(industry_from_fs)
    vm = (dcf_anchors or {}).get("valuation_method", "")

    is_financial = (
        vm == "peer_multiples_only_financial"
        or vm == "peer_multiples_only"  # v9 backward compat
        or normalized in _SKIP_DCF_FINANCIAL
    )

    is_asset_heavy = (
        vm == "peer_multiples_only_asset_heavy"
        or normalized in _SKIP_DCF_ASSET_HEAVY
    )

    is_reit = normalized.startswith("REIT") or (
        isinstance(dcf_anchors, dict)
        and str(dcf_anchors.get("industry", "")).startswith("REIT")
    )

    return is_financial, is_asset_heavy, is_reit, normalized


# ═══════════════════════════════════════════════════════════════════════
# VALUE FORMATTING  (mirrors JS ``fmt()``)
# ═══════════════════════════════════════════════════════════════════════


def _fmt(val: Any, f: str) -> str:
    """Format *val* according to format specifier *f*."""
    if val is None or val == "":
        return "\u2014"

    cleaned = val
    if isinstance(val, str):
        cleaned = val.lstrip("$").replace(",", "")
    n = _safe_num(cleaned)

    if f == "year":
        return str(val)
    if f == "yr":
        return str(val) if n is None else f"{int(n)} years"
    if f == "int":
        return str(val) if n is None else f"{int(round(n)):,}"
    if f == "intM":
        return str(val) if n is None else f"{int(round(n)):,}M"
    if f == "dec1":
        return str(val) if n is None else f"{n:.1f}"
    if f == "dec2":
        return str(val) if n is None else f"{n:.2f}"
    if f == "$int":
        return str(val) if n is None else f"${int(round(n)):,}"
    if f == "$dec1":
        return str(val) if n is None else f"${n:.1f}"
    if f == "$dec2":
        return str(val) if n is None else f"${n:.2f}"
    if f == "$smart":
        return _fmt_dollar_smart(val, n, signed=True)
    if f == "$abs":
        return str(val) if n is None else f"${abs(int(round(n))):,}"
    if f == "$abs_smart":
        return _fmt_dollar_smart(val, n, signed=False)
    if f == "$B_dec1":
        if n is None:
            return str(val)
        ab = abs(n)
        sign = "-" if n < 0 else ""
        if ab >= 1000:
            return f"{sign}${ab / 1000:.2f}T"
        return f"{sign}${ab:.1f}B"
    if f == "pct1":
        return str(val) if n is None else f"{n:.1f}%"
    if f == "pct2":
        return str(val) if n is None else f"{n:.2f}%"
    if f == "x1":
        return str(val) if n is None else f"{n:.1f}x"
    if f == "x2":
        return str(val) if n is None else f"{n:.2f}x"
    if f == "days":
        return str(val) if n is None else str(round(n))
    # text / default
    return str(val)


def _fmt_dollar_smart(val: Any, n: Optional[float], *, signed: bool) -> str:
    """Format a value in millions as human-readable dollars."""
    if n is None:
        return str(val)
    ab = abs(n)
    if ab < 0.001:
        return "\u2014"
    sign = ("-" if n < 0 else "") if signed else ""
    if ab >= 1e6:
        return f"{sign}${ab / 1e6:.2f}T"
    if ab >= 1e3:
        b = f"{ab / 1e3:.2f}"
        if b.endswith(".00"):
            b = b[:-3]
        return f"{sign}${b}B"
    if ab >= 1:
        m = f"{ab:.2f}"
        if m.endswith(".00"):
            m = m[:-3]
        return f"{sign}${m}M"
    if ab >= 0.001:
        return f"{sign}${int(ab * 1000)}K"
    return f"{sign}${ab:.2f}M"


# ═══════════════════════════════════════════════════════════════════════
# TABLE RENDERER
# ═══════════════════════════════════════════════════════════════════════


def _render_table(rows: list[dict], cols: list[_Col], *, min_peers: int = 4) -> str:
    """Render *rows* as a markdown table using column definitions *cols*."""
    if not rows or not cols:
        return ""
    # Resolve alt keys when primary key has no data
    resolved = []
    for c in cols:
        alt = c.get("alt")
        if alt and not any(
            r.get(c["k"]) not in (None, "", 0) for r in rows
        ):
            if any(r.get(alt) not in (None, "", 0) for r in rows):
                resolved.append({**c, "k": alt})
                continue
        resolved.append(c)

    # Filter out columns with no data (except text/year label columns)
    active = [
        c
        for c in resolved
        if c["f"] in ("text", "year")
        or any(
            r.get(c["k"]) not in (None, "", 0, "\u2014") for r in rows
        )
    ]
    if len(active) <= 1:
        return ""

    # For peer-type tables (rows with "company" key), drop rows with mostly
    # missing data while keeping subject, Peer Median, and at least min_peers.
    data_cols = [c for c in active if c["f"] not in ("text", "year")]
    if data_cols and any(r.get("company") for r in rows):
        def _fill_ratio(r: dict) -> float:
            filled = sum(1 for c in data_cols if r.get(c["k"]) not in (None, "", 0, "\u2014"))
            return filled / len(data_cols) if data_cols else 0

        # Separate pinned rows (first = subject, last = Peer Median) from peers
        pinned_top = rows[:1]
        pinned_bot = [rows[-1]] if len(rows) > 1 and "Median" in str(rows[-1].get("company", "")) else []
        middle = rows[1:(-1 if pinned_bot else len(rows))]

        # Sort peers by fill ratio descending; prefer peers with >=75% data
        scored = sorted(middle, key=_fill_ratio, reverse=True)
        kept = [r for r in scored if _fill_ratio(r) >= 0.75]
        # If not enough high-quality peers, relax to >=50%
        if len(kept) < min_peers:
            kept = [r for r in scored if _fill_ratio(r) >= 0.5]
        # Still not enough — take the best available
        if len(kept) < min_peers:
            kept = scored[:min_peers]
        # Cap at 7 peers to avoid overly long tables
        kept = kept[:7]
        rows = pinned_top + kept + pinned_bot

    header = "| " + " | ".join(c["h"] for c in active) + " |"
    divider = "| " + " | ".join("---" for _ in active) + " |"
    body_lines = [
        "| " + " | ".join(_fmt(r.get(c["k"]), c["f"]) for c in active) + " |"
        for r in rows
    ]
    return "\n".join([header, divider, *body_lines])


# ═══════════════════════════════════════════════════════════════════════
# FOOTNOTE HELPERS
# ═══════════════════════════════════════════════════════════════════════

_MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def _fmt_date(iso_date: Optional[str]) -> Optional[str]:
    if not iso_date:
        return None
    try:
        parts = iso_date.split("-")
        y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
        return f"{_MONTH_NAMES[m - 1]} {d}, {y}"
    except Exception:
        return None


class _FootnoteCtx:
    """Encapsulates fiscal period / data-as-of metadata for footnotes."""

    def __init__(self, fiscal_periods: Optional[str], data_as_of: Optional[str],
                 latest_annual_year: str, latest_year: str):
        self.fiscal_periods = fiscal_periods
        self.data_as_of = data_as_of
        self.latest_annual_year = latest_annual_year
        self.latest_year = latest_year

    def peer_footnote(self, *, include_date: bool = False) -> str:
        parts: list[str] = []
        if self.fiscal_periods:
            parts.append("Fiscal periods: " + self.fiscal_periods)
        if include_date and self.data_as_of:
            formatted = _fmt_date(self.data_as_of)
            if formatted:
                parts.append("Valuation data as of " + formatted)
        if not parts:
            return ""
        return "\n\n*" + ". ".join(parts) + ".*"

    def data_year_note(self) -> str:
        yr = self.latest_annual_year or self.latest_year
        return f"\n\n*Data reflects {yr} filing.*" if yr else ""

    def table_footnote(self, parent_key: str, cols: list[_Col]) -> str:
        if parent_key in _PEER_TABLE_KEYS:
            return self.peer_footnote(
                include_date=(parent_key in ("valuation_comps", "valuation_comps_reit", "valuation_comps_financial"))
            )
        if cols and any(c["k"] == "year" for c in cols):
            return ""
        return self.data_year_note()


# ═══════════════════════════════════════════════════════════════════════
# DCF COMPUTATION — mirrors JS ``computeDcfScenario``
# v8.3: NaN-safe roic_pct guard
# ═══════════════════════════════════════════════════════════════════════


def compute_dcf_scenario(assumptions: dict, anchors: dict) -> DCFScenario:
    """Run a dynamic-horizon NOPAT-based DCF and return a :class:`DCFScenario`.

    Projection horizon adapts to growth profile:
      - CAGR > 20%  → 10 years  (early-stage / hyper-growth)
      - CAGR 10-20% → 7 years   (growth)
      - CAGR < 10%  → 5 years   (mature)
    """

    def _pct_to_dec(v: Any, fallback_pct: float) -> float:
        if v is None:
            return fallback_pct / 100.0
        n = _safe_num(v)
        if n is None:
            return fallback_pct / 100.0
        return n if abs(n) < 1 else n / 100.0

    def _pct_to_dec_dilution(v: Any, fallback_pct: float) -> float:
        if v is None:
            return fallback_pct / 100.0
        n = _safe_num(v)
        if n is None:
            return fallback_pct / 100.0
        if abs(n) >= 1:
            return n / 100.0
        if abs(n) > 0.05:
            return n / 100.0
        return n

    rev_cagr = _pct_to_dec(assumptions.get("revenue_cagr"), 8)
    term_margin = _pct_to_dec(assumptions.get("terminal_op_margin"), 30)
    term_growth = _pct_to_dec(assumptions.get("terminal_growth"), 3)
    wacc = _pct_to_dec(assumptions.get("wacc"), 9)
    annual_dilution = _pct_to_dec_dilution(
        assumptions.get("annual_dilution_pct"), 0
    )

    # Dynamic projection horizon based on growth profile
    if rev_cagr > 0.20:
        proj_years = 10   # early-stage / hyper-growth
    elif rev_cagr > 0.10:
        proj_years = 7    # growth
    else:
        proj_years = 5    # mature

    base_rev = anchors.get("revenue_usd_m", 0) or 0
    net_debt = anchors.get("net_debt_usd_m", 0) or 0
    base_shares = anchors.get("shares_diluted_m", 1) or 1

    proj_tax_rate = (anchors.get("effective_tax_rate_pct") or 21) / 100.0
    term_tax_rate = (anchors.get("terminal_tax_rate_pct") or 21) / 100.0
    capex_pct_rev = (anchors.get("capex_pct_of_revenue") or 5) / 100.0
    da_pct_rev = (anchors.get("da_pct_of_revenue") or 3.5) / 100.0

    # v8.3 FIX: NaN-safe roic — guard against "NM" string in anchors
    roic_raw = anchors.get("roic_pct")
    roic_val = (
        roic_raw
        if isinstance(roic_raw, (int, float)) and not math.isnan(roic_raw)
        else 15
    )
    roic = roic_val / 100.0
    net_capex_pct_rev = max(0, capex_pct_rev - da_pct_rev)
    starting_op_margin = (
        anchors.get("operating_margin_pct")
        or anchors.get("fcf_margin_pct")
        or 20
    ) / 100.0

    proj_rev = base_rev
    total_pv_fcf = 0.0
    shares = float(base_shares)

    for y in range(1, proj_years + 1):
        proj_rev *= 1 + rev_cagr
        blended_op_margin = starting_op_margin + (
            term_margin - starting_op_margin
        ) * (y / proj_years)
        nopat = proj_rev * blended_op_margin * (1 - proj_tax_rate)
        fcff = nopat - proj_rev * net_capex_pct_rev
        total_pv_fcf += fcff / ((1 + wacc) ** y)
        shares *= 1 + annual_dilution

    terminal_nopat = proj_rev * term_margin * (1 - term_tax_rate)

    used_gwacc = False
    used_min_floor = False

    # For high-SBC software/tech companies, GAAP ROIC < 5% is an accounting
    # artifact (SBC depresses net income, inflates invested capital). Use a
    # sector-appropriate floor of 15% ROIC so terminal reinvestment isn't
    # absurdly inflated by g/WACC.
    effective_roic = roic
    if roic <= 0.05:
        effective_roic = 0.15  # sector floor for capital-light businesses
        used_gwacc = True
    term_reinvest = min(term_growth / effective_roic, 0.5) if effective_roic > 0 else 0.20

    if term_reinvest < 0.05:
        used_min_floor = True
    term_reinvest = max(term_reinvest, 0.05)

    terminal_fcff = terminal_nopat * (1 - term_reinvest)
    tv = terminal_fcff * (1 + term_growth) / (wacc - term_growth)
    pv_tv = tv / ((1 + wacc) ** proj_years)
    ev = total_pv_fcf + pv_tv
    eq_val = ev - net_debt

    return DCFScenario(
        revenue_cagr=rev_cagr,
        terminal_op_margin=term_margin,
        terminal_nopat=round(terminal_nopat),
        terminal_fcff=round(terminal_fcff),
        terminal_growth=term_growth,
        wacc=wacc,
        effective_tax_rate=proj_tax_rate,
        terminal_tax_rate=term_tax_rate,
        reinvestment_rate=term_reinvest,
        annual_dilution_pct=annual_dilution,
        enterprise_value=round(ev),
        net_debt=round(net_debt),
        equity_value=round(eq_val),
        shares_outstanding=round(shares),
        fair_value_per_share=round(eq_val / shares * 100) / 100 if shares else 0,
        projection_years=proj_years,
        dilution_applied=annual_dilution > 0,
        used_gwacc=used_gwacc,
        used_min_floor=used_min_floor,
    )


def _compute_sens_matrix(
    base_assumptions: dict, anchors: dict,
    wacc_values: list, growth_values: list,
) -> list[list[int]]:
    """Build sensitivity matrix (WACC rows x Growth cols)."""
    matrix: list[list[int]] = []
    for w in wacc_values:
        row: list[int] = []
        for g in growth_values:
            scenario = compute_dcf_scenario(
                {**base_assumptions, "wacc": w, "terminal_growth": g}, anchors
            )
            row.append(round(scenario.fair_value_per_share))
        matrix.append(row)
    return matrix


# ═══════════════════════════════════════════════════════════════════════
# DCF RENDERING
# ═══════════════════════════════════════════════════════════════════════


def _render_dcf(
    dcf_table: dict, anchors: dict,
) -> str:
    """Render bull/base/bear DCF table as markdown."""
    if not dcf_table:
        return ""
    bull = compute_dcf_scenario(dcf_table.get("bull") or {}, anchors)
    base = compute_dcf_scenario(dcf_table.get("base") or {}, anchors)
    bear = compute_dcf_scenario(dcf_table.get("bear") or {}, anchors)

    # Convert decimals to percentages for display
    for s in (bull, base, bear):
        s.revenue_cagr *= 100
        s.terminal_op_margin *= 100
        s.terminal_growth *= 100
        s.wacc *= 100
        s.annual_dilution_pct = (s.annual_dilution_pct or 0) * 100
        s.effective_tax_rate = (s.effective_tax_rate or 0) * 100
        s.terminal_tax_rate = (s.terminal_tax_rate or 0) * 100
        s.reinvestment_rate = (s.reinvestment_rate or 0) * 100

    metric_rows = [
        ("Projection Horizon", "projection_years", "yr"),
        ("Revenue CAGR", "revenue_cagr", "pct1"),
        ("Terminal Op. Margin", "terminal_op_margin", "pct1"),
        ("Effective Tax Rate", "effective_tax_rate", "pct1"),
        ("Terminal Tax Rate", "terminal_tax_rate", "pct1"),
        ("Terminal NOPAT", "terminal_nopat", "$smart"),
        ("Reinvestment Rate", "reinvestment_rate", "pct1"),
        ("Terminal FCFF", "terminal_fcff", "$smart"),
        ("Terminal Growth", "terminal_growth", "pct1"),
        ("WACC", "wacc", "pct1"),
        ("Annual Dilution", "annual_dilution_pct", "pct1"),
        ("Enterprise Value", "enterprise_value", "$smart"),
        ("Net Debt", "net_debt", "$smart"),
        ("Equity Value", "equity_value", "$smart"),
        ("Diluted Shares (M)", "shares_outstanding", "intM"),
        ("Fair Value / Share", "fair_value_per_share", "$dec2"),
    ]

    h = "| Metric | Bull | Base | Bear |"
    dv = "| --- | --- | --- | --- |"
    body = []
    for label, key, fmt_spec in metric_rows:
        body.append(
            f"| {label} | {_fmt(getattr(bull, key), fmt_spec)} "
            f"| {_fmt(getattr(base, key), fmt_spec)} "
            f"| {_fmt(getattr(bear, key), fmt_spec)} |"
        )

    notes: list[str] = []
    if base.projection_years != 5:
        notes.append(
            f"Dynamic horizon: {base.projection_years}-year projection "
            f"(CAGR {'>' if base.revenue_cagr > 20 else ''}{'10-20' if base.revenue_cagr <= 20 else '20'}%)."
        )
    if base.dilution_applied:
        notes.append(
            f"Share count reflects {base.annual_dilution_pct:.1f}% annual dilution."
        )
    if base.used_gwacc:
        notes.append(
            "ROIC floor applied (15%) \u2014 trailing GAAP ROIC below 5% (likely SBC-depressed)."
        )
    if base.used_min_floor:
        notes.append("Reinvestment rate floored at 5% minimum.")

    result = "\n".join([h, dv, *body])
    if notes:
        result += "\n\n*" + " ".join(notes) + "*"

    # ── Probability-Weighted Fair Value ──────────────────────────
    prob_bull = _safe_num(dcf_table.get("bull", {}).get("probability_pct")) or 25
    prob_base = _safe_num(dcf_table.get("base", {}).get("probability_pct")) or 50
    prob_bear = _safe_num(dcf_table.get("bear", {}).get("probability_pct")) or 25
    # Normalize if they don't sum to 100
    total_prob = prob_bull + prob_base + prob_bear
    if total_prob > 0 and total_prob != 100:
        prob_bull = prob_bull / total_prob * 100
        prob_base = prob_base / total_prob * 100
        prob_bear = prob_bear / total_prob * 100

    weighted_fv = (
        bull.fair_value_per_share * prob_bull / 100
        + base.fair_value_per_share * prob_base / 100
        + bear.fair_value_per_share * prob_bear / 100
    )
    result += (
        f"\n\n**Probability-Weighted Fair Value: ${weighted_fv:,.2f}** "
        f"(Bull {prob_bull:.0f}% × ${bull.fair_value_per_share:,.2f} + "
        f"Base {prob_base:.0f}% × ${base.fair_value_per_share:,.2f} + "
        f"Bear {prob_bear:.0f}% × ${bear.fair_value_per_share:,.2f})"
    )

    # ── Scenario Narratives ──────────────────────────────────────
    for label, scenario_data, fv in [
        ("Bull Case", dcf_table.get("bull", {}), bull.fair_value_per_share),
        ("Base Case", dcf_table.get("base", {}), base.fair_value_per_share),
        ("Bear Case", dcf_table.get("bear", {}), bear.fair_value_per_share),
    ]:
        narrative = scenario_data.get("narrative", "")
        triggers = scenario_data.get("key_triggers", [])
        risk = scenario_data.get("scenario_risk", "")
        prob = _safe_num(scenario_data.get("probability_pct")) or 0
        if narrative:
            result += f"\n\n**{label}** (${fv:,.2f}/share, {prob:.0f}% probability)"
            result += f" — {narrative}"
            if triggers:
                result += f"\n**Triggers:** {'; '.join(triggers)}"
            if risk:
                result += f"\n**Risk:** {risk}"

    return result


def _render_sens_matrix(
    sens_table: dict, base_assumptions: dict, anchors: dict,
) -> str:
    """Render sensitivity matrix as markdown."""
    if not sens_table:
        return ""
    w_vals = sens_table.get("wacc_values", [])
    g_vals = sens_table.get("growth_values", [])
    if not w_vals or not g_vals:
        return ""

    matrix = _compute_sens_matrix(base_assumptions, anchors, w_vals, g_vals)

    def _pct_label(v: Any) -> str:
        n = _safe_num(v)
        if n is None:
            return str(v)
        return f"{n:.1f}%" if n > 1 else f"{n * 100:.1f}%"

    h = "| WACC \\ Growth | " + " | ".join(_pct_label(g) for g in g_vals) + " |"
    dv = "| --- | " + " | ".join("---" for _ in g_vals) + " |"
    body = []
    for i, row in enumerate(matrix):
        wl = _pct_label(w_vals[i]) if i < len(w_vals) else "\u2014"
        body.append(
            f"| {wl} | " + " | ".join(f"${v:,}" for v in row) + " |"
        )
    return "\n".join([h, dv, *body])


def _render_peers(peer_table: Optional[dict]) -> str:
    """Render peer valuation table as markdown.

    For financial sectors (banks, insurance), renders P/E, P/B, P/TBV, Div Yield, ROE.
    For standard sectors, renders EV/Revenue, EV/EBITDA, P/FCF, P/E, P/B.
    """
    if not peer_table:
        return ""
    subj = peer_table.get("subject_company") or {}
    peers = peer_table.get("peers") or []
    med = peer_table.get("peer_median") or {}
    is_financial = peer_table.get("is_financial", False)
    all_rows = [subj, *peers, med]

    if is_financial:
        # Financial sector: P/E, P/B, P/TBV, Div Yield, ROE
        if not any(
            r.get("p_e") is not None or r.get("p_b") is not None
            or r.get("p_tbv") is not None
            for r in all_rows
        ):
            return ""

        cols = ["Company", "P/E", "P/B", "P/TBV", "Div. Yield", "ROE"]

        def _row(p: dict) -> str:
            return "| " + " | ".join([
                p.get("company_name") or "\u2014",
                _fmt(p.get("p_e"), "x1"),
                _fmt(p.get("p_b"), "x1"),
                _fmt(p.get("p_tbv"), "x1"),
                _fmt(p.get("div_yield"), "pct1"),
                _fmt(p.get("roe"), "pct1"),
            ]) + " |"

        med_row = "| " + " | ".join([
            "Peer Median",
            _fmt(med.get("p_e"), "x1"),
            _fmt(med.get("p_b"), "x1"),
            _fmt(med.get("p_tbv"), "x1"),
            _fmt(med.get("div_yield"), "pct1"),
            _fmt(med.get("roe"), "pct1"),
        ]) + " |"
    else:
        # Standard sector: EV/Revenue, EV/EBITDA, P/FCF, P/E, P/B
        if not any(
            r.get("ev_revenue") is not None
            or r.get("ev_ebitda") is not None
            or r.get("p_fcf") is not None
            or r.get("p_e") is not None
            or r.get("p_b") is not None
            for r in all_rows
        ):
            return ""

        has_pb = any(r.get("p_b") is not None for r in all_rows)
        cols = ["Company", "EV/Revenue", "EV/EBITDA", "P/FCF", "P/E"]
        if has_pb:
            cols.append("P/B")

        def _row(p: dict) -> str:
            cells = [
                p.get("company_name") or "\u2014",
                _fmt(p.get("ev_revenue"), "x1"),
                _fmt(p.get("ev_ebitda"), "x1"),
                _fmt(p.get("p_fcf"), "x1"),
                _fmt(p.get("p_e"), "x1"),
            ]
            if has_pb:
                cells.append(_fmt(p.get("p_b"), "x1"))
            return "| " + " | ".join(cells) + " |"

        med_cells = [
            "Peer Median",
            _fmt(med.get("ev_revenue"), "x1"),
            _fmt(med.get("ev_ebitda"), "x1"),
            _fmt(med.get("p_fcf"), "x1"),
            _fmt(med.get("p_e"), "x1"),
        ]
        if has_pb:
            med_cells.append(_fmt(med.get("p_b"), "x1"))
        med_row = "| " + " | ".join(med_cells) + " |"

    h = "| " + " | ".join(cols) + " |"
    dv = "| " + " | ".join("---" for _ in cols) + " |"

    return "\n".join([h, dv, _row(subj), *[_row(p) for p in peers], med_row])


# ═══════════════════════════════════════════════════════════════════════
# PROSE RENDERER  (used by aggregator pass-through)
# ═══════════════════════════════════════════════════════════════════════


def render_prose(obj: Any) -> str:
    """Recursively render structured JSON to prose (Aggregate 1/2 style)."""
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, (int, float)):
        return str(obj)
    if isinstance(obj, list):
        return "\n".join(filter(None, (render_prose(item) for item in obj)))
    if isinstance(obj, dict):
        parts: list[str] = []
        for key, value in obj.items():
            if key == "section_number":
                continue
            if value is None:
                continue
            rendered = render_prose(value)
            if rendered:
                parts.append(rendered)
        return "\n\n".join(parts)
    return ""


# ═══════════════════════════════════════════════════════════════════════
# SECTION RENDERER — structured JSON -> markdown
# ═══════════════════════════════════════════════════════════════════════

_SKIP_KEYS: set[str] = {
    "section_number", "section_title", "word_target",
    "section_thesis", "quality_score", "growth_score",
    "moat_score", "fair_value",
}

_EMPTY_RE = re.compile(
    r"does not (?:publicly )?disclose"
    r"|not (?:publicly )?(?:disclosed|available|reported)"
    r"|no (?:public )?(?:data|information) (?:is )?available",
    re.IGNORECASE,
)

_EMPTY_CONTENT_RE = re.compile(
    r"does not (?:publicly )?disclose"
    r"|not (?:publicly )?(?:disclosed|available|reported)"
    r"|no (?:public )?(?:data|information) (?:is )?available"
    r"|data (?:is )?(?:not available|unavailable|insufficient)",
    re.IGNORECASE,
)


def _is_risk_block(n: Any) -> bool:
    return (
        isinstance(n, dict)
        and not isinstance(n, list)
        and "label" in n
        and "transmission" in n
        and "probability_and_monitoring" in n
    )


def _is_labeled_block(n: Any) -> bool:
    return (
        isinstance(n, dict)
        and "label" in n
        and isinstance(n.get("label"), str)
        and "transmission" not in n
        and "evidence" not in n
        and "paragraph" in n
        and isinstance(n.get("paragraph"), str)
    )


def _is_table_wrapper(n: Any) -> bool:
    if not isinstance(n, dict):
        return False
    keys = set(n.keys())
    if "intro" in keys and "table" in keys and isinstance(n.get("table"), list):
        return True
    if "intro" in keys and "analysis" in keys and len(keys) <= 3:
        return True
    return False


def _is_moat_block(n: Any) -> bool:
    return (
        isinstance(n, dict)
        and "label" in n
        and "evidence" in n
        and "mechanism" in n
    )


def _is_peer_comp_block(n: Any, pk: str) -> bool:
    return (
        isinstance(n, dict)
        and pk in _PEER_COMP_KEYS
        and "intro" in n
        and "analysis" in n
    )


class _SectionRenderer:
    """Stateful section renderer that holds assembly-wide context."""

    def __init__(
        self,
        dcf_anchors: dict,
        structured_map: dict,
        precomputed_peer_table: Optional[dict],
        precomputed_tables: dict,
        peer_bench_data: dict,
        footnote_ctx: _FootnoteCtx,
    ):
        self.dcf_anchors = dcf_anchors
        self.structured_map = structured_map
        self.precomputed_peer_table = precomputed_peer_table
        self.precomputed_tables = precomputed_tables
        self.peer_bench_data = peer_bench_data
        self.fn = footnote_ctx
        self._rendered_sector_tables: set[str] = set()

    def _is_financial_sector(self) -> bool:
        """Check if this is a financial-sector company (banks, insurance, REITs)."""
        vm = (self.dcf_anchors or {}).get("valuation_method", "")
        return vm.startswith("peer_multiples_only_financial")

    def _is_reit_sector(self) -> bool:
        ind = (self.dcf_anchors or {}).get("industry", "") or ""
        return isinstance(ind, str) and ind.startswith("REIT")

    def _render_peer_bench_table(self, table_name: str) -> str:
        table_data = self.peer_bench_data.get(table_name)
        if not table_data or not isinstance(table_data, list) or len(table_data) == 0:
            return ""
        cols = COLS.get(table_name)
        if not cols:
            return ""
        return _render_table(table_data, cols)

    def _render_sector_kpi_table(self, table_name: str) -> str:
        """Render a sector-specific KPI table from precomputed tables."""
        table_data = self.precomputed_tables.get(table_name)
        if not table_data or not isinstance(table_data, list) or len(table_data) == 0:
            return ""
        cols = COLS.get(table_name)
        if not cols:
            return ""
        return _render_table(table_data, cols)

    def render_section(self, obj: Any) -> str:
        """Recursively render a structured section dict to markdown."""
        if not obj or not isinstance(obj, dict):
            return ""
        out: list[str] = []
        self._walk(obj, "", out)
        return "\n".join(x for x in out if x is not None)

    def _walk(self, node: Any, pk: str, out: list[str]) -> None:  # noqa: C901
        if node is None:
            return
        if isinstance(pk, str) and pk in _SKIP_KEYS:
            return

        # Special-case rendering for known keys
        if pk == "dcf_table" and isinstance(node, dict):
            out.extend(["", _render_dcf(node, self.dcf_anchors), ""])
            return

        if pk == "sensitivity_table" and isinstance(node, dict):
            base_assum = _safe_get(
                self.structured_map, "section_12", "dcf_analysis", "dcf_table", "base",
                default={},
            )
            out.extend(
                ["", _render_sens_matrix(node, base_assum, self.dcf_anchors), ""]
            )
            return

        if pk == "peer_table" and isinstance(node, dict):
            r = _render_peers(self.precomputed_peer_table or node)
            if r:
                out.extend(["", r, ""])
            return

        if pk == "dcf_analysis" and isinstance(node, dict):
            if node.get("intro"):
                out.extend([node["intro"], ""])
            if node.get("dcf_table"):
                self._walk(node["dcf_table"], "dcf_table", out)
            if node.get("analysis"):
                out.extend([node["analysis"], ""])
            return

        if pk == "sensitivity_analysis" and isinstance(node, dict):
            if node.get("intro"):
                out.extend([node["intro"], ""])
            if node.get("sensitivity_table"):
                self._walk(node["sensitivity_table"], "sensitivity_table", out)
            if node.get("analysis"):
                out.extend([node["analysis"], ""])
            return

        if pk == "peer_valuation" and isinstance(node, dict):
            if node.get("intro"):
                out.extend([node["intro"], ""])
            rendered_val = False
            # REIT: prefer P/FFO table when available
            reit_val = self.peer_bench_data.get("valuation_comps_reit")
            if reit_val and isinstance(reit_val, list) and len(reit_val) > 0:
                r = self._render_peer_bench_table("valuation_comps_reit")
                if r:
                    out.extend([r + self.fn.peer_footnote(include_date=True), ""])
                    rendered_val = True
            if not rendered_val and self.precomputed_peer_table:
                r = _render_peers(self.precomputed_peer_table)
                if r:
                    out.extend([r + self.fn.peer_footnote(include_date=True), ""])
            if node.get("analysis"):
                out.extend([node["analysis"], ""])
            return

        # ── Scenario Analysis (non-DCF modes) ────────────────────
        def _clean_implied_multiple(s: str) -> str:
            """Fix garbled implied_multiple strings from LLM.

            LLM frequently emits stray numbers, control chars, and broken
            arrow characters between the multiple/description and the $ value.
            Examples:
              'P/B of 2.0x \\x0210 $83/share' → 'P/B of 2.0x → $83/share'
              'P/E of 45.0x 16; 16 16 --> $160' → 'P/E of 45.0x → $160'
              'DDM: 4.5% growth, 8.5% CoE 10 $66' → 'DDM: 4.5% growth, 8.5% CoE → $66'
              'P/B of 2.0x 10 00 10' → 'P/B of 2.0x'
            """
            if not s:
                return s
            # Strip control characters (LLM sometimes emits STX/ETX)
            s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", s)
            # Normalize arrow variants to →
            s = s.replace("-->", "→").replace("->", "→")
            # Collapse multiple spaces
            s = re.sub(r"  +", " ", s)
            # Strip duplicate x-multiples after the primary one
            # e.g., "40.0x 10.0x 1.0x 1.0x" → "40.0x"
            s = re.sub(r"(\d+\.?\d*x)(?:\s+\d+\.?\d*x)+", r"\1", s)
            # Strip stray garbage (digits, semicolons, spaces, commas)
            # between content and a $ value or → $ value.
            # Catches: "2.0x 16; 16 16 $160" and "CoE 10 $66"
            # NOTE: no 'x' in char class to avoid eating valid multiples like "2.8x"
            s = re.sub(
                r"(\S)\s+[\d\.\s;,]+(?:→\s*)?\$",
                r"\1 → $",
                s,
            )
            # Remove trailing stray numbers with no $ at all
            # e.g., "P/B of 2.0x 10 00 10" → "P/B of 2.0x"
            s = re.sub(
                r"(\d+\.?\d*x)\s+[\d\.\s;,]+$",
                r"\1",
                s,
            )
            # Fix missing $ sign: "32.0x 199/share" → "32.0x → $199/share"
            if "/share" in s and "$" not in s:
                s = re.sub(
                    r"(\d+\.?\d*x)\s+(\d[\d,]*(?:\.\d{1,2})?)/share",
                    r"\1 → $\2/share",
                    s,
                )
            # Remove stray short tokens between multiple and arrow
            # e.g., "36.0x 1arr → $223" → "36.0x → $223"
            s = re.sub(r"(\d+\.?\d*x)\s+\S{1,6}\s+(→)", r"\1 \2", s)
            # Ensure arrow is present when there's a $ value without one
            if "→" not in s and "$" in s:
                s = re.sub(r"(\S)\s+\$", r"\1 → $", s)
            return s.strip()

        if pk == "scenario_analysis" and isinstance(node, dict):
            # ── Summary table first ──
            _case_order = [
                ("bull_case", "Bull"),
                ("base_case", "Base"),
                ("bear_case", "Bear"),
            ]
            _has_any = any(node.get(k) for k, _ in _case_order)
            if _has_any:
                out.append("")
                out.append("| Scenario | Probability | Implied Value |")
                out.append("| --- | --- | --- |")
                for case_key, case_label in _case_order:
                    case = node.get(case_key, {})
                    if not case:
                        continue
                    prob = _safe_num(case.get("probability_pct")) or 0
                    implied = _clean_implied_multiple(case.get("implied_multiple", ""))
                    prob_str = f"{prob:.0f}%" if prob else "—"
                    out.append(f"| {case_label} | {prob_str} | {implied} |")
                out.append("")

            # ── Detailed case-by-case narratives ──
            for case_key, case_label in _case_order:
                case = node.get(case_key, {})
                if not case:
                    continue
                narrative = case.get("narrative", "")
                implied = _clean_implied_multiple(case.get("implied_multiple", ""))
                triggers = case.get("key_triggers", [])
                risk = case.get("scenario_risk", "")
                prob = _safe_num(case.get("probability_pct")) or 0
                header = f"**{case_label} Case**"
                if implied:
                    header += f" ({implied})"
                if prob:
                    header += f" — {prob:.0f}% probability"
                out.append(header)
                if narrative:
                    out.extend([narrative, ""])
                if triggers:
                    out.extend([f"**Triggers:** {'; '.join(triggers)}", ""])
                if risk:
                    out.extend([f"**Risk:** {risk}", ""])
            return

        # ── Structured "What Must Be True" ───────────────────────
        if pk == "what_must_be_true" and isinstance(node, list):
            # Structured array of conviction items
            for i, item in enumerate(node):
                if isinstance(item, str):
                    # Backward-compat: string items
                    if item.strip():
                        out.extend([item, ""])
                    continue
                if isinstance(item, dict):
                    cond = item.get("condition", "")
                    rationale = item.get("rationale", "")
                    metric = item.get("monitoring_metric", "")
                    timeframe = item.get("timeframe", "")
                    if cond:
                        parts = [f"**{i + 1}. {cond}**"]
                        if rationale:
                            parts.append(f" — {rationale}")
                        monitor_parts = []
                        if metric:
                            monitor_parts.append(f"Monitor: {metric}")
                        if timeframe:
                            monitor_parts.append(f"Timeframe: {timeframe}")
                        if monitor_parts:
                            parts.append(f" *({'; '.join(monitor_parts)})*")
                        out.extend(["".join(parts), ""])
            return

        # ── Catalyst Calendar ────────────────────────────────────
        if pk == "catalyst_calendar" and isinstance(node, list) and len(node) > 0:
            first = node[0]
            if isinstance(first, dict) and "event" in first:
                out.append("")
                out.append("| Date/Window | Event | Impact | Detail |")
                out.append("| --- | --- | --- | --- |")
                for item in node:
                    if isinstance(item, dict):
                        date_w = str(item.get("date_or_window", "")).replace("|", "/")
                        event = str(item.get("event", "")).replace("|", "/")
                        impact = str(item.get("impact", "")).replace("|", "/")
                        detail = str(item.get("detail", "")).replace("|", "/")
                        out.append(f"| {date_w} | {event} | {impact} | {detail} |")
                out.append("")
                return

        if _is_peer_comp_block(node, pk):
            table_key_map = {
                "profitability_comparison": "profitability_comps",
                "growth_comparison": "growth_comps",
                "leverage_comparison": "leverage_comps",
                "efficiency_comparison": "efficiency_comps",
                "returns_comparison": "returns_comps",
                # "geographic_comparison" — skipped
            }
            # geographic_comparison fully removed from schema — skip if LLM still produces it
            if pk == "geographic_comparison":
                return
            if node.get("intro"):
                out.extend([node["intro"], ""])
            mapped_name = table_key_map.get(pk)
            rendered = False
            if mapped_name:
                r = self._render_peer_bench_table(mapped_name)
                if r:
                    footnote = self.fn.peer_footnote()
                    out.extend([r + footnote, ""])
                    rendered = True
            if not rendered and isinstance(node.get("table"), list) and len(node["table"]) > 0:
                col_keys = list(node["table"][0].keys())
                found = False
                for col_def in COLS.values():
                    col_def_keys = {c["k"] for c in col_def}
                    if all(k in col_def_keys for k in col_keys):
                        r = _render_table(node["table"], col_def)
                        if r:
                            out.extend([r, ""])
                            found = True
                        break
                if not found:
                    auto_cols = []
                    for k in col_keys:
                        f = "text"
                        if any(
                            sub in k
                            for sub in ("pct", "margin", "yield", "growth")
                        ):
                            f = "pct1"
                        elif any(sub in k for sub in ("_to_", "ratio", "coverage")):
                            f = "x1"
                        elif "usd_b" in k:
                            f = "$B_dec1"
                        elif "days" in k:
                            f = "dec1"
                        h = (
                            k.replace("_", " ")
                            .title()
                            .replace("Pct", "%")
                            .replace("Usd B", "($B)")
                        )
                        auto_cols.append({"k": k, "h": h, "f": f})
                    r = _render_table(node["table"], auto_cols)
                    if r:
                        out.extend([r, ""])
            if node.get("analysis"):
                out.extend([node["analysis"], ""])
            return

        # Array handling
        if isinstance(node, list):
            if len(node) > 0 and isinstance(node[0], dict):
                first = node[0]
                cols = COLS.get(pk)
                if cols:
                    precomp = self.precomputed_tables.get(pk)
                    data = precomp if precomp and len(precomp) > 0 else node
                    out.extend([
                        "",
                        _render_table(data, cols) + self.fn.table_footnote(pk, cols),
                        "",
                    ])
                    return
                if _is_risk_block(first):
                    for i, item in enumerate(node):
                        if isinstance(item, str):
                            if item.strip():
                                out.extend([item, ""])
                            continue
                        if i > 0:
                            out.extend(["", ""])
                        _trans = item.get("transmission", "")
                        _pam = item.get("probability_and_monitoring", "")
                        # Merge probability_and_monitoring into a single block
                        _body = f"{_trans} {_pam}".strip() if _pam else _trans
                        out.extend([
                            f"**{item.get('label', '')}** \u2014 {_body}",
                            "",
                        ])
                    return
                if _is_moat_block(first):
                    for item in node:
                        if isinstance(item, str):
                            if item.strip():
                                out.extend([item, ""])
                            continue
                        out.extend([
                            f"**{item.get('label', '')}** \u2014 {item.get('evidence', '')}",
                            "",
                            item.get("mechanism", ""),
                            "",
                        ])
                    return
                if _is_labeled_block(first):
                    for item in node:
                        if isinstance(item, str):
                            if item.strip():
                                out.extend([item, ""])
                            continue
                        para = (item.get("paragraph") or "").strip()
                        # Skip items with empty/whitespace paragraphs or
                        # boilerplate "does not disclose" text.
                        if not para or _EMPTY_RE.search(para):
                            continue
                        out.extend([
                            f"**{item.get('label', '')}** \u2014 {para}",
                            "",
                        ])
                    return
                for item in node:
                    self._walk(item, pk, out)
            return

        # Object handling
        if isinstance(node, dict):
            if _is_table_wrapper(node):
                if node.get("intro"):
                    out.extend([node["intro"], ""])
                # Try direct COLS match, then mapped sector table key
                _tbl_key = pk
                if pk not in COLS:
                    _tbl_key = _NARRATIVE_TO_SECTOR_TABLE.get(pk, pk)
                cols = COLS.get(_tbl_key)
                if cols and _tbl_key not in self._rendered_sector_tables:
                    precomp = self.precomputed_tables.get(_tbl_key)
                    data = (
                        precomp
                        if precomp and len(precomp) > 0
                        else (node.get("table") if isinstance(node.get("table"), list) else [])
                    )
                    if data:
                        out.extend([
                            _render_table(data, cols)
                            + self.fn.table_footnote(_tbl_key, cols),
                            "",
                        ])
                        self._rendered_sector_tables.add(_tbl_key)
                if node.get("analysis") and isinstance(node["analysis"], str):
                    out.extend([node["analysis"], ""])
                return

            if _is_risk_block(node):
                _trans = node.get("transmission", "")
                _pam = node.get("probability_and_monitoring", "")
                _body = f"{_trans} {_pam}".strip() if _pam else _trans
                out.extend([
                    f"**{node.get('label', '')}** \u2014 {_body}",
                    "",
                ])
                return
            if _is_moat_block(node):
                out.extend([
                    f"**{node.get('label', '')}** \u2014 {node.get('evidence', '')}",
                    "",
                    node.get("mechanism", ""),
                    "",
                ])
                return
            if _is_labeled_block(node):
                para = (node.get("paragraph") or "").strip()
                if para and not _EMPTY_RE.search(para):
                    out.extend([
                        f"**{node.get('label', '')}** \u2014 {para}",
                        "",
                    ])
                return
            if (
                "classification" in node
                and "paragraph" in node
                and isinstance(node.get("paragraph"), str)
            ):
                out.extend([
                    f"**{node.get('classification', '')}** \u2014 {node['paragraph']}",
                    "",
                ])
                return

            # Check for nested bold-label children (guidance_accuracy, etc.)
            child_keys = node.keys()
            if any(ck in NESTED_BOLD_LABELS for ck in child_keys):
                for ck, cv in node.items():
                    if cv is None:
                        continue
                    bl = NESTED_BOLD_LABELS.get(ck)
                    if bl and isinstance(cv, str) and len(cv) > 20:
                        out.extend([f"**{bl}** \u2014 {cv}", ""])
                    elif isinstance(cv, str) and len(cv) > 20:
                        out.extend([cv, ""])
                    elif isinstance(cv, dict):
                        r = self.render_section({ck: cv})
                        if r.strip():
                            out.append(r)
                return

            for key, val in node.items():
                if key in _SKIP_KEYS or val is None:
                    continue
                if isinstance(val, str) and len(val) > 20:
                    # Strip stray leading colon/semicolon from LLM output
                    val = re.sub(r"^\s*[:;]\s*", "", val).strip()
                    out.extend([val, ""])
                    continue
                if isinstance(val, (dict, list)):
                    self._walk(val, key, out)
                    continue

    # ─── Section with subsection headers ─────────────────────────────

    def render_section_with_headers(self, obj: Any, section_num: int) -> str:
        """Render section with ``## Subsection`` headers per SECTION_FORMATS."""
        if not obj or not isinstance(obj, dict):
            return ""
        fmt_def = SECTION_FORMATS.get(section_num)
        if not fmt_def:
            return self.render_section(obj)

        use_bold_lead_in = fmt_def.get("style") == "bold-lead-in"
        out: list[str] = []
        skip = _SKIP_KEYS | {"company_description"}

        for key, val in obj.items():
            if key in skip or val is None:
                continue
            # Skip empty-content strings
            if (
                isinstance(val, str)
                and _EMPTY_CONTENT_RE.search(val)
                and len(val) < 120
            ):
                continue
            # Skip empty intro+analysis wrappers
            if isinstance(val, dict) and not isinstance(val, list) and "intro" in val:
                intro_empty = not val.get("intro") or (
                    isinstance(val["intro"], str)
                    and (len(val["intro"]) < 20 or _EMPTY_CONTENT_RE.search(val["intro"]))
                )
                analysis_empty = not val.get("analysis") or (
                    isinstance(val["analysis"], str)
                    and (
                        len(val["analysis"]) < 20
                        or _EMPTY_CONTENT_RE.search(val["analysis"])
                    )
                )
                if intro_empty and analysis_empty:
                    # Still render if a precomputed table exists (sector-specific
                    # or generic) — the table should appear even without prose.
                    _mapped_tbl = _NARRATIVE_TO_SECTOR_TABLE.get(key)
                    _has_table = (
                        (_mapped_tbl and _mapped_tbl in self.precomputed_tables)
                        or key in self.precomputed_tables
                        or key in COLS
                    )
                    if not _has_table:
                        continue

            if key == "mandatory_final_sentence" and isinstance(val, str):
                out.extend(["", val, ""])
                continue

            subsection_name = KEY_TO_SUBSECTION.get(key)
            if key == "overall_assessment" and section_num == 8:
                subsection_name = "Overall Assessment"
            if key == "company_description" or (
                key == "opening_paragraph" and section_num == 11
            ):
                subsection_name = None

            # ── Subsection heading (deferred until content confirmed) ──
            pending_heading = None
            if (
                subsection_name
                and subsection_name in fmt_def.get("subsections", [])
            ):
                if use_bold_lead_in:
                    if isinstance(val, str) and len(val) > 20:
                        out.extend([f"**{subsection_name}** \u2014 {val}", ""])
                        continue
                    pending_heading = f"\n**{subsection_name}**\n"
                else:
                    pending_heading = f"\n## {subsection_name}\n"

            if isinstance(val, str) and len(val) > 20:
                if pending_heading:
                    out.append(pending_heading)
                out.extend([val, ""])
                continue

            if isinstance(val, (dict, list)):
                # Check for nested bold-label children
                if isinstance(val, dict):
                    child_keys = val.keys()
                    if any(ck in NESTED_BOLD_LABELS for ck in child_keys):
                        child_lines: list[str] = []
                        for ck, cv in val.items():
                            if cv is None:
                                continue
                            bl = NESTED_BOLD_LABELS.get(ck)
                            if bl and isinstance(cv, str) and len(cv) > 20:
                                child_lines.extend([f"**{bl}** \u2014 {cv}", ""])
                            elif isinstance(cv, str) and len(cv) > 20:
                                child_lines.extend([cv, ""])
                            elif isinstance(cv, dict):
                                r = self.render_section({ck: cv})
                                if r.strip():
                                    child_lines.append(r)
                        if child_lines:
                            if pending_heading:
                                out.append(pending_heading)
                            out.extend(child_lines)
                        continue
                rendered = self.render_section({key: val})
                if rendered.strip():
                    if pending_heading:
                        out.append(pending_heading)
                    out.append(rendered)
                continue

        return "\n".join(x for x in out if x is not None)


# ═══════════════════════════════════════════════════════════════════════
# PRE-COMPUTED FINANCIAL TABLES
# ═══════════════════════════════════════════════════════════════════════


def _drop_empty_rows(rows: list[dict]) -> list[dict]:
    return [
        row
        for row in rows
        if any(v is not None for k, v in row.items() if k != "year")
    ]


def _strip_fy(year_key: str) -> str:
    return re.sub(r"\s*FY\s*$", "", str(year_key))


def _build_precomputed_tables(  # noqa: C901
    inc_stmt: dict,
    cf_stmt: dict,
    rev_splits: dict,
    cap_alloc: dict,
    returns: dict,
    bal_sheet: dict,
    work_cap: dict,
    comp_landscape: Any,
    latest_year: str,
) -> dict:
    """Build pre-computed financial tables from fact sheet raw data."""
    tables: dict[str, list[dict]] = {}

    # ── Segment revenue ─────────────────────────────────────────────
    seg_pct = rev_splits.get("segment_revenue_pct_of_total") or {}
    seg_rev = rev_splits.get("segment_revenue_usd_m") or {}
    seg_growth = rev_splits.get("segment_yoy_growth_pct") or {}
    seg_years = sorted(seg_pct.keys())
    seg_ly = seg_years[-1] if seg_years else ""
    if seg_ly and seg_pct.get(seg_ly):
        rows = sorted(
            [
                {
                    "segment": segment,
                    "revenue_m": _safe_get(seg_rev, seg_ly, segment),
                    "pct_of_total": (pct * 100 if pct is not None else None),
                    "yoy_growth": _safe_get(seg_growth, seg_ly, segment),
                }
                for segment, pct in seg_pct[seg_ly].items()
            ],
            key=lambda r: r.get("pct_of_total") or 0,
            reverse=True,
        )
        if rows:
            tables["revenue_by_segment"] = rows

    # ── Geographic split — SKIPPED (SEC XBRL geographic axes too inconsistent)

    # ── Capital allocation ───────────────────────────────────────────
    rd = cap_alloc.get("rd_usd_m") or {}
    capex = cap_alloc.get("capex_usd_m") or {}
    ma = cap_alloc.get("acquisitions_net_usd_m") or {}
    divs = cap_alloc.get("dividends_paid_usd_m") or {}
    bb = cap_alloc.get("buybacks_usd_m") or {}
    # Use the broadest series as year driver (insurance/banks have no R&D)
    _ca_year_source = rd or capex or ma or divs or bb
    ca_years = sorted(_ca_year_source.keys())
    if ca_years:
        rows = [
            {
                "year": _strip_fy(yr),
                "rd_m": rd.get(yr),
                "capex_m": capex.get(yr),
                "ma_m": ma.get(yr),
                "dividends_m": divs.get(yr),
                "buybacks_m": bb.get(yr),
            }
            for yr in ca_years
            if any(
                d.get(yr) is not None for d in [rd, capex, ma, divs, bb]
            )
        ]
        if rows:
            tables["capital_allocation"] = rows

    # ── Revenue growth ───────────────────────────────────────────────
    rev = inc_stmt.get("revenue_usd_m") or {}
    growth = inc_stmt.get("revenue_growth_pct") or {}
    rev_years = sorted(k for k in rev if rev[k] is not None)
    rows = _drop_empty_rows([
        {
            "year": _strip_fy(yr),
            "revenue_m": rev.get(yr),
            "yoy_growth": growth.get(yr),
        }
        for yr in rev_years
    ])
    if rows:
        tables["revenue_growth"] = rows

    # ── Margins ──────────────────────────────────────────────────────
    gm = inc_stmt.get("gross_margin_pct") or {}
    om = inc_stmt.get("operating_margin_pct") or {}
    nm = inc_stmt.get("net_margin_pct") or {}
    eps = inc_stmt.get("eps_diluted") or {}
    # Use the broadest available margin series as year driver
    # (insurance/banks have no gross_margin, so fall back to operating or net)
    _margin_year_source = gm or om or nm or eps
    margin_years = sorted(k for k in _margin_year_source if _margin_year_source[k] is not None)
    rows = _drop_empty_rows([
        {
            "year": _strip_fy(yr),
            "gross_margin": gm.get(yr),
            "operating_margin": om.get(yr),
            "net_margin": nm.get(yr),
            "eps_diluted": eps.get(yr),
        }
        for yr in margin_years
    ])
    if rows:
        tables["margins"] = rows

    # ── Cash flow ────────────────────────────────────────────────────
    ocf = cf_stmt.get("operating_cash_flow_usd_m") or {}
    cap_pct = cf_stmt.get("capex_pct_of_revenue") or {}
    fcf = cf_stmt.get("free_cash_flow_usd_m") or {}
    fcf_m = cf_stmt.get("fcf_margin_pct") or {}
    ocf_years = sorted(k for k in ocf if ocf[k] is not None)
    rows = _drop_empty_rows([
        {
            "year": _strip_fy(yr),
            "ocf_m": ocf.get(yr),
            "capex_pct_rev": cap_pct.get(yr),
            "fcf_m": fcf.get(yr),
            "fcf_margin": fcf_m.get(yr),
        }
        for yr in ocf_years
    ])
    if rows:
        tables["cash_flow"] = rows

    # ── Returns & capital efficiency ─────────────────────────────────
    roic = returns.get("roic_pct") or {}
    roe = returns.get("roe_pct") or {}  # Fixed: was mirroring JS bug (reading roic_pct)
    om_vals = inc_stmt.get("operating_margin_pct") or {}
    # Use the broadest available return series as year driver
    _ret_year_source = roic or roe or om_vals
    roic_years = sorted(k for k in _ret_year_source if _ret_year_source[k] is not None)
    rows = _drop_empty_rows([
        {
            "year": _strip_fy(yr),
            "roe": roe.get(yr),
            "roic": roic.get(yr),
            "operating_margin": om_vals.get(yr),
        }
        for yr in roic_years
    ])
    if rows:
        tables["returns_capital_efficiency"] = rows

    # ── Leverage & balance sheet ─────────────────────────────────────
    nd_ebitda = bal_sheet.get("net_debt_to_ebitda") or {}
    int_cov = bal_sheet.get("interest_coverage_ratio") or {}
    dso = work_cap.get("dso_days") or {}
    nd_years = sorted(k for k in nd_ebitda if nd_ebitda[k] is not None)
    rows = _drop_empty_rows([
        {
            "year": _strip_fy(yr),
            "net_debt_ebitda": nd_ebitda.get(yr),
            "interest_coverage": int_cov.get(yr),
            "dso_days": dso.get(yr),
        }
        for yr in nd_years
    ])
    if rows:
        tables["leverage_balance_sheet"] = rows

    # ── Competitive landscape ────────────────────────────────────────
    if isinstance(comp_landscape, list) and len(comp_landscape) > 0:
        tables["competitive_landscape"] = comp_landscape

    return tables


# ═══════════════════════════════════════════════════════════════════════
# PRECOMPUTED PEER TABLE
# ═══════════════════════════════════════════════════════════════════════


def _median(arr: list) -> Optional[float]:
    v = sorted(x for x in arr if x is not None and not (isinstance(x, float) and math.isnan(x)))
    if not v:
        return None
    mid = len(v) // 2
    raw = v[mid] if len(v) % 2 else (v[mid - 1] + v[mid]) / 2
    return round(raw, 2)


def _build_precomputed_peer_table(  # noqa: C901
    peers_obj: dict,
    peer_bench: dict,
    val_data: dict,
    ticker: str,
    company_name: str,
    latest_year: str,
    is_financial: bool = False,
) -> Optional[dict]:
    """Build the precomputed peer table from fact sheet data.

    For financial sectors (banks, insurance), uses P/E, P/B, P/TBV, Div Yield, ROE
    instead of EV/Revenue, EV/EBITDA, P/FCF.
    """

    peer_latest = peers_obj.get("latest") or []
    if not peer_latest:
        peer_latest = _safe_get(peer_bench, "peers_full", "latest") or []

    # Exclude subject from peer list to avoid duplication
    ticker_up = (ticker or "").upper()
    peer_latest_filtered = [
        p for p in peer_latest
        if (p.get("symbol") or "").upper() != ticker_up
    ]

    if is_financial:
        # Financial sector: P/E, P/B, P/TBV, Div Yield, ROE
        peer_entries = [
            {
                "company_name": p.get("symbol"),
                "p_e": p.get("price_to_earnings"),
                "p_b": p.get("price_to_book"),
                "p_tbv": p.get("price_to_tangible_book"),
                "div_yield": p.get("dividend_yield_pct"),
                "roe": p.get("roe_pct"),
            }
            for p in peer_latest_filtered
        ]
    else:
        peer_entries = [
            {
                "company_name": p.get("symbol"),
                "ev_revenue": p.get("ev_to_sales"),
                "ev_ebitda": p.get("ev_to_ebitda"),
                "p_fcf": p.get("price_to_fcf"),
                "p_e": p.get("price_to_earnings"),
                "p_b": p.get("price_to_book"),
            }
            for p in peer_latest_filtered
        ]

    if not peer_entries and isinstance(_safe_get(val_data, "peers", "by_symbol"), dict):
        by_symbol = val_data["peers"]["by_symbol"]
        for sym, years_data in by_symbol.items():
            yrs = sorted(years_data.keys())
            if not yrs:
                continue
            p = years_data[yrs[-1]]
            if is_financial:
                peer_entries.append({
                    "company_name": sym,
                    "p_e": p.get("price_to_earnings"),
                    "p_b": p.get("price_to_book"),
                    "p_tbv": p.get("price_to_tangible_book"),
                    "div_yield": p.get("dividend_yield_pct"),
                    "roe": p.get("roe_pct"),
                })
            else:
                peer_entries.append({
                    "company_name": sym,
                    "ev_revenue": p.get("ev_to_sales"),
                    "ev_ebitda": p.get("ev_to_ebitda"),
                    "p_fcf": p.get("price_to_fcf"),
                    "p_e": p.get("price_to_earnings"),
                    "p_b": p.get("price_to_book"),
                })

    # NM-cap extreme outlier multiples in peer entries and subject multiples
    _NM_PEER_CAPS = {
        "p_e": (0, 150), "p_fcf": (0, 150), "p_b": (0, 50), "p_tbv": (0, 50),
        "ev_revenue": (0, 50), "ev_ebitda": (0, 100), "roe": (-200, 200),
        "div_yield": (-50, 100),
    }

    def _nm_cap_val(val: Any, field: str) -> Any:
        """Return None if value exceeds NM thresholds."""
        if val is None or field not in _NM_PEER_CAPS:
            return val
        lo, hi = _NM_PEER_CAPS[field]
        try:
            n = float(val)
        except (TypeError, ValueError):
            return val
        return None if n < lo or n > hi else val

    for entry in peer_entries:
        for fld in list(entry.keys()):
            if fld != "company_name":
                entry[fld] = _nm_cap_val(entry[fld], fld)

    def _get_latest_val(obj: Any) -> Any:
        if not isinstance(obj, dict):
            return None
        if obj.get(latest_year) is not None:
            return obj[latest_year]
        stripped = re.sub(r"\s*FY\s*$", "", latest_year)
        if obj.get(stripped) is not None:
            return obj[stripped]
        keys = sorted(k for k in obj if obj[k] is not None)
        return obj[keys[-1]] if keys else None

    # Subject multiples — use subject data from peer fetch if available
    subj_from_peers = next(
        (p for p in peer_latest if (p.get("symbol") or "").upper() == ticker_up),
        {},
    )

    if is_financial:
        subj_multiples = {
            "company_name": ticker or company_name,
            "p_e": _nm_cap_val(subj_from_peers.get("price_to_earnings") or _get_latest_val(val_data.get("price_to_earnings")), "p_e"),
            "p_b": _nm_cap_val(subj_from_peers.get("price_to_book") or _get_latest_val(val_data.get("price_to_book")), "p_b"),
            "p_tbv": _nm_cap_val(subj_from_peers.get("price_to_tangible_book"), "p_tbv"),
            "div_yield": _nm_cap_val(subj_from_peers.get("dividend_yield_pct"), "div_yield"),
            "roe": _nm_cap_val(subj_from_peers.get("roe_pct"), "roe"),
        }
    else:
        subj_multiples = {
            "company_name": ticker or company_name,
            "ev_revenue": _nm_cap_val(subj_from_peers.get("ev_to_sales") or _get_latest_val(val_data.get("ev_to_sales")), "ev_revenue"),
            "ev_ebitda": _nm_cap_val(subj_from_peers.get("ev_to_ebitda") or _get_latest_val(val_data.get("ev_to_ebitda")), "ev_ebitda"),
            "p_fcf": _nm_cap_val(subj_from_peers.get("price_to_fcf") or _get_latest_val(val_data.get("price_to_fcf")), "p_fcf"),
            "p_e": _nm_cap_val(subj_from_peers.get("price_to_earnings") or _get_latest_val(val_data.get("price_to_earnings")), "p_e"),
            "p_b": _nm_cap_val(subj_from_peers.get("price_to_book") or _get_latest_val(val_data.get("price_to_book")), "p_b"),
        }

    # Recompute medians from NM-capped peer entries (never from raw pre-computed medians
    # which may include outliers that were since capped)
    medians: dict
    if is_financial:
        if peer_entries:
            medians = {
                "p_e": _median([p.get("p_e") for p in peer_entries]),
                "p_b": _median([p.get("p_b") for p in peer_entries]),
                "p_tbv": _median([p.get("p_tbv") for p in peer_entries]),
                "div_yield": _median([p.get("div_yield") for p in peer_entries]),
                "roe": _median([p.get("roe") for p in peer_entries]),
            }
        else:
            medians = {"p_e": None, "p_b": None, "p_tbv": None, "div_yield": None, "roe": None}
    else:
        if peer_entries:
            medians = {
                "ev_revenue": _median([p.get("ev_revenue") for p in peer_entries]),
                "ev_ebitda": _median([p.get("ev_ebitda") for p in peer_entries]),
                "p_fcf": _median([p.get("p_fcf") for p in peer_entries]),
                "p_e": _median([p.get("p_e") for p in peer_entries]),
                "p_b": _median([p.get("p_b") for p in peer_entries]),
            }
        else:
            medians = {
                "ev_revenue": None, "ev_ebitda": None,
                "p_fcf": None, "p_e": None, "p_b": None,
            }

    has_any = peer_entries or subj_multiples.get("p_e") is not None or subj_multiples.get("ev_revenue") is not None
    if has_any:
        return {
            "subject_company": subj_multiples,
            "peers": peer_entries,
            "peer_median": medians,
            "is_financial": is_financial,
        }
    return None


# ═══════════════════════════════════════════════════════════════════════
# POST-PROCESSING
# ═══════════════════════════════════════════════════════════════════════


def _post_process(text: str, section_num: int) -> str:
    """Apply formatting cleanups to rendered section text."""
    # Strip null bytes (LLM encoding glitch for apostrophes)
    text = text.replace("\x00", "'")
    # Strip score tags
    text = re.sub(
        r"^(MOAT|GROWTH|QUALITY|FAIR_VALUE):\s*\S+\s*$", "", text, flags=re.MULTILINE
    ).strip()
    text = re.sub(
        r"\s*(?:Quality|Growth|Moat)\s*[Ss]core:?\s*\d+\.?\d*\.?\s*",
        " ",
        text,
        flags=re.IGNORECASE,
    ).strip()

    # Normalize large dollar amounts (millions)
    def _norm_millions(m: re.Match) -> str:
        n_str = m.group(1).replace(",", "")
        v = _safe_num(n_str)
        if v is None:
            return m.group(0)
        if v >= 1e6:
            return f"${v / 1e6:.2f}T"
        if v >= 1000:
            return f"${v / 1000:.2f}B"
        return f"${round(v)}M"

    text = re.sub(
        r"\$([0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?)\s*(?:M|million)",
        _norm_millions,
        text,
        flags=re.IGNORECASE,
    )

    def _norm_bare_millions(m: re.Match) -> str:
        v = _safe_num(m.group(1))
        if v is None:
            return m.group(0)
        if v >= 1e6:
            return f"${v / 1e6:.2f}T"
        if v >= 1000:
            return f"${v / 1000:.2f}B"
        return m.group(0)

    text = re.sub(r"\$(\d{4,})(?:\.\d+)?\s*M\b", _norm_bare_millions, text)

    # Normalize large dollar amounts (billions)
    def _norm_billions(m: re.Match) -> str:
        n_str = m.group(1).replace(",", "")
        v = _safe_num(n_str)
        if v is None:
            return m.group(0)
        if v >= 1000:
            return f"${v / 1000:.2f}T"
        return f"${v:.2f}B"

    text = re.sub(
        r"\$([0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?)\s*(?:B|billion)",
        _norm_billions,
        text,
        flags=re.IGNORECASE,
    )

    def _norm_written_millions(m: re.Match) -> str:
        n_str = m.group(1).replace(",", "")
        v = _safe_num(n_str)
        if v is None:
            return m.group(0)
        if v >= 1000:
            return f"${v / 1000:.2f}B"
        return f"${round(v)}M"

    text = re.sub(
        r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?)\s*million",
        _norm_written_millions,
        text,
        flags=re.IGNORECASE,
    )

    # Catch bare unformatted large floats the LLM pastes from fact sheets
    # e.g. "R&D Expense: 34550000000.0" → "R&D Expense: $34.55B"
    def _norm_bare_raw(m: re.Match) -> str:
        prefix = m.group(1)  # "$" or ""
        n_str = m.group(2)
        v = _safe_num(n_str)
        if v is None:
            return m.group(0)
        sign = "-" if v < 0 else ""
        a = abs(v)
        if a >= 1e12:
            formatted = f"{sign}${a / 1e12:.2f}T"
        elif a >= 1e9:
            formatted = f"{sign}${a / 1e9:.2f}B"
        elif a >= 1e6:
            formatted = f"{sign}${a / 1e6:.2f}M"
        else:
            return m.group(0)
        # Strip trailing .00
        formatted = formatted.replace(".00T", "T").replace(".00B", "B").replace(".00M", "M")
        return formatted

    text = re.sub(
        r"(\$?)(-?\d{7,}(?:\.\d+)?)\b",
        _norm_bare_raw,
        text,
    )

    # Also catch comma-formatted bare dollar amounts without M/B suffix
    # e.g. "$417,061,000,000" → "$417.06B"
    def _norm_comma_raw(m: re.Match) -> str:
        n_str = m.group(1).replace(",", "")
        v = _safe_num(n_str)
        if v is None:
            return m.group(0)
        sign = "-" if v < 0 else ""
        a = abs(v)
        if a >= 1e12:
            formatted = f"{sign}${a / 1e12:.2f}T"
        elif a >= 1e9:
            formatted = f"{sign}${a / 1e9:.2f}B"
        elif a >= 1e6:
            formatted = f"{sign}${a / 1e6:.2f}M"
        else:
            return m.group(0)
        formatted = formatted.replace(".00T", "T").replace(".00B", "B").replace(".00M", "M")
        return formatted

    text = re.sub(
        r"\$(\d{1,3}(?:,\d{3}){2,})(?:\.\d+)?\b",
        _norm_comma_raw,
        text,
    )

    # Strip leaked field names
    text = re.sub(
        r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+){2,})\s+(?:of|is|at|was|=)\s+",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\(([a-z][a-z0-9]*(?:_[a-z0-9]+){2,})\s+(?:of|is|at)\s+([^)]+)\)",
        r"(\2)",
        text,
        flags=re.IGNORECASE,
    )

    # Remove empty subsection headers
    text = re.sub(
        r"\n## [^\n]+\n+(?:The company does not (?:publicly )?disclose[^\n]*\.?\s*\n*)+",
        "\n",
        text,
        flags=re.IGNORECASE,
    )

    # Fix negative dollar formatting
    text = re.sub(r'"-?\$([0-9,.]+[MBTKM]?)"', r"$\1", text)
    text = re.sub(r"(^|\s)-\$([0-9,.]+[MBTKM]?)", r"\1$\2", text)

    # S13: italicise final paragraph
    if section_num == 13:
        lines = text.rstrip().split("\n")
        for i in range(len(lines) - 1, -1, -1):
            line = lines[i].strip()
            if not line or line.startswith("|") or re.match(r"^#{1,3}\s", line) or line.startswith("---"):
                continue
            lines[i] = f"*{lines[i].strip()}*"
            break
        text = "\n".join(lines)

    # Strip leaked raw JSON artifacts: { "raw_text": "..." }, braces, quoted keys
    text = re.sub(r'^\s*\{\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\}\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*"raw_text"\s*:\s*".*"?\s*,?\s*$', '', text, flags=re.MULTILINE)
    # Strip raw JSON key-value lines: "some_key": "value",
    text = re.sub(r'^\s*"[a-z_]+":\s*(?:"[^"]*"|\d+|null|true|false)\s*,?\s*$', '', text, flags=re.MULTILINE)
    # Strip lines that look like JSON object starts: "some_key": {
    text = re.sub(r'^\s*"[a-z_]+":\s*\{\s*$', '', text, flags=re.MULTILINE)
    # Strip LLM meltdown / apology loops
    text = re.sub(
        r"(?:(?:Sorry|Apologies|Done|End|Stop|Let'?s? (?:go|produce|craft|finalize|output)|"
        r"Now (?:final|produce|output|real)|Thank you|Goodbye|Finished|The end|"
        r"I'?(?:ll|m)|Okay|Outputting|Vielen|END|STOP)[.\s]*){5,}",
        "",
        text,
        flags=re.IGNORECASE,
    )
    # Strip "# Explanation" or "## Explanation" LLM commentary headers
    text = re.sub(r"^\s*#+ Explanation\b.*$", "", text, flags=re.MULTILINE)

    # Collapse excess blank lines
    text = re.sub(r"\n{4,}", "\n\n\n", text)

    return text


# ═══════════════════════════════════════════════════════════════════════
# SCORE EXTRACTION
# ═══════════════════════════════════════════════════════════════════════


def extract_scores(structured_map: dict, section_map: Optional[dict] = None) -> dict:
    """Extract moat, growth, quality scores from structured_map.

    Falls back to regex extraction from ``section_map`` prose if structured
    fields are missing.
    """
    section_map = section_map or {}

    # ── Moat ─────────────────────────────────────────────────────────
    s5 = structured_map.get("section_5") or {}
    moat_score: Any = None

    if s5.get("moat_score") is not None:
        moat_score = s5["moat_score"]
    else:
        cls = _safe_get(s5, "overall_assessment", "classification")
        if cls and cls.upper() in MOAT_CLASS_MAP:
            moat_score = MOAT_CLASS_MAP[cls.upper()]
        else:
            # Regex fallback from prose
            m = re.search(r"MOAT:\s*(\S+)", section_map.get("section_5", ""))
            if m:
                n = _safe_num(m.group(1))
                if n is not None:
                    moat_score = n
                elif m.group(1).upper() in MOAT_CLASS_MAP:
                    moat_score = MOAT_CLASS_MAP[m.group(1).upper()]
                else:
                    moat_score = m.group(1)

    # ── Growth ───────────────────────────────────────────────────────
    s9 = structured_map.get("section_9") or {}
    growth_score: Any = None
    if s9.get("growth_score") is not None:
        growth_score = _to_num(s9["growth_score"])
    else:
        m = re.search(r"GROWTH:\s*(\S+)", section_map.get("section_9", ""))
        if m:
            growth_score = _to_num(m.group(1))

    # ── Quality ──────────────────────────────────────────────────────
    s10 = structured_map.get("section_10") or {}
    quality_score: Any = None
    if s10.get("quality_score") is not None:
        quality_score = _to_num(s10["quality_score"])
    else:
        m = re.search(r"QUALITY:\s*(\S+)", section_map.get("section_10", ""))
        if m:
            quality_score = _to_num(m.group(1))

    return {
        "moat": moat_score,
        "growth": growth_score,
        "quality": quality_score,
    }


# ═══════════════════════════════════════════════════════════════════════
# DATA BLOCK BUILDER
# ═══════════════════════════════════════════════════════════════════════


def _norm100(v):
    """Normalize score to 0-100 scale (LLM may output 0-1, 1-10, or 0-100)."""
    if v is None:
        return None
    n = float(v)
    if 0 < n < 1:
        return round(n * 100, 1)  # 0-1 scale → 0-100
    if 1 <= n <= 10:
        return round(n * 10, 1)   # 1-10 scale → 10-100
    return round(n, 1)            # already 0-100


def build_data_block(
    scores: dict,
    fair_value_info: dict,
    section_outputs: dict,
    *,
    dcf_anchors: Optional[dict] = None,
    is_financial_sector: bool = False,
    is_asset_heavy_sector: bool = False,
    normalized_industry: str = "",
) -> dict:
    """Build the data block consumed by Discord scorecard and PDF generation.

    *fair_value_info* should contain keys ``fair_value``, ``fair_value_method``.
    """
    structured_map = section_outputs.get("structured_map") or {}
    s1_struct = structured_map.get("section_1") or {}
    s5_struct = structured_map.get("section_5") or {}

    moat_classification = (
        _safe_get(s5_struct, "overall_assessment", "classification")
        or (
            scores.get("moat")
            if isinstance(scores.get("moat"), str) and _safe_num(scores.get("moat")) is None
            else None
        )
    )

    fair_value_method = fair_value_info.get("fair_value_method", "dcf")

    fair_value_note = None
    if fair_value_method == "bank_equity":
        fair_value_note = "Bank equity model (justified P/B \u00d7 BVPS)"
    elif fair_value_method == "ddm":
        fair_value_note = "Dividend Discount Model (DDM)"
    elif fair_value_method == "pffo_implied":
        fair_value_note = "Implied from FFO/share \u00d7 peer median P/FFO"
    elif is_financial_sector:
        if fair_value_method == "pe_implied":
            fair_value_note = "Implied from peer median P/E"
        elif fair_value_method == "pb_implied":
            fair_value_note = "Implied from peer median P/B"
        else:
            fair_value_note = "Not available \u2014 insufficient EPS/book value or peer data"
    elif is_asset_heavy_sector:
        primary = (dcf_anchors or {}).get("primary_multiple", "EV/EBITDA")
        fair_value_note = (
            f"Peer {primary} framework \u2014 "
            "no single price target (cyclical asset owner)"
        )

    return {
        "company": section_outputs.get("company_name", ""),
        "ticker": section_outputs.get("ticker", ""),
        "description": s1_struct.get("company_description", ""),
        "moat": moat_classification,
        "moat_score": _to_num(scores.get("moat")),
        "growth": _norm100(_to_num(scores.get("growth"))),
        "quality": _norm100(_to_num(scores.get("quality"))),
        "fair_value": fair_value_info.get("fair_value"),
        "fair_value_method": fair_value_method,
        "fair_value_note": fair_value_note,
        "primary_multiple": (dcf_anchors or {}).get("primary_multiple"),
        "secondary_multiple": (dcf_anchors or {}).get("secondary_multiple"),
    }


# ═══════════════════════════════════════════════════════════════════════
# DATA SUMMARY HEADER — rendered at top of memo
# ═══════════════════════════════════════════════════════════════════════


def _render_data_summary(
    data_block: dict,
    val_data: dict,
    inc_stmt: dict,
    cf_stmt: dict,
    margins: dict,
    returns: dict,
    fs_identity: dict,
    peer_bench: dict,
    ly: str,
) -> str:
    """Render a compact data summary block for the top of the memo.

    Gives readers all key metrics at a glance before diving into the
    full analysis.
    """

    def _v(d: dict, key: str, year: str = "") -> Any:
        """Get value — optionally from year-keyed sub-dict."""
        sub = d.get(key)
        if isinstance(sub, dict) and year:
            return sub.get(year)
        return sub

    def _f_pct(v: Any) -> str:
        n = _safe_num(v)
        return f"{n:.1f}%" if n is not None else "\u2014"

    def _f_mult(v: Any) -> str:
        n = _safe_num(v)
        return f"{n:.1f}x" if n is not None else "\u2014"

    def _f_dollar(v: Any) -> str:
        n = _safe_num(v)
        if n is None:
            return "\u2014"
        return f"${n:,.2f}"

    def _f_dollar_b(v: Any) -> str:
        n = _safe_num(v)
        if n is None:
            return "\u2014"
        if abs(n) >= 1000:
            return f"${n / 1000:.2f}T"
        return f"${n:.1f}B"

    def _f_rev(v: Any) -> str:
        """Format revenue in M → show as $X.XB or $X,XXXM."""
        n = _safe_num(v)
        if n is None:
            return "\u2014"
        if abs(n) >= 1000:
            return f"${n / 1000:.1f}B"
        return f"${n:,.0f}M"

    # ── Identity ──
    ticker = data_block.get("ticker", "")
    company = data_block.get("company", "")
    sector = fs_identity.get("sector", "")
    industry = fs_identity.get("industry", "")
    exchange = fs_identity.get("exchange", "")

    # ── Scores ──
    moat_class = data_block.get("moat") or ""
    moat_score = data_block.get("moat_score")
    growth_score = data_block.get("growth")
    quality_score = data_block.get("quality")

    moat_str = f"{moat_class.upper()} ({_safe_num(moat_score):.0f})" if moat_class and _safe_num(moat_score) is not None else (moat_class.upper() if moat_class else (_fmt(moat_score, "dec1") if moat_score else "\u2014"))

    # ── Pricing ──
    price = _safe_num(
        val_data.get("_current_price")
        or fs_identity.get("price")
    )
    mkt_cap = _safe_num(
        val_data.get("_current_market_cap_b")
        or _v(val_data, "market_cap_usd_b", ly)
    )
    fair_value = _safe_num(data_block.get("fair_value"))
    fv_method = data_block.get("fair_value_method", "")
    fv_note = data_block.get("fair_value_note", "")

    upside = None
    if fair_value and price and price > 0:
        upside = ((fair_value / price) - 1) * 100

    # ── Multiples (prefer _current_* keys, fall back to year-keyed) ──
    pe = _safe_num(val_data.get("_current_pe") or _v(val_data, "price_to_earnings", ly))
    ev_ebitda = _safe_num(val_data.get("_current_ev_ebitda") or _v(val_data, "ev_to_ebitda", ly))
    p_fcf = _safe_num(val_data.get("_current_p_fcf") or _v(val_data, "price_to_fcf", ly))
    div_yield = _safe_num(val_data.get("_current_dividend_yield_pct") or _v(val_data, "dividend_yield_pct", ly))
    p_book = _safe_num(_v(val_data, "price_to_book", ly))
    fcf_yield = _safe_num(val_data.get("_current_fcf_yield_pct") or _v(val_data, "fcf_yield_pct", ly))

    # ── Peer medians ──
    pm = peer_bench.get("peer_medians") or {}
    peer_pe = _safe_num(pm.get("price_to_earnings"))
    peer_ev_ebitda = _safe_num(pm.get("ev_to_ebitda"))

    # ── Financials ──
    revenue = _safe_num(_v(inc_stmt, "revenue_usd_m", ly))
    rev_growth = _safe_num(_v(inc_stmt, "revenue_growth_pct", ly))
    gross_m = _safe_num(_v(margins, "gross_margin_pct", ly))
    op_m = _safe_num(_v(margins, "operating_margin_pct", ly))
    net_m = _safe_num(_v(margins, "net_margin_pct", ly))
    roic = _safe_num(_v(returns, "roic_pct", ly))
    roe = _safe_num(_v(returns, "roe_pct", ly))
    fcf_m = _safe_num(_v(cf_stmt, "fcf_margin_pct", ly))
    eps = _safe_num(_v(inc_stmt, "eps_diluted", ly))

    # ── Build lines ──
    lines: list[str] = []
    lines.append(f"# DATA SUMMARY \u2014 {company} ({ticker})")
    lines.append("")

    # Identity row
    id_parts = []
    if sector:
        id_parts.append(f"**Sector:** {sector}")
    if industry:
        id_parts.append(f"**Industry:** {industry}")
    if exchange:
        id_parts.append(f"**Exchange:** {exchange}")
    if ly:
        id_parts.append(f"**Latest FY:** {ly}")
    if id_parts:
        lines.append(" | ".join(id_parts))
        lines.append("")

    # Scores row
    score_parts = [f"**Moat:** {moat_str}"]
    score_parts.append(f"**Growth:** {_fmt(growth_score, 'dec1')}")
    score_parts.append(f"**Quality:** {_fmt(quality_score, 'dec1')}")
    lines.append(" | ".join(score_parts))
    lines.append("")

    # Pricing row
    price_parts = []
    if price is not None:
        price_parts.append(f"**Price:** ${price:,.2f}")
    if mkt_cap is not None:
        price_parts.append(f"**Mkt Cap:** {_f_dollar_b(mkt_cap)}")
    if fair_value is not None:
        fv_label = f" ({fv_method})" if fv_method else ""
        price_parts.append(f"**Fair Value:** ${fair_value:,.2f}{fv_label}")
    if upside is not None:
        sign = "+" if upside >= 0 else ""
        price_parts.append(f"**Upside:** {sign}{upside:.1f}%")
    if price_parts:
        lines.append(" | ".join(price_parts))
        lines.append("")

    # Multiples table
    mult_headers = ["P/E", "EV/EBITDA", "P/FCF", "P/B", "Div Yield", "FCF Yield"]
    mult_values = [
        _f_mult(pe), _f_mult(ev_ebitda), _f_mult(p_fcf),
        _f_mult(p_book), _f_pct(div_yield), _f_pct(fcf_yield),
    ]
    # Peer row
    peer_values = [
        _f_mult(peer_pe) if peer_pe else "\u2014",
        _f_mult(peer_ev_ebitda) if peer_ev_ebitda else "\u2014",
        "\u2014", "\u2014", "\u2014", "\u2014",
    ]

    lines.append("| " + " | ".join(mult_headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(mult_headers)) + " |")
    lines.append("| " + " | ".join(mult_values) + " |")
    lines.append("| " + " | ".join(f"*Peer: {v}*" if v != "\u2014" else "\u2014" for v in peer_values) + " |")
    lines.append("")

    # Financials table
    fin_headers = ["Revenue", "Rev Growth", "Gross Margin", "Op Margin",
                   "Net Margin", "ROIC", "ROE", "FCF Margin", "EPS"]
    fin_values = [
        _f_rev(revenue), _f_pct(rev_growth), _f_pct(gross_m), _f_pct(op_m),
        _f_pct(net_m), _f_pct(roic), _f_pct(roe), _f_pct(fcf_m), _f_dollar(eps),
    ]
    lines.append("| " + " | ".join(fin_headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(fin_headers)) + " |")
    lines.append("| " + " | ".join(fin_values) + " |")
    lines.append("")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
# RENDER SECTION MARKDOWN — public API
# ═══════════════════════════════════════════════════════════════════════


def render_section_markdown(
    section_num: int,
    structured_output: dict,
    *,
    dcf_anchors: Optional[dict] = None,
    structured_map: Optional[dict] = None,
    precomputed_peer_table: Optional[dict] = None,
    precomputed_tables: Optional[dict] = None,
    peer_bench_data: Optional[dict] = None,
    footnote_ctx: Optional[_FootnoteCtx] = None,
) -> str:
    """Render a single section from structured JSON to markdown.

    This is the public entry point for rendering individual sections
    outside of the full ``assemble_memo`` pipeline.
    """
    renderer = _SectionRenderer(
        dcf_anchors=dcf_anchors or {},
        structured_map=structured_map or {},
        precomputed_peer_table=precomputed_peer_table,
        precomputed_tables=precomputed_tables or {},
        peer_bench_data=peer_bench_data or {},
        footnote_ctx=footnote_ctx or _FootnoteCtx("", None, "", ""),
    )
    rendered = renderer.render_section_with_headers(structured_output, section_num)
    return _post_process(rendered, section_num)


# ═══════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT: assemble_memo
# ═══════════════════════════════════════════════════════════════════════


def assemble_memo(  # noqa: C901 — direct port of 1265-line JS node
    section_outputs: dict,
    fact_sheet: dict,
    source_registry: Optional[dict] = None,
) -> MemoAssembly:
    """Merge aggregate outputs, compute DCF, format, and return MemoAssembly.

    Parameters
    ----------
    section_outputs : dict
        Merged dict with keys ``company_name``, ``ticker``,
        ``section_map``, ``structured_map``, ``scores``,
        ``dcf_anchors``, ``precomputed_peer_table``,
        ``sources_appendix``.
    fact_sheet : dict
        The full fact sheet from the data pipeline.
    source_registry : dict, optional
        Source registry metadata.
    """
    warnings: list[str] = []

    company_name: str = section_outputs.get("company_name", "")
    ticker: str = section_outputs.get("ticker", "")
    raw_section_map: dict = section_outputs.get("section_map") or {}
    structured_map: dict = section_outputs.get("structured_map") or {}
    sources_appendix: str = section_outputs.get("sources_appendix", "")
    scores: dict = dict(section_outputs.get("scores") or {})

    # ─── Raw data resolution ────────────────────────────────────────
    fs_dcf = fact_sheet or {}
    meta = fs_dcf.get("_meta") or {}
    R = meta.get("_raw") or {}

    inc_stmt = R.get("s11_income_statement") or fs_dcf.get("s11_income_statement") or {}
    cf_stmt = R.get("s11_cash_flow") or fs_dcf.get("s11_cash_flow") or {}
    cap_struct = R.get("s2_capital_structure") or fs_dcf.get("s2_capital_structure") or {}
    share_d = R.get("s5_share_data") or fs_dcf.get("s5_share_data") or {}
    bal_sheet = R.get("s11_balance_sheet") or fs_dcf.get("s11_balance_sheet") or {}
    rev_splits = R.get("s2_s4_revenue_splits") or fs_dcf.get("s2_s4_revenue_splits") or {}
    cap_alloc = R.get("s9_capital_allocation") or fs_dcf.get("s9_capital_allocation") or {}
    returns = R.get("s11_returns") or fs_dcf.get("s11_returns") or {}
    work_cap = R.get("s7_working_capital") or fs_dcf.get("s7_working_capital") or {}
    margins = R.get("s5_subject_margins") or fs_dcf.get("s5_subject_margins") or {}
    peer_bench = R.get("s12_peer_benchmarking") or fs_dcf.get("s12_peer_benchmarking") or {}
    val_data = (
        R.get("s13_valuation")
        or R.get("s12_valuation")
        or fs_dcf.get("s13_valuation")
        or fs_dcf.get("s12_valuation")
        or {}
    )
    peers_obj = fs_dcf.get("s6_peers") or {}
    data_as_of = meta.get("data_as_of")

    # Latest year from revenue data
    rev_obj = inc_stmt.get("revenue_usd_m") or {}
    rev_years = sorted(k for k in rev_obj if rev_obj[k] is not None)
    ly = rev_years[-1] if rev_years else ""

    # ─── DCF Anchors ────────────────────────────────────────────────
    dcf_anchors = section_outputs.get("dcf_anchors")

    if not dcf_anchors or not dcf_anchors.get("revenue_usd_m"):
        if dcf_anchors and dcf_anchors.get("valuation_method"):
            pass  # skipDCF sector — keep as-is
        else:
            dcf_anchors = {
                "revenue_usd_m": _safe_get(inc_stmt, "revenue_usd_m", ly, default=0),
                "fcf_margin_pct": _safe_get(cf_stmt, "fcf_margin_pct", ly, default=20),
                "net_debt_usd_m": (
                    _safe_get(cap_struct, "net_debt_usd_m", ly)
                    if _safe_get(cap_struct, "net_debt_usd_m", ly) is not None
                    else _safe_get(bal_sheet, "net_debt_usd_m", ly, default=0)
                ),
                "shares_diluted_m": (
                    # Prefer latest quarterly share count (more current than annual
                    # weighted average, especially after ASR/buyback programs)
                    share_d.get("shares_diluted_latest_q_millions")
                    or _safe_get(share_d, "shares_diluted_millions", ly, default=1)
                ),
                "sbc_usd_m": _safe_get(share_d, "sbc_usd_m", ly, default=0),
                "sbc_pct_of_revenue": _safe_get(share_d, "sbc_pct_of_revenue", ly, default=0),
            }

    # ─── Sector detection ───────────────────────────────────────────
    fs_identity = fs_dcf.get("s1_identity") or {}
    industry_from_fs = fs_identity.get("industry", "")

    is_financial, is_asset_heavy, is_reit, normalized_industry = _detect_sector(
        dcf_anchors, industry_from_fs
    )
    skip_dcf = is_financial or is_asset_heavy

    # ─── Rebuild stripped dcf_anchors if needed ──────────────────────
    vm_from_anchors = (dcf_anchors or {}).get("valuation_method", "")
    if is_financial and vm_from_anchors != "peer_multiples_only_financial":
        dcf_anchors = {
            "valuation_method": "peer_multiples_only_financial",
            "industry": normalized_industry,
        }
    if is_asset_heavy and vm_from_anchors != "peer_multiples_only_asset_heavy":
        dcf_anchors = {
            "valuation_method": "peer_multiples_only_asset_heavy",
            "industry": normalized_industry,
            "primary_multiple": (
                section_outputs.get("dcf_anchors", {}).get("primary_multiple", "EV/EBITDA")
                if section_outputs.get("dcf_anchors")
                else "EV/EBITDA"
            ),
            "secondary_multiple": (
                section_outputs.get("dcf_anchors", {}).get("secondary_multiple", "P/FCF")
                if section_outputs.get("dcf_anchors")
                else "P/FCF"
            ),
        }

    # ─── Enrich DCF anchors (standard path only) ────────────────────
    if not skip_dcf:
        tax_rates = inc_stmt.get("effective_tax_rate_pct") or {}
        tax_years = sorted(k for k in tax_rates if tax_rates[k] is not None)
        recent_3 = tax_years[-3:]
        avg_tax = 21.0
        if recent_3:
            avg_tax = sum(tax_rates[y] for y in recent_3) / len(recent_3)
        dcf_anchors["effective_tax_rate_pct"] = max(5, min(35, avg_tax))
        dcf_anchors["terminal_tax_rate_pct"] = 21

        capex_pct = cf_stmt.get("capex_pct_of_revenue") or {}
        dcf_anchors["capex_pct_of_revenue"] = capex_pct.get(ly, 5) if capex_pct.get(ly) is not None else 5

        da_vals = cf_stmt.get("da_usd_m") or {}
        rev_vals = inc_stmt.get("revenue_usd_m") or {}
        da_latest = da_vals.get(ly)
        rev_latest = rev_vals.get(ly)
        if da_latest is not None and rev_latest is not None and rev_latest > 0:
            dcf_anchors["da_pct_of_revenue"] = (da_latest / rev_latest) * 100
        else:
            dcf_anchors["da_pct_of_revenue"] = dcf_anchors.get("capex_pct_of_revenue", 5) * 0.7

        dcf_anchors["net_capex_pct_of_revenue"] = max(
            0,
            dcf_anchors.get("capex_pct_of_revenue", 5)
            - dcf_anchors.get("da_pct_of_revenue", 3.5),
        )

        roic_vals = returns.get("roic_pct") or {}
        roic_years = sorted(k for k in roic_vals if roic_vals[k] is not None)
        # v8.3 FIX: NaN-safe roic from fact sheet
        roic_raw = roic_vals.get(roic_years[-1]) if roic_years else None
        dcf_anchors["roic_pct"] = (
            roic_raw
            if isinstance(roic_raw, (int, float)) and not math.isnan(roic_raw)
            else 15
        )

        op_margin_vals = inc_stmt.get("operating_margin_pct") or {}
        dcf_anchors["operating_margin_pct"] = (
            op_margin_vals.get(ly)
            if op_margin_vals.get(ly) is not None
            else dcf_anchors.get("fcf_margin_pct", 20)
        )

    # ─── Precomputed peer table ──────────────────────────────────────
    precomputed_peer_table = section_outputs.get("precomputed_peer_table")
    if not precomputed_peer_table:
        precomputed_peer_table = _build_precomputed_peer_table(
            peers_obj, peer_bench, val_data, ticker, company_name, ly,
            is_financial=is_financial,
        )

    # ─── Pre-computed financial tables ───────────────────────────────
    comp_landscape = (
        R.get("s6_competitive_landscape")
        or fs_dcf.get("s6_competitive_landscape")
    )
    precomputed_tables = _build_precomputed_tables(
        inc_stmt, cf_stmt, rev_splits, cap_alloc, returns,
        bal_sheet, work_cap, comp_landscape, ly,
    )

    # ─── Sector-specific KPI tables ─────────────────────────────────
    sector_kpis = fs_dcf.get("_sec_sector_kpis")
    if sector_kpis:
        try:
            from pipeline.sector_tables import build_sector_kpi_tables
            sector_tables = build_sector_kpi_tables(sector_kpis)
            if sector_tables:
                precomputed_tables.update(sector_tables)
        except Exception:
            pass  # Non-critical — sector tables are optional

    # ─── Fair value extraction (via industry-specific valuation models) ─
    if not scores.get("fair_value"):
        _valuation_result = None

        # Try the industry-specific valuation dispatcher first
        # Skip dispatcher for REITs — their P/FFO-based legacy path is more appropriate
        # than the generic P/E-based peer multiples the dispatcher computes.
        if _run_valuation_dispatcher is not None and not is_reit:
            try:
                _valuation_result = _run_valuation_dispatcher(
                    industry=industry_from_fs,
                    sic_code=str(fs_identity.get("sic_code", "")),
                    fact_sheet=fs_dcf,
                )
                if _valuation_result and _valuation_result.get("fair_value") is not None:
                    fv = _valuation_result["fair_value"]
                    if isinstance(fv, (int, float)) and fv > 0:
                        scores["fair_value"] = round(fv * 100) / 100
                        scores["fair_value_method"] = _valuation_result.get(
                            "fair_value_method", "model"
                        )
                        scores["_valuation_detail"] = {
                            "mode": _valuation_result.get("valuation_mode"),
                            "method": _valuation_result.get("method"),
                            "cross_checks": _valuation_result.get("cross_checks", {}),
                        }
                        warnings.append(
                            f"VALUATION: Used {_valuation_result.get('method', '?')} model "
                            f"for {industry_from_fs}. "
                            f"Fair value: ${scores['fair_value']}. "
                            f"Cross-checks: {json.dumps(_valuation_result.get('cross_checks', {}), default=str)}"
                        )
            except Exception as val_err:
                warnings.append(
                    f"VALUATION_DISPATCHER error: {type(val_err).__name__}: {val_err}"
                )

        # Fallback: legacy inline computation if dispatcher didn't produce a value
        if not scores.get("fair_value"):
            if is_financial:
                eps_diluted = _safe_get(inc_stmt, "eps_diluted", ly)
                bvps = (
                    _safe_get(bal_sheet, "book_value_per_share", ly)
                    or _safe_get(bal_sheet, "bvps", ly)
                )
                peers_obj2 = fs_dcf.get("s6_peers") or {}
                peer_bench2 = fs_dcf.get("s12_peer_benchmarking") or {}
                peer_median_pe = _safe_num(
                    _safe_get(peers_obj2, "peer_medians", "price_to_earnings")
                    or _safe_get(peer_bench2, "peer_medians", "price_to_earnings")
                    or _safe_get(precomputed_peer_table, "peer_median", "p_e")
                )
                peer_median_pb = _safe_num(
                    _safe_get(peers_obj2, "peer_medians", "price_to_book")
                    or _safe_get(peer_bench2, "peer_medians", "price_to_book")
                    or _safe_get(precomputed_peer_table, "peer_median", "p_b")
                )

                if is_reit:
                    # REITs: prefer P/FFO over P/B or P/E
                    reit_val_comps = peer_bench.get("valuation_comps_reit") or []
                    reit_med = next(
                        (r for r in reit_val_comps if (r.get("company") or "").lower() == "peer median"),
                        {},
                    )
                    peer_median_pffo = _safe_num(reit_med.get("p_ffo"))
                    # Get FFO per share from sector KPIs
                    _sec_kpis = fs_dcf.get("_sec_sector_kpis") or {}
                    _kpi_inner = _sec_kpis.get("kpis") or _sec_kpis
                    _kpi_rows = (
                        _kpi_inner.get("computedRatios")
                        or _kpi_inner.get("computedMetrics")
                        or _kpi_inner.get("computed")
                        or []
                    )
                    _ffo_ps = None
                    if isinstance(_kpi_rows, list) and _kpi_rows:
                        _latest_kpi = max(_kpi_rows, key=lambda r: r.get("date", ""))
                        _ffo_ps = _safe_num(_latest_kpi.get("ffoPerShare"))

                    if _ffo_ps and _ffo_ps > 0 and peer_median_pffo and peer_median_pffo > 0:
                        scores["fair_value"] = round(_ffo_ps * peer_median_pffo * 100) / 100
                        scores["fair_value_method"] = "pffo_implied"
                    elif bvps and bvps > 0 and peer_median_pb and peer_median_pb > 0:
                        scores["fair_value"] = round(bvps * peer_median_pb * 100) / 100
                        scores["fair_value_method"] = "pb_implied"
                    elif eps_diluted and eps_diluted > 0 and peer_median_pe and peer_median_pe > 0:
                        scores["fair_value"] = round(eps_diluted * peer_median_pe * 100) / 100
                        scores["fair_value_method"] = "pe_implied"
                    else:
                        scores["fair_value"] = None
                        scores["fair_value_method"] = "unavailable"
                else:
                    # Banks / Insurers / Credit Services
                    if eps_diluted and eps_diluted > 0 and peer_median_pe and peer_median_pe > 0:
                        scores["fair_value"] = round(eps_diluted * peer_median_pe * 100) / 100
                        scores["fair_value_method"] = "pe_implied"
                    elif bvps and bvps > 0 and peer_median_pb and peer_median_pb > 0:
                        scores["fair_value"] = round(bvps * peer_median_pb * 100) / 100
                        scores["fair_value_method"] = "pb_implied"
                    else:
                        scores["fair_value"] = None
                        scores["fair_value_method"] = "unavailable"

            elif is_asset_heavy:
                scores["fair_value"] = None
                scores["fair_value_method"] = "peer_multiples_only"

            else:
                # Standard DCF path (base-case only fallback)
                full_dcf_table = _safe_get(
                    structured_map, "section_12", "dcf_analysis", "dcf_table"
                )
                dcf_base = (full_dcf_table or {}).get("base") if full_dcf_table else None
                if dcf_base:
                    base_scenario = compute_dcf_scenario(dcf_base, dcf_anchors)
                    scores["fair_value"] = base_scenario.fair_value_per_share
                    scores["fair_value_method"] = "dcf"

        # ── Probability-weighted DCF override (runs for ALL DCF tickers) ──
        # If the LLM produced bull/base/bear with probability weights,
        # override whatever fair_value was set above with the weighted FV.
        if not is_financial and not is_asset_heavy:
            _dcf_table_pw = _safe_get(
                structured_map, "section_12", "dcf_analysis", "dcf_table"
            )
            if _dcf_table_pw and isinstance(_dcf_table_pw, dict):
                _pw_base = _dcf_table_pw.get("base")
                _pw_bull = _dcf_table_pw.get("bull")
                _pw_bear = _dcf_table_pw.get("bear")
                if _pw_base and _pw_bull and _pw_bear:
                    _pw_base_s = compute_dcf_scenario(_pw_base, dcf_anchors)
                    _pw_bull_s = compute_dcf_scenario(_pw_bull, dcf_anchors)
                    _pw_bear_s = compute_dcf_scenario(_pw_bear, dcf_anchors)
                    pb = _safe_num(_pw_bull.get("probability_pct")) or 25
                    pm = _safe_num(_pw_base.get("probability_pct")) or 50
                    pr = _safe_num(_pw_bear.get("probability_pct")) or 25
                    total_p = pb + pm + pr
                    if total_p > 0:
                        pb, pm, pr = pb / total_p, pm / total_p, pr / total_p
                    weighted = (
                        _pw_bull_s.fair_value_per_share * pb
                        + _pw_base_s.fair_value_per_share * pm
                        + _pw_bear_s.fair_value_per_share * pr
                    )
                    scores["fair_value"] = round(weighted * 100) / 100
                    scores["fair_value_method"] = "dcf_weighted"

    # ─── Score extraction ────────────────────────────────────────────
    extracted = extract_scores(structured_map, raw_section_map)
    scores["moat"] = extracted["moat"] if extracted["moat"] is not None else scores.get("moat")
    scores["growth"] = (
        extracted["growth"] if extracted["growth"] is not None else scores.get("growth")
    )
    scores["quality"] = (
        extracted["quality"] if extracted["quality"] is not None else scores.get("quality")
    )

    # ─── Footnote context ────────────────────────────────────────────
    fn_ctx = _FootnoteCtx(
        fiscal_periods=peer_bench.get("fiscal_periods"),
        data_as_of=data_as_of,
        latest_annual_year=meta.get("latest_annual_year", ""),
        latest_year=ly,
    )

    # ─── Build section renderer ──────────────────────────────────────
    renderer = _SectionRenderer(
        dcf_anchors=dcf_anchors,
        structured_map=structured_map,
        precomputed_peer_table=precomputed_peer_table,
        precomputed_tables=precomputed_tables,
        peer_bench_data=peer_bench,
        footnote_ctx=fn_ctx,
    )

    # ─── Peer-primary S12 reordering ────────────────────────────────
    # For REITs, energy, and mining industries the peer valuation is the
    # PRIMARY model and should render before scenarios.  The LLM may
    # output keys in any order, so we force the dict key order here.
    _PEER_PRIMARY_PREFIXES = ("REIT", "Oil & Gas", "Coal", "Uranium", "Gold", "Silver", "Copper")
    _s12 = structured_map.get("section_12")
    if (
        isinstance(_s12, dict)
        and "peer_valuation" in _s12
        and "scenario_analysis" in _s12
        and any(normalized_industry.startswith(p) for p in _PEER_PRIMARY_PREFIXES)
    ):
        _peer_first_order = []
        _after = []
        _saw_peer = False
        _saw_scenario = False
        for k in _s12:
            if k == "peer_valuation":
                _saw_peer = True
            elif k == "scenario_analysis":
                _saw_scenario = True
        # Only reorder if scenario currently comes before peer
        _keys = list(_s12.keys())
        if _saw_peer and _saw_scenario:
            _pi = _keys.index("peer_valuation")
            _si = _keys.index("scenario_analysis")
            if _si < _pi:
                # Swap: put peer_valuation right where scenario_analysis was
                _reordered = {}
                for k in _keys:
                    if k == "scenario_analysis":
                        _reordered["peer_valuation"] = _s12["peer_valuation"]
                        _reordered["scenario_analysis"] = _s12["scenario_analysis"]
                    elif k == "peer_valuation":
                        continue  # already inserted
                    else:
                        _reordered[k] = _s12[k]
                structured_map["section_12"] = _reordered

    # ─── Rebuild section_map from structured_map ─────────────────────
    section_map: dict[str, str] = {}
    for key in raw_section_map:
        num_match = re.match(r"section_(\d+)", key)
        num = int(num_match.group(1)) if num_match else None

        if num and key in structured_map and isinstance(structured_map[key], dict) and "raw_text" not in structured_map[key]:
            rendered = renderer.render_section_with_headers(structured_map[key], num)
            if rendered and len(rendered.strip()) > 100:
                fmt_def = SECTION_FORMATS.get(num)
                header = (
                    f"# SECTION {num}: {fmt_def['title'].upper()}" if fmt_def else ""
                )
                section_map[key] = f"{header}\n\n{rendered}" if header else rendered
            else:
                section_map[key] = raw_section_map[key]
        else:
            section_map[key] = raw_section_map[key]

    # ─── Inject sector-specific KPI tables into Section 10 ──────────
    _sector_table_titles = {
        "bank_core_metrics": "Core Banking Metrics",
        "bank_credit_quality": "Credit Quality",
        "bank_capital_funding": "Capital & Funding",
        "insurance_underwriting": "Underwriting Performance",
        "reit_operations": "REIT Operating Metrics",
        "energy_operations": "Upstream Operations",
        "energy_financials": "Energy Financials",
        "retail_operations": "Retail Operations",
        "tech_financials": "Technology Financials",
        "tech_growth_metrics": "Growth & Efficiency Metrics",
        "healthcare_financials": "Healthcare Financials",
        "industrials_operations": "Industrial Operations",
        "utilities_operations": "Utilities Operations",
    }
    sector_table_md = ""
    for tbl_key in _SECTOR_TABLE_KEYS:
        if tbl_key in renderer._rendered_sector_tables:
            continue  # Already rendered inline with narrative
        r = renderer._render_sector_kpi_table(tbl_key)
        if r:
            title = _sector_table_titles.get(tbl_key, tbl_key.replace("_", " ").title())
            sector_table_md += f"\n\n## {title}\n\n{r}"

    if sector_table_md and "section_10" in section_map:
        section_map["section_10"] += sector_table_md

    # ─── Post-processing ────────────────────────────────────────────
    formatted_sections: dict[str, str] = {}
    for key, raw_content in section_map.items():
        num_match = re.match(r"section_(\d+)", key)
        if not num_match:
            formatted_sections[key] = raw_content
            continue
        num = int(num_match.group(1))
        formatted_sections[key] = _post_process(raw_content, num)

    # ─── Patch Section 12: Replace LLM-hallucinated fair values with
    #     actual computed values.  The writer outputs assumptions AND
    #     the narrative in one shot, before the deterministic valuation
    #     engine has run, so it may cite a different dollar figure.
    #     Applies to ALL valuation modes (DCF, bank equity, peer). ──────
    if (
        scores.get("fair_value")
        and "section_12" in formatted_sections
    ):
        _actual_fv = scores["fair_value"]
        _s12 = formatted_sections["section_12"]

        # Compute bull/bear values for the correction blurb
        _dcf_tbl = _safe_get(
            structured_map, "section_12", "dcf_analysis", "dcf_table", default={}
        )
        _bull_fv = (
            compute_dcf_scenario(_dcf_tbl.get("bull") or {}, dcf_anchors).fair_value_per_share
            if _dcf_tbl.get("bull") else None
        )
        _bear_fv = (
            compute_dcf_scenario(_dcf_tbl.get("bear") or {}, dcf_anchors).fair_value_per_share
            if _dcf_tbl.get("bear") else None
        )

        # Build the correction blurb
        _range_str = ""
        if _bull_fv and _bear_fv:
            _range_str = (
                f" The three-scenario range spans ${_bear_fv:,.0f} (bear) to "
                f"${_bull_fv:,.0f} (bull)."
            )

        # Use correct label depending on fair value method
        _fv_method_label = scores.get("fair_value_method", "dcf")

        # Determine the right model name based on fair value method
        _is_peer_method = _fv_method_label in (
            "peer_pe", "pe_implied", "pb_implied", "pffo_implied", "model",
            "peer_ev_ebitda", "peer_multiples_fallback", "peer_pe_fallback",
        )
        _is_bank_equity = _fv_method_label in ("bank_equity",)
        _is_nav = _fv_method_label in ("nav",)
        _is_ddm = _fv_method_label in ("ddm",)

        if _is_bank_equity:
            _model_name = "Bank Equity"
        elif _is_ddm:
            _model_name = "DDM"
        elif _is_nav:
            _model_name = "NAV"
        elif _is_peer_method:
            _model_name = "Peer-Implied"
        else:
            _model_name = "DCF"

        if _fv_method_label == "dcf_weighted":
            _fv_label = "probability-weighted"
            # Compute base case for reference
            _base_fv_only = (
                compute_dcf_scenario(
                    _dcf_tbl.get("base") or {}, dcf_anchors
                ).fair_value_per_share
                if _dcf_tbl.get("base") else None
            )
            _base_ref = (
                f" Base case alone: ${_base_fv_only:,.0f}."
                if _base_fv_only else ""
            )
        elif _is_peer_method:
            if _fv_method_label == "pffo_implied":
                _fv_label = "P/FFO implied"
            else:
                _fv_label = f"{_fv_method_label.replace('_', ' ').title()}"
            _base_ref = ""
        elif _is_bank_equity:
            _fv_label = "justified P/B model"
            _base_ref = ""
        elif _is_ddm:
            _fv_label = "dividend discount model"
            _base_ref = ""
        elif _is_nav:
            _fv_label = "net asset value model"
            _base_ref = ""
        else:
            _fv_label = "base case"
            _base_ref = ""

        _fv_note = (
            f"**Computed {_model_name} Fair Value: ${_actual_fv:,.2f} per share** "
            f"({_fv_label}, derived from the assumptions above).{_base_ref}{_range_str}"
        )

        # Insert the correction just before the "Fair Value Conclusion" subsection,
        # or at the very end of section 12 if we can't find the subsection.
        _fvc_pattern = re.compile(
            r"(##\s*Fair\s+Value\s+Conclusion)", re.IGNORECASE
        )
        if _fvc_pattern.search(_s12):
            _s12 = _fvc_pattern.sub(
                rf"\n\n{_fv_note}\n\n\1", _s12, count=1,
            )
        else:
            _s12 += f"\n\n{_fv_note}"

        # ── Scrub hallucinated dollar fair values from LLM prose ──────
        # The LLM may still include dollar amounts in the valuation_synthesis
        # text. Replace them with the computed values.
        # Match patterns like "$309/share", "$309 per share", "$309.50",
        # "fair value of $309", "price target of $XX"
        _known_values = {_actual_fv}
        if _bull_fv:
            _known_values.add(_bull_fv)
        if _bear_fv:
            _known_values.add(_bear_fv)
        if _fv_method_label == "dcf_weighted" and _base_fv_only:
            _known_values.add(_base_fv_only)

        # Add any values from the precomputed peer table so we don't scrub them
        # (peer-implied fair values, peer medians, etc.)
        if precomputed_peer_table:
            for _ptv in [
                _safe_get(precomputed_peer_table, "subject", "p_e"),
                _safe_get(precomputed_peer_table, "subject", "p_b"),
                _safe_get(precomputed_peer_table, "subject", "p_fcf"),
            ]:
                if _ptv and isinstance(_ptv, (int, float)):
                    _known_values.add(float(_ptv))

        # Extract dollar values from the Scenario Analysis and Peer Valuation
        # sections so they are treated as "known" and not scrubbed.
        _scenario_section_match = re.search(
            r"##\s*Scenario\s+Analysis\s*\n(.*?)(?=\n##|\Z)",
            _s12, re.IGNORECASE | re.DOTALL,
        )
        _peer_section_match = re.search(
            r"##\s*Peer\s+Valuation\s*\n(.*?)(?=\n##|\Z)",
            _s12, re.IGNORECASE | re.DOTALL,
        )
        _all_scenario_text = ""
        if _scenario_section_match:
            _all_scenario_text += _scenario_section_match.group(1)
        if _peer_section_match:
            _all_scenario_text += _peer_section_match.group(1)
        if _all_scenario_text:
            for _dm in re.finditer(r"\$[\d,]+(?:\.\d{1,2})?", _all_scenario_text):
                try:
                    _sv = float(_dm.group(0).replace("$", "").replace(",", ""))
                    _known_values.add(_sv)
                except ValueError:
                    pass
        # Also add the current price as a known value
        _cp = fs_identity.get("price")
        if _cp and isinstance(_cp, (int, float)):
            _known_values.add(float(_cp))

        # Find the Fair Value Conclusion section and scrub hallucinated $ amounts.
        # FVC scrub disabled — replacing hallucinated $ amounts with a single
        # _actual_fv created worse output (Bull/Base/Bear all showing identical
        # values).  The scenario table contains correctly computed values;
        # narrative text is better left with the LLM's own values.
        _skip_fvc_scrub = True
        _fvc_match = re.search(
            r"##\s*Fair\s+Value\s+Conclusion\s*\n(.*)",
            _s12, re.IGNORECASE | re.DOTALL
        ) if not _skip_fvc_scrub else None
        if _fvc_match:
            _fvc_text = _fvc_match.group(1)
            # Find all dollar amounts in the FVC text
            _dollar_pattern = re.compile(
                r'\$[\d,]+(?:\.\d{1,2})?'
            )

            def _scrub_dollar(m):
                val_str = m.group(0).replace("$", "").replace(",", "")
                try:
                    val = float(val_str)
                except ValueError:
                    return m.group(0)
                # Keep if it's a known computed value (within 5% tolerance)
                for kv in _known_values:
                    if kv and abs(val - kv) / max(kv, 0.01) < 0.05:
                        return m.group(0)
                # Hallucinated value — replace with actual computed fair value
                if _actual_fv:
                    return f"${_actual_fv:,.2f}"
                return m.group(0)

            _fvc_scrubbed = _dollar_pattern.sub(_scrub_dollar, _fvc_text)
            if _fvc_scrubbed != _fvc_text:
                _s12 = _s12[:_fvc_match.start(1)] + _fvc_scrubbed

        formatted_sections["section_12"] = _s12

        # ── Scrub hallucinated dollar fair values from S1 and S14 ─────
        # Only for DCF mode — peer/bank_equity/DDM values are self-consistent.
        if not _skip_fvc_scrub:
            for _skey in ("section_1", "section_14"):
                if _skey not in formatted_sections:
                    continue
                _stxt = formatted_sections[_skey]
                _val_re = re.search(
                    r"(?:##\s*(?:Valuation|The\s+Verdict|Fair\s+Value)[^\n]*\n)(.*?)(?=\n##|\Z)",
                    _stxt, re.IGNORECASE | re.DOTALL,
                )
                if _val_re:
                    _vtxt = _val_re.group(1)
                    _vtxt_scrubbed = _dollar_pattern.sub(_scrub_dollar, _vtxt)
                    if _vtxt_scrubbed != _vtxt:
                        formatted_sections[_skey] = (
                            _stxt[:_val_re.start(1)] + _vtxt_scrubbed + _stxt[_val_re.end(1):]
                        )

    # ─── Catalyst Calendar (from agent_3 research) ───────────────────
    _catalyst_cal = section_outputs.get("catalyst_calendar", [])
    if _catalyst_cal and isinstance(_catalyst_cal, list) and len(_catalyst_cal) > 0:
        _cal_lines = [
            "\n## Catalyst Calendar\n",
            "| Date/Window | Event | Impact | Detail |",
            "| --- | --- | --- | --- |",
        ]
        for _cat_item in _catalyst_cal:
            if isinstance(_cat_item, dict):
                _d = str(_cat_item.get('date_or_window', '')).replace('|', '/')
                _e = str(_cat_item.get('event', '')).replace('|', '/')
                _i = str(_cat_item.get('impact', '')).replace('|', '/')
                _dt = str(_cat_item.get('detail', '')).replace('|', '/')
                _cal_lines.append(f"| {_d} | {_e} | {_i} | {_dt} |")
        if len(_cal_lines) > 3:  # Has at least one data row
            _cal_md = "\n".join(_cal_lines)
            # Append to section 12 if it exists, otherwise section 14
            if "section_12" in formatted_sections:
                formatted_sections["section_12"] += f"\n\n{_cal_md}"
            elif "section_14" in formatted_sections:
                formatted_sections["section_14"] += f"\n\n{_cal_md}"

    # ─── Section 15: Sources ─────────────────────────────────────────
    if sources_appendix:
        formatted_sections["section_15"] = (
            f"# SECTION 15: SOURCES & CITATIONS\n\n{sources_appendix}\n\n"
            "---\nNote: Financial data tables [F] compiled from company SEC filings. "
            "Qualitative analysis sourced from the documents cited above. "
            "All figures in USD unless otherwise noted."
        )

    # ─── Post-process: strip stray leading colons after ## headers ───
    _colon_after_header = re.compile(
        r"(##\s+[^\n]+\n\s*)\n?\s*[:;]\s*",
    )
    for _sk, _sv in list(formatted_sections.items()):
        if isinstance(_sv, str) and re.search(r"[:;]", _sv):
            formatted_sections[_sk] = _colon_after_header.sub(r"\1\n", _sv)

    # ─── Assemble final memo ─────────────────────────────────────────
    formatted_memo = "\n\n---\n\n".join(
        formatted_sections.get(f"section_{n}", "")
        for n in READING_ORDER
        if formatted_sections.get(f"section_{n}")
    )

    # ─── Data block ──────────────────────────────────────────────────
    data_block = build_data_block(
        scores,
        {
            "fair_value": scores.get("fair_value"),
            "fair_value_method": scores.get("fair_value_method", "dcf"),
        },
        {
            "company_name": company_name,
            "ticker": ticker,
            "structured_map": structured_map,
        },
        dcf_anchors=dcf_anchors,
        is_financial_sector=is_financial,
        is_asset_heavy_sector=is_asset_heavy,
        normalized_industry=normalized_industry,
    )

    # ─── Prepend data_block to memo ─────────────────────────────────
    # The data_block is the structured dict consumed by the website
    # dashboard.  Dump it at the top of the memo so it's visible at a
    # glance and easy to verify.
    _db_lines = ["# DATA BLOCK", "```"]
    for _db_key, _db_val in data_block.items():
        _db_lines.append(f"{_db_key} : {_db_val}")
    _db_lines.append("```")
    formatted_memo = "\n".join(_db_lines) + "\n\n---\n\n" + formatted_memo

    # ─── Validation & warnings ───────────────────────────────────────
    missing = [n for n in READING_ORDER if n <= 14 and f"section_{n}" not in formatted_sections]
    if missing:
        warnings.append(f"Missing sections: {', '.join(str(n) for n in missing)}")

    # Debug warnings (matching JS output)
    warnings.append(f"SCORE_DEBUG final scores={json.dumps(scores, default=str)}")
    warnings.append(f"SCORE_DEBUG data_block={json.dumps(data_block, default=str)}")
    warnings.append(f"DCF_DEBUG anchors={json.dumps(dcf_anchors, default=str)}")
    warnings.append(
        f'DCF_DEBUG industry_from_fs="{industry_from_fs}" '
        f'normalized="{normalized_industry}" '
        f"isFinancialSector={is_financial} isAssetHeavySector={is_asset_heavy}"
    )

    if data_block.get("fair_value") is not None and data_block["fair_value"] < 0 and not skip_dcf:
        warnings.append(
            f"DCF_SANITY: Base-case fair value is negative "
            f"(${data_block['fair_value']}). Check writer assumptions format."
        )
    if is_financial:
        fv_str = f"${data_block['fair_value']}" if data_block.get("fair_value") is not None else "unavailable"
        warnings.append(
            f"FINANCIAL_SECTOR: DCF skipped for "
            f"{dcf_anchors.get('industry') or normalized_industry}. "
            f"Fair value method: {scores.get('fair_value_method')}. "
            f"Fair value: {fv_str}."
        )
    if is_asset_heavy:
        warnings.append(
            f"ASSET_HEAVY_SECTOR: DCF skipped for "
            f"{dcf_anchors.get('industry') or normalized_industry}. "
            f"Primary multiple: {dcf_anchors.get('primary_multiple', 'EV/EBITDA')}. "
            "No dollar fair value computed \u2014 "
            "peer multiple comparison is the valuation framework."
        )

    if data_block.get("moat") is None and data_block.get("moat_score") is None:
        warnings.append("MOAT score missing")
    if data_block.get("growth") is None:
        warnings.append("GROWTH score missing")
    if data_block.get("quality") is None:
        warnings.append("QUALITY score missing")
    if data_block.get("fair_value") is None and not skip_dcf:
        warnings.append("FAIR_VALUE missing")
    if not data_block.get("description"):
        s1_struct = structured_map.get("section_1")
        s1_keys = ", ".join(s1_struct.keys()) if s1_struct else "NO S1"
        warnings.append(f"Company description missing \u2014 S1 keys: {s1_keys}")

    word_count = len(formatted_memo.split())
    table_sets = len(re.findall(r"^\| ---", formatted_memo, re.MULTILINE))
    if word_count < 8000:
        warnings.append(f"Word count low: ~{word_count} words")
    if word_count > 20000:
        warnings.append(f"Word count high: ~{word_count} words")
    if table_sets == 0:
        warnings.append("No tables rendered")

    # ─── Build sections_ordered ──────────────────────────────────────
    sections_ordered = [
        {
            "section_number": n,
            "section_title": (
                SECTION_FORMATS[n]["title"]
                if n in SECTION_FORMATS
                else ("Sources & Citations" if n == 15 else "")
            ),
            "content": formatted_sections.get(f"section_{n}", ""),
        }
        for n in READING_ORDER
        if formatted_sections.get(f"section_{n}")
    ]

    return MemoAssembly(
        company_name=company_name,
        ticker=ticker,
        formatted_memo=formatted_memo,
        data_block=data_block,
        section_map=formatted_sections,
        sections_ordered=sections_ordered,
        word_count=word_count,
        tables_rendered=table_sets,
        warnings=warnings,
    )


# ═══════════════════════════════════════════════════════════════════════
# CONVENIENCE: merge_aggregates — replaces JS input routing
# ═══════════════════════════════════════════════════════════════════════


def merge_aggregates(
    agg1: dict, agg2: dict, sr_data: dict,
) -> tuple[dict, dict]:
    """Merge Aggregate 1 + 2 + source-registry into (section_outputs, fact_sheet).

    This replaces the JS routing logic at the top of Final_Assembly.js that
    splits ``$("For Assembly").all()`` items by inspecting their keys.

    Returns a tuple of ``(section_outputs, fact_sheet)`` suitable as arguments
    to :func:`assemble_memo`.
    """
    company_name = agg1.get("company_name") or agg2.get("company_name", "")
    ticker = agg1.get("ticker") or agg2.get("ticker", "")
    raw_section_map = {**(agg1.get("section_map") or {}), **(agg2.get("section_map") or {})}
    structured_map = {**(agg1.get("structured_map") or {}), **(agg2.get("structured_map") or {})}
    sources_appendix = (
        sr_data.get("sources_appendix")
        or agg1.get("sources_appendix")
        or agg2.get("sources_appendix")
        or ""
    )
    scores = {**(agg1.get("scores") or {}), **(agg2.get("scores") or {})}

    section_outputs = {
        "company_name": company_name,
        "ticker": ticker,
        "section_map": raw_section_map,
        "structured_map": structured_map,
        "scores": scores,
        "sources_appendix": sources_appendix,
        "dcf_anchors": agg2.get("dcf_anchors") or agg1.get("dcf_anchors"),
        "precomputed_peer_table": (
            agg2.get("precomputed_peer_table") or agg1.get("precomputed_peer_table")
        ),
    }

    fact_sheet = sr_data.get("fact_sheet") or {}

    return section_outputs, fact_sheet
