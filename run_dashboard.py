#!/usr/bin/env python3
"""Dashboard → R2 uploader.

Reads two tabs from the Ape Axiom Master Sheet (Google Sheets),
converts each to JSON, and uploads to Cloudflare R2.

Replaces the n8n "Dashbord Copy" workflow.

Usage:
    python run_dashboard.py              # upload both tabs to R2
    python run_dashboard.py --dry-run    # print what would be uploaded without uploading
"""

import argparse
import json
import os
import sys
from datetime import datetime

import boto3
import gspread

# ── Config ──────────────────────────────────────────────────────
SPREADSHEET_ID = "1C_zvwhm-UHbBCvVFXYyz1rWMsi3EdhxXGQ6B0qbzcyc"

# The two tabs to export
TABS = [
    {
        "name": "Daily Price Sheet",
        "gid": 0,
        "r2_prefix": "Daily Price",
        "r2_filename_template": "DailyPrices {date_short}.json",
    },
    {
        "name": "Daily Mkt Cap",
        "gid": 773679524,
        "r2_prefix": "Dashboard",
        "r2_filename_template": "dashboard {date_short}.json",
    },
]

# Google service account credentials
GOOGLE_CREDS_PATH = os.getenv(
    "GOOGLE_SERVICE_ACCOUNT_JSON",
    os.path.join(os.path.dirname(__file__), "..", "openclaw", "credentials", "google-service-account.json"),
)

# R2 config (reuse from settings)
from config.settings import CF_R2_ENDPOINT, CF_R2_ACCESS_KEY, CF_R2_SECRET_KEY, CF_R2_BUCKET


def get_sheets_client():
    """Authenticate with Google Sheets using service account."""
    creds_path = os.path.abspath(GOOGLE_CREDS_PATH)
    if not os.path.exists(creds_path):
        print(f"ERROR: Google service account file not found at {creds_path}")
        sys.exit(1)
    gc = gspread.service_account(filename=creds_path)
    return gc


def read_sheet_tab(gc, tab_name: str) -> list[dict]:
    """Read all rows from a Google Sheets tab as list of dicts."""
    spreadsheet = gc.open_by_key(SPREADSHEET_ID)
    worksheet = spreadsheet.worksheet(tab_name)
    return worksheet.get_all_records()


def build_r2_key(tab_config: dict, now: datetime) -> str:
    """Build R2 object key matching n8n naming convention.

    Daily Price/{yyyy}/{MM}/DailyPrices {MM-DD-yy}.json
    Dashboard/{yyyy}/{MM}/dashboard {MM-DD-yy}.json
    """
    date_short = now.strftime("%m-%d-%y")
    filename = tab_config["r2_filename_template"].format(date_short=date_short)
    return f"{tab_config['r2_prefix']}/{now.year}/{now.strftime('%m')}/{filename}"


def upload_to_r2(key: str, data: bytes):
    """Upload bytes to Cloudflare R2."""
    s3 = boto3.client(
        "s3",
        endpoint_url=CF_R2_ENDPOINT,
        aws_access_key_id=CF_R2_ACCESS_KEY,
        aws_secret_access_key=CF_R2_SECRET_KEY,
    )
    s3.put_object(
        Bucket=CF_R2_BUCKET,
        Key=key,
        Body=data,
        ContentType="application/json",
    )


def main():
    parser = argparse.ArgumentParser(description="Upload dashboard sheets to R2")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be uploaded without uploading")
    args = parser.parse_args()

    now = datetime.now()
    print(f"Dashboard upload — {now.strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)

    gc = get_sheets_client()

    for tab in TABS:
        print(f"\n📊 Reading tab: {tab['name']}...")
        rows = read_sheet_tab(gc, tab["name"])
        print(f"   {len(rows)} rows")

        json_bytes = json.dumps(rows, indent=2, default=str).encode("utf-8")
        r2_key = build_r2_key(tab, now)

        if args.dry_run:
            print(f"   DRY RUN — would upload to: {r2_key} ({len(json_bytes):,} bytes)")
        else:
            print(f"   Uploading to: {r2_key} ({len(json_bytes):,} bytes)...")
            upload_to_r2(r2_key, json_bytes)
            print(f"   ✅ Done")

    print("\n" + "=" * 50)
    print("Dashboard upload complete.")


if __name__ == "__main__":
    main()
