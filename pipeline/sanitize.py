"""Unified sanitation / presentation layer for writer-facing payloads.

Purpose:
- Convert internal tags to human-readable labels
- Format numeric values consistently ($B / $T / %, x, days, per-share)
- Preserve raw machine-readable facts elsewhere in the pipeline
- Give writers a single sanitized view instead of ad hoc cleaning + humanize patches
"""

from __future__ import annotations

import math
import re
from typing import Any

try:
    # Use the existing label map as a seed while centralizing sanitation here.
    from pipeline.distributors import _TAG_TO_HUMAN as LEGACY_LABELS
except Exception:  # pragma: no cover - defensive fallback
    LEGACY_LABELS = {}


LABEL_OVERRIDES: dict[str, str] = {
    "pct_cashflow": "Cash Flow Margin",
    "cashflow_margin_pct": "Cash Flow Margin",
    "fcf_margin_pct": "Free Cash Flow Margin",
    "ocf_margin_pct": "Operating Cash Flow Margin",
    "price_to_earnings": "P/E",
    "price_to_book": "P/B",
    "price_to_tangible_book": "P/TBV",
    "price_to_fcf": "P/FCF",
    "ev_to_ebitda": "EV/EBITDA",
    "ev_to_sales": "EV/Sales",
    "ffo_per_share": "FFO / Share",
    "affo_per_share": "AFFO / Share",
    "noi": "NOI",
    "noi_margin": "NOI Margin",
    "cap_rate_proxy": "Cap Rate Proxy",
    "capex_pct_rev": "CapEx / Revenue",
    "capex_pct_of_revenue": "CapEx / Revenue",
    "capex_to_revenue_pct": "CapEx / Revenue",
    "CapEx as % of Revenue": "CapEx / Revenue",
    "CapEx as % of Revenue (%)": "CapEx / Revenue",
    "s10_financial_flags": "Financial Quality Flags",
}

TOKEN_LABELS: dict[str, str] = {**LEGACY_LABELS, **LABEL_OVERRIDES}

PRESERVE_KEY_PATTERNS = [
    re.compile(r"^section_\d+$"),
    re.compile(r"^_"),
]


def _preserve_key(key: str) -> bool:
    return any(p.match(key) for p in PRESERVE_KEY_PATTERNS)


def _is_formatted_string(s: str) -> bool:
    if not isinstance(s, str):
        return False
    # Only treat short scalar-like strings as already formatted values.
    if len(s) > 40:
        return False
    return any(tok in s for tok in ("$", "%", "x", "→")) or s == "NM"


def _sanitize_text_tokens(text: str) -> str:
    """Replace internal tag references inside free-text strings."""
    out = text
    if "_" not in out and not re.search(r"[a-z][A-Z]", out):
        return out
    for raw_key in sorted(TOKEN_LABELS.keys(), key=len, reverse=True):
        label = TOKEN_LABELS[raw_key]
        out = re.sub(rf"\b{re.escape(raw_key)}\b", label, out)
    return out


