"""Company profile from SEC EDGAR submissions data."""

from __future__ import annotations

from sec.client import get_submissions


# SIC code ranges to sector names
SIC_SECTORS = {
    (100, 999): "Agriculture",
    (1000, 1499): "Mining",
    (1500, 1799): "Construction",
    (2000, 3999): "Manufacturing",
    (4000, 4999): "Transportation & Utilities",
    (5000, 5199): "Wholesale Trade",
    (5200, 5999): "Retail Trade",
    (6000, 6799): "Finance & Insurance",
    (6800, 6999): "Real Estate",
    (7000, 8999): "Services",
    (9100, 9729): "Government",
    (9900, 9999): "Non-classifiable",
}

# More specific SIC mappings for sector-specific KPIs
SIC_SUBSECTORS = {
    # Banking & Financial Services
    (6020, 6029): "banking",   # national commercial banks, state banks
    (6035, 6036): "banking",   # savings institutions
    (6710, 6712): "banking",   # holding offices (bank holding companies)
    (6021, 6022): "banking",   # national/state commercial banks
    (6141, 6159): "banking",   # personal/business credit institutions

    # Insurance
    (6311, 6399): "insurance",
    (6411, 6411): "insurance",  # insurance agents/brokers

    # REITs / Real Estate
    (6500, 6599): "reits",
    (6798, 6798): "reits",     # real estate investment trusts

    # Tech — comprehensive coverage
    (3571, 3579): "tech",      # electronic computers & peripherals (AAPL, DELL, HPQ)
    (3661, 3679): "tech",      # communications equipment, semiconductors
    (3674, 3674): "tech",      # semiconductors (NVDA, AMD, INTC, QCOM)
    (5045, 5045): "tech",      # computers & peripherals wholesale
    (5065, 5065): "tech",      # electronic parts wholesale
    (5112, 5112): "tech",      # stationery/office supplies (sometimes tech distributors)
    (7371, 7379): "tech",      # computer services, software, data processing (MSFT, CRM, ORCL)
    (7372, 7372): "tech",      # prepackaged software
    (3577, 3577): "tech",      # computer peripheral equipment
    (3672, 3672): "tech",      # printed circuit boards
    (3825, 3825): "tech",      # instruments for measurement (some tech companies)
    (4813, 4813): "tech",      # telephone communications (sometimes VoIP/cloud comms)
    (4812, 4812): "tech",      # radiotelephone communications
    (4899, 4899): "tech",      # communications services NEC
    (5734, 5734): "tech",      # computer & software stores

    # Retail
    (5200, 5999): "retail",    # retail trade
    (5411, 5411): "retail",    # grocery stores
    (5912, 5912): "retail",    # drug stores

    # Energy — oil, gas, refining, oilfield services
    (1311, 1311): "energy",    # crude petroleum & natural gas
    (1381, 1389): "energy",    # oil/gas field services
    (2911, 2911): "energy",    # petroleum refining
    (1382, 1382): "energy",    # oil/gas field services NEC
    (5171, 5172): "energy",    # petroleum product wholesalers
    (4922, 4924): "energy",    # natural gas transmission/distribution
    (4610, 4619): "energy",    # pipelines NEC

    # Utilities
    (4911, 4991): "utilities",
    (4931, 4932): "utilities", # electric and gas services combined

    # Healthcare / Pharma
    (2830, 2836): "healthcare",  # pharmaceutical preparations
    (2860, 2869): "healthcare",  # industrial chemicals (some pharma-adjacent)
    (3841, 3851): "healthcare",  # surgical/medical instruments, ophthalmic
    (8000, 8099): "healthcare",  # health services
    (8731, 8734): "healthcare",  # commercial R&D / testing labs (biotech)

    # Industrials / Aerospace & Defense
    (3711, 3799): "industrials",  # motor vehicles, aircraft, ships, transportation equip
    (3721, 3728): "industrials",  # aircraft & parts
    (3761, 3769): "industrials",  # guided missiles, space vehicles
    (3812, 3812): "industrials",  # search/detection/navigation systems (defense electronics)
    (3559, 3569): "industrials",  # special industry machinery
    (3440, 3499): "industrials",  # fabricated metals
    (3510, 3549): "industrials",  # industrial machinery
    (3580, 3599): "industrials",  # misc industrial equipment
    (3610, 3699): "industrials",  # electronic/electrical equipment

    # Consumer Staples
    (2000, 2099): "consumer_staples",  # food & kindred products (PEP, GIS, K)
    (2100, 2199): "consumer_staples",  # tobacco products (MO, PM)
    (2840, 2844): "consumer_staples",  # soap, detergents, cleaning (PG, CL, CLX)

    # Materials / Chemicals
    (2810, 2819): "materials",  # industrial inorganic chemicals (LIN, APD)
    (2820, 2829): "materials",  # plastics materials, synthetics (DOW, DD)
    (2850, 2859): "materials",  # paints, varnishes, lacquers (SHW, PPG)
    (2870, 2899): "materials",  # agricultural chemicals, misc chemicals
    (3310, 3399): "materials",  # primary metal industries (NUE, STLD)
    (3241, 3299): "materials",  # stone, clay, glass (VMC, MLM)
    (2611, 2631): "materials",  # paper mills (IP, WRK)
    (2650, 2670): "materials",  # paperboard containers
}


