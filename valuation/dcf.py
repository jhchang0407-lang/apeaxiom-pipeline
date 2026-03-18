"""DCF Valuation Model.

Standard 10-year discounted cash flow model for tech, consumer, healthcare,
and other industries where FCF projections are reliable.

Ported from Final_Assembly.js DCF computation section.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DCFAnchors:
    """Input parameters for DCF computation."""

    revenue_latest: float = 0.0  # Latest year revenue (same unit as net_debt/sbc)
    fcf_margin: float = 0.0  # FCF/Revenue as decimal (e.g., 0.25)
    revenue_growth: float = 0.0  # Revenue growth rate as decimal
    net_debt: float = 0.0  # Net debt (positive = debt, negative = net cash)
    shares_diluted: float = 0.0  # Weighted avg diluted shares (same unit as revenue)
    sbc_annual: float = 0.0  # Annual stock-based compensation
    wacc: float = 0.10  # Weighted avg cost of capital
    terminal_growth: float = 0.025  # Long-term growth rate
    projection_years: int = 10
    growth_decay_rate: float = 0.85  # How fast growth decays toward terminal


@dataclass
class DCFResult:
    """Output of DCF computation."""

    fair_value_per_share: Optional[float] = None
    enterprise_value: Optional[float] = None
    equity_value: Optional[float] = None
    pv_fcfs: list = field(default_factory=list)
    terminal_value: Optional[float] = None
    pv_terminal: Optional[float] = None
    sensitivity_table: list = field(default_factory=list)
    assumptions: dict = field(default_factory=dict)
    method: str = "dcf"
    error: Optional[str] = None


def _run_dcf_core(anchors: DCFAnchors) -> DCFResult:
    """Core DCF computation without sensitivity table (avoids recursion)."""
    result = DCFResult(method="dcf")

    # Validate inputs
    if anchors.revenue_latest <= 0:
        result.error = "Revenue is zero or negative"
        return result
    if anchors.shares_diluted <= 0:
        result.error = "Shares outstanding is zero or negative"
        return result
    if anchors.wacc <= anchors.terminal_growth:
        result.error = "WACC must be greater than terminal growth rate"
        return result

    wacc = anchors.wacc
    g_terminal = anchors.terminal_growth
    decay = anchors.growth_decay_rate

    # Project revenue and FCF
    revenue = anchors.revenue_latest
    growth = anchors.revenue_growth
    pv_fcfs = []

    for year in range(1, anchors.projection_years + 1):
        # Growth decays toward terminal rate
        yr_growth = max(g_terminal, growth * (decay ** year))
        revenue *= (1 + yr_growth)
        fcf = revenue * anchors.fcf_margin
        pv = fcf / ((1 + wacc) ** year)
        pv_fcfs.append({
            "year": year,
            "revenue": round(revenue),
            "growth_rate": round(yr_growth, 4),
            "fcf": round(fcf),
            "pv_fcf": round(pv),
        })

    # Terminal value (Gordon Growth Model)
    terminal_fcf = pv_fcfs[-1]["fcf"]
    terminal_value = terminal_fcf * (1 + g_terminal) / (wacc - g_terminal)
    pv_terminal = terminal_value / ((1 + wacc) ** anchors.projection_years)

    # Enterprise value
    sum_pv_fcfs = sum(f["pv_fcf"] for f in pv_fcfs)
    enterprise_value = sum_pv_fcfs + pv_terminal

    # Equity value
    equity_value = enterprise_value - anchors.net_debt

    # Fair value per share
    fair_value = equity_value / anchors.shares_diluted

    result.fair_value_per_share = round(fair_value, 2)
    result.enterprise_value = round(enterprise_value)
    result.equity_value = round(equity_value)
    result.pv_fcfs = pv_fcfs
    result.terminal_value = round(terminal_value)
    result.pv_terminal = round(pv_terminal)

    result.assumptions = {
        "revenue_latest": anchors.revenue_latest,
        "initial_growth": anchors.revenue_growth,
        "fcf_margin": anchors.fcf_margin,
        "wacc": wacc,
        "terminal_growth": g_terminal,
        "net_debt": anchors.net_debt,
        "shares_diluted": anchors.shares_diluted,
        "projection_years": anchors.projection_years,
        "growth_decay_rate": anchors.growth_decay_rate,
    }

    return result


def run_dcf(anchors: DCFAnchors, include_sensitivity: bool = True) -> DCFResult:
    """Run 10-year DCF valuation.

    Revenue projection with growth decay:
        Year N growth = max(terminal_growth, growth * decay^N)

    FCF = Revenue x FCF_margin

    Terminal Value = FCF_terminal x (1 + g) / (WACC - g)

    Enterprise Value = Sum(PV of FCFs) + PV(Terminal Value)

    Equity Value = EV - Net Debt
    """
    result = _run_dcf_core(anchors)

    if result.error or not include_sensitivity:
        return result

    # Build sensitivity table (uses _run_dcf_core to avoid recursion)
    result.sensitivity_table = _build_sensitivity(anchors)

    return result


def _build_sensitivity(anchors: DCFAnchors) -> list[dict]:
    """Build WACC x Growth Rate sensitivity table."""
    wacc_range = [
        anchors.wacc - 0.02,
        anchors.wacc - 0.01,
        anchors.wacc,
        anchors.wacc + 0.01,
        anchors.wacc + 0.02,
    ]
    growth_range = [
        max(0, anchors.revenue_growth - 0.03),
        max(0, anchors.revenue_growth - 0.015),
        anchors.revenue_growth,
        anchors.revenue_growth + 0.015,
        anchors.revenue_growth + 0.03,
    ]

    table = []
    for wacc in wacc_range:
        if wacc <= anchors.terminal_growth:
            continue
        row = {"wacc": round(wacc, 4)}
        for growth in growth_range:
            modified = DCFAnchors(
                revenue_latest=anchors.revenue_latest,
                fcf_margin=anchors.fcf_margin,
                revenue_growth=growth,
                net_debt=anchors.net_debt,
                shares_diluted=anchors.shares_diluted,
                sbc_annual=anchors.sbc_annual,
                wacc=wacc,
                terminal_growth=anchors.terminal_growth,
                projection_years=anchors.projection_years,
                growth_decay_rate=anchors.growth_decay_rate,
            )
            # Use core (no sensitivity) to avoid recursion
            res = _run_dcf_core(modified)
            row[f"growth_{round(growth * 100, 1)}pct"] = (
                round(res.fair_value_per_share, 2) if res.fair_value_per_share else None
            )
        table.append(row)

    return table
