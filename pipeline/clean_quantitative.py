"""
Clean Quantitative Facts — Python port of Clean_Quantitative_Facts.js

Purpose:
  1. Prefix $ on dollar values, append % on percentages, x on multiples
  2. Auto-scale: >1000M → $B, >1000B → $T
  3. Negative values use accounting parentheses: ($91.4B)
  4. Store raw values in _meta._raw for downstream arithmetic (distributors)
  5. Detect mixed-scale pct_of_total fields (decimal vs already %)

Input:  raw quantitative fact sheet dict from build_quantitative_facts()
Output: formatted fact sheet with string-formatted values + _raw backup
"""

from __future__ import annotations

import copy
import math
import re
from typing import Any


# ── FORMATTING HELPERS ──────────────────────────────────────────

def _rd(v: Any, decimals: int = 1) -> float | None:
    """Round to sensible precision."""
    if v is None:
        return None
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(fv) or math.isinf(fv):
        return None
    return round(fv, decimals)


def _fmt_locale(v: float, max_frac: int = 1) -> str:
    """Format number with commas (US locale style)."""
    if v == int(v):
        return f"{int(v):,}"
    return f"{v:,.{max_frac}f}"


def fmt_m(v: Any) -> str | None:
    """Dollar prefix + auto-scale: >1000M → $B, else $M.
    Negative values use accounting parentheses: ($91.4B)"""
    if v is None:
        return None
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(fv) or math.isinf(fv):
        return None

    absv = abs(fv)
    neg = fv < 0

    if absv >= 1_000_000:               # $1T+
        t = absv / 1_000_000
        decimals = 1 if t >= 10 else 2
        s = f"${_rd(t, decimals)}T"
    elif absv >= 1000:                   # $1B+
        b = absv / 1000
        decimals = 1 if b >= 100 else 2
        s = f"${_rd(b, decimals)}B"
    else:
        s = f"${_fmt_locale(_rd(absv, 1), 1)}M"

    return f"({s})" if neg else s


def fmt_b(v: Any) -> str | None:
    """Dollar prefix for billions. >1000B → $T."""
    if v is None:
        return None
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(fv) or math.isinf(fv):
        return None

    absv = abs(fv)
    neg = fv < 0

    if absv >= 1000:
        s = f"${_rd(absv / 1000, 2)}T"
    else:
        decimals = 1 if absv >= 100 else 2
        s = f"${_rd(absv, decimals)}B"

    return f"({s})" if neg else s


def fmt_pct(v: Any) -> str | None:
    """Percent suffix: 11.83 → '11.8%'"""
    if v is None:
        return None
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(fv) or math.isinf(fv):
        return None
    return f"{_rd(fv, 1)}%"


def fmt_x(v: Any) -> str | None:
    """Multiple suffix: 25.3 → '25.3x'"""
    if v is None:
        return None
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(fv) or math.isinf(fv):
        return None
    return f"{_rd(fv, 1)}x"


def fmt_days(v: Any) -> int | None:
    """Round days to integer."""
    if v is None:
        return None
    try:
        return round(float(v))
    except (TypeError, ValueError):
        return None


# ── APPLY TO YEAR-KEYED DICTS ──────────────────────────────────

def _is_year_key(k: str) -> bool:
    """Check if a key looks like a year key (starts with 4 digits)."""
    return bool(re.match(r"^\d{4}", str(k)))


def _is_year_dict(obj: Any) -> bool:
    """Check if obj is a dict with year-like keys."""
    if not isinstance(obj, dict) or not obj:
        return False
    return any(_is_year_key(k) for k in obj)


def _is_nested_year_dict(obj: Any) -> bool:
    """Check if obj is a year-keyed dict where values are themselves dicts."""
    if not _is_year_dict(obj):
        return False
    return any(
        isinstance(v, dict) and not isinstance(v, list)
        for v in obj.values()
        if v is not None
    )


def _apply_to_year_dict(obj: dict, fn) -> dict:
    """Apply formatter fn to values of year-keyed entries."""
    if not isinstance(obj, dict):
        return obj
    return {
        k: fn(v) if _is_year_key(k) else v
        for k, v in obj.items()
    }


def _apply_to_nested_year_dict(obj: dict, fn) -> dict:
    """Apply formatter fn to inner values of nested year dicts."""
    if not isinstance(obj, dict):
        return obj
    out = {}
    for year, inner in obj.items():
        if isinstance(inner, dict):
            out[year] = {seg: fn(val) for seg, val in inner.items()}
        else:
            out[year] = inner
    return out


