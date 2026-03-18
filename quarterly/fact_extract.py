"""Fact Extract — Deterministic enrichment of raw research output.

Computes derived metrics from the research agent's structured JSON:
  - Beat/miss calculations (revenue, EPS)
  - Margin YoY changes
  - Segment coverage cross-check
  - Guidance midpoints
  - Sector-specific derived KPIs

Pure Python, no AI. Fast and deterministic.
"""

from __future__ import annotations

from typing import Any


def _safe_num(val: Any) -> float | None:
    """Safely convert a value to float, returning None on failure."""
    if val is None:
        return None
    try:
        n = float(val)
        return n
    except (TypeError, ValueError):
        return None


def extract_quarterly_facts(research: dict, sector_family: str = "generic") -> dict:
    """Enrich raw research JSON with derived calculations.

    Args:
        research: Structured dict from the research agent.
        sector_family: Canonical sector family for sector-specific derivations.

    Returns:
        Enriched facts dict ready for the distributor.
    """
    h = research.get("headline") or {}
    mg = research.get("margins") or {}
    g = research.get("guidance") or {}

    # ── Beat / Miss ──────────────────────────────────────────────
    revenue_beat_miss_m = None
    revenue_beat_miss_pct = None
    eps_beat_miss = None
    eps_basis_warning = None

    rev_actual = _safe_num(h.get("revenue_actual_m"))
    rev_consensus = _safe_num(h.get("revenue_consensus_m"))
    if rev_actual is not None and rev_consensus is not None and rev_consensus != 0:
        revenue_beat_miss_m = round(rev_actual - rev_consensus, 1)
        revenue_beat_miss_pct = round((revenue_beat_miss_m / rev_consensus) * 100, 2)

    eps_actual = _safe_num(h.get("eps_actual"))
    eps_consensus = _safe_num(h.get("eps_consensus"))
    if eps_actual is not None and eps_consensus is not None:
        eps_beat_miss = round(eps_actual - eps_consensus, 2)

    if h.get("eps_basis") == "adjusted":
        eps_basis_warning = "EPS figures are adjusted, not GAAP. Beat/miss comparison may not be like-for-like."

    # ── Margin YoY Changes ───────────────────────────────────────
    gross_margin_yoy_change = None
    operating_margin_yoy_change = None
    net_margin_yoy_change = None

    gm_curr = _safe_num(mg.get("gross_margin_pct"))
    gm_prior = _safe_num(mg.get("gross_margin_prior_year_pct"))
    if gm_curr is not None and gm_prior is not None:
        gross_margin_yoy_change = round(gm_curr - gm_prior, 2)

    om_curr = _safe_num(mg.get("operating_margin_pct"))
    om_prior = _safe_num(mg.get("operating_margin_prior_year_pct"))
    if om_curr is not None and om_prior is not None:
        operating_margin_yoy_change = round(om_curr - om_prior, 2)

    nm_curr = _safe_num(mg.get("net_margin_pct"))
    nm_prior = _safe_num(mg.get("net_margin_prior_year_pct"))
    if nm_curr is not None and nm_prior is not None:
        net_margin_yoy_change = round(nm_curr - nm_prior, 2)

    # ── Segment Cross-Check ──────────────────────────────────────
    segments = research.get("segments") or []

    segment_revenue_total = sum(
        _safe_num(s.get("revenue_m")) or 0 for s in segments
    )

    segment_coverage_pct = None
    segment_coverage_warning = None
    if rev_actual and segment_revenue_total > 0:
        segment_coverage_pct = round((segment_revenue_total / rev_actual) * 100, 1)
        if segment_coverage_pct < 70:
            segment_coverage_warning = (
                f"Segment revenues sum to only {segment_coverage_pct}% of total revenue "
                "— likely missing segments."
            )
        elif segment_coverage_pct > 110:
            segment_coverage_warning = (
                f"Segment revenues sum to {segment_coverage_pct}% of total revenue "
                "— possible double-counting."
            )

    # ── Guidance Midpoints ───────────────────────────────────────
    nq_low = _safe_num(g.get("next_quarter_revenue_low_m"))
    nq_high = _safe_num(g.get("next_quarter_revenue_high_m"))
    next_q_revenue_mid = round((nq_low + nq_high) / 2, 1) if nq_low is not None and nq_high is not None else None

    fy_low = _safe_num(g.get("full_year_revenue_low_m"))
    fy_high = _safe_num(g.get("full_year_revenue_high_m"))
    full_year_revenue_mid = round((fy_low + fy_high) / 2, 1) if fy_low is not None and fy_high is not None else None

    # ── Build Enriched Output ────────────────────────────────────
    facts = {
        "quarter": {
            "reported": research.get("quarter_reported"),
            "end_date": research.get("quarter_end_date"),
            "earnings_date": research.get("earnings_date"),
        },
        "headline": {
            **h,
            "revenue_beat_miss_m": revenue_beat_miss_m,
            "revenue_beat_miss_pct": revenue_beat_miss_pct,
            "eps_beat_miss": eps_beat_miss,
            "eps_basis_warning": eps_basis_warning,
        },
        "margins": {
            **mg,
            "gross_margin_yoy_change": gross_margin_yoy_change,
            "operating_margin_yoy_change": operating_margin_yoy_change,
            "net_margin_yoy_change": net_margin_yoy_change,
        },
        "segments": {
            "items": segments,
            "segment_revenue_total": segment_revenue_total,
            "segment_coverage_pct": segment_coverage_pct,
            "segment_coverage_warning": segment_coverage_warning,
        },
        "guidance": {
            **g,
            "next_q_revenue_mid": next_q_revenue_mid,
            "full_year_revenue_mid": full_year_revenue_mid,
        },
        "management": research.get("management") or {},
        "market_reaction": research.get("market_reaction") or {},
        "analysts": research.get("analysts") or [],
        "sources": research.get("sources") or [],
    }

    # ── Sector KPIs (pass through if present) ────────────────────
    sector_kpis = research.get("sector_kpis")
    if sector_kpis and isinstance(sector_kpis, dict):
        facts["sector_kpis"] = sector_kpis

    return facts
