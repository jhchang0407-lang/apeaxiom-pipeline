"""Quarterly Fact Check — Deterministic number verification and patching.

Walks writer prose strings, extracts financial claims ($X.XB, X.X%, Xx),
matches them against a value index built from raw facts + precomputed tables,
patches close-but-wrong numbers, and flags unverifiable claims.

No AI involved — pure numeric matching.
"""

from __future__ import annotations

import math
import re
from typing import Any

# ═══════════════════════════════════════════════════════════════════════
# REGEX PATTERNS
# ═══════════════════════════════════════════════════════════════════════

DOLLAR_RE = re.compile(r"\$([0-9,]+(?:\.[0-9]+)?)\s*(T|B|M|K)\b")
PCT_RE = re.compile(r"(?<![.$\d])(\d+(?:\.\d+)?)\s*%")
RATIO_RE = re.compile(r"(?<![.$\d])(\d+(?:\.\d+)?)\s*x\b")


# ═══════════════════════════════════════════════════════════════════════
# VALUE INDEX BUILDER
# ═══════════════════════════════════════════════════════════════════════

def _build_value_index(raw_facts: dict, precomputed: dict) -> list[dict]:
    """Collect every numeric value from raw facts and precomputed tables."""
    index: list[dict] = []

    def _add(val: Any, source: str) -> None:
        if val is None or not isinstance(val, (int, float)):
            return
        if isinstance(val, float) and math.isnan(val):
            return
        if abs(val) < 0.01:
            return
        index.append({"raw": val, "source": source})

    def _walk(obj: Any, path: str) -> None:
        if obj is None:
            return
        if isinstance(obj, (int, float)):
            _add(obj, path)
        elif isinstance(obj, dict):
            for k, v in obj.items():
                _walk(v, f"{path}.{k}" if path else k)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                _walk(item, f"{path}[{i}]")

    # Flatten segment structure before indexing
    facts_for_index = dict(raw_facts)
    segments = raw_facts.get("segments", {})
    if isinstance(segments, dict) and "items" in segments:
        facts_for_index["segments"] = segments["items"]

    _walk(facts_for_index, "fact")

    # Index precomputed table values (parse formatted strings back to numbers)
    for table_name in [
        "precomputed_results_table",
        "precomputed_beat_miss_table",
        "precomputed_segment_table",
        "precomputed_sector_kpi_table",
    ]:
        table = precomputed.get(table_name, [])
        if not isinstance(table, list):
            continue
        for row in table:
            if not isinstance(row, dict):
                continue
            label = row.get("metric") or row.get("segment") or "?"
            for key, val in row.items():
                if isinstance(val, str):
                    parsed = _parse_formatted(val)
                    if parsed is not None:
                        _add(parsed, f"{table_name}.{label}.{key}")

    return index


def _parse_formatted(s: str) -> float | None:
    """Parse a pre-formatted string like '$4.40B' or '12.3%' back to a number."""
    if not s or not isinstance(s, str):
        return None

    # Dollar amounts with suffix
    m = re.match(r"\$?([0-9,.]+)\s*(T|B|M|K)", s, re.IGNORECASE)
    if m:
        num = float(m.group(1).replace(",", ""))
        unit = m.group(2).upper()
        if math.isnan(num):
            return None
        # Convert to millions for consistency
        if unit == "T":
            return num * 1e6
        if unit == "B":
            return num * 1000
        if unit == "M":
            return num
        if unit == "K":
            return num / 1000

    # Strip common non-numeric chars
    clean = re.sub(r"[,$%x\s]", "", s)
    clean = re.sub(r"ppts?", "", clean, flags=re.IGNORECASE)
    try:
        n = float(clean)
        return n if not math.isnan(n) else None
    except (ValueError, TypeError):
        return None


# ═══════════════════════════════════════════════════════════════════════
# MATCHING
# ═══════════════════════════════════════════════════════════════════════

def _find_match(
    claimed_number: float,
    claimed_type: str,
    claimed_unit: str | None,
    value_index: list[dict],
) -> dict | None:
    """Find the closest match in the value index."""
    best_match = None
    best_dist = float("inf")

    for entry in value_index:
        abs_raw = abs(entry["raw"])

        if claimed_type == "dollar":
            # Convert claimed to millions
            if claimed_unit == "T":
                claimed_in_m = claimed_number * 1e6
            elif claimed_unit == "B":
                claimed_in_m = claimed_number * 1000
            elif claimed_unit == "M":
                claimed_in_m = claimed_number
            elif claimed_unit == "K":
                claimed_in_m = claimed_number / 1000
            else:
                claimed_in_m = claimed_number

            dist_as_m = abs(claimed_in_m - abs_raw) / max(abs_raw, 1)
            dist_as_b = abs(claimed_number - abs_raw) / max(abs_raw, 1)
            dist = min(dist_as_m, dist_as_b)

        elif claimed_type in ("pct", "ratio"):
            dist = abs(claimed_number - abs_raw) / max(abs_raw, 0.1)

        else:
            continue

        if dist < best_dist and dist < 0.05:
            best_dist = dist
            best_match = entry

    if not best_match:
        return None

    if best_dist < 0.005:
        return {"match": best_match, "exact": True}

    # Build correction string
    correction = None
    if claimed_type == "dollar":
        correction = _format_dollar(best_match["raw"], claimed_unit)
    elif claimed_type == "pct":
        correction = f"{abs(best_match['raw']):.1f}%"
    elif claimed_type == "ratio":
        correction = f"{abs(best_match['raw']):.1f}x"

    return {
        "match": best_match,
        "exact": False,
        "correction": correction,
        "distance": best_dist,
    }


