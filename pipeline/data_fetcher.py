"""Async Data Fetcher — Stage 1 of the pipeline.

Fetches all data in parallel using asyncio.gather:
- SEC EDGAR: financials, quarterly, segments, profile, sector KPIs
  (called DIRECTLY via sec/ modules — no HTTP server needed)
- SEC Filings: 10-K and 10-Q full text (for research agents)
- FMP: analyst estimates, earnings surprises, market profile, peers

Returns a PipelineData object containing all raw data.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from config.settings import DEFAULT_ANNUAL_YEARS, DEFAULT_QUARTERLY_PERIODS

import httpx


@dataclass
class PipelineData:
    """Container for all fetched data."""

    ticker: str = ""

    # SEC EDGAR data (from direct module calls)
    sec_financials: dict = field(default_factory=dict)
    sec_quarterly: dict = field(default_factory=dict)
    sec_segments: dict = field(default_factory=dict)
    sec_profile: dict = field(default_factory=dict)
    sec_sector_kpis: dict = field(default_factory=dict)

    # SEC Filing text (for research agents)
    filing_10k: dict = field(default_factory=dict)
    filing_10q: dict = field(default_factory=dict)

    # FMP responses (kept endpoints)
    fmp_estimates: list = field(default_factory=list)
    fmp_surprises: list = field(default_factory=list)
    fmp_profile: dict = field(default_factory=dict)
    fmp_peers: list = field(default_factory=list)

    # FMP Financial Statements (fallback for SEC XBRL gaps)
    fmp_income_statement: list = field(default_factory=list)
    fmp_balance_sheet: list = field(default_factory=list)
    fmp_cash_flow: list = field(default_factory=list)

    # FMP Key Metrics (fallback for ROIC and other ratios)
    fmp_key_metrics: list = field(default_factory=list)

    # Timing
    fetch_duration_s: float = 0.0
    fetch_errors: list = field(default_factory=list)


# ── SEC MODULE WRAPPERS (sync → async via run_in_executor) ────

def _fetch_sec_financials_sync(ticker: str, years: int) -> dict:
    """Fetch annual financials directly from SEC EDGAR modules.

    Replicates what the FastAPI /financials/{ticker} endpoint returned:
    statements + ratios + growth + key_metrics + owner_earnings.
    """
    from sec.statements import get_annual_statements
    from sec.profile import get_profile
    from sec.ratios import (
        calculate_ratios,
        calculate_growth,
        calculate_key_metrics,
        calculate_owner_earnings,
    )

    # 1. Annual statements (IS, BS, CF + tag_map)
    stmts = get_annual_statements(ticker, years)

    is_data = stmts.get("income_statement", [])
    bs_data = stmts.get("balance_sheet", [])
    cf_data = stmts.get("cash_flow", [])
    tag_map = stmts.get("tag_map", {})

    # 2. Check if bank for ratio adjustments
    profile = get_profile(ticker)
    is_bank = profile.get("isBank", False)

    # 3. Ratios, growth, key metrics, owner earnings
    ratios = calculate_ratios(is_data, bs_data, cf_data, is_bank=is_bank)
    growth = calculate_growth(is_data, cf_data)
    key_metrics = calculate_key_metrics(is_data, bs_data, cf_data)
    owner_earnings = calculate_owner_earnings(is_data, cf_data)

    return {
        "ticker": ticker.upper(),
        "income_statement": is_data,
        "balance_sheet": bs_data,
        "cash_flow": cf_data,
        "ratios": ratios,
        "growth": growth,
        "key_metrics": key_metrics,
        "owner_earnings": owner_earnings,
        "tag_map": tag_map,
    }


def _fetch_sec_quarterly_sync(ticker: str, quarters: int) -> dict:
    """Fetch quarterly statements directly from SEC EDGAR modules.

    Replicates what the FastAPI /quarterly/{ticker} endpoint returned.
    """
    from sec.statements import get_quarterly_statements

    stmts = get_quarterly_statements(ticker, quarters)

    return {
        "ticker": ticker.upper(),
        "income_statement": stmts.get("income_statement", []),
        "balance_sheet": stmts.get("balance_sheet", []),
        "cash_flow": stmts.get("cash_flow", []),
    }


def _fetch_sec_segments_sync(ticker: str) -> dict:
    """Fetch revenue segments directly from SEC EDGAR modules."""
    from sec.segments import get_segments

    return get_segments(ticker)


def _fetch_sec_profile_sync(ticker: str) -> dict:
    """Fetch company profile directly from SEC EDGAR modules."""
    from sec.profile import get_profile

    return get_profile(ticker)


def _fetch_sec_sector_kpis_sync(ticker: str, years: int) -> dict:
    """Fetch sector-specific KPIs directly from SEC EDGAR modules."""
    from sec.sectors import get_sector_kpis

    return get_sector_kpis(ticker, years)


def _fetch_filing_text_sync(ticker: str, form: str) -> dict:
    """Fetch SEC filing text (edgartools is sync)."""
    from sec.filings import get_filing_text

    return get_filing_text(ticker, form)


# ── ASYNC WRAPPERS ────────────────────────────────────────────

async def _run_sync(func, *args) -> Any:
    """Run a synchronous function in the thread pool executor."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, func, *args)


