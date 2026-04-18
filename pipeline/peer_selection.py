"""Peer Selection Agent — LLM-powered comparable company selection.

Replaces the unreliable FMP /stock-peers endpoint with an AI agent that
selects 7-10 quality peer companies based on business model, scale, and
competitive dynamics.  Then fetches 5 years of FMP financial data for each.

Public entry points:
    select_peers()       → async, returns list of peer symbols + rationale
    fetch_peer_data()    → async, returns full peer financial data
    run_peer_pipeline()  → async, orchestrates selection + fetching
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from config.settings import FMP_API_KEY, FMP_BASE_URL, OPENAI_API_KEY


# ══════════════════════════════════════════════════════════════
# PEER SELECTION PROMPT
# ══════════════════════════════════════════════════════════════

PEER_SELECTION_SYSTEM = """\
You are a senior equity research analyst selecting peer companies for a
valuation comparable analysis.

SELECTION CRITERIA (in priority order):
1. DIRECT COMPETITORS — Companies competing for the same customers in
   the same markets.  Highest priority.
2. BUSINESS MODEL SIMILARITY — Similar revenue model (subscription vs
   transactional vs advertising), margin profiles, capital intensity.
3. END MARKET OVERLAP — Companies serving the same end markets even if
   the product differs.
4. SCALE COMPARABILITY — Revenue within 0.2x to 5x of the subject when
   possible.
5. GROWTH PROFILE MATCH — Mix of faster-growing and slower-growing peers
   to bracket the subject.
6. MARKET CAP MATCH — Strongly prefer companies with market cap within
   0.3x–3x of the subject.

RULES:
- Return exactly 12 peers (candidates). Quality matters — every peer must be
  a strong, defensible comparable.
- All tickers must be US-listed (NYSE / NASDAQ).  Use ADR tickers for
  foreign companies (e.g. TSM for TSMC, BABA for Alibaba).
- Never include the subject company itself, ETFs, indices, SPACs, or
  shell companies.
- Prefer companies with at least 3 years of public financial history.
- For multi-segment companies, select peers covering the most important
  competitive overlaps.
