"""FMP API Client — Async.

Kept for endpoints not available from SEC EDGAR:
- Analyst estimates (consensus data)
- Earnings surprises (estimates vs actual)
- Company profile (market data: price, market cap, beta)
- Peer financial data

Every FMP request in the memo pipeline (subject data, peer comps, quarterly)
funnels through ``fetch_fmp``, which applies a shared concurrency cap (rate
limiting) plus retry/backoff on transient failures.  This keeps peer-comp and
valuation tables fully populated instead of silently dropping cells when FMP
momentarily rate-limits or times out under the pipeline's concurrent load.
"""

import asyncio
import os
import random

import httpx

from config.settings import FMP_API_KEY, FMP_BASE_URL

# ── Rate limiting + retry ────────────────────────────────────────────
# FMP plans rate-limit on requests/sec.  A full run bursts the subject's ~15
# endpoints, each peer's 6 endpoints (× up to 12 peers), and the quarterly
# profile.  A shared semaphore bounds in-flight requests so we stop tripping the
# limit; transient failures that still slip through (429/5xx, timeouts,
# connection resets) are retried with exponential backoff + jitter.  Hard errors
# (401/403/404) raise immediately — retrying won't help.  Tunable via env.
FMP_MAX_CONCURRENCY = int(os.getenv("FMP_MAX_CONCURRENCY", "8"))
FMP_MAX_RETRIES = int(os.getenv("FMP_MAX_RETRIES", "4"))
FMP_TIMEOUT_S = float(os.getenv("FMP_TIMEOUT_S", "30"))
_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})

_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    """Lazily create the shared concurrency limiter (binds to the running loop)."""
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(FMP_MAX_CONCURRENCY)
    return _semaphore


async def fetch_fmp(
    client: httpx.AsyncClient,
    path: str,
    params: dict | None = None,
    *,
    retries: int = FMP_MAX_RETRIES,
) -> dict | list:
    """Make a rate-limited, retrying async FMP API request.

    A shared module-level semaphore caps concurrent FMP requests across the
    whole pipeline; transient failures (429/5xx, timeouts, connection resets)
    are retried with exponential backoff + jitter.  Hard errors (401/403/404)
    raise immediately.

    Args:
        client: httpx.AsyncClient instance
        path: API path (e.g., "/income-statement")
        params: Additional query parameters
        retries: Max retry attempts for transient failures

    Returns:
        Parsed JSON response

    Raises:
        httpx.HTTPError: on a hard error, or if all retries are exhausted (so
        callers can record/handle the failure rather than get a silent blank).
    """
    url = f"{FMP_BASE_URL}{path}"
    all_params = {"apikey": FMP_API_KEY}
    if params:
        all_params.update(params)

    sem = _get_semaphore()
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            async with sem:
                resp = await client.get(url, params=all_params, timeout=FMP_TIMEOUT_S)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code not in _TRANSIENT_STATUS:
                raise  # hard error (401/403/404/…) — don't waste retries
            last_exc = e
        except (httpx.TimeoutException, httpx.TransportError) as e:
            last_exc = e
        if attempt < retries:
            backoff = min(8.0, 0.5 * (2 ** attempt))  # 0.5, 1, 2, 4, 8 (capped)
            await asyncio.sleep(backoff + random.uniform(0.0, 0.25))
    # Exhausted retries — re-raise the last transient error.
    assert last_exc is not None
    raise last_exc


async def fetch_income_statement(
    client: httpx.AsyncClient, ticker: str, limit: int = 10,
) -> list:
    """Fetch annual income statements from FMP (fallback for SEC XBRL gaps)."""
    data = await fetch_fmp(client, "/income-statement", {
        "symbol": ticker, "period": "annual", "limit": str(limit),
    })
    return data if isinstance(data, list) else []


async def fetch_balance_sheet(
    client: httpx.AsyncClient, ticker: str, limit: int = 10,
) -> list:
    """Fetch annual balance sheet from FMP (fallback for SEC XBRL gaps)."""
    data = await fetch_fmp(client, "/balance-sheet-statement", {
        "symbol": ticker, "period": "annual", "limit": str(limit),
    })
    return data if isinstance(data, list) else []


async def fetch_cash_flow_statement(
    client: httpx.AsyncClient, ticker: str, limit: int = 10,
) -> list:
    """Fetch annual cash flow statements from FMP (fallback for SEC XBRL gaps)."""
    data = await fetch_fmp(client, "/cash-flow-statement", {
        "symbol": ticker, "period": "annual", "limit": str(limit),
    })
    return data if isinstance(data, list) else []


async def fetch_estimates(client: httpx.AsyncClient, ticker: str) -> list:
    """Fetch analyst consensus estimates."""
    return await fetch_fmp(client, "/analyst-estimates", {
        "symbol": ticker,
        "period": "annual",
    })


