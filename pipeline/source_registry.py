"""
Source Registry — Python port of Source_Registry.js (v2)

Scans the fact sheet for all source fields, deduplicates URLs,
assigns citation IDs [1]-[N], builds [F] for financial filing data,
injects s1_identity from FMP profile into the fact sheet.

Input:
  - fact_sheet: dict from clean_quantitative_facts()
  - fmp_profile: dict from FMP /profile endpoint
  - sec_profile: dict from SEC /profile endpoint (for CIK)

Output:
  - enriched fact_sheet with s1_identity
  - source_registry: { url: { id, label, url } }
  - url_to_id: { url: "[N]" }
  - sources_appendix: formatted text block
  - financial_cite: "[F]"
  - filing_label: human-readable filing citation
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote


# ── URL EXTRACTION ────────────────────────────────────────────

def _extract_urls(obj: Any, results: set[str]) -> None:
    """Recursively extract URLs from any field matching /source/i."""
    if obj is None:
        return
    if isinstance(obj, list):
        for item in obj:
            _extract_urls(item, results)
        return
    if not isinstance(obj, dict):
        return

    for k, v in obj.items():
        if not isinstance(k, str):
            continue
        if isinstance(v, str) and (
            re.search(r"source", k, re.IGNORECASE) or "http://" in v or "https://" in v
        ):
            urls = re.findall(r"https?://[^\s,;\"')\]]+", v)
            for u in urls:
                # Clean tracking params and trailing punctuation
                u = re.sub(r"\?utm_source=\w+$", "", u)
                u = u.rstrip(". \t")
                results.add(u)
        else:
            _extract_urls(v, results)


# ── AUTO-LABEL ────────────────────────────────────────────────

def _auto_label(url: str, company_name: str) -> str:
    """Generate a human-readable label for a URL."""
    if "sec.gov" in url:
        if "def14a" in url.lower():
            return f"{company_name} Proxy Statement (DEF 14A)"
        if re.search(r"8.?k", url, re.IGNORECASE):
            return f"{company_name} Current Report (8-K)"
        if any(x in url for x in ("form4", "F345", "wk-form4")):
            return f"{company_name} Insider Transaction (Form 4)"
        if re.search(r"\w+-\d{8}", url) or "10-K" in url:
            return f"{company_name} Annual Report (10-K)"
        return f"{company_name} SEC Filing"

    if "nasdaq.com" in url and "transcript" in url:
        return f"{company_name} Earnings Call Transcript — Nasdaq"
    if "seekingalpha.com" in url and "transcript" in url:
        return f"{company_name} Earnings Call Transcript — Seeking Alpha"
    if "newsroom" in url:
        return f"{company_name} Press Release"
    if "britannica.com" in url:
        return "Company History — Encyclopædia Britannica"
    if "businesswire.com" in url:
        return "Industry Market Data — IDC via BusinessWire"
    if "canalys.com" in url:
        return "Smartphone Market Analysis — Canalys"
    if "samsung.com" in url:
        return "Samsung Electronics Financial Results"
    if "huawei.com" in url:
        return "Huawei Annual Report"

    # Fallback: domain name
    m = re.match(r"https?://(?:www\.)?([^/]+)", url)
    domain = m.group(1) if m else "Unknown"
    return f"Source — {domain}"


# ══════════════════════════════════════════════════════════════
# FMP PRICING INJECTION
# ══════════════════════════════════════════════════════════════

def _inject_fmp_valuation(fact_sheet: dict, fmp: dict) -> None:
    """Compute current valuation multiples from FMP price + SEC financials.

    SEC filings don't include market price, so valuation ratios like P/E,
    EV/EBITDA, EV/Sales are null from SEC alone.  FMP profile gives us the
    current price & market cap which we combine with SEC net income, EBITDA,
    revenue, FCF to compute these ratios for the latest year.
    """
    price = fmp.get("price")
    mkt_cap = fmp.get("marketCap") or fmp.get("mktCap")
    if not price or not mkt_cap:
        return

    meta = fact_sheet.get("_meta", {})
    latest = meta.get("latest_annual_year", "")
    raw = meta.get("_raw", {})

    inc = raw.get("s11_income_statement") or fact_sheet.get("s11_income_statement", {})
    cf = raw.get("s11_cash_flow") or fact_sheet.get("s11_cash_flow", {})
    bal = raw.get("s11_balance_sheet") or fact_sheet.get("s11_balance_sheet", {})

    def _n(d: dict, key: str) -> float | None:
        v = d.get(key, {}).get(latest) if isinstance(d.get(key), dict) else None
        if v is None or v == "NM":
            return None
        try:
            return float(v)
        except (ValueError, TypeError):
            return None

    # Raw financial values from SEC
    revenue = _n(inc, "revenue_usd_m")
    net_income = _n(inc, "net_income_usd_m")
    ebitda = _n(inc, "ebitda_usd_m")
    eps = _n(inc, "eps_diluted")
    fcf = _n(cf, "free_cash_flow_usd_m") or _n(cf, "fcf_usd_m")
    total_debt = _n(bal, "total_debt_usd_m")
    cash = _n(bal, "cash_and_equivalents_usd_m") or _n(bal, "cash_and_short_term_usd_m")
    total_equity = _n(bal, "total_equity_usd_m")
    shares = _n(fact_sheet.get("s5_share_data", {}), "shares_diluted_millions")

    mkt_cap_m = mkt_cap / 1_000_000  # to millions
    mkt_cap_b = mkt_cap / 1_000_000_000  # to billions

    # Enterprise value = mkt_cap + total_debt - cash
    net_debt = ((total_debt or 0) - (cash or 0))
    ev_m = mkt_cap_m + net_debt
    ev_b = ev_m / 1000

    # Compute multiples
    pe = round(price / eps, 2) if eps and eps > 0 else None
    ev_ebitda = round(ev_m / ebitda, 2) if ebitda and ebitda > 0 else None
    ev_sales = round(ev_m / revenue, 2) if revenue and revenue > 0 else None
    ev_fcf = round(ev_m / fcf, 2) if fcf and fcf > 0 else None
    p_fcf = round(mkt_cap_m / fcf, 2) if fcf and fcf > 0 else None
    p_sales = round(mkt_cap_m / revenue, 2) if revenue and revenue > 0 else None
    p_book = None
    bvps_raw = _n(bal, "book_value_per_share")
    if bvps_raw and bvps_raw > 0:
        p_book = round(price / bvps_raw, 2)
    elif total_equity and shares and shares > 0:
        bvps_calc = total_equity / shares
        if bvps_calc > 0:
            p_book = round(price / bvps_calc, 2)

    fcf_yield = round((fcf / mkt_cap_m) * 100, 2) if fcf and fcf > 0 else None
    earnings_yield = round((net_income / mkt_cap_m) * 100, 2) if net_income and net_income > 0 else None
    dividend_yield = None
    div = fmp.get("lastDividend") or fmp.get("lastDiv")
    if div and div > 0 and price:
        dividend_yield = round((div / price) * 100, 2)

    # Inject into s13_valuation for the latest year
    val = fact_sheet.get("s13_valuation") or fact_sheet.get("s12_valuation", {})

    def _set_latest(d: dict, key: str, value):
        """Set value for latest year in a year-keyed dict."""
        if value is not None:
            if key not in d:
                d[key] = {}
            if isinstance(d[key], dict):
                d[key][latest] = value

    _set_latest(val, "market_cap_usd_b", round(mkt_cap_b, 2))
    _set_latest(val, "enterprise_value_usd_b", round(ev_b, 2))
    _set_latest(val, "ev_to_sales", ev_sales)
    _set_latest(val, "ev_to_ebitda", ev_ebitda)
    _set_latest(val, "ev_to_fcf", ev_fcf)
    _set_latest(val, "price_to_earnings", pe)
    _set_latest(val, "price_to_fcf", p_fcf)
    _set_latest(val, "price_to_sales", p_sales)
    _set_latest(val, "price_to_book", p_book)
    _set_latest(val, "earnings_yield_pct", earnings_yield)
    _set_latest(val, "fcf_yield_pct", fcf_yield)
    _set_latest(val, "dividend_yield_pct", dividend_yield)

    # Also inject into raw
    if "_raw" in meta:
        raw_val = raw.get("s13_valuation") or raw.get("s12_valuation", {})
        for k in ("market_cap_usd_b", "enterprise_value_usd_b", "ev_to_sales",
                   "ev_to_ebitda", "ev_to_fcf", "price_to_earnings", "price_to_fcf",
                   "price_to_sales", "price_to_book", "earnings_yield_pct",
                   "fcf_yield_pct", "dividend_yield_pct"):
            v = val.get(k, {}).get(latest) if isinstance(val.get(k), dict) else None
            if v is not None:
                _set_latest(raw_val, k, v)

    # Store current price data in s13 for assembly/scorecard
    val["_current_price"] = price
    val["_current_market_cap_b"] = round(mkt_cap_b, 2)
    val["_current_ev_b"] = round(ev_b, 2)
    val["_current_pe"] = pe
    val["_current_ev_ebitda"] = ev_ebitda
    val["_current_p_fcf"] = p_fcf
    val["_current_fcf_yield_pct"] = fcf_yield
    val["_current_dividend_yield_pct"] = dividend_yield

    fact_sheet["s13_valuation"] = val


# ══════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════

def build_source_registry(
    fact_sheet: dict,
    fmp_profile: dict | None = None,
    sec_profile: dict | None = None,
    filing_10k: dict | None = None,
    filing_10q: dict | None = None,
) -> dict:
    """Build the source registry and inject s1_identity.

    Args:
        fact_sheet: Quantitative fact sheet (will be mutated)
        fmp_profile: FMP /profile response (market data)
        sec_profile: SEC /profile response (CIK, SIC)
        filing_10k: 10-K filing text dict (for citation metadata)
        filing_10q: 10-Q filing text dict (for citation metadata)

    Returns:
        Dict with keys:
            fact_sheet, source_registry, url_to_id,
            sources_appendix, financial_cite, filing_label
    """
    fmp = fmp_profile or {}
    sec = sec_profile or {}
    meta = fact_sheet.get("_meta", {})

    # ── Inject s1_identity into fact sheet ────────────────────
    fact_sheet["s1_identity"] = {
        "company_name": fmp.get("companyName", sec.get("name", "")),
        "ticker": fmp.get("symbol", meta.get("symbol", "")),
        "exchange": fmp.get("exchangeShortName", fmp.get("exchange", "")),
        "sector": fmp.get("sector", sec.get("sector", "")),
        "industry": fmp.get("industry", sec.get("industry", "")),
        "country": fmp.get("country", sec.get("stateOfIncorporation", "")),
        "city": fmp.get("city", ""),
        "state": fmp.get("state", sec.get("stateOfIncorporation", "")),
        "ceo": fmp.get("ceo", ""),
        "employee_count": fmp.get("fullTimeEmployees") or None,
        "ipo_date": fmp.get("ipoDate") or None,
        "website": fmp.get("website", ""),
        "description": fmp.get("description", ""),
        "cik": fmp.get("cik") or sec.get("cik") or None,
        "source": "Company SEC filings",

        # v2: Market data
        "price": fmp.get("price"),
        "market_cap": fmp.get("marketCap") or fmp.get("mktCap"),
        "beta": fmp.get("beta"),
        "last_dividend": fmp.get("lastDividend") or fmp.get("lastDiv"),
        "range": fmp.get("range"),  # "95.77-120.70" (52-week)
        "change": fmp.get("change") or fmp.get("changes"),
        "change_pct": fmp.get("changePercentage"),
        "volume": fmp.get("volume") or fmp.get("volAvg"),
        "average_volume": fmp.get("averageVolume") or fmp.get("volAvg"),
        "currency": fmp.get("currency", "USD"),
    }

    ident = fact_sheet["s1_identity"]

    # ── Inject FMP price-derived valuation multiples ──────────
    # SEC doesn't have market price; FMP profile gives us current
    # price/mktcap which we combine with SEC financial data to
    # compute P/E, EV/EBITDA, EV/Sales, P/FCF for the latest year.
    _inject_fmp_valuation(fact_sheet, fmp)

    # ── [F] Financial filing citation ─────────────────────────
    ticker = ident["ticker"]
    company_name = ident["company_name"]
    years = meta.get("annual_years", [])
    quarters = meta.get("quarterly_periods", [])
    cik = ident.get("cik")

    first_year = years[0].replace(" FY", "") if years else ""
    last_year = years[-1].replace(" FY", "") if years else ""
    latest_q = quarters[0] if quarters else ""

    filing_label = (
        f"{company_name} ({ticker}) Annual Reports (10-K) "
        f"FY{first_year}–FY{last_year}"
    )
    if latest_q:
        filing_label += f" and Quarterly Reports (10-Q) through {latest_q}"

    if cik:
        filing_url = (
            f"https://www.sec.gov/cgi-bin/browse-edgar?"
            f"action=getcompany&CIK={cik}&type=10-K"
        )
    else:
        filing_url = (
            f"https://www.sec.gov/cgi-bin/browse-edgar?"
            f"company={quote(ticker)}&type=10-K"
        )

    # ── Extract every URL from source fields ──────────────────
    url_set: set[str] = set()
    _extract_urls(fact_sheet, url_set)

    # ── Build numbered registry ───────────────────────────────
    registry: dict[str, dict] = {}
    for idx, url in enumerate(sorted(url_set), start=1):
        registry[url] = {
            "id": idx,
            "label": _auto_label(url, company_name),
            "url": url,
        }

    # ── URL → "[N]" lookup ────────────────────────────────────
    url_to_id = {url: f"[{entry['id']}]" for url, entry in registry.items()}

    # ── Structured named sources ──────────────────────────────
    named_sources: dict[str, dict] = {
        "F": {
            "id": "F",
            "label": filing_label,
            "url": filing_url,
            "type": "financial_filings",
        }
    }

    # ── Sources appendix text ─────────────────────────────────
    lines = [f"[F] {filing_label}", f"    {filing_url}", ""]

    # Add specific filing section citations from 10-K/10-Q text
    f10k = filing_10k or {}
    f10q = filing_10q or {}
    if f10k.get("business") or f10k.get("mda") or f10k.get("risk_factors"):
        tenk_date = f10k.get("filing_date", "")
        tenk_period = f10k.get("period", "")
        date_str = f" (filed {tenk_date})" if tenk_date else ""
        period_str = f" for {tenk_period}" if tenk_period else ""
        f1_label = (
            f"{company_name} ({ticker}) 10-K Annual Report{period_str}{date_str} — "
            f"Items 1, 1A, 7, 8 (Business Description, Risk Factors, MD&A, Financial Statements)"
        )
        named_sources["F1"] = {
            "id": "F1",
            "label": f1_label,
            "url": filing_url,
            "type": "10k_sections",
        }
        lines.append(f"[F1] {f1_label}")
        lines.append("")
    if f10q.get("mda") or f10q.get("risk_factors"):
        tenq_date = f10q.get("filing_date", "")
        tenq_period = f10q.get("period", "")
        date_str = f" (filed {tenq_date})" if tenq_date else ""
        period_str = f" for {tenq_period}" if tenq_period else ""
        f2_label = (
            f"{company_name} ({ticker}) 10-Q Quarterly Report{period_str}{date_str} — "
            f"Items 1, 1A, 2 (Financial Statements, Risk Factors, MD&A)"
        )
        named_sources["F2"] = {
            "id": "F2",
            "label": f2_label,
            "url": filing_url,
            "type": "10q_sections",
        }
        lines.append(f"[F2] {f2_label}")
        lines.append("")

    # Add FMP market data source
    if fmp.get("price") or fmp.get("marketCap"):
        named_sources["M"] = {
            "id": "M",
            "label": "Market data (price, market cap, volume, beta) sourced from Financial Modeling Prep API as of report date",
            "url": None,
            "type": "market_data",
        }
        lines.append(
            f"[M] Market data (price, market cap, volume, beta) sourced from "
            f"Financial Modeling Prep API as of report date"
        )
        lines.append("")

    # Add peer data source if peers exist
    peer_bench = fact_sheet.get("s12_peer_benchmarking", {})
    if peer_bench.get("peers_full") or peer_bench.get("profitability_comps"):
        named_sources["P"] = {
            "id": "P",
            "label": "Peer financial data compiled from SEC filings and FMP API for comparable company analysis",
            "url": None,
            "type": "peer_data",
        }
        lines.append(
            f"[P] Peer financial data compiled from SEC filings and FMP API "
            f"for comparable company analysis"
        )
        lines.append("")

    for entry in sorted(registry.values(), key=lambda e: e["id"]):
        lines.append(f"[{entry['id']}] {entry['label']}")
        lines.append(f"    {entry['url']}")
        lines.append("")

    return {
        "fact_sheet": fact_sheet,
        "source_registry": registry,
        "named_sources": named_sources,
        "url_to_id": url_to_id,
        "sources_appendix": "\n".join(lines),
        "financial_cite": "[F]",
        "filing_label": filing_label,
    }