def _apply_to_pct_of_total(obj: dict) -> dict:
    """Format pct_of_total fields, detecting decimal vs percentage scale.

    Segment pcts: stored as decimal (0.557 = 55.7%)
    Geo pcts: stored as already ×100 (17.9 = 17.9%)
    Detect: if max absolute value <= 1.5, treat as decimal; otherwise already %
    """
    if not isinstance(obj, dict):
        return obj

    # Collect all numeric values to determine scale
    all_vals: list[float] = []

    def _collect(o):
        if not isinstance(o, dict):
            return
        for v in o.values():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                all_vals.append(abs(v))
            elif isinstance(v, dict):
                _collect(v)

    _collect(obj)

    max_val = max(all_vals) if all_vals else 0
    is_decimal = max_val <= 1.5

    def _fmt(v):
        if v is None:
            return None
        try:
            pct_val = float(v) * 100 if is_decimal else float(v)
        except (TypeError, ValueError):
            return None
        return f"{_rd(pct_val, 1)}%"

    out = {}
    for year, inner in obj.items():
        if isinstance(inner, dict):
            out[year] = {seg: _fmt(val) for seg, val in inner.items()}
        else:
            out[year] = _fmt(inner)
    return out


def _apply_to_array_items(arr: list) -> list:
    """Format array items (quarterly data, estimates, etc.) by key suffix."""
    if not isinstance(arr, list):
        return arr
    result = []
    for item in arr:
        if not isinstance(item, dict):
            result.append(item)
            continue
        out = {}
        for k, v in item.items():
            if v is None:
                out[k] = v
            elif k.endswith("_usd_m"):
                out[k] = fmt_m(v)
            elif k.endswith("_usd_b"):
                out[k] = fmt_b(v)
            elif k.endswith("_pct"):
                out[k] = fmt_pct(v)
            else:
                out[k] = v
        result.append(out)
    return result


# ── MULTIPLE KEYS — these get "x" suffix ──────────────────────

MULTIPLE_KEYS = {
    "ev_to_sales", "ev_to_ebitda", "ev_to_fcf", "ev_to_ocf",
    "price_to_earnings", "price_to_fcf", "price_to_sales", "price_to_book",
    "debt_to_equity_ratio", "current_ratio", "net_debt_to_ebitda",
    "interest_coverage_ratio", "income_quality",
}


# ── RECURSIVE SECTION FORMATTER ───────────────────────────────

def _fmt_section(section: Any) -> Any:
    """Walk a section dict and format values by key pattern."""
    if not isinstance(section, dict):
        return section

    out = {}
    for key, val in section.items():
        if not isinstance(key, str):
            continue

        # Arrays — quarterly, estimates, owner_earnings
        if isinstance(val, list):
            out[key] = _apply_to_array_items(val)
            continue

        # Standalone numeric values
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            if "_pct" in key:
                out[key] = fmt_pct(val)
            elif key.endswith("_usd_m"):
                out[key] = fmt_m(val)
            elif key.endswith("_usd_b"):
                out[key] = fmt_b(val)
            elif key in MULTIPLE_KEYS:
                out[key] = fmt_x(val)
            else:
                out[key] = val
            continue

        # Non-year-dict objects — recurse
        if isinstance(val, dict) and not _is_year_dict(val) and not _is_nested_year_dict(val):
            out[key] = _fmt_section(val)
            continue

        # Nested year dicts where inner values are objects
        # e.g. ar_vs_revenue_growth: { "2025 FY": { ar_growth_pct: 25.24, ... } }
        if (_is_nested_year_dict(val)
                and not key.endswith("_usd_m")
                and not key.endswith("_usd_b")
                and "_pct" not in key):
            formatted = {}
            for yr, inner in val.items():
                if isinstance(inner, dict):
                    formatted[yr] = {}
                    for ik, iv in inner.items():
                        if not isinstance(iv, (int, float)) or isinstance(iv, bool):
                            formatted[yr][ik] = iv
                        elif ik.endswith("_usd_m"):
                            formatted[yr][ik] = fmt_m(iv)
                        elif ik.endswith("_usd_b"):
                            formatted[yr][ik] = fmt_b(iv)
                        elif "_pct" in ik:
                            formatted[yr][ik] = fmt_pct(iv)
                        else:
                            formatted[yr][ik] = iv
                else:
                    formatted[yr] = inner
            out[key] = formatted
            continue

        # Match by key pattern and apply formatter
        if key.endswith("_usd_m"):
            if _is_nested_year_dict(val):
                out[key] = _apply_to_nested_year_dict(val, fmt_m)
            else:
                out[key] = _apply_to_year_dict(val, fmt_m)
        elif key.endswith("_usd_b"):
            if _is_nested_year_dict(val):
                out[key] = _apply_to_nested_year_dict(val, fmt_b)
            else:
                out[key] = _apply_to_year_dict(val, fmt_b)
        elif key.endswith("_pct_of_total"):
            out[key] = _apply_to_pct_of_total(val)
        elif "_pct" in key:
            if _is_nested_year_dict(val):
                out[key] = _apply_to_nested_year_dict(val, fmt_pct)
            else:
                out[key] = _apply_to_year_dict(val, fmt_pct)
        elif key.endswith("_days"):
            if _is_nested_year_dict(val):
                out[key] = _apply_to_nested_year_dict(val, fmt_days)
            else:
                out[key] = _apply_to_year_dict(val, fmt_days)
        elif key in MULTIPLE_KEYS:
            out[key] = _apply_to_year_dict(val, fmt_x)
        else:
            out[key] = val

    return out


