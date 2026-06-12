"""Industry → Valuation Method Configuration.

Ported from Section_Distributor_2.js INDUSTRY_VALUATION_CONFIG + SKIP_DCF_INDUSTRIES.

Three valuation modes:
  "dcf"             → Standard DCF + peer multiples (tech, consumer, healthcare)
  "financial_peer"  → Banks/Insurance/REITs — EV meaningless, use P/E + P/B
  "industry_peer"   → Commodity/cyclical/regulated — EV valid but FCF unreliable
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class ValuationConfig:
    """Configuration for a specific industry's valuation approach."""

    method: str  # "ev_ebitda" or "pe"
    secondary: str  # Fallback metric
    rationale: str  # Why DCF doesn't work for this industry
    sector_note: str  # Additional sector-specific context


# Industries where EV is meaningless (financial institutions)
SKIP_DCF_INDUSTRIES = {
    "Banks - Diversified", "Banks - Regional",
    "Insurance - Diversified", "Insurance - Life",
    "Insurance - Property & Casualty", "Insurance - Specialty",
    "Insurance - Reinsurance",
    "Mortgage Finance", "Thrifts & Mortgage Finance",
    "Credit Services",
}

# Industries where DCF is unreliable but EV is valid
INDUSTRY_VALUATION_CONFIG: dict[str, ValuationConfig] = {
    # Oil & Gas
    "Oil & Gas E&P": ValuationConfig(
        method="ev_ebitda", secondary="pe",
        rationale="Commodity price cyclicality and depletion charges make FCF projections unreliable; EV/EBITDA normalizes for D&A policy differences",
        sector_note="For E&P companies, production volumes and reserve replacement rates drive long-term value but are not captured in GAAP financials.",
    ),
    "Oil & Gas Integrated": ValuationConfig(
        method="ev_ebitda", secondary="pe",
        rationale="Integrated O&G capex cycles span decades; EBITDA captures operating economics before volatile exploration write-offs",
        sector_note="Integrated majors are valued on through-cycle cash generation; single-year FCF is misleading.",
    ),
    "Oil & Gas Midstream": ValuationConfig(
        method="ev_ebitda", secondary="pe",
        rationale="Massive depreciation on pipeline assets distorts GAAP FCF; EV/EBITDA is the industry standard",
        sector_note="Distributable cash flow is the industry metric; EBITDA is the best available proxy.",
    ),
    "Oil & Gas Refining & Marketing": ValuationConfig(
        method="ev_ebitda", secondary="pe",
        rationale="Crack spread cyclicality and turnaround capex make FCF lumpy; EV/EBITDA smooths maintenance vs growth capex",
        sector_note="Refining margins are mean-reverting; mid-cycle EBITDA is more informative than any single year's FCF.",
    ),
    "Oil & Gas Equipment & Services": ValuationConfig(
        method="ev_ebitda", secondary="pe",
        rationale="Revenue tied to commodity-driven drilling activity cycles; EV/EBITDA normalizes for equipment depreciation",
        sector_note="OFS companies' earnings are a leveraged bet on drilling activity.",
    ),
    "Oil & Gas Drilling": ValuationConfig(
        method="ev_ebitda", secondary="pe",
        rationale="Rig fleet depreciation and dayrate cyclicality make GAAP FCF unreliable",
        sector_note="Dayrate contracts and rig utilization drive value; EBITDA captures current earning power.",
    ),

    # Mining & Metals
    "Gold": ValuationConfig(
        method="ev_ebitda", secondary="pe",
        rationale="Gold price cyclicality and mine depletion/impairment charges make GAAP FCF volatile",
        sector_note="Gold miners are also valued on EV/oz of reserves; P/NAV is the institutional standard.",
    ),
    "Silver": ValuationConfig(
        method="ev_ebitda", secondary="pe",
        rationale="Silver price cyclicality and mine depletion charges distort GAAP FCF",
        sector_note="Many silver miners have significant base metal byproduct credits.",
    ),
    "Copper": ValuationConfig(
        method="ev_ebitda", secondary="pe",
        rationale="Copper price cycles and large-scale mine development capex create FCF noise",
        sector_note="Copper miners' capex is heavily front-loaded for mine development.",
    ),
    "Other Industrial Metals & Mining": ValuationConfig(
        method="ev_ebitda", secondary="pe",
        rationale="Commodity cyclicality, mine depletion, and impairment charges make GAAP FCF unreliable",
        sector_note="Diversified miners may have different commodity exposures.",
    ),
    "Other Precious Metals & Mining": ValuationConfig(
        method="ev_ebitda", secondary="pe",
        rationale="Precious metals price cyclicality and mine depletion charges",
        sector_note="Precious metals miners often trade at premium EV/EBITDA multiples.",
    ),
    "Coking Coal": ValuationConfig(
        method="ev_ebitda", secondary="pe",
        rationale="Coking coal prices are tied to steel production cycles",
        sector_note="Met coal is essential for steelmaking with limited substitutes.",
    ),
    "Thermal Coal": ValuationConfig(
        method="ev_ebitda", secondary="pe",
        rationale="Commodity price cycles and mine depletion charges distort GAAP FCF",
        sector_note="Thermal coal faces secular decline but growing demand in emerging markets.",
    ),
    "Steel": ValuationConfig(
        method="ev_ebitda", secondary="pe",
        rationale="Steel price cyclicality and blast furnace maintenance capex create volatile GAAP FCF",
        sector_note="Steel companies' profitability is driven by metal spreads.",
    ),
    "Aluminum": ValuationConfig(
        method="ev_ebitda", secondary="pe",
        rationale="Aluminum price cycles and smelter energy cost volatility make GAAP FCF unreliable",
        sector_note="Smelter energy costs are the primary cost differentiator.",
    ),
    "Uranium": ValuationConfig(
        method="ev_ebitda", secondary="pe",
        rationale="Uranium contract pricing cycles and mine development timelines make FCF highly variable",
        sector_note="Uranium producers are valued on lbs of reserves and contract book.",
    ),

    # Shipping & Transportation
    "Marine Shipping": ValuationConfig(
        method="ev_ebitda", secondary="pe",
        rationale="Charter rate cyclicality and vessel depreciation policy differences create volatile GAAP FCF",
        sector_note="Shipping companies are also valued on P/NAV (fleet market value minus debt).",
    ),
    "Airlines": ValuationConfig(
        method="ev_ebitda", secondary="pe",
        rationale="Fleet depreciation, operating lease capitalization, and fuel cost volatility make GAAP FCF unreliable",
        sector_note="Airlines are highly cyclical with thin margins; EV/EBITDA normalizes for fleet age and lease structure.",
    ),
    "Airlines, Airports & Air Services": ValuationConfig(
        method="ev_ebitda", secondary="pe",
        rationale="Fleet depreciation, operating lease capitalization, and fuel cost volatility make GAAP FCF unreliable",
        sector_note="Airlines are highly cyclical with thin margins; EV/EBITDA normalizes for fleet age and lease structure.",
    ),

    # Utilities
    "Utilities - Regulated Electric": ValuationConfig(
        method="pe", secondary="ev_ebitda",
        rationale="Regulated utility earnings reflect allowed ROE on rate base; capex is mandated by regulators",
        sector_note="Earnings growth driven by rate base growth and rate case outcomes.",
    ),
    "Utilities - Regulated Gas": ValuationConfig(
        method="pe", secondary="ev_ebitda",
        rationale="Same regulated rate base model as electric utilities",
        sector_note="Gas utilities face additional volume risk from weather and efficiency gains.",
    ),
    "Utilities - Regulated Water": ValuationConfig(
        method="pe", secondary="ev_ebitda",
        rationale="Regulated water utilities earn allowed ROE on rate base; massive infrastructure replacement capex",
        sector_note="Water utilities have the most predictable demand but largest infrastructure replacement needs.",
    ),
    "Utilities - Diversified": ValuationConfig(
        method="pe", secondary="ev_ebitda",
        rationale="Mix of regulated and unregulated operations; P/E captures the blended return structure",
        sector_note="Diversified utilities' unregulated segment may warrant different multiples.",
    ),
    "Utilities - Independent Power Producers": ValuationConfig(
        method="ev_ebitda", secondary="pe",
        rationale="Unregulated merchant power producers have commodity-like exposure to wholesale power prices",
        sector_note="IPPs' earnings depend on power price curves and capacity payments.",
    ),
    "Utilities - Renewable": ValuationConfig(
        method="ev_ebitda", secondary="pe",
        rationale="Tax credit structures (ITC/PTC) distort GAAP earnings and FCF",
        sector_note="CAFD is the industry metric but unavailable from standard financial APIs.",
    ),

    # FMP variant names (without "Utilities - " prefix)
    "Regulated Electric": ValuationConfig(
        method="pe", secondary="ev_ebitda",
        rationale="Regulated utility earnings reflect allowed ROE on rate base; capex is mandated by regulators",
        sector_note="Earnings growth driven by rate base growth and rate case outcomes.",
    ),
    "Regulated Gas": ValuationConfig(
        method="pe", secondary="ev_ebitda",
        rationale="Same regulated rate base model as electric utilities",
        sector_note="Gas utilities face additional volume risk from weather and efficiency gains.",
    ),
    "Regulated Water": ValuationConfig(
        method="pe", secondary="ev_ebitda",
        rationale="Regulated water utilities earn allowed ROE on rate base",
        sector_note="Water utilities have the most predictable demand.",
    ),
    "Independent Power Producers": ValuationConfig(
        method="ev_ebitda", secondary="pe",
        rationale="Unregulated merchant power producers have commodity-like exposure to wholesale power prices",
        sector_note="IPPs' earnings depend on power price curves and capacity payments.",
    ),
    "Renewable": ValuationConfig(
        method="ev_ebitda", secondary="pe",
        rationale="Tax credit structures (ITC/PTC) distort GAAP earnings and FCF",
        sector_note="CAFD is the industry metric but unavailable from standard financial APIs.",
    ),

    # Groceries / Consumer Staples (added per user request)
    "Grocery Stores": ValuationConfig(
        method="ev_ebitda", secondary="pe",
        rationale="Thin margins and high capex for store openings/remodels make FCF volatile; EV/EBITDA normalizes",
        sector_note="Same-store sales growth and operating leverage are key metrics.",
    ),
    "Food Distribution": ValuationConfig(
        method="ev_ebitda", secondary="pe",
        rationale="Distribution margins are thin; EBITDA captures scale economics better than post-capex FCF",
        sector_note="Volume growth and customer retention drive value.",
    ),

    # REITs — P/E (proxy for P/FFO) is standard; GAAP depreciation crushes ROE/BVPS
    "REIT - Diversified": ValuationConfig(
        method="pe", secondary="ev_ebitda",
        rationale="REIT GAAP depreciation depresses book value and ROE; P/E (proxy for P/FFO) is the industry standard",
        sector_note="REITs are valued on FFO/AFFO multiples; GAAP P/E approximates P/FFO for peer comparison.",
    ),
    "REIT - Office": ValuationConfig(
        method="pe", secondary="ev_ebitda",
        rationale="REIT GAAP depreciation depresses book value and ROE; P/E is the industry standard",
        sector_note="Office REITs face secular remote-work headwinds; occupancy and renewal spreads are key.",
    ),
    "REIT - Retail": ValuationConfig(
        method="pe", secondary="ev_ebitda",
        rationale="REIT GAAP depreciation depresses book value and ROE; P/E is the industry standard",
        sector_note="Net-lease REITs valued on contractual rent stability and acquisition yield spreads.",
    ),
    "REIT - Residential": ValuationConfig(
        method="pe", secondary="ev_ebitda",
        rationale="REIT GAAP depreciation depresses book value and ROE; P/E is the industry standard",
        sector_note="Residential REITs driven by rent growth, occupancy, and supply pipeline.",
    ),
    "REIT - Industrial": ValuationConfig(
        method="pe", secondary="ev_ebitda",
        rationale="REIT GAAP depreciation depresses book value and ROE; P/E is the industry standard",
        sector_note="Industrial/logistics REITs benefit from e-commerce and supply-chain nearshoring.",
    ),
    "REIT - Healthcare Facilities": ValuationConfig(
        method="pe", secondary="ev_ebitda",
        rationale="REIT GAAP depreciation depresses book value and ROE; P/E is the industry standard",
        sector_note="Healthcare REITs depend on operator credit and reimbursement environment.",
    ),
    "REIT - Hotel & Motel": ValuationConfig(
        method="pe", secondary="ev_ebitda",
        rationale="REIT GAAP depreciation depresses book value and ROE; P/E is the industry standard",
        sector_note="Hotel REITs are cyclical; RevPAR and occupancy drive near-term value.",
    ),
    "REIT - Mortgage": ValuationConfig(
        method="pe", secondary="ev_ebitda",
        rationale="Mortgage REITs are interest-rate sensitive; P/E captures spread income better than GAAP book value",
        sector_note="mREITs are valued on book value discount/premium and spread income sustainability.",
    ),
    "REIT - Specialty": ValuationConfig(
        method="pe", secondary="ev_ebitda",
        rationale="REIT GAAP depreciation depresses book value and ROE; P/E is the industry standard",
        sector_note="Specialty REITs (data centers, cell towers, self-storage) often command premium multiples.",
    ),
}