- Avoid dilutive picks — no tangential or weak comparisons.
"""


# ══════════════════════════════════════════════════════════════
# 1. SELECT PEERS VIA LLM
# ══════════════════════════════════════════════════════════════

async def select_peers(
    ticker: str,
    company_name: str,
    sector: str = "",
    industry: str = "",
    market_cap: float | None = None,
    description: str = "",
    model: str = "gpt-5-mini",
) -> list[dict]:
    """Use LLM to select 7-10 quality peer companies.

    Returns list of {"symbol": "MSFT", "rationale": "..."}.
    """
    import openai

    mkt_cap_str = f"${market_cap / 1e9:.1f}B" if market_cap else "N/A"

    user_msg = (
        f"Select 12 peer companies (candidates) for the following subject company:\n\n"
        f"Company: {company_name}\n"
        f"Ticker: {ticker}\n"
        f"Sector: {sector or 'N/A'}\n"
        f"Industry: {industry or 'N/A'}\n"
        f"Market Cap: {mkt_cap_str}\n"
        f"Description: {(description or 'N/A')[:500]}\n\n"
        f"Return the JSON peer list now."
    )

    client = openai.AsyncOpenAI(api_key=OPENAI_API_KEY)

    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": PEER_SELECTION_SYSTEM + '\n\nRespond with valid JSON only: {"peers": [{"symbol": "TICKER", "rationale": "reason"}, ...]}'},
            {"role": "user", "content": user_msg},
        ],
        response_format={"type": "json_object"},
        max_completion_tokens=8000,
    )

    content = response.choices[0].message.content
    parsed = json.loads(content)
    peers = parsed.get("peers", [])

    # Normalize and deduplicate
    seen = set()
    result = []
    for p in peers:
        sym = p.get("symbol", "").upper().strip()
        if sym and sym != ticker.upper() and sym not in seen:
            seen.add(sym)
            result.append({
                "symbol": sym,
                "rationale": p.get("rationale", ""),
            })

    return result[:12]


# ══════════════════════════════════════════════════════════════
# 2. FETCH FINANCIAL DATA FOR PEERS
# ══════════════════════════════════════════════════════════════

# Bank tickers that need special revenue handling
_BANK_TICKERS = frozenset({
    "JPM", "BAC", "WFC", "GS", "MS", "C", "USB", "PNC", "TFC",
    "SCHW", "COF", "BK", "STT", "FITB", "HBAN", "MTB", "KEY",
    "RF", "CFG", "ALLY",
})


async def _fetch_fmp(
    client: Any,
    path: str,
    params: dict | None = None,
) -> dict | list:
    """FMP API helper."""
    import httpx

    url = f"{FMP_BASE_URL}{path}"
    all_params = {"apikey": FMP_API_KEY}
    if params:
        all_params.update(params)
    resp = await client.get(url, params=all_params, timeout=30.0)
    resp.raise_for_status()
    return resp.json()


async def _fetch_single_peer(
    client: Any,
    symbol: str,
    years: int = 5,
) -> list[dict]:
    """Fetch all financial data for a single peer, returning list of year-records."""
    try:
        # Parallel fetch: key-metrics, ratios, growth, income-statement, cash-flow
        km_task = _fetch_fmp(client, "/key-metrics", {"symbol": symbol, "limit": years})
        ratios_task = _fetch_fmp(client, "/ratios", {"symbol": symbol, "limit": years})
        growth_task = _fetch_fmp(client, "/financial-growth", {"symbol": symbol, "limit": years})
        income_task = _fetch_fmp(client, "/income-statement", {"symbol": symbol, "limit": years})
        cashflow_task = _fetch_fmp(client, "/cash-flow-statement", {"symbol": symbol, "limit": years})
        profile_task = _fetch_fmp(client, "/profile", {"symbol": symbol})

        km, ratios, growth, income, cashflow, profile = await asyncio.gather(
            km_task, ratios_task, growth_task, income_task, cashflow_task,
            profile_task, return_exceptions=True,
        )

        # Normalize results
        km = km if isinstance(km, list) else []
        ratios = ratios if isinstance(ratios, list) else []
        growth = growth if isinstance(growth, list) else []
        income = income if isinstance(income, list) else []
        cashflow = cashflow if isinstance(cashflow, list) else []
        profile_data = profile[0] if isinstance(profile, list) and profile else {}

        is_bank = symbol.upper() in _BANK_TICKERS
        _sector = (profile_data.get("sector") or "").lower()
        _industry = (profile_data.get("industry") or "").lower()
        is_insurance = "insurance" in _industry
        _is_financial = is_bank or is_insurance or _sector == "financial services"

        # Build year-keyed records by fiscal year
        by_year: dict[str, dict] = {}

        for rec in km:
            fy = _extract_fy(rec)
            if not fy:
                continue
            by_year.setdefault(fy, {"symbol": symbol, "fiscal_year": fy})
            d = by_year[fy]
            # --- Fields that exist in key-metrics endpoint ---
            d["_km_market_cap_usd_b"] = _to_b(rec.get("marketCap"))  # Keep for EV recalc
            d["market_cap_usd_b"] = _to_b(rec.get("marketCap"))
            d["enterprise_value_usd_b"] = _to_b(rec.get("enterpriseValue"))
            d["ev_to_sales"] = _r(rec.get("evToSales"))
            d["ev_to_ebitda"] = _r(rec.get("evToEBITDA"))
            d["ev_to_fcf"] = _r(rec.get("evToFreeCashFlow"))
            d["ev_to_ocf"] = _r(rec.get("evToOperatingCashFlow"))
            d["earnings_yield_pct"] = _pct(rec.get("earningsYield"))
            d["fcf_yield_pct"] = _pct(rec.get("freeCashFlowYield"))
            d["price_to_fcf"] = _r(rec.get("priceToFreeCashFlowRatio"))
            d["net_debt_to_ebitda"] = _r(rec.get("netDebtToEBITDA"))
            # Return metrics live in key-metrics, NOT ratios
            d["roe_pct"] = _pct(rec.get("returnOnEquity"))
            d["roa_pct"] = _pct(rec.get("returnOnAssets"))
            # FMP key-metrics provides ROCE, not ROIC — close proxy for non-financials.
            # Null for banks/insurance/financials where capital return metrics are meaningless.
            d["roic_pct"] = None if _is_financial else _pct(rec.get("returnOnCapitalEmployed"))

        for rec in ratios:
            fy = _extract_fy(rec)
            if not fy:
                continue
            by_year.setdefault(fy, {"symbol": symbol, "fiscal_year": fy})
            d = by_year[fy]
            # --- Fields that exist in ratios endpoint ---
            d["gross_margin_pct"] = _pct(rec.get("grossProfitMargin"))
            d["operating_margin_pct"] = _pct(rec.get("operatingProfitMargin"))
            d["net_margin_pct"] = _pct(rec.get("netProfitMargin"))
            d["ebitda_margin_pct"] = _pct(rec.get("ebitdaMargin"))
            d["current_ratio"] = _r(rec.get("currentRatio"))
            d["debt_to_equity"] = _r(rec.get("debtToEquityRatio"))
            d["interest_coverage"] = _r(rec.get("interestCoverageRatio"))
            # Per-share data for self-computed valuation ratios
            d["tangible_book_value_per_share"] = _r(rec.get("tangibleBookValuePerShare"))
            d["book_value_per_share"] = _r(rec.get("bookValuePerShare"))
            d["dividend_per_share"] = _r(rec.get("dividendPerShare"))

        for rec in growth:
            fy = _extract_fy(rec)
            if not fy:
                continue
            by_year.setdefault(fy, {"symbol": symbol, "fiscal_year": fy})
            d = by_year[fy]
            d["revenue_growth_pct"] = _pct(rec.get("growthRevenue") or rec.get("revenueGrowth"))
            d["eps_diluted_growth_pct"] = _pct(rec.get("growthEPS") or rec.get("epsgrowth"))
            d["fcf_growth_pct"] = _pct(rec.get("growthFreeCashFlow") or rec.get("freeCashFlowGrowth"))

        for rec in income:
            fy = _extract_fy(rec)
            if not fy:
                continue
            by_year.setdefault(fy, {"symbol": symbol, "fiscal_year": fy})
            d = by_year[fy]

            # Banks: use grossProfit as revenue proxy
            if is_bank:
                raw_rev = rec.get("grossProfit") or rec.get("revenue") or 0
            else:
                raw_rev = rec.get("revenue") or 0

            d["revenue_usd_b"] = _to_b(raw_rev)
            d["ebitda_usd_b"] = _to_b(rec.get("ebitda"))
            d["net_income_usd_b"] = _to_b(rec.get("netIncome"))
            d["eps_diluted"] = rec.get("epsdiluted") or rec.get("eps")
            d["shares_diluted_m"] = _r((rec.get("weightedAverageShsOutDil") or 0) / 1e6)
            d["_raw_revenue"] = raw_rev  # Temp: used for ratio calculations
            # R&D from income statement
            rd = rec.get("researchAndDevelopmentExpenses") or 0
            d["rd_to_revenue_pct"] = _r(rd * 100 / raw_rev) if raw_rev and raw_rev > 0 else None
            # Operating income growth (for growth_comps table)
            d["operating_income_growth_pct"] = _pct(rec.get("growthOperatingIncome")) if rec.get("growthOperatingIncome") is not None else None

        # Cash-flow statement: SBC, CAPEX
        for rec in cashflow:
            fy = _extract_fy(rec)
            if not fy or fy not in by_year:
                continue
            d = by_year[fy]
            raw_rev = d.get("_raw_revenue") or 0
            sbc = abs(rec.get("stockBasedCompensation") or 0)
            capex = abs(rec.get("capitalExpenditure") or 0)
            d["sbc_to_revenue_pct"] = _r(sbc * 100 / raw_rev) if raw_rev > 0 else None
            d["capex_to_revenue_pct"] = _r(capex * 100 / raw_rev) if raw_rev > 0 else None

        # Add profile metadata and self-compute valuation ratios
        # from stock price + financial data (more reliable than FMP precomputed)
        stock_price = profile_data.get("price")
        for fy_data in by_year.values():
            fy_data["company_name"] = profile_data.get("companyName", symbol)
            fy_data["industry"] = profile_data.get("industry", "")
            fy_data["sector"] = profile_data.get("sector", "")

            shares_m = fy_data.get("shares_diluted_m") or 0

            if stock_price and stock_price > 0:
                # Fully diluted market cap: stock price × diluted shares
                if shares_m > 0:
                    fd_mkt_cap = stock_price * shares_m * 1e6
                    fy_data["market_cap_usd_b"] = _r(fd_mkt_cap / 1e9)

                    # Fully diluted EV: market cap + net debt
                    net_debt = fy_data.get("enterprise_value_usd_b")
                    ev_from_km = fy_data.get("enterprise_value_usd_b")
                    mkt_cap_from_km = (fy_data.get("_km_market_cap_usd_b") or 0) * 1e9
                    if ev_from_km and mkt_cap_from_km > 0:
                        # EV = mkt_cap + net_debt_equiv
                        # net_debt_equiv = EV(km) - mkt_cap(km)
                        net_debt_equiv = ev_from_km * 1e9 - mkt_cap_from_km
                        fy_data["enterprise_value_usd_b"] = _r((fd_mkt_cap + net_debt_equiv) / 1e9)

                # P/E from stock price / EPS
                eps = fy_data.get("eps_diluted")
                if eps and isinstance(eps, (int, float)) and eps > 0:
                    fy_data["price_to_earnings"] = _r(stock_price / eps)

                # P/B from stock price / book value per share
                bvps = fy_data.get("book_value_per_share")
                if bvps and bvps > 0:
                    fy_data["price_to_book"] = _r(stock_price / bvps)

                # P/TBV from stock price / tangible book value per share
                tbvps = fy_data.get("tangible_book_value_per_share")
                if tbvps and tbvps > 0:
                    fy_data["price_to_tangible_book"] = _r(stock_price / tbvps)

                # P/S from stock price / revenue per share
                rev_b = fy_data.get("revenue_usd_b")
                if rev_b and shares_m > 0:
                    rev_per_share = (rev_b * 1e9) / (shares_m * 1e6)
                    if rev_per_share > 0:
                        fy_data["price_to_sales"] = _r(stock_price / rev_per_share)

                # Dividend Yield from dividend per share / stock price
                dps = fy_data.get("dividend_per_share")
                if dps and dps > 0:
                    fy_data["dividend_yield_pct"] = _r(dps * 100 / stock_price)

                # P/FCF self-compute from FCF yield if FMP pre-computed is missing
                # fcf_yield_pct is stored as percentage (e.g. 7.22 for 7.22%).
                # P/FCF = 1 / (fcf_yield_pct / 100)
                if not fy_data.get("price_to_fcf"):
                    _fcf_yield = fy_data.get("fcf_yield_pct")
                    if _fcf_yield and _fcf_yield > 0:
                        fy_data["price_to_fcf"] = _r(100.0 / _fcf_yield)

            # Clean up temp fields
            fy_data.pop("_raw_revenue", None)
            fy_data.pop("_km_market_cap_usd_b", None)

        # Sort by fiscal year descending
        records = sorted(by_year.values(), key=lambda x: x.get("fiscal_year", ""), reverse=True)
        return records

    except Exception as e:
        return [{"symbol": symbol, "error": str(e)}]


async def fetch_peer_data(
    peer_symbols: list[str],
    years: int = 5,
) -> dict:
    """Fetch financial data for all peers concurrently.

    Returns dict with:
        by_symbol: {MSFT: {2024: {...}, 2023: {...}}, ...}
        latest: [{symbol, ...metrics...}, ...]
        peer_medians: {ev_to_ebitda: 15.2, ...}
    """
    import httpx

    async with httpx.AsyncClient() as client:
        tasks = [_fetch_single_peer(client, sym, years) for sym in peer_symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    by_symbol: dict[str, dict] = {}
    latest: list[dict] = []

    for peer_records in results:
        if isinstance(peer_records, Exception) or not peer_records:
            continue
        if isinstance(peer_records, list) and peer_records:
            sym = peer_records[0].get("symbol", "")
            if not sym:
                continue

            by_symbol[sym] = {}
            for rec in peer_records:
                fy = rec.get("fiscal_year", "")
                if fy:
                    by_symbol[sym][fy] = rec

            # Latest = first record (sorted desc)
            latest.append(peer_records[0])

    # Compute medians
    peer_medians = _compute_peer_medians(latest)

    return {
        "by_symbol": by_symbol,
        "latest": latest,
        "peer_medians": peer_medians,
    }


def _compute_peer_medians(latest: list[dict]) -> dict:
    """Compute median values across all peers for key metrics."""
    keys = [
        "revenue_usd_b", "gross_margin_pct", "operating_margin_pct",
        "ebitda_margin_pct", "net_margin_pct", "roic_pct", "roe_pct",
        "ev_to_sales", "ev_to_ebitda", "ev_to_fcf", "price_to_earnings",
        "price_to_fcf", "price_to_book", "price_to_tangible_book",
        "net_debt_to_ebitda", "revenue_growth_pct",
        "eps_diluted_growth_pct", "fcf_growth_pct", "dividend_yield_pct",
        "fcf_yield_pct",
    ]

    medians: dict[str, float | None] = {}
    for key in keys:
        vals = [
            p[key] for p in latest
            if p.get(key) is not None
            and isinstance(p.get(key), (int, float))
        ]
        if vals:
            vals.sort()
            n = len(vals)
            if n % 2 == 1:
                medians[key] = round(vals[n // 2], 2)
            else:
                medians[key] = round((vals[n // 2 - 1] + vals[n // 2]) / 2, 2)
        else:
            medians[key] = None

    return medians


def _infer_kpi_family(sector: str, industry: str) -> str:
    """Infer SEC sector-KPI module family for peer coverage scoring.

    We only score/refine peers for sectors where we have a dedicated SEC KPI module.
    """
    sec = (sector or "").lower().strip()
    ind = (industry or "").lower().strip()

    # Financial split
    if "reit" in ind:
        return "reits"
    if "insurance" in ind and sec in {"financial services", "financial"}:
        return "insurance"
    if "bank" in ind or sec in {"financial services", "financial"}:
        return "banking"

    # Sector-level mapping
    if sec == "technology":
        return "tech"
    if sec == "energy":
        return "energy"
    if sec == "healthcare":
        return "healthcare"
    if sec == "industrials":
        return "industrials"
    if sec in {"materials", "basic materials"}:
        return "materials"
    if sec == "utilities":
        return "utilities"
    if sec in {"consumer defensive", "consumer staples"}:
        return "consumer_staples"
    if sec in {"consumer cyclical", "consumer discretionary", "consumer disc"}:
        # Use retail KPI module when possible; otherwise skip KPI refinement.
        return "retail" if "retail" in ind else ""

    return ""


_KPI_REQUIRED_KEYS: dict[str, list[str]] = {
    # These keys are from SEC sector KPI modules (computedRatios/computedMetrics).
    # Keep them small and high-signal so we don't over-filter otherwise-good peers.
    "reits": ["ffoPerShare", "affoPerShare", "debtToEbitda"],
    "banking": ["netInterestMargin", "efficiencyRatio", "roe", "nplRatio"],
    "insurance": ["combinedRatio", "investmentYield", "roe"],
    "tech": ["ruleOf40", "rdIntensity", "sbcAsPercentOfRevenue"],
    "energy": ["fcfPerShare", "capexToRevenue", "debtToEbitda"],
    "healthcare": ["rdIntensity", "fcfMargin", "netDebtToEbitda"],
    "industrials": ["bookToBill", "backlogToRevenue", "roic"],
    "utilities": ["rateBaseGrowth", "capexIntensity", "debtToEbitda"],
    "retail": ["inventoryTurnover", "sameStoreSalesProxy", "grossMargin"],
    "consumer_staples": ["grossMargin", "fcfConversion", "debtToEbitda"],
    "materials": ["capexIntensity", "debtToEbitda", "roce"],
}


def _latest_sector_row(sector_kpis: dict | None) -> dict | None:
    if not sector_kpis or not isinstance(sector_kpis, dict):
        return None
    kpis = sector_kpis.get("kpis", sector_kpis)
    if not isinstance(kpis, dict):
        return None
    computed = kpis.get("computedRatios") or kpis.get("computedMetrics") or []
    if not isinstance(computed, list) or not computed:
        return None
    rows = [r for r in computed if isinstance(r, dict) and r.get("date")]
    if not rows:
        return None
    return max(rows, key=lambda r: r.get("date", ""))


async def _score_peers_by_sector_kpis(
    symbols: list[str],
    *,
    family: str,
    years: int = 5,
    sector_override: str | None = None,
    concurrency: int = 4,
) -> dict[str, dict]:
    """Return per-symbol KPI coverage stats via SEC sector modules."""
    try:
        from sec.sectors import get_sector_kpis
    except Exception:
        return {}

    required = _KPI_REQUIRED_KEYS.get(family) or []
    if not required:
        return {}

    sem = asyncio.Semaphore(concurrency)

    async def _one(sym: str):
        async with sem:
            sk = await asyncio.to_thread(get_sector_kpis, sym, years, sector_override)
            row = _latest_sector_row(sk)
            present = 0
            for k in required:
                v = row.get(k) if row else None
                if v is not None:
                    present += 1
            return sym, {
                "present": present,
                "required": len(required),
                "missing": [k for k in required if not row or row.get(k) is None],
            }

    out: dict[str, dict] = {}
    tasks = [_one(s.upper()) for s in symbols]
    for sym, stats in await asyncio.gather(*tasks, return_exceptions=False):
        out[sym] = stats
    return out


# ══════════════════════════════════════════════════════════════
# 3. ORCHESTRATOR — SELECT + FETCH
# ══════════════════════════════════════════════════════════════

async def run_peer_pipeline(
    ticker: str,
    company_name: str,
    sector: str = "",
    industry: str = "",
    market_cap: float | None = None,
    description: str = "",
    model: str = "gpt-5-mini",
    years: int = 5,
) -> dict:
    """Full peer pipeline: select peers via LLM → fetch their financials.

    Returns dict with:
        peers_selected: [{symbol, rationale}, ...]
        peer_data: {by_symbol, latest, peer_medians}
        peer_count: int
    """
    # Step 1: Select peers
    peers = await select_peers(
        ticker=ticker,
        company_name=company_name,
        sector=sector,
        industry=industry,
        market_cap=market_cap,
        description=description,
        model=model,
    )

    if not peers:
        return {
            "peers_selected": [],
            "peer_data": {"by_symbol": {}, "latest": [], "peer_medians": {}},
            "peer_count": 0,
        }

    symbols = [p["symbol"] for p in peers]

    # Step 2: Fetch financials for all peers + subject company
    # Include subject ticker so comp tables have subject row data
    all_symbols = [ticker.upper()] + [s for s in symbols if s.upper() != ticker.upper()]
    peer_data = await fetch_peer_data(all_symbols, years=years)

    # Step 3: Refine peers based on sector-KPI coverage (when applicable)
    family = _infer_kpi_family(sector, industry)
    if family and len(symbols) > 5:
        try:
            # If the SUBJECT doesn't report the sector KPIs, don't over-fit peer selection.
            subj_stats = await _score_peers_by_sector_kpis(
                [ticker.upper()],
                family=family,
                years=years,
                sector_override=sector or None,
            )
            if subj_stats.get(ticker.upper(), {}).get("present", 0) == 0:
                family = ""
        except Exception:
            family = family

    if family and len(symbols) > 5:
        try:
            stats = await _score_peers_by_sector_kpis(
                symbols,
                family=family,
                years=years,
                sector_override=sector or None,
            )
            idx = {s.upper(): i for i, s in enumerate(symbols)}
            ranked = sorted(
                [s.upper() for s in symbols],
                key=lambda s: (
                    stats.get(s, {}).get("present", 0),
                    -idx.get(s, 0),
                ),
                reverse=True,
            )
            chosen = ranked[:5]

            # Update peer list (keep original rationales, append KPI coverage)
            chosen_set = set(chosen)
            refined: list[dict] = []
            for p in peers:
                sym = (p.get("symbol") or "").upper()
                if sym in chosen_set:
                    st = stats.get(sym, {})
                    pres = st.get("present")
                    req = st.get("required")
                    miss = st.get("missing") or []
                    extra = f" (KPI coverage: {pres}/{req})" if req else ""
                    if miss and req and pres is not None and pres < req:
                        extra += f" missing: {', '.join(miss[:3])}"
                    refined.append({
                        "symbol": sym,
                        "rationale": (p.get("rationale") or "") + extra,
                    })
            peers = refined[:5]

            # Filter peer_data to subject + chosen peers
            keep = {ticker.upper(), *chosen}
            peer_data["latest"] = [
                r for r in peer_data.get("latest", [])
                if (r.get("symbol") or "").upper() in keep
            ]
            peer_data["by_symbol"] = {
                k: v for k, v in (peer_data.get("by_symbol") or {}).items()
                if k.upper() in keep
            }
            peer_data["peer_medians"] = _compute_peer_medians(peer_data.get("latest", []))
        except Exception:
            pass  # Best-effort refinement; never block peer selection.

    return {
        "peers_selected": peers,
        "peer_data": peer_data,
        "peer_count": len(peer_data["latest"]),
    }


# ══════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════

def _r(v: Any) -> float | None:
    """Round to 2 decimal places or return None."""
    if v is None:
        return None
    try:
        f = float(v)
        return round(f, 2) if f == f else None  # NaN check
    except (ValueError, TypeError):
        return None


def _pct(v: Any) -> float | None:
    """Convert fraction to percentage, round to 2 decimals."""
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:  # NaN
            return None
        # FMP sometimes returns as fraction, sometimes as percent
        if -1 < f < 1 and abs(f) < 0.99:
            return round(f * 100, 2)
        return round(f, 2)
    except (ValueError, TypeError):
        return None


def _to_b(v: Any) -> float | None:
    """Convert raw value to billions."""
    if v is None:
        return None
    try:
        f = float(v)
        return round(f / 1_000_000_000, 2) if f == f else None
    except (ValueError, TypeError):
        return None


def _extract_fy(rec: dict) -> str:
    """Extract fiscal year label from FMP record."""
    # FMP uses "calendarYear" or "fiscalYear" + "period"
    period = rec.get("period", "FY")
    cal_year = rec.get("calendarYear") or rec.get("fiscalYear")
    if cal_year:
        return f"{cal_year} {period}"

    date = rec.get("date", "")
    if date:
        match = re.match(r"(\d{4})", date)
        if match:
            return f"{match.group(1)} FY"
    return ""
