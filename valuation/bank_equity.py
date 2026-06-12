"""Bank Equity Valuation Model.

For banks, insurance companies, and other financial institutions where
EV is meaningless (leverage is a core input, not a capital structure choice).

Uses the Justified P/B (Gordon Growth) approach:
    Justified P/B = (ROE - g) / (CoE - g)
    Fair Value = Book Value per Share × Justified P/B

Also computes excess return model:
    Value = BV + PV of excess returns
    Excess Return = (ROE - CoE) × BV
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BankEquityAnchors:
    """Input parameters for bank equity valuation."""

    book_value_per_share: float = 0.0
    tangible_bv_per_share: float = 0.0  # TBVPS (optional, more conservative)
    roe: float = 0.0  # Return on equity as decimal (e.g., 0.15 for 15%)
    cost_of_equity: float = 0.10  # Estimated cost of equity
    sustainable_growth: float = 0.03  # Long-term growth rate
    eps_latest: float = 0.0  # Latest EPS for P/E cross-check
    dividend_per_share: float = 0.0  # For dividend discount cross-check

    # Sector KPIs (optional, for context)
    cet1_ratio: Optional[float] = None
    nim: Optional[float] = None  # Net interest margin
    efficiency_ratio: Optional[float] = None
    npl_ratio: Optional[float] = None


@dataclass
class BankEquityResult:
    """Output of bank equity valuation."""

    fair_value_per_share: Optional[float] = None
    justified_pb: Optional[float] = None
    excess_return_value: Optional[float] = None
    pe_implied_value: Optional[float] = None  # P/E cross-check
    ddm_value: Optional[float] = None  # Dividend discount cross-check
    assumptions: dict = field(default_factory=dict)
    method: str = "bank_equity"
    error: Optional[str] = None


def run_bank_equity(anchors: BankEquityAnchors) -> BankEquityResult:
    """Run bank equity valuation (Justified P/B + Excess Return).

    Primary: Justified P/B = (ROE - g) / (CoE - g) × BV per share
    Cross-checks: P/E implied, DDM if dividends available
    """
    result = BankEquityResult(method="bank_equity")

    if anchors.book_value_per_share <= 0:
        result.error = "Book value per share is zero or negative"
        return result

    roe = anchors.roe
    coe = anchors.cost_of_equity
    g = anchors.sustainable_growth

    if coe <= g:
        result.error = "Cost of equity must exceed sustainable growth rate"
        return result

    if roe < g:
        result.error = "ROE below sustainable growth rate — justified P/B is negative"
        return result

    # Justified P/B ratio
    justified_pb = (roe - g) / (coe - g)
    result.justified_pb = round(justified_pb, 2)

    # Fair value via P/B
    bv = anchors.book_value_per_share
    fair_value = bv * justified_pb
    result.fair_value_per_share = round(fair_value, 2)

    # Excess return model
    # Value = BV + PV(Excess Returns perpetuity)
    # Excess Return = (ROE - CoE) × BV
    excess_return = (roe - coe) * bv
    if coe - g > 0:
        pv_excess = excess_return / (coe - g)
        result.excess_return_value = round(bv + pv_excess, 2)

    # P/E cross-check (if EPS available)
    if anchors.eps_latest > 0 and roe > 0:
        # Justified P/E for banks = Payout × (1 + g) / (CoE - g)
        payout_ratio = 1.0 - (g / roe) if roe > 0 else 0.5
        justified_pe = payout_ratio * (1 + g) / (coe - g) if (coe - g) > 0 else 0
        result.pe_implied_value = round(anchors.eps_latest * justified_pe, 2)

    # DDM cross-check (if dividends available)
    if anchors.dividend_per_share > 0:
        # Gordon Growth: P = D₁ / (CoE - g)
        d1 = anchors.dividend_per_share * (1 + g)
        if coe - g > 0:
            result.ddm_value = round(d1 / (coe - g), 2)

    result.assumptions = {
        "book_value_per_share": bv,
        "roe": roe,
        "cost_of_equity": coe,
        "sustainable_growth": g,
        "justified_pb": result.justified_pb,
        "cet1_ratio": anchors.cet1_ratio,
        "nim": anchors.nim,
        "efficiency_ratio": anchors.efficiency_ratio,
    }

    return result
