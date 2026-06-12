"""Sector detection and KPI dispatcher.

Detects a company's sector from its SIC code and dispatches to the
appropriate sector module for specialized KPI extraction.

Supports an optional sector_override parameter so callers can pass FMP's
sector/industry classification when SIC codes don't map well.
"""

from __future__ import annotations

from sec.profile import _sic_to_subsector
from sec.client import get_companyfacts, get_submissions

# Mapping from FMP industry/sector strings to our subsector names
_FMP_SECTOR_MAP = {
    # FMP sector names
    "technology": "tech",
    "financial services": "banking",
    "healthcare": "healthcare",
    "energy": "energy",
    "consumer cyclical": "retail",
    "real estate": "reits",
    "industrials": "industrials",
    # FMP industry names (more specific)
    "software": "tech",
    "semiconductors": "tech",
    "semiconductor equipment & materials": "tech",
    "information technology services": "tech",
    "electronic gaming & multimedia": "tech",
    "software - application": "tech",
    "software - infrastructure": "tech",
    "consumer electronics": "tech",
    "communication equipment": "tech",
    "computer hardware": "tech",
    "scientific & technical instruments": "tech",
    "banks - diversified": "banking",
    "banks - regional": "banking",
    "banks": "banking",
    "credit services": "banking",
    "capital markets": "banking",
    "financial data & stock exchanges": "banking",
    "insurance - diversified": "insurance",
    "insurance - life": "insurance",
    "insurance - property & casualty": "insurance",
    "insurance - reinsurance": "insurance",
    "insurance - specialty": "insurance",
    "insurance brokers": "insurance",
    "reit - diversified": "reits",
    "reit - industrial": "reits",
    "reit - office": "reits",
    "reit - residential": "reits",
    "reit - retail": "reits",
    "reit - healthcare facilities": "reits",
    "reit - specialty": "reits",
    "reit - hotel & motel": "reits",
    "reit - mortgage": "reits",
    "oil & gas e&p": "energy",
    "oil & gas integrated": "energy",
    "oil & gas midstream": "energy",
    "oil & gas refining & marketing": "energy",
    "oil & gas equipment & services": "energy",
    "drug manufacturers": "healthcare",
    "drug manufacturers - general": "healthcare",
    "drug manufacturers - specialty & generic": "healthcare",
    "biotechnology": "healthcare",
    "medical devices": "healthcare",
    "medical instruments & supplies": "healthcare",
    "diagnostics & research": "healthcare",
    "health information services": "healthcare",
    "medical care facilities": "healthcare",
    "medical distribution": "healthcare",
    "pharmaceutical retailers": "healthcare",
    "specialty retail": "retail",
    "discount stores": "retail",
    "department stores": "retail",
    "home improvement retail": "retail",
    "grocery stores": "retail",
    "restaurants": "retail",
    "apparel retail": "retail",
    "internet retail": "retail",
    "auto & truck dealerships": "retail",
    "aerospace & defense": "industrials",
    "industrial distribution": "industrials",
    "conglomerates": "industrials",
    "farm & heavy construction machinery": "industrials",
    "specialty industrial machinery": "industrials",
    "electrical equipment & parts": "industrials",
    "building products & equipment": "industrials",
    "engineering & construction": "industrials",
    "railroads": "industrials",
    "airlines": "industrials",
    "trucking": "industrials",
    "marine shipping": "industrials",
    "integrated freight & logistics": "industrials",
    "waste management": "industrials",
    "rental & leasing services": "industrials",
    "staffing & employment services": "industrials",
    "utilities - regulated electric": "utilities",
    "utilities - diversified": "utilities",
    "utilities - regulated gas": "utilities",
    "utilities - renewable": "utilities",
    "utilities - independent power producers": "utilities",
    "utilities - regulated water": "utilities",
    # Consumer Staples / Consumer Defensive
    "consumer defensive": "consumer_staples",
    "household & personal products": "consumer_staples",
    "packaged foods": "consumer_staples",
    "beverages - non-alcoholic": "consumer_staples",
    "beverages - brewers": "consumer_staples",
    "beverages - wineries & distilleries": "consumer_staples",
    "tobacco": "consumer_staples",
    "confectioners": "consumer_staples",
    "food distribution": "consumer_staples",
    "farm products": "consumer_staples",
    "education & training services": "consumer_staples",
    # Basic Materials
    "basic materials": "materials",
    "specialty chemicals": "materials",
    "chemicals": "materials",
    "steel": "materials",
    "aluminum": "materials",
    "copper": "materials",
    "gold": "materials",
    "silver": "materials",
    "other industrial metals & mining": "materials",
    "other precious metals & mining": "materials",
    "building materials": "materials",
    "paper & paper products": "materials",
    "lumber & wood production": "materials",
    "coking coal": "materials",
    "agricultural inputs": "materials",
}


