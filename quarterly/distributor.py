"""Quarterly Distributor — Pre-compute tables and build writer prompt.

Sector-aware: adds sector-specific KPI tables and writer guidance.
Single source of truth for all pre-computed data that both the writer
and formatter consume.
"""

from __future__ import annotations

import json
from typing import Any

from quarterly.sector_prompts import get_sector_config


# ═══════════════════════════════════════════════════════════════════════
# FORMATTING HELPERS
# ═══════════════════════════════════════════════════════════════════════

def _fmt_dollar(val: Any) -> str | None:
    """Format a value in $M as human-readable dollars."""
    if val is None:
        return None
    try:
        n = float(val)
    except (TypeError, ValueError):
        return None
    ab = abs(n)
    sign = "-" if n < 0 else ""
    if ab >= 1_000_000:
        return f"{sign}${ab / 1_000_000:.2f}T"
    if ab >= 1_000:
        b = f"{ab / 1_000:.2f}"
        b = b.rstrip("0").rstrip(".") if b.endswith("0") else b
        return f"{sign}${b}B"
    if ab >= 1:
        m = f"{ab:.2f}"
        m = m.rstrip("0").rstrip(".") if m.endswith("0") else m
        return f"{sign}${m}M"
    if ab > 0:
        return f"{sign}${ab:.2f}M"
    return "$0"


def _pct_fmt(val: Any, decimals: int = 1) -> str | None:
    if val is None:
        return None
    try:
        n = float(val)
    except (TypeError, ValueError):
        return None
    return f"{n:.{decimals}f}%"


def _ppts_fmt(val: Any) -> str | None:
    if val is None:
        return None
    try:
        n = float(val)
    except (TypeError, ValueError):
        return None
    sign = "+" if n > 0 else ""
    return f"{sign}{n:.2f} ppts"


def _fmt_context_value(val: Any, key: str = "") -> Any:
    """Pre-format a context value for the writer (copy-paste ready)."""
    if val is None:
        return val
    if isinstance(val, str):
        return val
    if isinstance(val, (int, float)):
        k = key.lower()
        # Percentages
        if any(x in k for x in ("pct", "margin", "growth", "yield", "rate", "ratio")):
            return _pct_fmt(val)
        # EPS / per-share
        if any(x in k for x in ("eps", "per_share", "dps")):
            return f"${val:.2f}"
        # Stock prices / targets
        if any(x in k for x in ("price", "stock", "close", "target")):
            sign = "-" if val < 0 else ""
            return f"{sign}${abs(val):.2f}"
        # Dollar values in millions
        if any(x in k for x in ("revenue", "_m", "sales", "income", "cash_flow",
                                  "capex", "ebitda", "fcf", "mid", "nii",
                                  "premiums", "backlog")):
            return _fmt_dollar(val)
        return val
    if isinstance(val, list):
        return [_fmt_context_obj(item) for item in val]
    if isinstance(val, dict):
        return _fmt_context_obj(val)
    return val


def _fmt_context_obj(obj: Any) -> Any:
    """Pre-format all values in a dict."""
    if not obj or not isinstance(obj, dict):
        return obj
    return {k: _fmt_context_value(v, k) for k, v in obj.items()}


# ═══════════════════════════════════════════════════════════════════════
# WRITER SCHEMA (single source of truth)
# ═══════════════════════════════════════════════════════════════════════

WRITER_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "section_title": {
            "type": "string",
            "description": "Must be: LATEST FINANCIAL HIGHLIGHTS",
        },
        "section_thesis": {
            "type": "string",
            "description": "1-2 sentences. What does this quarter tell us about the thesis? HIDDEN.",
        },
        "opening_paragraph": {
            "type": "string",
            "description": "3-4 sentences. Lead with headline result and single most important thing.",
        },
        "results_commentary": {
            "type": "string",
            "description": (
                "2-3 sentences placed AFTER the results and beat/miss tables. "
                "Do not restate the numbers. Explain what drove the beat or miss — "
                "was it volume, pricing, mix, one-offs? Connect the headline to "
                "the thesis. Reference consensus expectations only to explain "
                "the magnitude or quality of the surprise."
            ),
        },
        "segment_performance": {
            "type": "string",
            "description": "3-5 sentences. Lead with the segment that drove the quarter.",
        },
        "margin_analysis": {
            "type": "string",
            "description": "3-4 sentences. Explain what DROVE margin expansion or contraction.",
        },
        "guidance_and_management": {
            "type": "string",
            "description": "4-6 sentences. Forward guidance with context on market credibility.",
        },
        "market_reaction": {
            "type": "string",
            "description": "3-4 sentences. Stock move and WHY. Analyst actions with logic.",
        },
        "investment_implications": {
            "type": "string",
            "description": "3-5 sentences. Does this quarter strengthen or weaken the thesis?",
        },
    },
    "required": [
        "section_title", "section_thesis", "opening_paragraph",
        "results_commentary",
        "segment_performance", "margin_analysis",
        "guidance_and_management", "market_reaction", "investment_implications",
    ],
    "additionalProperties": False,
}


