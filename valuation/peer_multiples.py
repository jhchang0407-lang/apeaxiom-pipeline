"""Peer Multiple Valuation Model.

EV/EBITDA and P/E based valuation using peer group medians.
Used for airlines, cyclicals, utilities, groceries, and as a
cross-check for all other valuation models.

Ported from Section_Distributor_2.js implied fair value computation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
import statistics


@dataclass
class PeerMultipleAnchors:
    """Input parameters for peer multiple valuation."""

    # Subject company financials
    ebitda: float = 0.0  # Latest year EBITDA
    eps: float = 0.0  # Latest year EPS (diluted)
    net_debt: float = 0.0  # Net debt (positive = debt)
    shares_diluted: float = 0.0  # Diluted shares outstanding
    revenue: float = 0.0  # Latest revenue
    book_value_per_share: float = 0.0  # BV per share

    # Peer data
    peer_ev_ebitda: list[float] = field(default_factory=list)  # Peer EV/EBITDA ratios
    peer_pe: list[float] = field(default_factory=list)  # Peer P/E ratios
    peer_pb: list[float] = field(default_factory=list)  # Peer P/B ratios
    peer_ev_sales: list[float] = field(default_factory=list)  # Peer EV/Sales ratios

    # Configuration
    primary_method: str = "ev_ebitda"  # "ev_ebitda" or "pe"


@dataclass
class PeerMultipleResult:
    """Output of peer multiple valuation."""

    fair_value_per_share: Optional[float] = None
    ev_ebitda_implied: Optional[float] = None  # Price from EV/EBITDA
    pe_implied: Optional[float] = None  # Price from P/E
    pb_implied: Optional[float] = None  # Price from P/B
    ev_sales_implied: Optional[float] = None  # Price from EV/Sales
    peer_medians: dict = field(default_factory=dict)
    assumptions: dict = field(default_factory=dict)
    method: str = "peer_multiples"
    error: Optional[str] = None


def _safe_median(values: list[float]) -> Optional[float]:
    """Calculate median filtering out None, NaN, and extreme values."""
    clean = [v for v in values if v is not None and v == v and 0 < v < 500]
    if len(clean) < 2:
        return clean[0] if clean else None
    return statistics.median(clean)


def run_peer_multiples(anchors: PeerMultipleAnchors) -> PeerMultipleResult:
    """Run peer multiple valuation.

    EV/EBITDA method:
        Implied EV = Peer median EV/EBITDA × Subject EBITDA
        Implied equity = EV - Net Debt
        Implied price = Equity / Shares

    P/E method:
        Implied price = Peer median P/E × Subject EPS
    """
    result = PeerMultipleResult(method="peer_multiples")

    if anchors.shares_diluted <= 0:
        result.error = "Shares outstanding is zero or negative"
        return result

    medians = {}

    # EV/EBITDA method
    med_ev_ebitda = _safe_median(anchors.peer_ev_ebitda)
    if med_ev_ebitda and anchors.ebitda > 0:
        implied_ev = med_ev_ebitda * anchors.ebitda
        implied_equity = implied_ev - anchors.net_debt
        ev_ebitda_price = implied_equity / anchors.shares_diluted
        result.ev_ebitda_implied = round(ev_ebitda_price, 2)
        medians["ev_ebitda"] = round(med_ev_ebitda, 2)

    # P/E method
    med_pe = _safe_median(anchors.peer_pe)
    if med_pe and anchors.eps > 0:
        result.pe_implied = round(med_pe * anchors.eps, 2)
        medians["pe"] = round(med_pe, 2)

    # P/B method
    med_pb = _safe_median(anchors.peer_pb)
    if med_pb and anchors.book_value_per_share > 0:
        result.pb_implied = round(med_pb * anchors.book_value_per_share, 2)
        medians["pb"] = round(med_pb, 2)

    # EV/Sales method
    med_ev_sales = _safe_median(anchors.peer_ev_sales)
    if med_ev_sales and anchors.revenue > 0:
        implied_ev = med_ev_sales * anchors.revenue
        implied_equity = implied_ev - anchors.net_debt
        ev_sales_price = implied_equity / anchors.shares_diluted
        result.ev_sales_implied = round(ev_sales_price, 2)
        medians["ev_sales"] = round(med_ev_sales, 2)

    result.peer_medians = medians

    # Select primary fair value
    if anchors.primary_method == "ev_ebitda":
        result.fair_value_per_share = result.ev_ebitda_implied or result.pe_implied
    elif anchors.primary_method == "pe":
        result.fair_value_per_share = result.pe_implied or result.ev_ebitda_implied
    else:
        result.fair_value_per_share = result.ev_ebitda_implied or result.pe_implied

    if result.fair_value_per_share is None:
        result.error = "Insufficient peer data for multiple valuation"

    result.assumptions = {
        "primary_method": anchors.primary_method,
        "ebitda": anchors.ebitda,
        "eps": anchors.eps,
        "net_debt": anchors.net_debt,
        "shares_diluted": anchors.shares_diluted,
        "num_peers_ev_ebitda": len([v for v in anchors.peer_ev_ebitda if v]),
        "num_peers_pe": len([v for v in anchors.peer_pe if v]),
    }

    return result
