#!/usr/bin/env python3
"""Batch S&P 500 Pipeline — Run memo + quarterly for all S&P 500 tickers.

Usage:
    python run_batch_sp500.py                    # memo + quarterly, upload to R2
    python run_batch_sp500.py --memo-only        # memo only
    python run_batch_sp500.py --quarterly-only   # quarterly only
    python run_batch_sp500.py --concurrency 5    # run 5 at a time (default: 3)
    python run_batch_sp500.py --resume           # skip tickers already in output/
    python run_batch_sp500.py --dry-run          # just list tickers, don't run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime

_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

TICKERS_FILE = os.path.join(_here, "sp500_tickers.json")
PROGRESS_FILE = os.path.join(_here, "sp500_progress.json")


def load_tickers() -> list[str]:
    with open(TICKERS_FILE) as f:
        return json.load(f)


def load_progress() -> dict:
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {"memo_done": [], "quarterly_done": [], "memo_failed": [], "quarterly_failed": []}


def save_progress(progress: dict):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f, indent=2)


def _mark(progress: dict, key: str, ticker: str):
    """Record a ticker under a progress key without duplicating across resumed runs."""
    if ticker not in progress[key]:
        progress[key].append(ticker)


async def run_memo_single(ticker: str, progress: dict, sem: asyncio.Semaphore):
    """Run memo pipeline for one ticker with semaphore-based concurrency."""
    async with sem:
        try:
            from run_memo import generate_memo
            result = await generate_memo(ticker, upload=True)
            if result and result.get("status") == "ok":
                _mark(progress, "memo_done", ticker)
                print(f"  ✓ MEMO {ticker} done ({result.get('pipeline_duration_s', 0):.0f}s)")
            else:
                _mark(progress, "memo_failed", ticker)
                print(f"  ✗ MEMO {ticker} failed")
        except Exception as e:
            _mark(progress, "memo_failed", ticker)
            print(f"  ✗ MEMO {ticker} error: {e}")
        save_progress(progress)


async def run_quarterly_single(ticker: str, progress: dict, sem: asyncio.Semaphore):
    """Run quarterly pipeline for one ticker with semaphore-based concurrency."""
    async with sem:
        try:
            from run_quarterly import run_single
            result = await run_single(ticker, quarter_hint=None, upload=True)
            if result:
                _mark(progress, "quarterly_done", ticker)
                print(f"  ✓ QTRLY {ticker} done")
            else:
                _mark(progress, "quarterly_failed", ticker)
                print(f"  ✗ QTRLY {ticker} failed (no output)")
        except Exception as e:
            _mark(progress, "quarterly_failed", ticker)
            print(f"  ✗ QTRLY {ticker} error: {e}")
        save_progress(progress)


async def main():
    parser = argparse.ArgumentParser(description="Batch S&P 500 memo + quarterly pipeline")
    parser.add_argument("--memo-only", action="store_true", help="Only run memo pipeline")
    parser.add_argument("--quarterly-only", action="store_true", help="Only run quarterly pipeline")
    parser.add_argument("--concurrency", "-c", type=int, default=3,
                        help="Max concurrent pipelines (default: 3)")
    parser.add_argument("--resume", "-r", action="store_true",
                        help="Resume from progress file, skip completed tickers")
    parser.add_argument("--dry-run", action="store_true", help="List tickers without running")
    parser.add_argument("--start", type=int, default=0, help="Start index (0-based)")
    parser.add_argument("--limit", type=int, default=0, help="Max tickers to process (0 = all)")
    args = parser.parse_args()

    tickers = load_tickers()
    progress = load_progress() if args.resume else {
        "memo_done": [], "quarterly_done": [],
        "memo_failed": [], "quarterly_failed": [],
    }

    run_memo = not args.quarterly_only
    run_quarterly = not args.memo_only

    # Filter out already-completed tickers if resuming
    memo_tickers = tickers
    quarterly_tickers = tickers
    if args.resume:
        memo_tickers = [t for t in tickers if t not in progress["memo_done"]]
        quarterly_tickers = [t for t in tickers if t not in progress["quarterly_done"]]

    # Apply start/limit
    if args.start > 0:
        memo_tickers = memo_tickers[args.start:]
        quarterly_tickers = quarterly_tickers[args.start:]
    if args.limit > 0:
        memo_tickers = memo_tickers[:args.limit]
        quarterly_tickers = quarterly_tickers[:args.limit]

    print(f"\n{'=' * 60}")
    print(f"  S&P 500 BATCH PIPELINE")
    print(f"{'=' * 60}")
    print(f"  Total S&P 500 tickers: {len(tickers)}")
    if run_memo:
        print(f"  Memo to process: {len(memo_tickers)}")
    if run_quarterly:
        print(f"  Quarterly to process: {len(quarterly_tickers)}")
    print(f"  Concurrency: {args.concurrency}")
    if args.resume:
        print(f"  Resuming — memo done: {len(progress['memo_done'])}, "
              f"quarterly done: {len(progress['quarterly_done'])}")
    print(f"{'=' * 60}\n")

    if args.dry_run:
        if run_memo:
            print("Memo tickers:", " ".join(memo_tickers))
        if run_quarterly:
            print("\nQuarterly tickers:", " ".join(quarterly_tickers))
        return

    sem = asyncio.Semaphore(args.concurrency)
    t0 = time.time()

    # Run memos first, then quarterly
    if run_memo and memo_tickers:
        print(f"\n--- PHASE 1: MEMO PIPELINE ({len(memo_tickers)} tickers) ---\n")
        tasks = [run_memo_single(t, progress, sem) for t in memo_tickers]
        await asyncio.gather(*tasks)

    if run_quarterly and quarterly_tickers:
        print(f"\n--- PHASE 2: QUARTERLY PIPELINE ({len(quarterly_tickers)} tickers) ---\n")
        tasks = [run_quarterly_single(t, progress, sem) for t in quarterly_tickers]
        await asyncio.gather(*tasks)

    elapsed = time.time() - t0

    print(f"\n{'=' * 60}")
    print(f"  BATCH COMPLETE — {elapsed / 60:.1f} minutes")
    print(f"{'=' * 60}")
    print(f"  Memo:      {len(progress['memo_done'])} done, {len(progress['memo_failed'])} failed")
    print(f"  Quarterly: {len(progress['quarterly_done'])} done, {len(progress['quarterly_failed'])} failed")
    if progress["memo_failed"]:
        print(f"  Memo failures: {', '.join(progress['memo_failed'][:20])}")
    if progress["quarterly_failed"]:
        print(f"  Qtrly failures: {', '.join(progress['quarterly_failed'][:20])}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    asyncio.run(main())
