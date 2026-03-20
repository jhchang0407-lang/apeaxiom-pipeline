#!/usr/bin/env python3
"""Market Data → R2 uploader.

Replaces Google Sheets dependency for dashboard and daily price data.
Pulls directly from FMP API, calculates all metrics in Python, uploads to R2.

Outputs:
  Dashboard/  → 23-row × 503-ticker JSON (market cap, PE, PS, FCF, etc.)
  Daily Price/ → date × 503-ticker JSON  (252 trading days of closing prices)

Usage:
    python run_market_data.py              # run both
    python run_market_data.py --dashboard  # dashboard only
    python run_market_data.py --prices     # daily prices only
    python run_market_data.py --dry-run    # print without uploading
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any

import boto3
import httpx

_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_here, ".env"))
except ImportError:
    pass

from config.settings import (
    FMP_API_KEY, FMP_BASE_URL,
    CF_R2_ENDPOINT, CF_R2_ACCESS_KEY, CF_R2_SECRET_KEY, CF_R2_BUCKET,
)

# ── Config ───────────────────────────────────────────────────────
BATCH_SIZE = 10           # tickers processed concurrently per batch
RATE_LIMIT_DELAY = 1.0    # 1s between batches (was 50ms — too aggressive)
MAX_CONCURRENT = 10       # semaphore: max simultaneous FMP HTTP requests
PROFILE_BATCH_SIZE = 20   # symbols per batch-profile API call

# Lazy semaphore (created inside async context)
_REQUEST_SEM: asyncio.Semaphore | None = None

def _sem() -> asyncio.Semaphore:
    global _REQUEST_SEM
    if _REQUEST_SEM is None:
        _REQUEST_SEM = asyncio.Semaphore(MAX_CONCURRENT)
    return _REQUEST_SEM


# ── R2 Upload ────────────────────────────────────────────────────
def _r2_client():
    return boto3.client(
        "s3",
        endpoint_url=CF_R2_ENDPOINT,
        aws_access_key_id=CF_R2_ACCESS_KEY,
        aws_secret_access_key=CF_R2_SECRET_KEY,
    )

def _r2_key(prefix: str, filename: str) -> str:
    now = datetime.now(timezone.utc)
    mm  = now.strftime("%m")
    mmm = now.strftime("%b")
    dd  = now.strftime("%d")
    yy  = now.strftime("%y")
    yyyy = now.strftime("%Y")
    return f"{prefix}/{yyyy}/{mm}/{filename}"

def upload_to_r2(data: Any, prefix: str, filename: str, dry_run: bool = False) -> str:
    key = _r2_key(prefix, filename)
    if dry_run:
        print(f"  [DRY RUN] Would upload → {key} ({len(json.dumps(data))/1024:.1f} KB)")
        return key
    s3 = _r2_client()
    s3.put_object(
        Bucket=CF_R2_BUCKET,
        Key=key,
        Body=json.dumps(data).encode(),
        ContentType="application/json",
    )
    print(f"  ✅ Uploaded → {key}")
    return key


# ── FMP Fetchers ─────────────────────────────────────────────────
async def _get(client: httpx.AsyncClient, url: str, params: dict) -> dict | list | None:
    params["apikey"] = FMP_API_KEY
    endpoint = url.rsplit("/", 1)[-1]
    async with _sem():
        for attempt in range(3):
            try:
                r = await client.get(url, params=params, timeout=30)
                if r.status_code == 429:
                    wait = int(r.headers.get("Retry-After", "60"))
                    print(f"\n  ⚠  Rate limited ({endpoint}) — waiting {wait}s ...", flush=True)
                    await asyncio.sleep(wait)
                    continue
                r.raise_for_status()
                return r.json()
            except httpx.HTTPStatusError as e:
                print(f"\n  ✗ HTTP {e.response.status_code} for {endpoint}", flush=True)
                return None
            except Exception:
                return None
    return None

async def fetch_profile(client: httpx.AsyncClient, ticker: str) -> dict:
    data = await _get(client, f"{FMP_BASE_URL}/profile", {"symbol": ticker})
    if data and isinstance(data, list) and data:
        return data[0]
    return {}

async def fetch_profiles_batch(client: httpx.AsyncClient, tickers: list[str]) -> dict[str, dict]:
    """Fetch up to PROFILE_BATCH_SIZE profiles in one API call. Returns {ticker: profile_dict}."""
    data = await _get(client, f"{FMP_BASE_URL}/profile", {"symbol": ",".join(tickers)})
    out: dict[str, dict] = {}
    if data and isinstance(data, list):
        for item in data:
            sym = item.get("symbol", "")
            if sym:
                out[sym] = item
    # Individual fallback for any missing tickers
    missing = [t for t in tickers if t not in out]
    if missing:
        tasks = [fetch_profile(client, t) for t in missing]
        fallbacks = await asyncio.gather(*tasks)
        for t, p in zip(missing, fallbacks):
            if p:
                out[t] = p
    return out

async def fetch_income_quarterly(client: httpx.AsyncClient, ticker: str) -> list:
    data = await _get(client, f"{FMP_BASE_URL}/income-statement",
                      {"symbol": ticker, "period": "quarter", "limit": 5})
    return data if isinstance(data, list) else []

async def fetch_cashflow_quarterly(client: httpx.AsyncClient, ticker: str) -> list:
    data = await _get(client, f"{FMP_BASE_URL}/cash-flow-statement",
                      {"symbol": ticker, "period": "quarter", "limit": 5})
    return data if isinstance(data, list) else []

async def fetch_income_annual(client: httpx.AsyncClient, ticker: str) -> list:
    data = await _get(client, f"{FMP_BASE_URL}/income-statement",
                      {"symbol": ticker, "period": "annual", "limit": 3})
    return data if isinstance(data, list) else []

async def fetch_key_metrics_ttm(client: httpx.AsyncClient, ticker: str) -> dict:
    data = await _get(client, f"{FMP_BASE_URL}/key-metrics",
                      {"symbol": ticker, "period": "ttm", "limit": 1})
    if data and isinstance(data, list) and data:
        return data[0]
    return {}

async def fetch_historical_prices(client: httpx.AsyncClient, ticker: str) -> list:
    data = await _get(client, f"{FMP_BASE_URL}/historical-price-eod/full",
                      {"symbol": ticker, "limit": 252})
    # stable API returns plain list
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("historical", [])
    return []


# ── Metric Calculators ───────────────────────────────────────────
def _ttm_sum(quarterly: list, field: str) -> float | None:
    """Sum last 4 quarters for TTM value."""
    vals = [q.get(field) for q in quarterly[:4] if q.get(field) is not None]
    if len(vals) < 4:
        return None
    return sum(vals)

def calc_dashboard_metrics(
    ticker: str,
    profile: dict,
    income_q: list,
    cashflow_q: list,
    income_a: list,
    key_metrics: dict,
) -> dict:
    """Calculate all 23 dashboard metrics for one ticker."""
    p = profile
    km = key_metrics

    # stable API field names differ from v3
    market_cap    = p.get("marketCap") or km.get("marketCap")
    price         = p.get("price")
    div_per_share = p.get("lastDividend")
    # React multiplies by 100 for display, so store as decimal (not percent)
    div_yield     = round(div_per_share / price, 6) if div_per_share and price else None

    exchange      = p.get("exchange")
    sector        = p.get("sector")
    industry      = p.get("industry")
    ceo           = p.get("ceo")
    website       = p.get("website")
    employees     = p.get("fullTimeEmployees")
    city          = p.get("city")
    state         = p.get("state")
    ipo_date      = p.get("ipoDate")
    name          = p.get("companyName")

    # TTM Revenue
    rev_ttm = _ttm_sum(income_q, "revenue")

    # TTM PS (Price-to-Sales)
    shares = p.get("sharesOutstanding") or (market_cap / price if market_cap and price else None)
    ps_ttm = None
    if market_cap and rev_ttm and rev_ttm > 0:
        ps_ttm = round(market_cap / rev_ttm, 2)

    # TTM FCF
    fcf_ttm = _ttm_sum(cashflow_q, "freeCashFlow")

    # FCF Margin — store as decimal (React multiplies by 100 for display)
    fcf_margin = None
    if fcf_ttm is not None and rev_ttm and rev_ttm > 0:
        fcf_margin = round(fcf_ttm / rev_ttm, 4)

    # EPS TTM — sum quarterly epsDiluted (stable API uses camelCase D)
    eps_ttm = _ttm_sum(income_q, "epsDiluted")
    if eps_ttm is not None:
        eps_ttm = round(float(eps_ttm), 4)

    # TTM PE — derive from earningsYield (earningsYield = 1/PE) in key_metrics
    pe_ttm = None
    ey = km.get("earningsYield")
    if ey and float(ey) != 0:
        pe_ttm = round(1.0 / float(ey), 2)
    elif eps_ttm and price and price > 0 and eps_ttm != 0:
        pe_ttm = round(price / eps_ttm, 2)

    # 1 Yr Sales Growth (LY vs LY-1 from annual)
    sales_growth_1yr = None
    if len(income_a) >= 2:
        rev0 = income_a[0].get("revenue")
        rev1 = income_a[1].get("revenue")
        if rev0 and rev1 and rev1 != 0:
            # Decimal form — React multiplies by 100 for display
            sales_growth_1yr = round((rev0 - rev1) / abs(rev1), 4)

    # EPS Growth (LY vs LY-1)
    eps_growth = None
    if len(income_a) >= 2:
        eps0 = income_a[0].get("epsDiluted")
        eps1 = income_a[1].get("epsDiluted")
        if eps0 is not None and eps1 and eps1 != 0:
            # Decimal form — React multiplies by 100 for display
            eps_growth = round((eps0 - eps1) / abs(eps1), 4)

    # 1 Yr Price Change — calculated later from price history
    # (placeholder here, filled in after price fetch)

    return {
        "Market Cap":            market_cap,
        "TTM PE":                pe_ttm,
        "1 Yr Price Change (calc)": None,   # filled after price history fetch
        "TTM PS":                ps_ttm,
        "Sales (TTM)":           rev_ttm,
        "EPS Growth (LY)":       eps_growth,
        "1 Yr Sales Growth (LY)": sales_growth_1yr,
        "FCF":                   fcf_ttm,
        "FCF Margin":            fcf_margin,
        "Dividend":              div_per_share,
        "Dividend Yield":        div_yield,
        "Exchange":              exchange,
        "Sector":                sector,
        "Industry":              industry,
        "CEO":                   ceo,
        "Website":               website,
        "Employees":             employees,
        "City":                  city,
        "State":                 state,
        "IPO Date":              ipo_date,
        "Name":                  name,
        "EPS (TTM)":             eps_ttm,
    }


# ── Batch Fetcher ────────────────────────────────────────────────
async def fetch_ticker_data(
    client: httpx.AsyncClient,
    ticker: str,
    include_prices: bool = True,
    prefetched_profile: dict | None = None,
) -> dict:
    """Fetch all data for one ticker concurrently.

    If prefetched_profile is provided, skips the /profile API call (saves 1 call/ticker).
    """
    tasks: list = []
    fetch_profile_here = prefetched_profile is None
    if fetch_profile_here:
        tasks.append(fetch_profile(client, ticker))
    tasks += [
        fetch_income_quarterly(client, ticker),
        fetch_cashflow_quarterly(client, ticker),
        fetch_income_annual(client, ticker),
        fetch_key_metrics_ttm(client, ticker),
    ]
    if include_prices:
        tasks.append(fetch_historical_prices(client, ticker))

    results = await asyncio.gather(*tasks)

    offset = 0
    if fetch_profile_here:
        profile = results[0]
        offset = 1
    else:
        profile = prefetched_profile

    return {
        "profile":     profile,
        "income_q":    results[offset],
        "cashflow_q":  results[offset + 1],
        "income_a":    results[offset + 2],
        "key_metrics": results[offset + 3],
        "prices":      results[offset + 4] if include_prices else [],
    }


async def fetch_all_tickers(
    tickers: list[str],
    include_prices: bool = True,
) -> dict[str, dict]:
    """Fetch all tickers in parallel batches with rate-limit-safe concurrency."""
    results = {}
    total = len(tickers)
    async with httpx.AsyncClient() as client:

        # ── Step 1: Batch-fetch all profiles (1 call per 20 tickers) ──
        print(f"  Pre-fetching {total} profiles in batches of {PROFILE_BATCH_SIZE}...")
        all_profiles: dict[str, dict] = {}
        for i in range(0, total, PROFILE_BATCH_SIZE):
            batch = tickers[i:i + PROFILE_BATCH_SIZE]
            batch_profiles = await fetch_profiles_batch(client, batch)
            all_profiles.update(batch_profiles)
            if i + PROFILE_BATCH_SIZE < total:
                await asyncio.sleep(RATE_LIMIT_DELAY)
        ok = sum(1 for p in all_profiles.values() if p)
        print(f"  Profiles: {ok}/{total} fetched")

        # ── Step 2: Fetch financial data (income, cashflow, key-metrics, prices) ──
        for i in range(0, total, BATCH_SIZE):
            batch = tickers[i:i + BATCH_SIZE]
            batch_tasks = [
                fetch_ticker_data(
                    client, t,
                    include_prices=include_prices,
                    prefetched_profile=all_profiles.get(t),
                )
                for t in batch
            ]
            batch_results = await asyncio.gather(*batch_tasks)
            for ticker, data in zip(batch, batch_results):
                results[ticker] = data
            done = min(i + BATCH_SIZE, total)
            print(f"  Financial data: {done}/{total} tickers...", end="\r", flush=True)
            if i + BATCH_SIZE < total:
                await asyncio.sleep(RATE_LIMIT_DELAY)
    print()
    return results


# ── Dashboard Builder ─────────────────────────────────────────────
def build_dashboard_json(
    tickers: list[str],
    all_data: dict[str, dict],
) -> list[dict]:
    """Build dashboard JSON: list of rows, each row is a metric across all tickers."""

    # Calc metrics per ticker
    ticker_metrics: dict[str, dict] = {}
    for ticker in tickers:
        d = all_data.get(ticker, {})
        metrics = calc_dashboard_metrics(
            ticker,
            profile      = d.get("profile", {}),
            income_q     = d.get("income_q", []),
            cashflow_q   = d.get("cashflow_q", []),
            income_a     = d.get("income_a", []),
            key_metrics  = d.get("key_metrics", {}),
        )
        # 1 Yr Price Change from price history
        prices = d.get("prices", [])
        if prices and len(prices) >= 2:
            # prices[0] = most recent, prices[-1] = oldest in the 252-day window
            current = prices[0].get("close") or prices[0].get("adjClose")
            old     = prices[-1].get("close") or prices[-1].get("adjClose")
            if current and old and old != 0:
                metrics["1 Yr Price Change (calc)"] = round((current - old) / old * 100, 2)
        ticker_metrics[ticker] = metrics

    # Pivot: list of rows keyed by metric name
    metric_names = list(next(iter(ticker_metrics.values())).keys())
    rows = []
    for metric in metric_names:
        row = {"Ticker": metric}
        for ticker in tickers:
            row[ticker] = ticker_metrics[ticker].get(metric)
        rows.append(row)

    return rows


# ── Daily Price Builder ──────────────────────────────────────────
def build_price_json(
    tickers: list[str],
    all_data: dict[str, dict],
) -> list[dict]:
    """Build daily price JSON: list of rows by date, each row has all ticker prices."""

    # Collect all dates across all tickers
    date_set: set[str] = set()
    price_by_ticker: dict[str, dict[str, float]] = {}

    for ticker in tickers:
        prices = all_data.get(ticker, {}).get("prices", [])
        price_by_ticker[ticker] = {}
        for bar in prices:
            date = bar.get("date")
            close = bar.get("close") or bar.get("adjClose")
            if date and close:
                price_by_ticker[ticker][date] = close
                date_set.add(date)

    # Sort dates ascending (oldest first)
    sorted_dates = sorted(date_set)

    rows = []
    for date in sorted_dates:
        row = {"date": date}
        for ticker in tickers:
            row[ticker] = price_by_ticker[ticker].get(date)
        rows.append(row)

    return rows


# ── R2 Filename Builder ──────────────────────────────────────────
def _build_filename(prefix: str) -> tuple[str, str]:
    """Return (r2_prefix, filename) matching n8n naming convention."""
    now = datetime.now(timezone.utc)
    mm  = now.strftime("%m")
    mmm = now.strftime("%b")
    dd  = now.strftime("%d")
    yy  = now.strftime("%y")
    yyyy = now.strftime("%Y")

    if prefix == "dashboard":
        r2_prefix = "Dashboard"
        filename  = f"dashboard {mm}-{mmm} {dd}, {yyyy}-{yy}.json"
    else:
        r2_prefix = "Daily Price"
        filename  = f"DailyPrices {mm}-{mmm} {dd}, {yyyy}-{yy}.json"

    return r2_prefix, filename


# ── Main ─────────────────────────────────────────────────────────
async def main():
    parser = argparse.ArgumentParser(description="Fetch market data and upload to R2")
    parser.add_argument("--dashboard", action="store_true", help="Dashboard only")
    parser.add_argument("--prices",    action="store_true", help="Daily prices only")
    parser.add_argument("--dry-run",   action="store_true", help="Print without uploading")
    args = parser.parse_args()

    run_dashboard = args.dashboard or not args.prices
    run_prices    = args.prices    or not args.dashboard

    # Load S&P 500 tickers
    tickers_path = os.path.join(_here, "sp500_tickers.json")
    with open(tickers_path) as f:
        tickers = json.load(f)
    print(f"Loaded {len(tickers)} tickers")

    t0 = time.time()
    print(f"\nFetching data from FMP (batch size {BATCH_SIZE})...")
    all_data = await fetch_all_tickers(tickers, include_prices=run_prices)
    print(f"Fetch complete in {time.time()-t0:.0f}s")

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%m-%b %d, %Y-%y")

    if run_dashboard:
        print("\nBuilding dashboard JSON...")
        dashboard = build_dashboard_json(tickers, all_data)
        r2_prefix, filename = _build_filename("dashboard")
        upload_to_r2(dashboard, r2_prefix, filename, dry_run=args.dry_run)

    if run_prices:
        print("\nBuilding daily price JSON...")
        prices = build_price_json(tickers, all_data)
        r2_prefix, filename = _build_filename("prices")
        upload_to_r2(prices, r2_prefix, filename, dry_run=args.dry_run)

    print(f"\nDone in {time.time()-t0:.0f}s total")


if __name__ == "__main__":
    asyncio.run(main())
