#!/usr/bin/env python3
"""Website Full Memo Pipeline — run locally or deploy to Modal.

Usage (CLI — local):
    python run_memo.py AAPL
    python run_memo.py AAPL --upload          # run pipeline + upload to R2
    python run_memo.py AAPL MSFT --upload     # multiple tickers

Usage (Modal — cloud):
    modal deploy run_memo.py
    modal run run_memo.py
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


# ── R2 Upload Utility ────────────────────────────────────────

_R2_ENDPOINT = "https://f3a5563fca3d8d1165c35edaa8c2cc48.r2.cloudflarestorage.com"


def _build_r2_key(ticker: str) -> str:
    """Build R2 object key matching n8n naming convention.

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


def upload_to_r2(
    content: bytes,
    key: str,
    content_type: str = "application/json",
) -> str:
    """Upload content to Cloudflare R2. Returns the R2 key."""
    import boto3

    from config.settings import CF_R2_ENDPOINT, CF_R2_ACCESS_KEY, CF_R2_SECRET_KEY, CF_R2_BUCKET

    s3 = boto3.client(
        "s3",
        endpoint_url=CF_R2_ENDPOINT,
        aws_access_key_id=CF_R2_ACCESS_KEY,
        aws_secret_access_key=CF_R2_SECRET_KEY,
        region_name="auto",
    )

    s3.put_object(
        Bucket=CF_R2_BUCKET,
        Key=key,
        Body=content,
        ContentType=content_type,
    )

    return key


def update_r2_index(r2_key: str) -> None:
    """Update _index.json in R2 with a new/updated key entry."""
    import boto3
    from config.settings import CF_R2_ENDPOINT, CF_R2_ACCESS_KEY, CF_R2_SECRET_KEY, CF_R2_BUCKET

    s3 = boto3.client(
        "s3",
        endpoint_url=CF_R2_ENDPOINT,
        aws_access_key_id=CF_R2_ACCESS_KEY,
        aws_secret_access_key=CF_R2_SECRET_KEY,
        region_name="auto",
    )

    # Fetch existing index
    try:
        resp = s3.get_object(Bucket=CF_R2_BUCKET, Key="_index.json")
        index = json.loads(resp["Body"].read())
    except Exception:
        index = {}

    # Parse the key to extract type and ticker
    parts = r2_key.split("/")
    data_type = parts[0]  # "Memo", "Quarterly", etc.
    ticker = parts[3] if len(parts) >= 4 and data_type in ("Memo", "Quarterly") else "_"

    if data_type not in index:
        index[data_type] = {}

    # Always update — upload scripts run once per event, so the latest upload
    # is always the newest. (String comparison broke with unpadded day numbers.)
    index[data_type][ticker] = r2_key

    s3.put_object(
        Bucket=CF_R2_BUCKET,
        Key="_index.json",
        Body=json.dumps(index, indent=2).encode(),
        ContentType="application/json",
    )


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
        "errors": result.errors[:10],
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
        "errors": result.errors[:10],
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


# ── Modal Cloud Deployment (optional) ────────────────────────
# Only set up Modal if the modal package is available and we're
# not running as a CLI script.

try:
    import modal

    app = modal.App("openclaw")

    image = (
        modal.Image.debian_slim(python_version="3.12")
        .pip_install(
            "fastapi>=0.115.0",
            "uvicorn>=0.32.0",
            "httpx>=0.27.0",
            "openai>=1.50.0",
            "pydantic>=2.10.0",
            "jinja2>=3.1.0",
            "edgartools>=3.0.0",
            "weasyprint>=62.0",
            "markdown>=3.7",
            "boto3>=1.35.0",
        )
    )

    secrets = modal.Secret.from_name("openclaw-secrets")

    @app.function(image=image, secrets=[secrets], timeout=600, memory=2048)
    async def modal_generate_memo(ticker: str) -> dict:
        """Modal-wrapped memo generation with R2 upload."""
        return await generate_memo(ticker, upload=True)

    @app.function(image=image, secrets=[secrets], timeout=600, memory=2048)
    @modal.web_endpoint(method="POST")
    async def memo_endpoint(request: dict) -> dict:
        """HTTP endpoint for triggering memo generation."""
        ticker = request.get("ticker", "")
        if not ticker:
            return {"error": "ticker is required"}
        return await modal_generate_memo.remote(ticker)

except ImportError:
    # Modal not installed — CLI-only mode
    pass


if __name__ == "__main__":
    asyncio.run(cli_main())
