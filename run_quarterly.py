#!/usr/bin/env python3
"""CLI runner for the Quarterly Earnings Pipeline.

Usage:
    python run_quarterly.py AAPL
    python run_quarterly.py AAPL --quarter "Q4 2025"
    python run_quarterly.py AAPL --upload          # also upload to R2
    python run_quarterly.py AAPL MSFT GOOG         # multiple tickers
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime

# Ensure project root is importable
_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)


async def run_single(ticker: str, quarter_hint: str | None, upload: bool) -> dict | None:
    """Run quarterly pipeline for a single ticker."""
    from quarterly.orchestrator import run_quarterly_pipeline

    print(f"\n{'=' * 60}")
    print(f"  QUARTERLY PIPELINE: {ticker.upper()}")
    print(f"{'=' * 60}")

    result = await run_quarterly_pipeline(ticker, quarter_hint=quarter_hint)

    if result.errors:
        print(f"\n  Errors: {result.errors[:5]}")

    if not result.markdown:
        print(f"  No output generated for {ticker.upper()}")
        return None

    # ── Save to local output ─────────────────────────────────────
    now = datetime.now()
    date_stamp = now.strftime("%m-%b %d, %Y-%y")
    folder_name = f"{ticker.upper()} {date_stamp}"
    out_dir = os.path.join(_here, "output", "quarterly", folder_name)
    os.makedirs(out_dir, exist_ok=True)

    prefix = f"{ticker.upper()} {date_stamp} Quarterly"

    # JSON payload
    json_path = os.path.join(out_dir, f"{prefix}.json")
    with open(json_path, "w") as f:
        json.dump(result.payload, f, indent=2, default=str)

    # Markdown
    md_path = os.path.join(out_dir, f"{prefix}.md")
    with open(md_path, "w") as f:
        f.write(result.markdown)

    # HTML dashboard
    if result.html:
        html_path = os.path.join(out_dir, f"{prefix}.html")
        with open(html_path, "w") as f:
            f.write(result.html)

    print(f"\n  Output saved to: {out_dir}")
    print(f"    - {prefix}.json")
    print(f"    - {prefix}.md")
    if result.html:
        print(f"    - {prefix}.html")

    # ── Upload to R2 (optional) ──────────────────────────────────
    if upload:
        try:
            import boto3
            from config.settings import CF_R2_ENDPOINT, CF_R2_ACCESS_KEY, CF_R2_SECRET_KEY, CF_R2_BUCKET

            s3 = boto3.client(
                "s3",
                endpoint_url=CF_R2_ENDPOINT,
                aws_access_key_id=CF_R2_ACCESS_KEY,
                aws_secret_access_key=CF_R2_SECRET_KEY,
            )

            r2_key = (
                f"Quarterly/{now.year}/{now.strftime('%m')}/"
                f"{ticker.upper()}/Quarterly {ticker.upper()} "
                f"{date_stamp}.json"
            )

            s3.put_object(
                Bucket=CF_R2_BUCKET,
                Key=r2_key,
                Body=json.dumps(result.payload, indent=2, default=str).encode(),
                ContentType="application/json",
            )
            print(f"  Uploaded to R2: {r2_key}")
        except Exception as e:
            print(f"  R2 upload failed: {e}")

    return result.payload


async def main():
    parser = argparse.ArgumentParser(description="Run Quarterly Earnings Pipeline")
    parser.add_argument("tickers", nargs="+", help="Stock ticker(s) to process")
    parser.add_argument("--quarter", "-q", default=None, help="Quarter hint, e.g. 'Q4 2025'")
    parser.add_argument("--upload", "-u", action="store_true", help="Upload to R2 after generation")
    args = parser.parse_args()

    t0 = time.time()
    results = []

    for ticker in args.tickers:
        payload = await run_single(ticker.strip().upper(), args.quarter, args.upload)
        if payload:
            results.append(payload)

    total = time.time() - t0
    print(f"\n{'=' * 60}")
    print(f"  COMPLETED: {len(results)}/{len(args.tickers)} tickers in {total:.0f}s")
    print(f"{'=' * 60}")

    # Summary
    for r in results:
        h = r.get("headline", {})
        rev = h.get("revenue_actual_m")
        eps = h.get("eps_actual")
        beat = h.get("revenue_beat_miss_pct")
        beat_str = f"+{beat:.1f}%" if beat and beat > 0 else f"{beat:.1f}%" if beat else "—"
        reported = r.get('quarter', {}).get('reported') or '?'
        if rev and eps:
            print(f"  {r['ticker']:6s} | {reported:12s} | Rev: ${rev:,.0f}M | EPS: ${eps:.2f} | Beat: {beat_str}")
        else:
            print(f"  {r['ticker']:6s} | {reported:12s} | Data incomplete")


if __name__ == "__main__":
    asyncio.run(main())
