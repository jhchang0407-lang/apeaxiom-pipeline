#!/usr/bin/env python3
"""Build and upload an R2 index file (_index.json) that maps each
data type + ticker to its latest R2 key.

This eliminates the need for the frontend to brute-force search
for memos day-by-day. One fetch of _index.json gives the exact key.

Usage:
    python build_r2_index.py           # build + upload index
    python build_r2_index.py --dry-run # print index without uploading
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict

_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

_R2_ENDPOINT = "https://f3a5563fca3d8d1165c35edaa8c2cc48.r2.cloudflarestorage.com"


def get_s3_client():
    import boto3
    from config.settings import CF_R2_ACCESS_KEY, CF_R2_SECRET_KEY

    return boto3.client(
        "s3",
        endpoint_url=_R2_ENDPOINT,
        aws_access_key_id=CF_R2_ACCESS_KEY,
        aws_secret_access_key=CF_R2_SECRET_KEY,
        region_name="auto",
    )


def list_all_objects(s3, bucket: str) -> list[dict]:
    """List all objects in the bucket using pagination."""
    objects = []
    continuation_token = None
    while True:
        kwargs = {"Bucket": bucket, "MaxKeys": 1000}
        if continuation_token:
            kwargs["ContinuationToken"] = continuation_token
        resp = s3.list_objects_v2(**kwargs)
        for obj in resp.get("Contents", []):
            objects.append(obj)
        if not resp.get("IsTruncated"):
            break
        continuation_token = resp.get("NextContinuationToken")
    return objects


def build_index(objects: list[dict]) -> dict:
    """Build index mapping type/ticker to latest R2 key.

    Uses R2 LastModified timestamp (not key string comparison) to determine
    the latest file, since day numbers in keys are unpadded (e.g. "2" > "14"
    in string sort but 2 < 14 by date).
    """
    # Group by type+ticker, tracking (LastModified, key) to pick the newest
    groups: dict[str, dict[str, tuple]] = defaultdict(dict)  # type → ticker → (modified, key)

    for obj in objects:
        key = obj["Key"]
        modified = obj.get("LastModified")  # datetime from S3 API
        parts = key.split("/")
        if len(parts) < 2:
            continue

        data_type = parts[0]

        if data_type in ("Memo", "Quarterly"):
            if len(parts) >= 4:
                ticker = parts[3]
                existing = groups[data_type].get(ticker)
                if existing is None or modified > existing[0]:
                    groups[data_type][ticker] = (modified, key)

        elif data_type in ("Dashboard", "Daily Price"):
            existing = groups[data_type].get("_")
            if existing is None or modified > existing[0]:
                groups[data_type]["_"] = (modified, key)

    # Strip the timestamps, keep only the keys
    return {dtype: {ticker: val[1] for ticker, val in tickers.items()}
            for dtype, tickers in groups.items()}

    return dict(groups)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Print index without uploading")
    args = parser.parse_args()

    from config.settings import CF_R2_BUCKET

    s3 = get_s3_client()
    print(f"Listing objects in bucket: {CF_R2_BUCKET}")
    objects = list_all_objects(s3, CF_R2_BUCKET)
    print(f"Found {len(objects)} objects")

    index = build_index(objects)

    # Summary
    for dtype, tickers in index.items():
        print(f"  {dtype}: {len(tickers)} entries")

    index_json = json.dumps(index, indent=2)

    if args.dry_run:
        print(index_json)
        return

    # Upload _index.json
    s3.put_object(
        Bucket=CF_R2_BUCKET,
        Key="_index.json",
        Body=index_json.encode(),
        ContentType="application/json",
    )
    print("Uploaded _index.json to R2")


if __name__ == "__main__":
    main()