def _format_dollar(raw: float, claimed_unit: str | None) -> str | None:
    """Format a raw value (in $M) to match the claimed unit scale."""
    ab = abs(raw)
    sign = "-" if raw < 0 else ""

    def _trim(s: str) -> str:
        return s[:-3] if s.endswith(".00") else s

    if claimed_unit == "T":
        if ab >= 1e6:
            return f"{sign}${ab / 1e6:.2f}T"
        if ab >= 1e3:
            return f"{sign}${_trim(f'{ab / 1e3:.2f}')}B"
        return f"{sign}${_trim(f'{ab:.2f}')}M"

    if claimed_unit == "B":
        if ab >= 1e6:
            return f"{sign}${ab / 1e6:.2f}T"
        if ab >= 1e3:
            return f"{sign}${_trim(f'{ab / 1e3:.2f}')}B"
        if ab >= 1:
            return f"{sign}${_trim(f'{ab:.2f}')}M"
        return f"{sign}${ab * 1000:.0f}K"

    if claimed_unit == "M":
        if ab >= 1e3:
            return f"{sign}${_trim(f'{ab / 1e3:.2f}')}B"
        return f"{sign}${_trim(f'{ab:.2f}')}M"

    if claimed_unit == "K":
        return f"{sign}${round(ab * 1000)}K"

    return None


# ═══════════════════════════════════════════════════════════════════════
# PATCH PROSE
# ═══════════════════════════════════════════════════════════════════════

_SKIP_KEYS = {"section_title", "section_thesis"}


def _patch_string(
    text: str,
    value_index: list[dict],
    stats: dict,
) -> str:
    """Patch a single prose string, correcting close-but-wrong numbers."""
    if not isinstance(text, str) or len(text) < 30:
        return text

    # ── Dollar amounts ──
    def _dollar_repl(m: re.Match) -> str:
        num_str, unit = m.group(1), m.group(2)
        num = float(num_str.replace(",", ""))
        if math.isnan(num):
            return m.group(0)

        result = _find_match(num, "dollar", unit, value_index)
        if not result:
            is_round = num == int(num) and num <= 100
            if num > 1 and not is_round:
                stats["suspicious"].append({
                    "claim": m.group(0),
                    "context": text[:120],
                })
            return m.group(0)

        if result["exact"]:
            stats["verified"] += 1
            return m.group(0)
        if result.get("correction"):
            stats["patched"] += 1
            return result["correction"]
        stats["verified"] += 1
        return m.group(0)

    text = DOLLAR_RE.sub(_dollar_repl, text)

    # ── Percentages ──
    def _pct_repl(m: re.Match) -> str:
        num = float(m.group(1))
        if math.isnan(num) or num == 0 or num == 100:
            return m.group(0)
        # Skip round integers — likely contextual references
        if num == int(num):
            return m.group(0)

        result = _find_match(num, "pct", None, value_index)
        if not result:
            return m.group(0)
        if result["exact"]:
            stats["verified"] += 1
            return m.group(0)
        if result.get("correction"):
            stats["patched"] += 1
            return result["correction"]
        stats["verified"] += 1
        return m.group(0)

    text = PCT_RE.sub(_pct_repl, text)

    # ── Ratios ──
    def _ratio_repl(m: re.Match) -> str:
        num = float(m.group(1))
        if math.isnan(num) or num == 0:
            return m.group(0)

        result = _find_match(num, "ratio", None, value_index)
        if not result:
            return m.group(0)
        if result["exact"]:
            stats["verified"] += 1
            return m.group(0)
        if result.get("correction"):
            stats["patched"] += 1
            return result["correction"]
        stats["verified"] += 1
        return m.group(0)

    text = RATIO_RE.sub(_ratio_repl, text)

    return text


def _walk_and_patch(obj: Any, value_index: list[dict], stats: dict) -> Any:
    """Recursively walk an object and patch all prose strings."""
    if obj is None:
        return obj
    if isinstance(obj, str):
        return _patch_string(obj, value_index, stats)
    if isinstance(obj, (int, float, bool)):
        return obj
    if isinstance(obj, list):
        return [_walk_and_patch(item, value_index, stats) for item in obj]
    if isinstance(obj, dict):
        result = {}
        for key, val in obj.items():
            if key in _SKIP_KEYS:
                result[key] = val
            else:
                result[key] = _walk_and_patch(val, value_index, stats)
        return result
    return obj


# ═══════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════

def fact_check_quarterly(
    writer_output: dict,
    raw_facts: dict,
    precomputed: dict,
) -> dict:
    """Verify and patch financial claims in quarterly writer output.

    Args:
        writer_output: Dict with prose paragraph strings from the writer.
        raw_facts: Enriched facts from fact_extract.
        precomputed: Distributed data including precomputed tables.

    Returns:
        Dict with:
            patched_output: The writer output with corrected numbers.
            patches_applied: Count of numbers that were corrected.
            verified_claims: Count of numbers verified as correct.
            suspicious_claims: List of unverifiable claims.
    """
    value_index = _build_value_index(raw_facts, precomputed)

    stats = {
        "patched": 0,
        "verified": 0,
        "suspicious": [],
    }

    patched = _walk_and_patch(writer_output, value_index, stats)

    return {
        "patched_output": patched,
        "patches_applied": stats["patched"],
        "verified_claims": stats["verified"],
        "suspicious_claims": stats["suspicious"][:15],
        "value_index_size": len(value_index),
    }