async def fetch_surprises(client: httpx.AsyncClient, ticker: str) -> list:
    """Fetch earnings surprises by querying earnings-calendar around filing dates.

    The legacy /earnings-surprises endpoint was deprecated Aug 2025.
    The broad /earnings-calendar endpoint has a 4000-record cap and often
    excludes the target ticker.  Instead we:
      1. Fetch the company's quarterly income statements (filing dates).
      2. Query /earnings-calendar with narrow 3-day windows around each
         filing date so the target company is always included.
    """
    import asyncio as _aio
    from datetime import datetime, timedelta

    ticker_up = ticker.upper()

    try:
        # Step 1: Get quarterly income statements → filing dates
        q_data = await fetch_fmp(client, "/income-statement", {
            "symbol": ticker, "period": "quarter", "limit": "16",
        })
        if not isinstance(q_data, list) or not q_data:
            return []

        filing_dates: list[str] = []
        for r in q_data[:12]:
            fd = r.get("filingDate") or r.get("date")
            if fd:
                filing_dates.append(fd)
        if not filing_dates:
            return []

        # Step 2: Query earnings-calendar with ±1 day around each filing date
        async def _query_window(fd_str: str) -> dict | None:
            try:
                d = datetime.strptime(fd_str, "%Y-%m-%d")
                start = (d - timedelta(days=1)).strftime("%Y-%m-%d")
                end = (d + timedelta(days=1)).strftime("%Y-%m-%d")
                data = await fetch_fmp(client, "/earnings-calendar", {
                    "from": start, "to": end,
                })
                if isinstance(data, list):
                    for rec in data:
                        if (rec.get("symbol") == ticker_up
                                and rec.get("epsActual") is not None):
                            return {
                                "date": rec.get("date"),
                                "epsActual": rec.get("epsActual"),
                                "epsEstimated": rec.get("epsEstimated"),
                                "revenueActual": rec.get("revenueActual"),
                                "revenueEstimated": rec.get("revenueEstimated"),
                                "symbol": rec.get("symbol"),
                            }
            except Exception:
                pass
            return None

        results = await _aio.gather(
            *[_query_window(fd) for fd in filing_dates[:8]],
            return_exceptions=True,
        )
        surprises = [r for r in results if isinstance(r, dict)]
        return sorted(surprises, key=lambda x: x.get("date", ""), reverse=True)
    except Exception:
        return []


async def fetch_key_metrics(client: httpx.AsyncClient, ticker: str, limit: int = 5) -> list:
    """Fetch key-metrics (ROIC, ROCE, invested capital, etc.) for a ticker.

    Used as fallback when SEC-derived ratios are missing.
    """
    data = await fetch_fmp(client, "/key-metrics", {
        "symbol": ticker, "period": "annual", "limit": str(limit),
    })
    return data if isinstance(data, list) else []


async def fetch_profile(client: httpx.AsyncClient, ticker: str) -> dict:
    """Fetch company profile with market data."""
    data = await fetch_fmp(client, "/profile", {"symbol": ticker})
    if isinstance(data, list) and len(data) > 0:
        return data[0]
    return data if isinstance(data, dict) else {}


async def fetch_peers(client: httpx.AsyncClient, ticker: str) -> list:
    """Fetch peer company financial data.

    Returns list of peer records with key metrics for benchmarking.
    """
    # Get peer stock list (FMP error responses are dicts, not lists)
    peer_list = await fetch_fmp(client, "/stock-peers", {"symbol": ticker})
    if not isinstance(peer_list, list) or not peer_list:
        return []

    first = peer_list[0]
    peers = first.get("peersList", []) if isinstance(first, dict) else []
    if not peers:
        return []

    # For each peer, fetch key metrics
    peer_data = []
    for peer_ticker in peers[:10]:  # Limit to 10 peers
        try:
            ratios = await fetch_fmp(
                client,
                "/ratios",
                {"symbol": peer_ticker, "limit": 1},
            )
            profile = await fetch_fmp(client, "/profile", {"symbol": peer_ticker})
            key_metrics = await fetch_fmp(
                client,
                "/key-metrics",
                {"symbol": peer_ticker, "limit": 1},
            )
            income = await fetch_fmp(
                client,
                "/income-statement",
                {"symbol": peer_ticker, "limit": 1},
            )

            if not ratios or not profile:
                continue

            p = profile[0] if isinstance(profile, list) else profile
            r = ratios[0] if isinstance(ratios, list) else ratios
            km = key_metrics[0] if isinstance(key_metrics, list) and key_metrics else {}
            inc = income[0] if isinstance(income, list) and income else {}

            peer_data.append({
                "symbol": peer_ticker,
                "companyName": p.get("companyName", ""),
                "industry": p.get("industry", ""),
                "sector": p.get("sector", ""),
                "mktCap": p.get("mktCap"),
                "price": p.get("price"),
                # Valuation multiples
                "peRatio": r.get("priceEarningsRatio") or km.get("peRatio"),
                "priceToBookRatio": r.get("priceToBookRatio"),
                "priceToSalesRatio": r.get("priceToSalesRatio"),
                "priceToFreeCashFlowsRatio": r.get("priceToFreeCashFlowsRatio"),
                "enterpriseValueOverEBITDA": km.get("enterpriseValueOverEBITDA")
                    or r.get("enterpriseValueMultiple"),
                "evToSales": km.get("evToSales"),
                # Profitability
                "grossProfitMargin": r.get("grossProfitMargin"),
                "operatingProfitMargin": r.get("operatingProfitMargin"),
                "netProfitMargin": r.get("netProfitMargin"),
                "returnOnEquity": r.get("returnOnEquity"),
                "returnOnAssets": r.get("returnOnAssets"),
                # Growth
                "revenueGrowth": inc.get("growthRevenue")
                    or r.get("revenueGrowth"),
                # Financials
                "revenue": inc.get("revenue"),
                "grossProfit": inc.get("grossProfit"),
                "ebitda": inc.get("ebitda"),
                "netIncome": inc.get("netIncome"),
                "eps": inc.get("eps"),
            })
        except Exception:
            continue

    return peer_data