# ═══════════════════════════════════════════════════════════════════════
# MAIN DISTRIBUTOR
# ═══════════════════════════════════════════════════════════════════════

def distribute_quarterly(
    facts: dict,
    sector_family: str,
    profile: dict,
) -> dict:
    """Pre-compute tables and build the writer prompt.

    Args:
        facts: Enriched facts from fact_extract.
        sector_family: Canonical sector family.
        profile: FMP company profile dict.

    Returns:
        Dict with precomputed tables, writer schema, writer template, and raw context.
    """
    config = get_sector_config(sector_family)
    h = facts.get("headline") or {}
    m = facts.get("margins") or {}
    seg_obj = facts.get("segments") or {}

    # ── Pre-compute: Results Table ────────────────────────────────
    results_table: list[dict] = []
    if h.get("revenue_actual_m") is not None:
        results_table.append({
            "metric": "Revenue",
            "actual": _fmt_dollar(h["revenue_actual_m"]),
            "yoy_change": _pct_fmt(h.get("revenue_yoy_growth_pct")),
        })
    if m.get("gross_margin_pct") is not None:
        results_table.append({
            "metric": "Gross Margin",
            "actual": _pct_fmt(m["gross_margin_pct"], 2),
            "yoy_change": _ppts_fmt(m.get("gross_margin_yoy_change")),
        })
    if m.get("operating_margin_pct") is not None:
        results_table.append({
            "metric": "Operating Margin",
            "actual": _pct_fmt(m["operating_margin_pct"], 2),
            "yoy_change": _ppts_fmt(m.get("operating_margin_yoy_change")),
        })
    if m.get("net_margin_pct") is not None:
        results_table.append({
            "metric": "Net Margin",
            "actual": _pct_fmt(m["net_margin_pct"], 2),
            "yoy_change": _ppts_fmt(m.get("net_margin_yoy_change")),
        })
    if h.get("eps_actual") is not None:
        results_table.append({
            "metric": "EPS (Diluted)",
            "actual": f"${h['eps_actual']:.2f}",
            "yoy_change": _pct_fmt(h.get("eps_yoy_growth_pct")),
        })

    # ── Pre-compute: Beat/Miss Table ─────────────────────────────
    beat_miss_table: list[dict] = []
    if h.get("revenue_actual_m") is not None and h.get("revenue_consensus_m") is not None:
        bm_str = None
        if h.get("revenue_beat_miss_m") is not None:
            bm_str = (
                f"{_fmt_dollar(h['revenue_beat_miss_m'])} / "
                f"{h['revenue_beat_miss_pct']:.2f}%"
            )
        beat_miss_table.append({
            "metric": "Revenue",
            "actual": _fmt_dollar(h["revenue_actual_m"]),
            "consensus": _fmt_dollar(h["revenue_consensus_m"]),
            "beat_miss": bm_str,
        })
    if h.get("eps_actual") is not None and h.get("eps_consensus") is not None:
        beat_miss_table.append({
            "metric": "EPS (Diluted)",
            "actual": f"${h['eps_actual']:.2f}",
            "consensus": f"${h['eps_consensus']:.2f}",
            "beat_miss": f"${h['eps_beat_miss']:.2f}" if h.get("eps_beat_miss") is not None else None,
        })

    # ── Pre-compute: Segment Table ───────────────────────────────
    segments = seg_obj.get("items", []) if isinstance(seg_obj, dict) else (seg_obj if isinstance(seg_obj, list) else [])
    total_seg_rev = seg_obj.get("segment_revenue_total", 0) if isinstance(seg_obj, dict) else sum(
        (s.get("revenue_m") or 0) for s in segments
    )

    segment_table: list[dict] = []
    for s in sorted(segments, key=lambda x: x.get("revenue_m") or 0, reverse=True):
        if s.get("revenue_m") is not None:
            pct_of_total = (
                f"{(s['revenue_m'] / total_seg_rev * 100):.1f}%"
                if total_seg_rev > 0 else None
            )
            segment_table.append({
                "segment": s.get("name", "Unknown"),
                "revenue": _fmt_dollar(s["revenue_m"]),
                "pct_of_total": pct_of_total,
                "yoy_growth": _pct_fmt(s.get("yoy_growth_pct")),
            })

    # ── Pre-compute: Sector KPI Table ────────────────────────────
    sector_kpi_table: list[dict] = []
    sector_kpis = facts.get("sector_kpis") or {}
    if sector_kpis:
        # Human-readable labels for common sector KPI keys
        _KPI_LABELS = {
            "net_interest_income_m": "Net Interest Income",
            "net_interest_margin_pct": "Net Interest Margin",
            "efficiency_ratio_pct": "Efficiency Ratio",
            "provision_for_credit_losses_m": "Provision for Credit Losses",
            "cet1_ratio_pct": "CET1 Capital Ratio",
            "nco_rate_pct": "Net Charge-Off Rate",
            "npl_ratio_pct": "Non-Performing Loan Ratio",
            "loan_growth_yoy_pct": "Loan Growth YoY",
            "noninterest_revenue_m": "Noninterest Revenue",
            "combined_ratio_pct": "Combined Ratio",
            "loss_ratio_pct": "Loss Ratio",
            "expense_ratio_pct": "Expense Ratio",
            "net_premiums_written_m": "Net Premiums Written",
            "net_premiums_written_growth_pct": "NPW Growth YoY",
            "investment_income_m": "Net Investment Income",
            "book_value_per_share": "Book Value / Share",
            "cat_losses_m": "Catastrophe Losses",
            "ffo_per_share": "FFO / Share",
            "affo_per_share": "AFFO / Share",
            "same_store_noi_growth_pct": "Same-Store NOI Growth",
            "occupancy_pct": "Occupancy Rate",
            "lease_spread_cash_pct": "Cash Lease Spread",
            "lease_spread_gaap_pct": "GAAP Lease Spread",
            "dividend_per_share": "Dividend / Share",
            "arr_m": "ARR",
            "rpo_m": "RPO",
            "ndr_pct": "Net Dollar Retention",
            "rule_of_40": "Rule of 40",
            "sbc_pct_of_revenue": "SBC % of Revenue",
            "crpo_m": "Current RPO",
            "customer_count": "Customer Count",
            "comp_sales_growth_pct": "Comp Sales Growth",
            "ecommerce_growth_pct": "E-Commerce Growth",
            "ecommerce_pct_of_sales": "E-Commerce % of Sales",
            "inventory_growth_pct": "Inventory Growth YoY",
            "organic_growth_pct": "Organic Revenue Growth",
            "book_to_bill": "Book-to-Bill Ratio",
            "backlog_m": "Order Backlog",
            "fcf_conversion_pct": "FCF Conversion",
            "organic_sales_growth_pct": "Organic Sales Growth",
            "price_contribution_pct": "Price Contribution",
            "volume_contribution_pct": "Volume/Mix Contribution",
            "rate_base_b": "Rate Base",
            "rate_base_growth_pct": "Rate Base Growth",
            "authorized_roe_pct": "Authorized ROE",
            "load_growth_pct": "Load Growth",
            "ffo_to_debt_pct": "FFO-to-Debt",
            "postpaid_phone_net_adds": "Postpaid Phone Net Adds",
            "arpu": "ARPU",
            "churn_pct": "Monthly Churn Rate",
            "service_revenue_growth_pct": "Service Revenue Growth",
            "rd_pct_of_revenue": "R&D % of Revenue",
        }
        for key, val in sector_kpis.items():
            if val is None:
                continue
            label = _KPI_LABELS.get(key, key.replace("_", " ").title())
            formatted = _fmt_context_value(val, key)
            # Round raw floats that don't match a format pattern
            if isinstance(formatted, float):
                formatted = round(formatted, 1)
            sector_kpi_table.append({"metric": label, "value": str(formatted)})

    # ── Warnings ─────────────────────────────────────────────────
    eps_basis_warning = h.get("eps_basis_warning")
    segment_coverage_warning = seg_obj.get("segment_coverage_warning") if isinstance(seg_obj, dict) else None

    # ── Pre-format context objects ───────────────────────────────
    guidance_fmt = _fmt_context_obj(facts.get("guidance") or {})
    management_fmt = _fmt_context_obj(facts.get("management") or {})
    market_reaction_fmt = _fmt_context_obj(facts.get("market_reaction") or {})
    analysts_fmt = [_fmt_context_obj(a) for a in (facts.get("analysts") or [])]

    # ── Sources ──────────────────────────────────────────────────
    sources = [
        {"id": f"S{i + 1}", "name": src.get("name", ""), "url": src.get("url", "")}
        for i, src in enumerate(facts.get("sources") or [])
    ]

    # ── Writer Template ──────────────────────────────────────────
    writer_guidance = config.get("writer_guidance", "")
    sector_label = sector_family.upper().replace("_", " ")

    writer_template = (
        "You are writing the LATEST FINANCIAL HIGHLIGHTS section of an institutional "
        "equity research memo.\n\n"
        "CRITICAL RULES:\n"
        "1. Tables are PRE-COMPUTED and appear alongside your prose. Your prose must add "
        "context, causation, and judgment — not restate what the tables show.\n"
        "2. Reference table numbers ONLY when building a causal argument.\n"
        "3. section_thesis is HIDDEN — it guides your writing but will not appear.\n"
        "4. Write dense, analytical prose. No bullet points. No headers.\n"
        "5. Do not use bold or italic formatting.\n"
        "6. All dollar figures are pre-formatted. Copy them exactly.\n"
        "7. Use [S1], [S2] citation tags at end of sentences before the period.\n\n"
        f"SECTOR: {sector_label}\n"
    )

    if writer_guidance:
        writer_template += f"SECTOR GUIDANCE: {writer_guidance}\n\n"

    writer_template += (
        f"PRECOMPUTED DATA (tables the reader already sees — do not narrate these):\n"
        f"- precomputed_results_table: {json.dumps(results_table)}\n"
        f"- precomputed_beat_miss_table: {json.dumps(beat_miss_table)}\n"
        f"- precomputed_segment_table: {json.dumps(segment_table)}\n"
    )

    if sector_kpi_table:
        writer_template += f"- sector_kpi_table: {json.dumps(sector_kpi_table)}\n"

    writer_template += (
        f"\nADDITIONAL CONTEXT (your analytical edge):\n"
        f"- Guidance: {json.dumps(guidance_fmt)}\n"
        f"- Management: {json.dumps(management_fmt)}\n"
        f"- Market Reaction: {json.dumps(market_reaction_fmt)}\n"
        f"- Analysts: {json.dumps(analysts_fmt)}\n"
        f"- EPS Note: {eps_basis_warning or 'GAAP basis — no adjustment warning.'}\n"
        f"- Segment Coverage: {segment_coverage_warning or 'Within expected range.'}\n"
    )

    # ── Output ───────────────────────────────────────────────────
    return {
        # Precomputed table data (formatter reads these)
        "precomputed_results_table": results_table,
        "precomputed_beat_miss_table": beat_miss_table,
        "precomputed_segment_table": segment_table,
        "precomputed_sector_kpi_table": sector_kpi_table,
        "sources": sources,
        # Raw context
        "quarter": facts.get("quarter") or {},
        "guidance": guidance_fmt,
        "management": management_fmt,
        "market_reaction": market_reaction_fmt,
        "analysts": analysts_fmt,
        # Warnings
        "eps_basis_warning": eps_basis_warning,
        "segment_coverage_warning": segment_coverage_warning,
        # Writer config
        "writer_schema": WRITER_SCHEMA,
        "writer_template": writer_template,
        # Sector metadata
        "sector_family": sector_family,
    }
