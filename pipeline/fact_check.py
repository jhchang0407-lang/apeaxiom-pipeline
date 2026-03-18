"""Quarterly Fact Verify & Patch — Python port of Fact_Check.js (346 lines).

DETERMINISTIC — no AI involved. Pure numeric matching.

Pipeline placement: AFTER Quarterly Writer, BEFORE Quarterly Formatter.

What it does:
  1. Reads raw fact data from the Fact Extract stage
  2. Reads precomputed tables from the Quarterly Distributor
  3. Walks writer prose strings and extracts financial claims
  4. Checks each against the raw fact data + precomputed tables
  5. If a number is close but wrong, patches it (5 % tolerance)
  6. If a number cannot be traced, flags it as suspicious

Source: memo_practice_nodes/Fact_Check.js v2
"""

from __future__ import annotations

import math
import re
from typing import Any


# ====================================================================
# VALUE INDEX
# Collects every numeric value from raw fact data + precomputed tables.
# ====================================================================

class _ValueIndex:
    """Mutable container that accumulates (value, source) pairs."""

    def __init__(self) -> None:
        self._entries: list[dict[str, Any]] = []

    # ── add ──────────────────────────────────────────────────────
    def add(self, val: Any, source: str) -> None:
        if val is None or not isinstance(val, (int, float)):
            return
        if math.isnan(val) or math.isinf(val):
            return
        if abs(val) < 0.01:
            return
        self._entries.append({"raw": val, "source": source})

    # ── recursive walk ──────────────────────────────────────────
    def walk_raw(self, obj: Any, path: str) -> None:
        if obj is None or not isinstance(obj, (dict, list)):
            return
        if isinstance(obj, list):
            for i, item in enumerate(obj):
                self.walk_raw(item, f"{path}[{i}]")
            return
        for key, val in obj.items():
            fp = f"{path}.{key}" if path else key
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                self.add(val, fp)
            elif isinstance(val, (dict, list)):
                self.walk_raw(val, fp)

    @property
    def entries(self) -> list[dict[str, Any]]:
        return self._entries

    def __len__(self) -> int:
        return len(self._entries)


def _parse_formatted(s: str) -> float | None:
    """Parse a pre-formatted string back to a numeric value.

    Handles patterns like "$4.40B", "$1,200M", "12.3%", "1.5x".
    Dollar values are normalised to millions for index consistency.
    """
    if not isinstance(s, str) or not s:
        return None

    # Suffix form: $1,234.56B  /  $4.40T  etc.
    suffix_match = re.search(r"\$?([\d,]+(?:\.\d+)?)\s*(T|B|M|K)", s, re.IGNORECASE)
    if suffix_match:
        num = _safe_float(suffix_match.group(1).replace(",", ""))
        if num is None:
            return None
        unit = suffix_match.group(2).upper()
        if unit == "T":
            return num * 1e6
        if unit == "B":
            return num * 1000
        if unit == "M":
            return num
        if unit == "K":
            return num / 1000
        return None

    # Fallback: strip common decoration and try float
    clean = re.sub(r"[,$%x\s]", "", s)
    clean = re.sub(r"ppts?", "", clean, flags=re.IGNORECASE)
    return _safe_float(clean)


def _safe_float(s: str) -> float | None:
    try:
        v = float(s)
        return None if math.isnan(v) else v
    except (ValueError, TypeError):
        return None


def _build_value_index(
    raw_facts: dict[str, Any],
    precomputed_tables: dict[str, Any],
) -> _ValueIndex:
    """Build the full value index from raw facts + precomputed tables."""
    idx = _ValueIndex()

    # ── Index raw fact data ──────────────────────────────────────
    # Flatten segment structure to avoid false matches on
    # segment_revenue_total and segment_coverage_pct.
    fact_copy = dict(raw_facts)
    segments_obj = raw_facts.get("segments")
    if (
        isinstance(segments_obj, dict)
        and isinstance(segments_obj.get("items"), list)
    ):
        fact_copy["segments"] = segments_obj["items"]

    idx.walk_raw(fact_copy, "fact")

    # ── Index precomputed table values ───────────────────────────
    for table_name in (
        "precomputed_results_table",
        "precomputed_beat_miss_table",
        "precomputed_segment_table",
    ):
        table = precomputed_tables.get(table_name) or []
        for row in table:
            for key, val in row.items():
                if isinstance(val, str):
                    parsed = _parse_formatted(val)
                    if parsed is not None:
                        row_label = row.get("metric") or row.get("segment") or ""
                        idx.add(parsed, f"{table_name}.{row_label}.{key}")

    return idx


# ====================================================================
# FORMATTING HELPERS (aligned with quarterly distributor v6)
# ====================================================================

def _trim_zeros(s: str) -> str:
    return s[:-3] if s.endswith(".00") else s


