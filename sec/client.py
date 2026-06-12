"""SEC EDGAR API client — fetches companyfacts and submissions."""

import hashlib
import json
import os
import threading
import time
from pathlib import Path

import httpx

from config.settings import SEC_USER_AGENT

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)

EDGAR_BASE = "https://data.sec.gov"

# Ticker → CIK mapping cache (populated lazily)
_cik_map: dict[str, int] | None = None
_last_request_time: float = 0.0
_rate_lock = threading.Lock()


def require_sec_user_agent() -> str:
    """Return the configured SEC User-Agent, failing fast if unset."""
    if not SEC_USER_AGENT:
        raise RuntimeError(
            "SEC_USER_AGENT is not set — SEC requires a User-Agent with your "
            "contact email, e.g. 'Jane Doe jane@example.com'. Set it in .env "
            "(see .env.template)."
        )
    return SEC_USER_AGENT


def _rate_limit():
    """SEC EDGAR allows 10 requests/sec. Enforce ~100ms between requests."""
    global _last_request_time
    with _rate_lock:
        elapsed = time.time() - _last_request_time
        if elapsed < 0.11:
            time.sleep(0.11 - elapsed)
        _last_request_time = time.time()


def _cache_path(key: str) -> Path:
    h = hashlib.md5(key.encode()).hexdigest()
    return CACHE_DIR / f"{h}.json"


def _cached_get(url: str, max_age_hours: int = 24) -> dict:
    """GET with local file cache."""
    cp = _cache_path(url)
    if cp.exists():
        age_h = (time.time() - cp.stat().st_mtime) / 3600
        if age_h < max_age_hours:
            try:
                return json.loads(cp.read_text())
            except json.JSONDecodeError:
                pass  # corrupt cache file — refetch

    headers = {"User-Agent": require_sec_user_agent()}
    for attempt in range(3):
        _rate_limit()
        try:
            r = httpx.get(url, headers=headers, timeout=30)
            r.raise_for_status()
            data = r.json()
            break
        except httpx.HTTPError:
            if attempt == 2:
                raise
            time.sleep(0.5 * (attempt + 1))

    tmp = cp.with_name(cp.name + ".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, cp)
    return data


def get_cik_map() -> dict[str, int]:
    """Load the full ticker → CIK mapping from SEC."""
    global _cik_map
    if _cik_map is not None:
        return _cik_map

    data = _cached_get("https://www.sec.gov/files/company_tickers.json", max_age_hours=168)
    _cik_map = {}
    for entry in data.values():
        ticker = entry["ticker"].upper()
        _cik_map[ticker] = entry["cik_str"]
    return _cik_map


def ticker_to_cik(ticker: str) -> int:
    """Resolve a ticker symbol to a CIK number."""
    cik_map = get_cik_map()
    ticker = ticker.upper()
    if ticker not in cik_map:
        raise ValueError(f"Ticker '{ticker}' not found in SEC EDGAR")
    return cik_map[ticker]


def get_companyfacts(ticker: str) -> dict:
    """Get all XBRL facts for a company. One call gets everything."""
    cik = ticker_to_cik(ticker)
    cik_padded = str(cik).zfill(10)
    url = f"{EDGAR_BASE}/api/xbrl/companyfacts/CIK{cik_padded}.json"
    return _cached_get(url, max_age_hours=12)


def get_submissions(ticker: str) -> dict:
    """Get company submission metadata (SIC code, name, etc.)."""
    cik = ticker_to_cik(ticker)
    cik_padded = str(cik).zfill(10)
    url = f"{EDGAR_BASE}/submissions/CIK{cik_padded}.json"
    return _cached_get(url, max_age_hours=24)