# ── FORMAT PEER RECORD ────────────────────────────────────────

def _fmt_peer_record(rec: dict) -> dict:
    """Format all numeric fields in a peer record."""
    if not isinstance(rec, dict):
        return rec
    out = {}
    for k, v in rec.items():
        if v is None or isinstance(v, str) or isinstance(v, bool):
            out[k] = v
            continue

        # Nested objects: segment_revenue_usd_b, geographic_revenue_pct, etc.
        if isinstance(v, dict):
            inner = {}
            for ik, iv in v.items():
                if isinstance(iv, (int, float)) and not isinstance(iv, bool):
                    if k.endswith("_usd_b"):
                        inner[ik] = fmt_b(iv)
                    elif k.endswith("_pct"):
                        inner[ik] = fmt_pct(iv)
                    else:
                        inner[ik] = iv
                else:
                    inner[ik] = iv
            out[k] = inner
            continue

        if not isinstance(v, (int, float)):
            out[k] = v
            continue

        # Numeric formatting by key pattern
        if k.endswith("_usd_b"):
            out[k] = fmt_b(v)
        elif k.endswith("_pct"):
            out[k] = fmt_pct(v)
        elif k.endswith("_days"):
            out[k] = round(v)
        elif ("ev_to_" in k or "price_to_" in k
              or k in ("net_debt_to_ebitda", "debt_to_equity",
                       "interest_coverage", "current_ratio", "income_quality")):
            out[k] = fmt_x(v)
        else:
            out[k] = v
    return out


def _fmt_peer_medians(pm: dict) -> dict:
    """Format peer median values."""
    if not isinstance(pm, dict):
        return pm
    out = {}
    for k, v in pm.items():
        if v is None or isinstance(v, str):
            out[k] = v
            continue
        if not isinstance(v, (int, float)):
            out[k] = v
            continue
        if k.endswith("_usd_b"):
            out[k] = fmt_b(v)
        elif k.endswith("_pct"):
            out[k] = fmt_pct(v)
        elif k.endswith("_days"):
            out[k] = round(v)
        elif ("ev_to_" in k or "price_to_" in k
              or k in ("net_debt_to_ebitda", "interest_coverage")):
            out[k] = fmt_x(v)
        else:
            out[k] = v
    return out


# ══════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════