def _format_dollar(raw: float, claimed_unit: str) -> str | None:
    """Format a raw value (in millions) to a dollar string with unit."""
    absv = abs(raw)
    sign = "-" if raw < 0 else ""

    if claimed_unit == "T":
        if absv >= 1e6:
            return f"{sign}${absv / 1e6:.2f}T"
        if absv >= 1e3:
            return f"{sign}${_trim_zeros(f'{absv / 1e3:.2f}')}B"
        return f"{sign}${_trim_zeros(f'{absv:.2f}')}M"

    if claimed_unit == "B":
        if absv >= 1e6:
            return f"{sign}${absv / 1e6:.2f}T"
        if absv >= 1e3:
            return f"{sign}${_trim_zeros(f'{absv / 1e3:.2f}')}B"
        if absv >= 1:
            return f"{sign}${_trim_zeros(f'{absv:.2f}')}M"
        return f"{sign}${int(absv * 1000)}K"

    if claimed_unit == "M":
        if absv >= 1e3:
            return f"{sign}${_trim_zeros(f'{absv / 1e3:.2f}')}B"
        return f"{sign}${_trim_zeros(f'{absv:.2f}')}M"

    if claimed_unit == "K":
        return f"{sign}${int(round(absv * 1000))}K"

    return None


def _format_pct(raw: float) -> str:
    return f"{abs(raw):.1f}%"


def _format_ratio(raw: float) -> str:
    return f"{abs(raw):.1f}x"


# ====================================================================
# FIND CLOSEST MATCH (5 % tolerance, 0.5 % exact threshold)
# ====================================================================

def _find_match(
    claimed_number: float,
    claimed_type: str,
    claimed_unit: str | None,
    value_index: _ValueIndex,
) -> dict[str, Any] | None:
    """Find the closest value-index entry within 5 % tolerance."""
    best_match = None
    best_dist = float("inf")

    for entry in value_index.entries:
        abs_raw = abs(entry["raw"])

        if claimed_type == "dollar":
            # Convert claimed number to millions for comparison
            multipliers = {"T": 1e6, "B": 1000, "M": 1, "K": 1 / 1000}
            claimed_in_m = claimed_number * multipliers.get(claimed_unit or "M", 1)

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

    if best_match is None:
        return None

    # Exact match (< 0.5 % distance)
    if best_dist < 0.005:
        return {"match": best_match, "exact": True}

    # Close but wrong -- compute correction
    correction = None
    if claimed_type == "dollar":
        correction = _format_dollar(best_match["raw"], claimed_unit or "M")
    elif claimed_type == "pct":
        correction = _format_pct(best_match["raw"])
    elif claimed_type == "ratio":
        correction = _format_ratio(best_match["raw"])

    return {
        "match": best_match,
        "exact": False,
        "correction": correction,
        "distance": best_dist,
    }


# ====================================================================
# REGEX PATTERNS
# ====================================================================

DOLLAR_RE = re.compile(r"\$([\d,]+(?:\.\d+)?)\s*(T|B|M|K)\b")
PCT_RE = re.compile(r"(?<![.$\d])(\d+(?:\.\d+)?)\s*%")
RATIO_RE = re.compile(r"(?<![.$\d])(\d+(?:\.\d+)?)\s*x\b")


# ====================================================================
# PATCH ENGINE
# ====================================================================

class _PatchState:
    """Mutable counters shared across all patch calls."""

    def __init__(self) -> None:
        self.patch_count: int = 0
        self.verified_count: int = 0
        self.suspicious_claims: list[dict[str, str]] = []


def _patch_string(
    s: str,
    value_index: _ValueIndex,
    state: _PatchState,
) -> str:
    """Scan a single prose string, verify or patch financial claims."""
    if not isinstance(s, str) or len(s) < 30:
        return s

    patched = s

    # ── Dollar amounts ───────────────────────────────────────────
    def _replace_dollar(m: re.Match) -> str:
        full = m.group(0)
        num = _safe_float(m.group(1).replace(",", ""))
        unit = m.group(2).upper()
        if num is None:
            return full

        result = _find_match(num, "dollar", unit, value_index)
        if result is None:
            # Flag as suspicious if meaningfully large and not a round number
            is_round = (num == int(num)) and num <= 100
            if num > 1 and not is_round:
                state.suspicious_claims.append({
                    "claim": full,
                    "context": s[:120],
                })
            return full
        if result["exact"]:
            state.verified_count += 1
            return full
        if result.get("correction"):
            state.patch_count += 1
            return result["correction"]
        state.verified_count += 1
        return full

    patched = DOLLAR_RE.sub(_replace_dollar, patched)

    # ── Percentages ──────────────────────────────────────────────
    def _replace_pct(m: re.Match) -> str:
        full = m.group(0)
        num = _safe_float(m.group(1))
        if num is None or num == 0 or num == 100:
            return full
        # Skip round integers -- likely contextual, not data claims
        if num == int(num):
            return full

        result = _find_match(num, "pct", None, value_index)
        if result is None:
            return full
        if result["exact"]:
            state.verified_count += 1
            return full
        if result.get("correction"):
            state.patch_count += 1
            return result["correction"]
        state.verified_count += 1
        return full

    patched = PCT_RE.sub(_replace_pct, patched)

    # ── Ratios ───────────────────────────────────────────────────
    def _replace_ratio(m: re.Match) -> str:
        full = m.group(0)
        num = _safe_float(m.group(1))
        if num is None or num == 0:
            return full

        result = _find_match(num, "ratio", None, value_index)
        if result is None:
            return full
        if result["exact"]:
            state.verified_count += 1
            return full
        if result.get("correction"):
            state.patch_count += 1
            return result["correction"]
        state.verified_count += 1
        return full

    patched = RATIO_RE.sub(_replace_ratio, patched)

    return patched