def _to_float(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _trim_zeros(s: str) -> str:
    return s[:-3] if s.endswith(".00") else s


def _fmt_money_from_millions(v: float) -> str:
    a = abs(v)
    sign = "-" if v < 0 else ""
    if a >= 1_000_000:
        return f"{sign}${_trim_zeros(f'{a / 1_000_000:.2f}')}T"
    if a >= 1000:
        return f"{sign}${_trim_zeros(f'{a / 1000:.2f}')}B"
    return f"{sign}${_trim_zeros(f'{a:.2f}')}M"


def _fmt_money_from_billions(v: float) -> str:
    a = abs(v)
    sign = "-" if v < 0 else ""
    if a >= 1000:
        return f"{sign}${_trim_zeros(f'{a / 1000:.2f}')}T"
    if a >= 1:
        return f"{sign}${_trim_zeros(f'{a:.2f}')}B"
    return f"{sign}${_trim_zeros(f'{a * 1000:.2f}')}M"


def _fmt_usd_raw(v: float) -> str:
    a = abs(v)
    sign = "-" if v < 0 else ""
    if a >= 1e12:
        return f"{sign}${_trim_zeros(f'{a / 1e12:.2f}')}T"
    if a >= 1e9:
        return f"{sign}${_trim_zeros(f'{a / 1e9:.2f}')}B"
    if a >= 1e6:
        return f"{sign}${_trim_zeros(f'{a / 1e6:.2f}')}M"
    if a >= 1000:
        return f"{sign}${a:,.0f}"
    return f"{sign}${_trim_zeros(f'{a:.2f}') }"


def _fmt_pct(v: float) -> str:
    return f"{round(v, 1)}%"


def _fmt_multiple(v: float) -> str:
    return f"{round(v, 1)}x"


def _fmt_days(v: float) -> str:
    return f"{round(v):.0f}"


def _fmt_per_share(v: float) -> str:
    return f"${_trim_zeros(f'{v:.2f}') }"


def _fmt_millions_count(v: float) -> str:
    if abs(v) >= 1000:
        return f"{v:,.1f}M"
    return f"{v:,.2f}M"


def infer_semantic_type(key: str) -> str:
    k = key or ""
    kl = k.lower()
    if kl.endswith("_usd_m"):
        return "usd_m"
    if kl.endswith("_usd_b"):
        return "usd_b"
    if kl.endswith("_pct") or kl.startswith("pct_") or "_margin_" in kl or kl.endswith("_margin") or "yield" in kl or "growth" in kl:
        return "pct"
    if kl.endswith("_days") or kl.startswith("days_"):
        return "days"
    if kl.endswith("_per_share") or kl in {"tbv_per_share", "book_value_per_share", "eps_diluted", "eps_basic"}:
        return "per_share"
    if "shares" in kl and ("million" in kl or kl.endswith("_m")):
        return "shares_m"
    if any(tok in kl for tok in ["price_to_", "ev_to_", "_ratio", "coverage", "multiple", "_to_"]):
        return "multiple"
    if kl in {"price", "current_price"}:
        return "usd_raw"
    return "plain"


def sanitize_scalar(key: str, value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        if _is_formatted_string(value):
            return value
        # Try to coerce clean numeric strings through formatter
        fv = _to_float(value)
        if fv is None:
            return _sanitize_text_tokens(value)
        value = fv

    fv = _to_float(value)
    if fv is None:
        return value

    semantic = infer_semantic_type(key)
    if semantic == "usd_m":
        return _fmt_money_from_millions(fv)
    if semantic == "usd_b":
        return _fmt_money_from_billions(fv)
    if semantic == "pct":
        return _fmt_pct(fv)
    if semantic == "multiple":
        return _fmt_multiple(fv)
    if semantic == "days":
        return _fmt_days(fv)
    if semantic == "per_share":
        return _fmt_per_share(fv)
    if semantic == "shares_m":
        return _fmt_millions_count(fv)
    if semantic == "usd_raw":
        return _fmt_usd_raw(fv)
    return round(fv, 2)


def sanitize_label(key: str) -> str:
    if key in LABEL_OVERRIDES:
        return LABEL_OVERRIDES[key]
    if key in LEGACY_LABELS:
        return LEGACY_LABELS[key]

    # If a key already looks human-readable, preserve it verbatim.
    if " " in key and "_" not in key:
        return key

    s = key
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s)  # camelCase → words
    s = s.replace("_usd_m", "").replace("_usd_b", "")
    s = s.replace("_pct", "").replace("_raw", "")
    s = s.replace("_per_share", " Per Share")
    s = s.replace("_", " ")
    s = re.sub(r"\s+", " ", s).strip()
    s = s.title()
    # Friendly acronym cleanup
    s = s.replace("Fcf", "FCF").replace("Ebitda", "EBITDA").replace("Roe", "ROE")
    s = s.replace("Roic", "ROIC").replace("Noi", "NOI").replace("Ffo", "FFO").replace("Affo", "AFFO")
    s = s.replace("Pct", "%")
    return s


def sanitize_payload(obj: Any, parent_key: str = "") -> Any:
    if isinstance(obj, dict):
        result = {}
        for key, val in obj.items():
            out_key = key if _preserve_key(str(key)) else sanitize_label(str(key))
            result[out_key] = sanitize_payload(val, parent_key=str(key))
        return result
    if isinstance(obj, list):
        return [sanitize_payload(item, parent_key=parent_key) for item in obj]
    return sanitize_scalar(parent_key, obj)


def sanitize_for_llm(obj: Any) -> Any:
    """Public alias for writer-facing sanitation."""
    return sanitize_payload(obj)