def _resolve_sector(sic: str, sector_override: str | None = None) -> str | None:
    """Resolve subsector from SIC code, with optional FMP override."""
    # Try SIC code first
    subsector = _sic_to_subsector(sic)
    if subsector is not None:
        return subsector

    # Fall back to FMP sector/industry override
    if sector_override:
        override_lower = sector_override.lower().strip()
        if override_lower in _FMP_SECTOR_MAP:
            return _FMP_SECTOR_MAP[override_lower]

    return None


def get_sector_kpis(ticker: str, years: int = 5, sector_override: str | None = None) -> dict:
    """Auto-detect sector and compute sector-specific KPIs.

    Args:
        ticker: Stock ticker symbol
        years: Number of years of data
        sector_override: Optional FMP sector/industry string to override SIC detection

    Returns dict with sector name and KPI data, or empty if no
    sector-specific module exists.
    """
    subs = get_submissions(ticker)
    sic = subs.get("sic", "")
    subsector = _resolve_sector(sic, sector_override)

    if subsector is None:
        return {
            "sector": "general",
            "sic": sic,
            "sicDescription": subs.get("sicDescription", ""),
            "kpis": {},
            "message": "No sector-specific KPIs available for this SIC code. "
                       "Try passing ?sector=<industry> from FMP profile.",
        }

    facts_data = get_companyfacts(ticker)
    all_facts = facts_data.get("facts", {})
    # Merge all XBRL namespaces — custom bank/insurance extensions often hold
    # capital ratios (CET1, Tier1) that aren't in us-gaap.
    # us-gaap is loaded last so it takes priority over custom tags.
    gaap: dict = {}
    for ns, tags in all_facts.items():
        if ns != "us-gaap" and isinstance(tags, dict):
            gaap.update(tags)
    gaap.update(all_facts.get("us-gaap", {}))

    kpis = {}
    if subsector == "banking":
        from sec.sectors.banks import compute_bank_kpis
        kpis = compute_bank_kpis(gaap, years)
    elif subsector == "insurance":
        from sec.sectors.insurance import compute_insurance_kpis
        kpis = compute_insurance_kpis(gaap, years)
    elif subsector == "reits":
        from sec.sectors.reits import compute_reit_kpis
        kpis = compute_reit_kpis(gaap, years)
    elif subsector == "tech":
        from sec.sectors.tech import compute_tech_kpis
        kpis = compute_tech_kpis(gaap, years)
    elif subsector == "retail":
        from sec.sectors.retail import compute_retail_kpis
        kpis = compute_retail_kpis(gaap, years)
    elif subsector == "energy":
        from sec.sectors.energy import compute_energy_kpis
        kpis = compute_energy_kpis(gaap, years)
    elif subsector == "healthcare":
        from sec.sectors.healthcare import compute_healthcare_kpis
        kpis = compute_healthcare_kpis(gaap, years)
    elif subsector == "industrials":
        from sec.sectors.industrials import compute_industrial_kpis
        kpis = compute_industrial_kpis(gaap, years)
    elif subsector == "utilities":
        from sec.sectors.utilities import compute_utility_kpis
        kpis = compute_utility_kpis(gaap, years)
    elif subsector == "consumer_staples":
        from sec.sectors.consumer_staples import compute_consumer_staples_kpis
        kpis = compute_consumer_staples_kpis(gaap, years)
    elif subsector == "materials":
        from sec.sectors.materials import compute_materials_kpis
        kpis = compute_materials_kpis(gaap, years)

    return {
        "sector": subsector,
        "sic": sic,
        "sicDescription": subs.get("sicDescription", ""),
        "kpis": kpis,
    }
