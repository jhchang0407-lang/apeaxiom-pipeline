#!/usr/bin/env python3
"""Master Automation — daily trigger for quarterly and full memo pipelines.

Replaces the n8n "Master Automation" workflow.

Logic:
  1. Quarterly: Check FMP earnings calendar (3 days ago) → match against
     "Earnings Ticker" watchlist from Google Sheets → run quarterly pipeline
     for each match.

  2. Full Memo: Check FMP 10-K filings (10 days ago) → match against
     S&P 500 list from Google Sheets ("Test" tab) → trigger full memo
     pipeline for each match via Modal webhook.

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

import gspread
import httpx

# Batch sizes — different concurrency per pipeline type
QUARTERLY_BATCH_SIZE = 5   # quarterly is lighter (web search + writer)
MEMO_BATCH_SIZE = 2        # full memo is heavier (SEC + LLM sections + valuation)

# ── Config ──────────────────────────────────────────────────────

from config.settings import FMP_API_KEY, FMP_BASE_URL

# Lookback windows
EARNINGS_LOOKBACK_DAYS = 3   # check earnings from 3 days ago
TENK_LOOKBACK_DAYS = 10      # check 10-K filings from 10 days ago

# Google Sheets
SPREADSHEET_ID = "1C_zvwhm-UHbBCvVFXYyz1rWMsi3EdhxXGQ6B0qbzcyc"
SP500_TAB = "Earnings Ticker"                # gid=1509867633 — full S&P 500 list

GOOGLE_CREDS_PATH = os.getenv(
    "GOOGLE_SERVICE_ACCOUNT_JSON",
    os.path.join(os.path.dirname(__file__), "..", "openclaw", "credentials", "google-service-account.json"),
)

# Modal webhook for full memos (from modal_app.py)
MODAL_MEMO_WEBHOOK = os.getenv("MODAL_MEMO_WEBHOOK", "")


# ── FMP API ─────────────────────────────────────────────────────

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
    resp = httpx.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
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
    resp = httpx.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    print(f"   FMP 10-K filers: {len(data)} companies filed ({from_date} → {to_date})")
    return {item["symbol"].upper() for item in data if "symbol" in item}


# ── Google Sheets ───────────────────────────────────────────────

def get_sheets_client():
    """Authenticate with Google Sheets using service account.

    Supports two modes:
      - File path: GOOGLE_SERVICE_ACCOUNT_JSON points to a .json file (local dev)
      - Inline JSON: GOOGLE_SERVICE_ACCOUNT_JSON contains the full JSON string (Railway/cloud)
    """
    creds_env = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")

    # If it looks like raw JSON (starts with {), parse it directly
    if creds_env.strip().startswith("{"):
        import json
        from google.oauth2.service_account import Credentials

        info = json.loads(creds_env)
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_info(info, scopes=scopes)
        return gspread.authorize(creds)

    # Otherwise treat it as a file path
    creds_path = os.path.abspath(GOOGLE_CREDS_PATH)
    if not os.path.exists(creds_path):
        print(f"ERROR: Google service account file not found at {creds_path}")
        sys.exit(1)
    return gspread.service_account(filename=creds_path)


def fetch_sp500_list(gc) -> set[str]:
    """Read S&P 500 ticker list from the 'Earnings Ticker' tab in Google Sheets.

    Used for both quarterly earnings matching and full memo 10-K matching.
    """
    spreadsheet = gc.open_by_key(SPREADSHEET_ID)
    worksheet = spreadsheet.worksheet(SP500_TAB)
    records = worksheet.get_all_records()
    # Extract ticker/symbol column (try common column names)
    tickers = set()
    for row in records:
        for key in ("symbol", "Symbol", "ticker", "Ticker", "TICKER"):
            if key in row and row[key]:
                tickers.add(str(row[key]).upper().strip())
                break
    # Fallback: if no records matched column names, try column A
    if not tickers:
        values = worksheet.col_values(1)
        for v in values:
            v = v.strip().upper()
            if v and v not in ("SYMBOL", "TICKER"):
                tickers.add(v)
    return tickers


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
    """Trigger full memo pipeline for a ticker.

    Tries Modal webhook first (if configured), otherwise runs locally
    via run_memo.py with --upload flag.
    """
    if dry_run:
        print(f"   DRY RUN: run_memo.py {ticker} --upload")
        return

    if not MODAL_MEMO_WEBHOOK:
        # Run locally via run_memo.py CLI (includes R2 upload)
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
        return

    # POST to Modal webhook
    print(f"   Triggering memo for {ticker} via webhook...")
    resp = httpx.post(MODAL_MEMO_WEBHOOK, json={"ticker": ticker}, timeout=600)
    if resp.status_code == 200:
        data = resp.json()
        print(f"   ✅ Full memo complete for {ticker} — {data.get('r2_key', '')}")
    else:
        print(f"   ❌ Full memo failed for {ticker} — HTTP {resp.status_code}")


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

    gc = get_sheets_client()

    # Fetch the S&P 500 list once — used for both checks
    sp500 = fetch_sp500_list(gc)
    print(f"\n   S&P 500 list ({SP500_TAB}): {len(sp500)} tickers")

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