def _prefix_match(text: str, prefix: str) -> bool:
    """Prefix match guarded against short/partial-word false positives.

    Requires the prefix to be at least 5 characters or to end at a word
    boundary (e.g. "Gold" must not match "Golden Entertainment").
    """
    if not prefix or not text.startswith(prefix):
        return False
    if len(text) == len(prefix):
        return True
    return len(prefix) >= 5 or not text[len(prefix)].isalnum()


def normalize_industry(s: str) -> str:
    """Normalize FMP industry strings to canonical form."""
    if not s:
        return ""
    # Replace em-dash/en-dash with " - "
    s = re.sub(r"[\u2014\u2013]", " - ", s)
    # Collapse multiple spaces
    s = re.sub(r"\s+", " ", s).strip()
    # Insert separator if missing
    prefixes = ["Banks", "Insurance", "REIT", "Thrifts", "Utilities"]
    for p in prefixes:
        if s.startswith(p + " ") and " - " not in s:
            s = p + " - " + s[len(p) + 1:]
            break
    # Oil & Gas dash normalization
    s = re.sub(r"^(Oil & Gas)\s+-\s+", r"\1 ", s)
    return s


def detect_valuation_mode(
    industry: str,
    sic_code: str = "",
) -> tuple[str, Optional[ValuationConfig]]:
    """Detect the appropriate valuation mode for a company.

    Args:
        industry: FMP industry string (will be normalized)
        sic_code: SIC code from SEC (backup detection)

    Returns:
        Tuple of (mode, config):
        - ("dcf", None) for standard DCF
        - ("financial_peer", None) for banks/insurance (justified P/B model)
        - ("industry_peer", ValuationConfig) for REITs/commodity/cyclical/regulated
    """
    normalized = normalize_industry(industry)

    # Check financial sector first
    if normalized in SKIP_DCF_INDUSTRIES:
        return "financial_peer", None

    # Check industry peer valuation — exact match first
    config = INDUSTRY_VALUATION_CONFIG.get(normalized)
    if config:
        return "industry_peer", config

    # Fuzzy match: check if normalized industry starts with a config key
    # (e.g., "Airlines, Airports & Air Services" starts with "Airlines")
    norm_lower = normalized.lower()
    if norm_lower:
        for cfg_key, cfg_val in INDUSTRY_VALUATION_CONFIG.items():
            key_lower = cfg_key.lower()
            if _prefix_match(norm_lower, key_lower) or _prefix_match(key_lower, norm_lower):
                return "industry_peer", cfg_val

    # SIC code fallback
    if sic_code:
        try:
            sic = int(sic_code)
        except (ValueError, TypeError):
            sic = 0

        # Banks (SIC 6020-6029, 6710-6712)
        if 6020 <= sic <= 6029 or 6710 <= sic <= 6712:
            return "financial_peer", None

        # Insurance (SIC 6311-6399)
        if 6311 <= sic <= 6399:
            return "financial_peer", None

        # REITs (SIC 6500-6599, 6798) — same P/E-primary config as the
        # industry-string path, not the bank justified-P/B model
        if 6500 <= sic <= 6599 or sic == 6798:
            return "industry_peer", INDUSTRY_VALUATION_CONFIG.get(
                "REIT - Diversified",
                ValuationConfig(
                    method="pe", secondary="ev_ebitda",
                    rationale="REIT GAAP depreciation depresses book value and ROE; P/E (proxy for P/FFO) is the industry standard",
                    sector_note="REITs are valued on FFO/AFFO multiples.",
                ),
            )

        # Oil & Gas (SIC 1311, 1381, 2911)
        if sic in (1311, 1381, 2911):
            return "industry_peer", INDUSTRY_VALUATION_CONFIG.get(
                "Oil & Gas E&P",
                ValuationConfig(
                    method="ev_ebitda", secondary="pe",
                    rationale="Commodity cyclicality makes FCF projections unreliable",
                    sector_note="EV/EBITDA is the standard O&G valuation metric.",
                ),
            )

        # Mining (SIC 1040-1099)
        if 1040 <= sic <= 1099:
            return "industry_peer", INDUSTRY_VALUATION_CONFIG.get(
                "Gold",
                ValuationConfig(
                    method="ev_ebitda", secondary="pe",
                    rationale="Mine depletion and commodity cyclicality distort FCF",
                    sector_note="Mining companies are valued on EV/EBITDA and reserves.",
                ),
            )

        # Airlines (SIC 4512)
        if sic == 4512:
            return "industry_peer", INDUSTRY_VALUATION_CONFIG.get("Airlines")

        # Utilities (SIC 4911-4941)
        if 4911 <= sic <= 4941:
            return "industry_peer", INDUSTRY_VALUATION_CONFIG.get(
                "Utilities - Regulated Electric",
                ValuationConfig(
                    method="pe", secondary="ev_ebitda",
                    rationale="Regulated rate base model",
                    sector_note="P/E reflects allowed returns.",
                ),
            )

    # Default: standard DCF
    return "dcf", None
