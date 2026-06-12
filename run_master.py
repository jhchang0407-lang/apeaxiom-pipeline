#!/usr/bin/env python3
"""Master Automation — daily trigger for quarterly and full memo pipelines.

Logic:
  1. Quarterly: Check FMP earnings calendar (3 days ago) → match against
     S&P 500 ticker list (sp500_tickers.json) → run quarterly pipeline
     for each match.

  2. Full Memo: Check FMP 10-K filings (10 days ago) → match against
     S&P 500 ticker list → run full memo pipeline for each match.

Usage:
    python run_master.py                  # run both checks
    python run_master.py --quarterly      # only check earnings / quarterly
    python run_master.py --memo           # only check 10-K / full memo
    python run_master.py --dry-run        # show what would run, don't execute
"""

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta

import httpx

# Batch sizes — different concurrency per pipeline type
QUARTERLY_BATCH_SIZE = 5   # quarterly is lighter (web search + writer)
MEMO_BATCH_SIZE = 2        # full memo is heavier (SEC + LLM sections + valuation)

# ── Config ──────────────────────────────────────────────────────

from config.settings import FMP_API_KEY, FMP_BASE_URL

# Lookback windows
EARNINGS_LOOKBACK_DAYS = 3   # check earnings from 3 days ago
TENK_LOOKBACK_DAYS = 10      # check 10-K filings from 10 days ago

# S&P 500 ticker list (local JSON)
SP500_TICKERS_PATH = os.path.join(os.path.dirname(__file__), "sp500_tickers.json")


# ── FMP API ─────────────────────────────────────────────────────

def _fmp_get(url: str, params: dict, retries: int = 2) -> list | None:
    """GET an FMP endpoint with simple retry. Returns None if all attempts fail
    so a transient FMP outage degrades to 'no matches' instead of crashing
    the daily automation."""
    for attempt in range(retries + 1):
        try:
            resp = httpx.get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            if attempt < retries:
                print(f"   ⚠️ FMP request failed ({e}); retrying in 5s...")
                time.sleep(5)
            else:
                print(f"   ❌ FMP request failed after {retries + 1} attempts: {e}")
    return None


