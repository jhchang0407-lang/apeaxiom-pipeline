"""Dividend Discount Model (DDM) — Multi-Stage Gordon Growth.

Standard valuation approach for regulated utilities and other
high-payout-ratio companies with predictable dividend growth.

Implements a 2-stage DDM:
  Stage 1 (5 years): Company-specific dividend growth rate
  Stage 2 (terminal): Long-term sustainable growth (GDP-like)

  Fair Value = Σ PV(D₁..Dₙ) + PV(Terminal Value)
  Terminal Value = Dₙ₊₁ / (CoE - g_terminal)

Also computes:
  - Implied yield at computed fair value
  - P/E cross-check using allowed ROE framework
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DDMAnchors:
    """Input parameters for DDM valuation."""

    dividend_per_share: float = 0.0  # Latest annual DPS
    eps_latest: float = 0.0  # Latest EPS (for payout ratio calc)
    payout_ratio: float = 0.0  # Payout ratio (0-1 decimal)

    # Growth
    dividend_growth_5yr: float = 0.0  # Historical 5yr DPS CAGR (decimal)
    earnings_growth: float = 0.0  # Expected earnings growth (decimal)
    stage1_growth: float = 0.0  # Override: stage 1 dividend growth

    # Discount rate
    cost_of_equity: float = 0.08  # CoE (CAPM-derived)

    # Terminal
    terminal_growth: float = 0.025  # Long-term sustainable growth

    # Cross-check data
    book_value_per_share: float = 0.0
    roe: float = 0.0  # ROE as decimal (0.12 = 12%)

    # Context
    price: float = 0.0  # Current stock price
    authorized_roe: Optional[float] = None  # Regulated utility allowed ROE


@dataclass
class DDMResult:
    """Output of DDM valuation."""

    fair_value_per_share: Optional[float] = None
    stage1_pv: Optional[float] = None  # PV of stage 1 dividends
    terminal_pv: Optional[float] = None  # PV of terminal value
    implied_yield: Optional[float] = None  # Dividend yield at fair value
    pe_crosscheck: Optional[float] = None  # P/E implied fair value
    assumptions: dict = field(default_factory=dict)
    method: str = "ddm"
    error: Optional[str] = None


def run_ddm(anchors: DDMAnchors) -> DDMResult:
    """Run 2-stage DDM valuation.

    Stage 1: 5 years of explicit dividend growth
    Stage 2: Gordon Growth terminal value

    Returns DDMResult with fair value per share and cross-checks.
    """
    result = DDMResult(method="ddm")

    if anchors.dividend_per_share <= 0:
        result.error = "No dividend per share — DDM requires positive dividends"
        return result

    coe = anchors.cost_of_equity
    g_terminal = anchors.terminal_growth

    if coe <= g_terminal:
        result.error = "Cost of equity must exceed terminal growth rate"
        return result

    # ── Stage 1 growth rate determination ──
    # Priority: explicit override > earnings growth > historical DPS CAGR
    if anchors.stage1_growth > 0:
        g1 = anchors.stage1_growth
    elif anchors.earnings_growth > 0:
        # Dividend growth roughly tracks earnings growth for stable payers
        g1 = anchors.earnings_growth
    elif anchors.dividend_growth_5yr > 0:
        g1 = anchors.dividend_growth_5yr
    else:
        # Default: slight premium to terminal growth
        g1 = g_terminal + 0.01

    # Clamp stage 1 growth to reasonable range (0-15%)
    g1 = max(0.0, min(g1, 0.15))

    # ── Stage 1: PV of explicit dividends (years 1-5) ──
    d0 = anchors.dividend_per_share
    stage1_pv = 0.0
    d_projected = d0
    for year in range(1, 6):
        d_projected = d_projected * (1 + g1)
        pv = d_projected / ((1 + coe) ** year)
        stage1_pv += pv

    result.stage1_pv = round(stage1_pv, 2)

    # ── Stage 2: Terminal value (Gordon Growth from year 6 onward) ──
    d6 = d_projected * (1 + g_terminal)  # First terminal dividend
    terminal_value = d6 / (coe - g_terminal)
    terminal_pv = terminal_value / ((1 + coe) ** 5)
    result.terminal_pv = round(terminal_pv, 2)

    # ── Fair value ──
    fair_value = stage1_pv + terminal_pv
    if fair_value > 0:
        result.fair_value_per_share = round(fair_value, 2)
        result.implied_yield = round(d0 * (1 + g1) / fair_value * 100, 2)
    else:
        result.error = "DDM produced non-positive fair value"
        return result

    # ── P/E cross-check ──
    # For regulated utilities: Justified P/E ≈ Payout / (CoE - g)
    if anchors.eps_latest > 0:
        payout = anchors.payout_ratio if anchors.payout_ratio > 0 else (
            d0 / anchors.eps_latest if anchors.eps_latest > 0 else 0.7
        )
        # Blend stage 1 and terminal growth
        blended_g = 0.6 * g1 + 0.4 * g_terminal
        justified_pe = payout * (1 + blended_g) / (coe - blended_g) if (coe - blended_g) > 0 else 0
        if justified_pe > 0:
            result.pe_crosscheck = round(anchors.eps_latest * justified_pe, 2)

    result.assumptions = {
        "dividend_per_share": d0,
        "stage1_growth": round(g1 * 100, 1),  # As percentage
        "terminal_growth": round(g_terminal * 100, 1),
        "cost_of_equity": round(coe * 100, 1),
        "projection_years": 5,
        "payout_ratio": round((anchors.payout_ratio or (d0 / anchors.eps_latest if anchors.eps_latest > 0 else 0)) * 100, 1),
    }

    return result
