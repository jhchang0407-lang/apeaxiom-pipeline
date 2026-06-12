#!/usr/bin/env python3
"""Full Memo Pipeline — generate an investment research memo for a ticker.

Usage:
    python run_memo.py AAPL
    python run_memo.py AAPL --upload          # run pipeline + upload to R2
    python run_memo.py AAPL MSFT --upload     # multiple tickers
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone

# Ensure project root is importable
_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

from config.r2 import upload_to_r2, update_r2_index


def _build_r2_key(ticker: str) -> str:
    """Build R2 object key for a memo.

    Pattern: Memo/{year}/{MM}/{TICKER}/{TICKER} {MM}-{MMM} {dd}, {yyyy}-{yy}.json
    Example: Memo/2026/03/AAPL/AAPL 03-Mar 15, 2026-26.json
    """
    now = datetime.now(timezone.utc)
    year = now.strftime("%Y")
    mm = now.strftime("%m")
    mmm = now.strftime("%b")
    dd = now.strftime("%d")
    yy = now.strftime("%y")
    symbol = ticker.upper()

    filename = f"{symbol} {mm}-{mmm} {dd}, {year}-{yy}.json"
    return f"Memo/{year}/{mm}/{symbol}/{filename}"


def _public_errors(errors: list[str], limit: int = 10) -> list[str]:
    """First line of each error only — stage errors can embed full
    tracebacks with local paths, which must not ship in the public payload."""
    return [e.splitlines()[0] for e in errors[:limit]]


# ── Core Pipeline Function ───────────────────────────────────

async def generate_memo(ticker: str, upload: bool = False) -> dict | None:
    """Run full memo pipeline for a single ticker.

    Args:
        ticker: Company ticker symbol
        upload: If True, upload JSON payload to R2

    Returns:
        Dict with status info, or None on failure
    """
    from pipeline.orchestrator import run_pipeline

    print(f"\n{'=' * 60}")
    print(f"  FULL MEMO PIPELINE: {ticker.upper()}")
    print(f"{'=' * 60}")

    t0 = time.time()
    result = await run_pipeline(ticker, mode="website")
    elapsed = time.time() - t0

    if not result.assembly_ok:
        print(f"  Assembly failed for {ticker.upper()}")
        if result.errors:
            print(f"  Errors: {result.errors[:5]}")
        return None

    # ── Build JSON payload ────────────────────────────────────
    now = datetime.now()
    date_stamp = now.strftime("%m-%b %d, %Y-%y")
    folder_name = f"{ticker.upper()} {date_stamp}"

    payload = {
        "ticker": ticker.upper(),
        "mode": "website",
        "generated_at": datetime.now(timezone.utc).isoformat(),

        # Dashboard data
        "data_block": result.data_block,
        "scorecard": result.scorecard_json,

        # Full memo content
        "memo_markdown": result.markdown,
        "memo_html": result.html,

        # Metadata
        "pipeline_duration_s": result.pipeline_duration_s,
        "stage_timings": result.stage_timings,
        "assembly_ok": result.assembly_ok,
        "errors": _public_errors(result.errors),
    }

    payload_bytes = json.dumps(payload, indent=2, default=str).encode("utf-8")

    # ── Save locally ──────────────────────────────────────────
    out_dir = os.path.join(_here, "output", "memo", folder_name)
    os.makedirs(out_dir, exist_ok=True)

    prefix = f"{ticker.upper()} {date_stamp}"
    json_path = os.path.join(out_dir, f"{prefix}.json")
    with open(json_path, "w") as f:
        json.dump(payload, f, indent=2, default=str)

    if result.markdown:
        md_path = os.path.join(out_dir, f"{prefix}.md")
        with open(md_path, "w") as f:
            f.write(result.markdown)

    if result.html:
        html_path = os.path.join(out_dir, f"{prefix}.html")
        with open(html_path, "w") as f:
            f.write(result.html)

    print(f"\n  Output saved to: {out_dir}")
    print(f"    - {prefix}.json")
    if result.markdown:
        print(f"    - {prefix}.md")
    if result.html:
        print(f"    - {prefix}.html")
    print(f"  Pipeline completed in {elapsed:.0f}s")

    # ── Upload to R2 (optional) ───────────────────────────────
    if upload:
        try:
            r2_key = _build_r2_key(ticker)
            upload_to_r2(payload_bytes, r2_key)
            print(f"  Uploaded to R2: {r2_key}")
            update_r2_index(r2_key)
            print(f"  Updated _index.json")
        except Exception as e:
            print(f"  R2 upload failed: {e}")

    return {
        "status": "ok",
        "ticker": ticker.upper(),
        "r2_key": _build_r2_key(ticker) if upload else None,
        "pipeline_duration_s": elapsed,
        "errors": _public_errors(result.errors),
    }


# ── CLI Entry Point ──────────────────────────────────────────

async def cli_main():
    parser = argparse.ArgumentParser(description="Run Full Memo Pipeline (website mode)")
    parser.add_argument("tickers", nargs="+", help="Stock ticker(s) to process")
    parser.add_argument("--upload", "-u", action="store_true", help="Upload to R2 after generation")
    args = parser.parse_args()

    t0 = time.time()
    results = []

    for ticker in args.tickers:
        info = await generate_memo(ticker.strip().upper(), upload=args.upload)
        if info:
            results.append(info)

    total = time.time() - t0
    print(f"\n{'=' * 60}")
    print(f"  COMPLETED: {len(results)}/{len(args.tickers)} tickers in {total:.0f}s")
    print(f"{'=' * 60}")

    for r in results:
        r2 = r.get("r2_key") or "local only"
        print(f"  {r['ticker']:6s} | {r2}")


if __name__ == "__main__":
    asyncio.run(cli_main())
