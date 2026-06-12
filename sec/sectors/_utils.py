"""Shared utilities for sector KPI extraction from XBRL facts."""

from __future__ import annotations

from datetime import date as _date

_UNIT_PREFERENCE = ("USD", "USD/shares", "shares")


def _pick_unit_key(units: dict) -> str:
    """Pick the unit key, preferring USD over filer-specific units."""
    for unit in _UNIT_PREFERENCE:
        if unit in units:
            return unit
    return next(iter(units))


def _duration_days(entry: dict) -> int | None:
    """Days between an entry's start and end dates, or None if unavailable."""
    start = entry.get("start")
    end = entry.get("end")
    if not start or not end:
        return None
    try:
        return (_date.fromisoformat(end) - _date.fromisoformat(start)).days
    except ValueError:
        return None


def extract_annual_values(
    gaap: dict,
    tag_candidates: list[str],
    years: int = 5,
) -> list[dict]:
    """Extract annual (10-K, FY) values for the best matching tag.

    Evaluates ALL candidate tags and picks the one whose most-recent
    entry has the latest date.  This avoids a common bug where a
    discontinued XBRL tag (e.g. RevenueFromContractWithCustomer... up to
    2021) is listed before the current tag (Revenues, 2021-2025) and
    would return stale data.

    Returns list of {date, fy, val} dicts sorted by date desc.
    """
    best_result: list[dict] | None = None
    best_max_date: str = ""

    for tag in tag_candidates:
        if tag not in gaap:
            continue
        units = gaap[tag].get("units", {})
        if not units:
            continue
        unit_key = _pick_unit_key(units)
        entries = units[unit_key]

        # Filter to 10-K FY entries
        fy_entries = [
            e for e in entries
            if e.get("form") == "10-K" and e.get("fp") == "FY"
        ]

        # Deduplicate by end date. The same end date can carry quarterly,
        # YTD, and full-year durations — prefer ~12-month durations, then
        # keep the latest filed. Instant tags (no start date) and end dates
        # without a full-year entry fall back to latest filed.
        groups: dict[str, list[dict]] = {}
        for e in fy_entries:
            groups.setdefault(e["end"], []).append(e)

        seen: dict[str, dict] = {}
        for end, group in groups.items():
            annual = [
                e for e in group
                if (d := _duration_days(e)) is not None and 340 <= d <= 380
            ]
            candidates = annual or group
            best = candidates[0]
            for e in candidates[1:]:
                if e.get("filed", "") > best.get("filed", ""):
                    best = e
            seen[end] = best

        result = sorted(seen.values(), key=lambda x: x["end"], reverse=True)
        if result:
            max_date = result[0]["end"]
            if max_date > best_max_date:
                best_max_date = max_date
                best_result = [
                    {"date": e["end"], "fy": e.get("fy"), "val": e["val"]}
                    for e in result[:years]
                ]

    return best_result or []


def safe_div(a: float | None, b: float | None) -> float | None:
    """Safe division."""
    if a is None or b is None or b == 0:
        return None
    return a / b


def build_timeseries(
    gaap: dict,
    metrics: dict[str, list[str]],
    years: int = 5,
) -> dict[str, list[dict]]:
    """Build time series for multiple metrics.

    Args:
        gaap: us-gaap facts dict
        metrics: {metric_name: [tag_candidates]}
        years: number of years

    Returns: {metric_name: [{date, fy, val}]}
    """
    return {
        name: extract_annual_values(gaap, tags, years)
        for name, tags in metrics.items()
    }
