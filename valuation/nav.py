"""Net Asset Value (NAV) Valuation Model.

For O&G, mining, and REITs where asset values drive the investment thesis
rather than future cash flow projections.

- O&G: PV of proved reserves (SEC standardized measure) + surface assets
- Mining: EV per oz of reserves × total reserves
- REITs: Property NOI ÷ cap rate = property value, less debt

Falls back to EV/EBITDA peer multiples if reserve data is unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class NAVAnchors:
    """Input parameters for NAV computation."""

    # Common
    net_debt: float = 0.0
    shares_diluted: float = 0.0
    sector: str = ""  # "oil_gas", "mining", "reit"

    # O&G specific
    proved_reserves_boe: Optional[float] = None  # Barrels of oil equivalent
    standardized_measure: Optional[float] = None  # SEC standardized measure ($)
    production_boe_per_day: Optional[float] = None

    # Mining specific
    proved_reserves_oz: Optional[float] = None  # Ounces of gold/silver/etc
    ev_per_oz_peer_median: Optional[float] = None

    # REIT specific
    noi: Optional[float] = None  # Net Operating Income ($)
    cap_rate: Optional[float] = None  # Capitalization rate (decimal)
    occupancy_rate: Optional[float] = None

    # Fallback
    ebitda: float = 0.0
    peer_ev_ebitda_median: Optional[float] = None


@dataclass
class NAVResult:
    """Output of NAV computation."""

    fair_value_per_share: Optional[float] = None
    nav_total: Optional[float] = None  # Total NAV
    nav_per_share: Optional[float] = None  # NAV per share
    ev_ebitda_crosscheck: Optional[float] = None  # EV/EBITDA implied value
    assumptions: dict = field(default_factory=dict)
    method: str = "nav"
    used_fallback: bool = False
    error: Optional[str] = None


def run_nav(anchors: NAVAnchors) -> NAVResult:
    """Run NAV valuation based on sector.

    For O&G: Uses SEC standardized measure of proved reserves
    For Mining: Uses EV/oz × reserves
    For REITs: Uses NOI / cap rate
    Falls back to EV/EBITDA if sector-specific data unavailable
    """
    result = NAVResult(method="nav")

    if anchors.shares_diluted <= 0:
        result.error = "Shares outstanding is zero or negative"
        return result

    nav = None

    if anchors.sector == "oil_gas" and anchors.standardized_measure:
        # O&G: SEC standardized measure IS the PV10 of reserves
        nav = anchors.standardized_measure
        result.assumptions["method_detail"] = "SEC Standardized Measure (PV10)"
        result.assumptions["proved_reserves_boe"] = anchors.proved_reserves_boe
        result.assumptions["production_boe_per_day"] = anchors.production_boe_per_day

    elif anchors.sector == "mining" and anchors.proved_reserves_oz and anchors.ev_per_oz_peer_median:
        # Mining: EV per oz × reserves
        nav = anchors.proved_reserves_oz * anchors.ev_per_oz_peer_median
        result.assumptions["method_detail"] = "EV per oz × reserves"
        result.assumptions["reserves_oz"] = anchors.proved_reserves_oz
        result.assumptions["ev_per_oz"] = anchors.ev_per_oz_peer_median

    elif anchors.sector == "reit" and anchors.noi and anchors.cap_rate and anchors.cap_rate > 0:
        # REIT: NOI / cap rate
        property_value = anchors.noi / anchors.cap_rate
        nav = property_value
        result.assumptions["method_detail"] = "NOI / Cap Rate"
        result.assumptions["noi"] = anchors.noi
        result.assumptions["cap_rate"] = anchors.cap_rate
        result.assumptions["occupancy_rate"] = anchors.occupancy_rate

    # Calculate per-share values
    if nav is not None:
        equity_value = nav - anchors.net_debt
        nav_per_share = equity_value / anchors.shares_diluted
        result.nav_total = round(nav)
        result.nav_per_share = round(nav_per_share, 2)
        result.fair_value_per_share = round(nav_per_share, 2)
    else:
        # Fallback to EV/EBITDA
        result.used_fallback = True
        if anchors.ebitda > 0 and anchors.peer_ev_ebitda_median:
            implied_ev = anchors.peer_ev_ebitda_median * anchors.ebitda
            equity = implied_ev - anchors.net_debt
            result.fair_value_per_share = round(equity / anchors.shares_diluted, 2)
            result.assumptions["method_detail"] = "EV/EBITDA fallback (sector-specific data unavailable)"
            result.assumptions["peer_ev_ebitda_median"] = anchors.peer_ev_ebitda_median
        else:
            result.error = "Insufficient data for NAV or EV/EBITDA fallback"

    # EV/EBITDA cross-check
    if anchors.ebitda > 0 and anchors.peer_ev_ebitda_median:
        implied_ev = anchors.peer_ev_ebitda_median * anchors.ebitda
        equity = implied_ev - anchors.net_debt
        result.ev_ebitda_crosscheck = round(equity / anchors.shares_diluted, 2)

    result.assumptions["net_debt"] = anchors.net_debt
    result.assumptions["shares_diluted"] = anchors.shares_diluted
    result.assumptions["sector"] = anchors.sector

    return result