async def _safe_fetch(coro, label: str, errors: list) -> Any:
    """Wrap a coroutine with error handling — log but don't crash."""
    try:
        return await coro
    except Exception as e:
        errors.append(f"{label}: {type(e).__name__}: {e}")
        return {} if "profile" in label or "kpi" in label else []


# ── MAIN FETCH FUNCTION ──────────────────────────────────────

async def fetch_all_data(
    ticker: str,
    years: int = DEFAULT_ANNUAL_YEARS,
    quarters: int = DEFAULT_QUARTERLY_PERIODS,
) -> PipelineData:
    """Fetch all data sources in parallel.

    This is Stage 1 of the pipeline. All fetches run concurrently
    using asyncio.gather for maximum speed.

    SEC data is fetched by calling the sec/ modules directly
    (no HTTP server needed). FMP data is fetched via HTTP.

    Args:
        ticker: Company ticker symbol
        years: Number of annual periods to fetch
        quarters: Number of quarterly periods to fetch

    Returns:
        PipelineData with all raw responses
    """
    from config.fmp_client import (
        fetch_estimates,
        fetch_surprises,
        fetch_profile,
        fetch_peers,
        fetch_income_statement,
        fetch_balance_sheet,
        fetch_cash_flow_statement,
        fetch_key_metrics,
    )

    ticker = ticker.upper()
    errors: list[str] = []
    t0 = time.time()

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            # SEC EDGAR (direct module calls via thread pool)
            _safe_fetch(
                _run_sync(_fetch_sec_financials_sync, ticker, years),
                "sec_financials", errors,
            ),
            _safe_fetch(
                _run_sync(_fetch_sec_quarterly_sync, ticker, quarters),
                "sec_quarterly", errors,
            ),
            _safe_fetch(
                _run_sync(_fetch_sec_segments_sync, ticker),
                "sec_segments", errors,
            ),
            _safe_fetch(
                _run_sync(_fetch_sec_profile_sync, ticker),
                "sec_profile", errors,
            ),
            _safe_fetch(
                _run_sync(_fetch_sec_sector_kpis_sync, ticker, years),
                "sec_sector_kpis", errors,
            ),
            # SEC Filing text (edgartools — sync, runs in thread pool)
            _safe_fetch(
                _run_sync(_fetch_filing_text_sync, ticker, "10-K"),
                "filing_10k", errors,
            ),
            _safe_fetch(
                _run_sync(_fetch_filing_text_sync, ticker, "10-Q"),
                "filing_10q", errors,
            ),
            # FMP (HTTP — kept endpoints)
            _safe_fetch(
                fetch_estimates(client, ticker),
                "fmp_estimates", errors,
            ),
            _safe_fetch(
                fetch_surprises(client, ticker),
                "fmp_surprises", errors,
            ),
            _safe_fetch(
                fetch_profile(client, ticker),
                "fmp_profile", errors,
            ),
            _safe_fetch(
                fetch_peers(client, ticker),
                "fmp_peers", errors,
            ),
            # FMP Financial Statements (fallback for SEC XBRL gaps)
            _safe_fetch(
                fetch_income_statement(client, ticker, limit=years + 2),
                "fmp_income_statement", errors,
            ),
            _safe_fetch(
                fetch_balance_sheet(client, ticker, limit=years + 2),
                "fmp_balance_sheet", errors,
            ),
            _safe_fetch(
                fetch_cash_flow_statement(client, ticker, limit=years + 2),
                "fmp_cash_flow", errors,
            ),
            # FMP Key Metrics (ROIC/ROCE fallback for banks/insurance)
            _safe_fetch(
                fetch_key_metrics(client, ticker, limit=years + 2),
                "fmp_key_metrics", errors,
            ),
        )

    duration = time.time() - t0

    return PipelineData(
        ticker=ticker,
        sec_financials=results[0] if isinstance(results[0], dict) else {},
        sec_quarterly=results[1] if isinstance(results[1], dict) else {},
        sec_segments=results[2] if isinstance(results[2], dict) else {},
        sec_profile=results[3] if isinstance(results[3], dict) else {},
        sec_sector_kpis=results[4] if isinstance(results[4], dict) else {},
        filing_10k=results[5] if isinstance(results[5], dict) else {},
        filing_10q=results[6] if isinstance(results[6], dict) else {},
        fmp_estimates=results[7] if isinstance(results[7], list) else [],
        fmp_surprises=results[8] if isinstance(results[8], list) else [],
        fmp_profile=results[9] if isinstance(results[9], dict) else {},
        fmp_peers=results[10] if isinstance(results[10], list) else [],
        fmp_income_statement=results[11] if isinstance(results[11], list) else [],
        fmp_balance_sheet=results[12] if isinstance(results[12], list) else [],
        fmp_cash_flow=results[13] if isinstance(results[13], list) else [],
        fmp_key_metrics=results[14] if isinstance(results[14], list) else [],
        fetch_duration_s=duration,
        fetch_errors=errors,
    )