def _parse_sic(sic_code: int | str) -> int:
    """Parse a SIC code defensively — API data can be non-numeric."""
    try:
        return int(sic_code) if sic_code else 0
    except (ValueError, TypeError):
        return 0


def _sic_to_sector(sic_code: int | str) -> str:
    """Map SIC code to broad sector name."""
    sic = _parse_sic(sic_code)
    for (low, high), sector in SIC_SECTORS.items():
        if low <= sic <= high:
            return sector
    return "Unknown"


def _sic_to_subsector(sic_code: int | str) -> str | None:
    """Map SIC code to specific subsector for sector-specific KPIs."""
    sic = _parse_sic(sic_code)
    for (low, high), subsector in SIC_SUBSECTORS.items():
        if low <= sic <= high:
            return subsector
    return None


def get_profile(ticker: str) -> dict:
    """Get company profile from SEC EDGAR submissions.

    Note: does NOT include market data (stock price, market cap).
    Those come from FMP's profile endpoint.
    """
    subs = get_submissions(ticker)

    sic = subs.get("sic", "")
    sic_desc = subs.get("sicDescription", "")
    sector = _sic_to_sector(sic)
    subsector = _sic_to_subsector(sic)

    # Fiscal year end (format: "0930" for September 30)
    fy_end = subs.get("fiscalYearEnd", "")
    fy_month = ""
    if fy_end and len(fy_end) == 4:
        month_num = int(fy_end[:2])
        months = [
            "", "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ]
        fy_month = months[month_num] if month_num <= 12 else ""

    # Get business address
    biz_addr = subs.get("addresses", {}).get("business", {})

    # Get recent filing info
    recent = subs.get("filings", {}).get("recent", {})
    filing_dates = recent.get("filingDate", [])
    latest_10k_date = ""
    latest_10q_date = ""
    for i, form in enumerate(recent.get("form", [])):
        if i >= len(filing_dates):
            break
        if form == "10-K" and not latest_10k_date:
            latest_10k_date = filing_dates[i]
        if form == "10-Q" and not latest_10q_date:
            latest_10q_date = filing_dates[i]
        if latest_10k_date and latest_10q_date:
            break

    # Exchange and tickers
    exchanges = subs.get("exchanges", [])
    tickers = subs.get("tickers", [])

    return {
        "symbol": ticker.upper(),
        "companyName": subs.get("name", ""),
        "cik": str(subs.get("cik", "")),
        "sic": sic,
        "sicDescription": sic_desc,
        "sector": sector,
        "subsector": subsector,
        "industry": sic_desc,
        "exchangeShortName": exchanges[0] if exchanges else "",
        "currency": "USD",  # SEC filings are in USD
        "country": biz_addr.get("stateOrCountry", "US"),
        "state": biz_addr.get("stateOrCountryDescription", ""),
        "city": biz_addr.get("city", ""),
        "zip": biz_addr.get("zipCode", ""),
        "fiscalYearEnd": fy_month,
        "fiscalYearEndRaw": fy_end,
        "latestAnnualFiling": latest_10k_date,
        "latestQuarterlyFiling": latest_10q_date,
        "entityType": subs.get("entityType", ""),
        "phone": subs.get("phone", ""),
        "website": "",  # Not in submissions data
        "description": "",  # Not in submissions data — would need to parse 10-K
        "isBank": subsector == "banking",
        "isInsurance": subsector == "insurance",
        "isReit": subsector == "reits",
    }