def fetch_earnings_tickers(lookback_days: int) -> set[str]:
    """Get tickers that reported earnings in the last N days from FMP.

    Scans a range rather than a single day to handle weekends/holidays.
    """
    now = datetime.now()
    to_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")          # yesterday
    from_date = (now - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    url = f"{FMP_BASE_URL}/earnings-calendar"
    params = {
        "from": from_date,
        "to": to_date,
        "apikey": FMP_API_KEY,
    }
    data = _fmp_get(url, params)
    if data is None:
        return set()
    print(f"   FMP earnings calendar: {len(data)} companies reported ({from_date} → {to_date})")
    return {item["symbol"].upper() for item in data if "symbol" in item}


def fetch_tenk_filers(lookback_days: int) -> set[str]:
    """Get tickers that filed 10-K in the last N days from FMP.

    Scans a range rather than a single day to handle weekends/holidays.
    """
    now = datetime.now()
    to_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    from_date = (now - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    url = f"{FMP_BASE_URL}/sec-filings-search/form-type"
    params = {
        "formType": "10-K",
        "from": from_date,
        "to": to_date,
        "limit": 500,
        "apikey": FMP_API_KEY,
    }
    data = _fmp_get(url, params)
    if data is None:
        return set()
    print(f"   FMP 10-K filers: {len(data)} companies filed ({from_date} → {to_date})")
    return {item["symbol"].upper() for item in data if "symbol" in item}


# ── S&P 500 Ticker List ────────────────────────────────────────

def load_sp500_tickers() -> set[str]:
    """Load S&P 500 ticker list from local JSON file."""
    with open(SP500_TICKERS_PATH) as f:
        return set(json.load(f))


# ── Batching ────────────────────────────────────────────────────

def run_in_batches(tickers: list[str], runner_fn, batch_size: int, dry_run: bool = False):
    """Run pipeline for tickers in batches, concurrently within each batch.

    Waits for the entire batch to finish before starting the next one.
    """
    total = len(tickers)
    for i in range(0, total, batch_size):
        batch = tickers[i : i + batch_size]
        batch_num = i // batch_size + 1
        total_batches = (total + batch_size - 1) // batch_size
        print(f"\n   ── Batch {batch_num}/{total_batches}: {', '.join(batch)} ──")

        if dry_run:
            for ticker in batch:
                runner_fn(ticker, dry_run=True)
        else:
            # Run batch concurrently — waits for ALL in batch to finish
            with concurrent.futures.ThreadPoolExecutor(max_workers=batch_size) as pool:
                futures = {pool.submit(runner_fn, t): t for t in batch}
                for future in concurrent.futures.as_completed(futures):
                    ticker = futures[future]
                    try:
                        future.result()
                    except Exception as e:
                        print(f"   ❌ Exception for {ticker}: {e}")

            # Brief pause between batches to let APIs breathe
            if i + batch_size < total:
                print(f"   ⏳ Waiting 10s before next batch...")
                time.sleep(10)


# ── Pipeline Triggers ───────────────────────────────────────────

# Use the project venv Python locally, or sys.executable in containers
VENV_PYTHON = os.path.join(os.path.dirname(__file__), "venv", "bin", "python")
PYTHON = VENV_PYTHON if os.path.exists(VENV_PYTHON) else sys.executable


def run_quarterly(ticker: str, dry_run: bool = False):
    """Run quarterly pipeline for a ticker."""
    cmd = [PYTHON, "run_quarterly.py", ticker, "--upload"]
    if dry_run:
        print(f"   DRY RUN: {' '.join(cmd)}")
        return
    print(f"   Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=os.path.dirname(__file__), capture_output=True, text=True)
    if result.returncode == 0:
        print(f"   ✅ Quarterly complete for {ticker}")
    else:
        print(f"   ❌ Quarterly failed for {ticker}")
        print(f"      stderr: {result.stderr[-500:]}" if result.stderr else "")


def run_full_memo(ticker: str, dry_run: bool = False):
    """Run full memo pipeline for a ticker via run_memo.py with --upload."""
    if dry_run:
        print(f"   DRY RUN: run_memo.py {ticker} --upload")
        return

    cmd = [PYTHON, "run_memo.py", ticker, "--upload"]
    print(f"   Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=os.path.dirname(__file__), capture_output=True, text=True)
    if result.returncode == 0:
        print(f"   ✅ Full memo complete for {ticker}")
        # Show last few lines of output for context
        lines = result.stdout.strip().split("\n")
        for line in lines[-5:]:
            print(f"      {line}")
    else:
        print(f"   ❌ Full memo failed for {ticker}")
        print(f"      stderr: {result.stderr[-500:]}" if result.stderr else "")


# ── Main ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Master Automation — daily trigger")
    parser.add_argument("--quarterly", action="store_true", help="Only run quarterly check")
    parser.add_argument("--memo", action="store_true", help="Only run full memo check")
    parser.add_argument("--dry-run", action="store_true", help="Show matches but don't run pipelines")
    args = parser.parse_args()

    # Default: run both
    run_both = not args.quarterly and not args.memo

    now = datetime.now()
    print(f"Master Automation — {now.strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    # Load the S&P 500 list from local JSON — used for both checks
    sp500 = load_sp500_tickers()
    print(f"\n   S&P 500 list: {len(sp500)} tickers")

    # ── Phase 1: Quarterly earnings check ──────────────────────
    # Runs FIRST and must fully complete before memos start
    if run_both or args.quarterly:
        print(f"\n📊 QUARTERLY CHECK — earnings in last {EARNINGS_LOOKBACK_DAYS} days")
        print(f"   (batch size: {QUARTERLY_BATCH_SIZE})")

        fmp_earnings = fetch_earnings_tickers(EARNINGS_LOOKBACK_DAYS)

        quarterly_matches = fmp_earnings & sp500
        if quarterly_matches:
            print(f"   🎯 Matches: {', '.join(sorted(quarterly_matches))}")
            run_in_batches(sorted(quarterly_matches), run_quarterly, QUARTERLY_BATCH_SIZE, dry_run=args.dry_run)
        else:
            print("   No matches — nothing to run")

        if run_both:
            print("\n   ✅ All quarterly runs complete. Starting full memos...")

    # ── Phase 2: Full memo 10-K check ────────────────────────
    # Runs AFTER all quarterly batches are done
    if run_both or args.memo:
        print(f"\n📝 FULL MEMO CHECK — 10-K filings in last {TENK_LOOKBACK_DAYS} days")
        print(f"   (batch size: {MEMO_BATCH_SIZE})")

        fmp_filers = fetch_tenk_filers(TENK_LOOKBACK_DAYS)

        memo_matches = fmp_filers & sp500
        if memo_matches:
            print(f"   🎯 Matches: {', '.join(sorted(memo_matches))}")
            run_in_batches(sorted(memo_matches), run_full_memo, MEMO_BATCH_SIZE, dry_run=args.dry_run)
        else:
            print("   No matches — nothing to run")

    print("\n" + "=" * 60)
    print("Master Automation complete.")


if __name__ == "__main__":
    main()