# Keys whose values should NOT be patched (titles, labels, etc.)
_SKIP_KEYS = frozenset(["section_title", "section_thesis"])


def _walk_and_patch(
    obj: Any,
    value_index: _ValueIndex,
    state: _PatchState,
) -> Any:
    """Recursively walk a data structure and patch all prose strings."""
    if obj is None:
        return obj
    if isinstance(obj, str):
        return _patch_string(obj, value_index, state)
    if isinstance(obj, (int, float, bool)):
        return obj
    if isinstance(obj, list):
        return [_walk_and_patch(item, value_index, state) for item in obj]
    if isinstance(obj, dict):
        result = {}
        for key, val in obj.items():
            if key in _SKIP_KEYS:
                result[key] = val
            else:
                result[key] = _walk_and_patch(val, value_index, state)
        return result
    return obj


# ====================================================================
# PUBLIC API
# ====================================================================

def fact_check_quarterly(
    writer_output: dict[str, Any],
    raw_facts: dict[str, Any],
    precomputed_tables: dict[str, Any],
) -> dict[str, Any]:
    """Verify and patch financial claims in quarterly writer output.

    This is a deterministic, regex-based fact checker. It extracts every
    numeric claim from the writer prose ($X.XXB, X.X%, X.Xx), matches
    each against a value index built from raw fact data and precomputed
    tables, and patches numbers that are close but wrong (within 5 %
    tolerance).

    Args:
        writer_output: Dict from the quarterly writer. May contain keys
            like ``output`` (str or JSON string), ``parsed`` (dict),
            ``text`` (str), or arbitrary nested prose fields.
        raw_facts: Raw fact data from the Fact Extract stage.
        precomputed_tables: Dict with ``precomputed_results_table``,
            ``precomputed_beat_miss_table``, ``precomputed_segment_table``.

    Returns:
        Dict with:
            ``patched_output`` -- the writer output with corrected numbers
            ``patches_applied`` -- count of numbers that were patched
            ``verified_claims`` -- count of numbers verified as correct
            ``suspicious_claims`` -- list of claims that could not be traced
    """
    # Build value index from all sources
    value_index = _build_value_index(raw_facts, precomputed_tables)

    state = _PatchState()

    # ── Patch the writer output ──────────────────────────────────
    patched = dict(writer_output)

    # Writer output can arrive in different shapes
    output_val = writer_output.get("output")
    if isinstance(output_val, str) and output_val.strip().startswith("{"):
        try:
            import json
            parsed = json.loads(output_val)
            patched_parsed = _walk_and_patch(parsed, value_index, state)
            patched["output"] = json.dumps(patched_parsed)
        except (json.JSONDecodeError, ValueError):
            patched["output"] = _patch_string(output_val, value_index, state)
    elif isinstance(output_val, dict):
        # Python pipeline returns structured dicts — walk and patch all prose
        patched["output"] = _walk_and_patch(output_val, value_index, state)
    elif isinstance(output_val, str):
        patched["output"] = _patch_string(output_val, value_index, state)

    parsed_val = writer_output.get("parsed")
    if isinstance(parsed_val, dict):
        patched["parsed"] = _walk_and_patch(parsed_val, value_index, state)

    text_val = writer_output.get("text")
    if isinstance(text_val, str):
        patched["text"] = _patch_string(text_val, value_index, state)

    # ── Attach verification metadata ────────────────────────────
    try:
        patched["_fact_check"] = {
            "patches_applied": state.patch_count,
            "verified_claims": state.verified_count,
            "suspicious_claims_count": len(state.suspicious_claims),
            "suspicious": state.suspicious_claims[:15],
            "value_index_size": len(value_index),
        }
    except Exception:
        pass  # metadata attachment failed silently

    return {
        "patched_output": patched,
        "patches_applied": state.patch_count,
        "verified_claims": state.verified_count,
        "suspicious_claims": state.suspicious_claims,
    }
