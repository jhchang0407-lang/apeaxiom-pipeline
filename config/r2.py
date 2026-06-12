"""Shared Cloudflare R2 helpers — client factory, uploads, and index updates.

Used by run_memo.py, run_quarterly.py, run_market_data.py, and
build_r2_index.py so the client construction and the _index.json
read-modify-write logic live in exactly one place.
"""

from __future__ import annotations

import json


def get_r2_client():
    """Build a boto3 S3 client pointed at Cloudflare R2.

    Raises RuntimeError with a clear message if R2 config is missing,
    rather than failing deep inside boto3.
    """
    import boto3

    from config.settings import (
        CF_R2_ENDPOINT,
        CF_R2_ACCESS_KEY,
        CF_R2_SECRET_KEY,
    )

    if not CF_R2_ENDPOINT or not CF_R2_ACCESS_KEY or not CF_R2_SECRET_KEY:
        raise RuntimeError(
            "R2 is not configured. Set CF_R2_ENDPOINT, CF_R2_ACCESS_KEY, "
            "CF_R2_SECRET_KEY, and CF_R2_BUCKET (see .env.template)."
        )

    return boto3.client(
        "s3",
        endpoint_url=CF_R2_ENDPOINT,
        aws_access_key_id=CF_R2_ACCESS_KEY,
        aws_secret_access_key=CF_R2_SECRET_KEY,
        region_name="auto",
    )


def upload_to_r2(content: bytes, key: str, content_type: str = "application/json") -> str:
    """Upload content to the configured R2 bucket. Returns the key."""
    from config.settings import CF_R2_BUCKET

    s3 = get_r2_client()
    s3.put_object(Bucket=CF_R2_BUCKET, Key=key, Body=content, ContentType=content_type)
    return key


def update_r2_index(r2_key: str, s3=None) -> None:
    """Update _index.json in R2 with a new/updated key entry.

    Only a genuinely missing index file starts a fresh one; any other
    read failure (auth, throttling, network) raises instead of silently
    wiping the index. Concurrent writers can still last-write-win on the
    whole file — build_r2_index.py rebuilds it from a bucket listing if
    an entry is ever lost.
    """
    from config.settings import CF_R2_BUCKET

    if s3 is None:
        s3 = get_r2_client()

    try:
        resp = s3.get_object(Bucket=CF_R2_BUCKET, Key="_index.json")
        index = json.loads(resp["Body"].read())
    except s3.exceptions.NoSuchKey:
        index = {}

    # Key pattern: "{Type}/{year}/{MM}/{TICKER}/{filename}" for Memo and
    # Quarterly; other types (e.g. MarketData) index under "_".
    parts = r2_key.split("/")
    data_type = parts[0]
    ticker = parts[3] if len(parts) >= 4 and data_type in ("Memo", "Quarterly") else "_"

    index.setdefault(data_type, {})[ticker] = r2_key

    s3.put_object(
        Bucket=CF_R2_BUCKET,
        Key="_index.json",
        Body=json.dumps(index, indent=2).encode(),
        ContentType="application/json",
    )