def clean_quantitative_facts(data: dict) -> dict:
    """Format the raw quantitative fact sheet for agent consumption.

    1. Snapshot raw values in _meta._raw for downstream arithmetic
    2. Apply formatting ($M/$B, %, x) to all sections
    3. Add extraction note with currency info

    Args:
        data: Raw quantitative fact sheet from build_quantitative_facts()

    Returns:
        Formatted fact sheet (mutated in-place, also returned)
    """
    if not data:
        return data

    # ── SNAPSHOT RAW VALUES FOR DOWNSTREAM ARITHMETIC ─────────
    # The distributor needs raw numbers for table building,
    # coverage ratios, and year-series extraction. Store a deep
    # clone BEFORE formatting overwrites them with strings.
    raw_sections = [
        "s2_s4_revenue_splits", "s4_rd", "s5_subject_margins", "s5_share_data",
        "s7_working_capital", "s9_capital_allocation", "s2_capital_structure",
        "s11_income_statement", "s11_cash_flow", "s11_balance_sheet", "s11_returns",
        "s13_valuation", "s12_peer_benchmarking",
    ]

    meta = data.get("_meta", {})
    meta["_raw"] = {}
    for key in raw_sections:
        if key in data:
            meta["_raw"][key] = copy.deepcopy(data[key])
    data["_meta"] = meta

    # ── APPLY TO MAIN SECTIONS ────────────────────────────────
    sections_to_format = [
        "s2_capital_structure", "s2_s4_revenue_splits", "s4_rd",
        "s5_subject_margins", "s5_share_data", "s7_working_capital",
        "s9_capital_allocation", "s11_income_statement", "s11_cash_flow",
        "s11_balance_sheet", "s11_returns", "s13_valuation",
    ]

    for key in sections_to_format:
        if key in data:
            data[key] = _fmt_section(data[key])

    # ── FORWARD ESTIMATES ─────────────────────────────────────
    if isinstance(data.get("s10_s13_forward_estimates"), list):
        data["s10_s13_forward_estimates"] = _apply_to_array_items(
            data["s10_s13_forward_estimates"]
        )

    # ── BEAT/MISS ─────────────────────────────────────────────
    beat_miss = data.get("s9_guidance_beat_miss", {})
    if isinstance(beat_miss.get("quarters"), list):
        beat_miss["quarters"] = _apply_to_array_items(beat_miss["quarters"])
    if isinstance(beat_miss.get("summary"), dict):
        s = beat_miss["summary"]
        for k in list(s.keys()):
            if k.endswith("_pct") and isinstance(s[k], (int, float)):
                s[k] = fmt_pct(s[k])

    # ── PEER COMP TABLES ──────────────────────────────────────
    peer_bench = data.get("s12_peer_benchmarking", {})
    if peer_bench:
        comp_tables = [
            "profitability_comps", "growth_comps", "valuation_comps",
            "leverage_comps", "efficiency_comps", "geographic_comps",
        ]
        for tbl in comp_tables:
            if isinstance(peer_bench.get(tbl), list):
                formatted_rows = []
                for row in peer_bench[tbl]:
                    if not isinstance(row, dict):
                        formatted_rows.append(row)
                        continue
                    out = {}
                    for k, v in row.items():
                        if v is None or isinstance(v, str):
                            out[k] = v
                        elif k == "company":
                            out[k] = v
                        elif k.endswith("_usd_b"):
                            out[k] = fmt_b(v)
                        elif k.endswith("_pct"):
                            out[k] = fmt_pct(v)
                        elif k.endswith("_days"):
                            out[k] = _rd(v, 0)
                        elif ("ev_to_" in k or "price_to_" in k
                              or k in ("net_debt_to_ebitda", "debt_to_equity",
                                       "interest_coverage", "current_ratio")):
                            out[k] = fmt_x(v)
                        else:
                            out[k] = v
                    formatted_rows.append(out)
                peer_bench[tbl] = formatted_rows

        # Peer medians (top-level)
        if isinstance(peer_bench.get("peer_medians"), dict):
            peer_bench["peer_medians"] = _fmt_peer_medians(peer_bench["peer_medians"])

        # peers_full — format all numeric fields in every peer record
        pf = peer_bench.get("peers_full", {})
        if isinstance(pf, dict):
            if isinstance(pf.get("by_symbol"), dict):
                for sym, years in pf["by_symbol"].items():
                    if isinstance(years, dict):
                        for yr, rec in years.items():
                            pf["by_symbol"][sym][yr] = _fmt_peer_record(rec)

            if isinstance(pf.get("latest"), list):
                pf["latest"] = [_fmt_peer_record(r) for r in pf["latest"]]

            if isinstance(pf.get("peer_medians"), dict):
                pf["peer_medians"] = _fmt_peer_medians(pf["peer_medians"])

    # ── COMPETITIVE LANDSCAPE ─────────────────────────────────
    if isinstance(data.get("s6_competitive_landscape"), list):
        formatted = []
        for row in data["s6_competitive_landscape"]:
            if not isinstance(row, dict):
                formatted.append(row)
                continue
            out = {}
            for k, v in row.items():
                if v is None or isinstance(v, str):
                    out[k] = v
                elif k == "company":
                    out[k] = v
                elif k.endswith("_usd_b"):
                    out[k] = fmt_b(v)
                elif k.endswith("_pct"):
                    out[k] = fmt_pct(v)
                else:
                    out[k] = v
            formatted.append(out)
        data["s6_competitive_landscape"] = formatted

    # ── PEER VALUATION MEDIANS IN S13 ─────────────────────────
    s13 = data.get("s13_valuation", {})
    if isinstance(s13.get("peer_valuation_medians"), dict):
        pvm = s13["peer_valuation_medians"]
        for k in list(pvm.keys()):
            v = pvm[k]
            if v is None or isinstance(v, str):
                continue
            if k.endswith("_pct"):
                pvm[k] = fmt_pct(v)
            elif "ev_to_" in k or "price_to_" in k:
                pvm[k] = fmt_x(v)

    # ── EXTRACTION NOTE ───────────────────────────────────────
    cur = meta.get("reported_currency", "USD")
    if cur == "USD":
        cur_note = "Dollar values prefixed with $."
    else:
        cur_symbol = f"{cur} "
        cur_note = (
            f"Values reported in {cur}. "
            f"Dollar amounts show {cur_symbol} prefix where converted to USD."
        )
    meta["extraction_note"] = (
        f"{cur_note} Percentages suffixed with %. "
        "Multiples suffixed with x. All values pre-formatted for direct use in prose."
    )

    return data
