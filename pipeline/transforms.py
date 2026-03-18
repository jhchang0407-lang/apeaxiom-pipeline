"""Data Transforms — Stage 2 of the pipeline.

Ports all JavaScript pivot/flatten/clean/normalize code nodes to Python.
Each function corresponds to a specific n8n code node.

Source n8n nodes → Python functions:
  n8n_fy_financials_pivot.js  → pivot_annual()
  Quarterly_Financials.js     → pivot_quarterly()
  Earnings_Estimate.js        → pivot_estimates()
  Earning_Surprises1.js       → pivot_surprises()
  Owner_Earnings.js           → pivot_owner_earnings()
  Clean_Segments.js           → normalize_segments()
  Currency_Injector.js        → inject_currency()
"""

from __future__ import annotations

import re
from typing import Any


# ── METADATA KEYS (excluded from metric rows) ──────────────────

METADATA_KEYS = {
    "calendarYear", "fiscalYear", "period", "symbol", "date",
    "fillingDate", "acceptedDate", "cik", "reportedCurrency",
}


# ============================================================
# ANNUAL FINANCIALS PIVOT
# From: n8n_fy_financials_pivot.js (110 lines)
# Input: SEC API /financials response + /segments response
# Output: List of dicts, each { metric, symbol, "YYYY FY": value }
# ============================================================

def pivot_annual(sec_financials: dict, sec_segments: dict | None = None) -> list[dict]:
    """Flatten SEC API arrays by date, merge segments, then pivot.

    Args:
        sec_financials: Response from /financials/{ticker}
        sec_segments: Response from /segments/{ticker} (optional)

    Returns:
        Pivoted list: rows = metrics, columns = "YYYY FY"
    """
    by_date: dict[str, dict] = {}
    arrays = [
        "income_statement", "balance_sheet", "cash_flow",
        "ratios", "growth", "key_metrics",
    ]

    for key in arrays:
        arr = sec_financials.get(key)
        if not isinstance(arr, list):
            continue
        for row in arr:
            date = row.get("date")
            if not date:
                continue
            if date not in by_date:
                by_date[date] = {
                    "date": date,
                    "calendarYear": row.get("calendarYear") or date[:4],
                    "period": row.get("period") or "FY",
                    "symbol": sec_financials.get("ticker", ""),
                }
            for field, val in row.items():
                if field in ("date", "calendarYear", "fiscalYear", "period", "symbol"):
                    continue
                if val is not None:
                    by_date[date][field] = val

    # Merge segments
    if sec_segments:
        _merge_segments(by_date, sec_segments)

    # Sort by date ascending
    items = sorted(by_date.values(), key=lambda d: d.get("date", ""))
    columns = [f"{item['calendarYear']} {item['period']}" for item in items]

    # Collect all metric keys
    all_keys = set()
    for item in items:
        for key in item:
            if key not in METADATA_KEYS:
                all_keys.add(key)

    # Pivot: rows = metrics, columns = year headers
    pivoted = []
    for metric in sorted(all_keys):
        row = {"metric": metric, "symbol": items[0].get("symbol", "") if items else ""}
        for i, col in enumerate(columns):
            row[col] = items[i].get(metric) if i < len(items) else None
        pivoted.append(row)

    return pivoted


def _merge_segments(by_date: dict, sec_segments: dict) -> None:
    """Merge product and geographic segments into flat items."""

    def flatten_segs(segs_obj: dict) -> dict:
        flat = {}
        for name, data in segs_obj.items():
            if isinstance(data, dict) and data is not None:
                flat[name] = data.get("value", data)
            else:
                flat[name] = data
        return flat

    # Product segments
    product_arr = sec_segments.get("product_segments") or sec_segments.get("product") or []
    for entry in product_arr:
        cal_year = str(entry.get("year") or entry.get("period") or "")
        cal_year = cal_year.replace("FY", "").replace("CY", "")
        match = next(
            (d for d in by_date.values() if d.get("calendarYear") == cal_year),
            None,
        )
        if match and entry.get("segments"):
            match["Segment Rev Split"] = flatten_segs(entry["segments"])

    # Geographic segments
    geo_arr = sec_segments.get("geographic_segments") or sec_segments.get("geographic") or []
    for entry in geo_arr:
        cal_year = str(entry.get("year") or entry.get("period") or "")
        cal_year = cal_year.replace("FY", "").replace("CY", "")
        match = next(
            (d for d in by_date.values() if d.get("calendarYear") == cal_year),
            None,
        )
        if match and entry.get("segments"):
            match["Geographic Rev Split"] = flatten_segs(entry["segments"])


# ============================================================
# QUARTERLY FINANCIALS PIVOT
# From: Quarterly_Financials.js (44 lines)
# Input: SEC API /quarterly response
# Output: List of dicts, each { metric, symbol, "YYYY QN": value }
# ============================================================

def pivot_quarterly(sec_quarterly: dict) -> list[dict]:
    """Pivot quarterly financial data into metric rows × quarter columns."""
    # Collect all items from all statement arrays
    items = []
    for key in ("income_statement", "balance_sheet", "cash_flow"):
        arr = sec_quarterly.get(key)
        if isinstance(arr, list):
            for row in arr:
                # Deduplicate by date
                date = row.get("date", "")
                if not any(i.get("date") == date for i in items):
                    items.append(row)
                else:
                    # Merge fields into existing item
                    existing = next(i for i in items if i.get("date") == date)
                    for field, val in row.items():
                        if field not in existing and val is not None:
                            existing[field] = val

    if not items:
        return []

    # Build unique column headers sorted ascending (exclude FY from quarterly)
    columns = sorted(set(
        f"{item.get('calendarYear') or item.get('fiscalYear', '')} {item.get('period', 'FY')}"
        for item in items
        if item.get("period", "FY") != "FY"  # Only quarterly periods
    ))

    # Collect all metric keys
    metadata = {"calendarYear", "fiscalYear", "period", "symbol", "date",
                "fillingDate", "acceptedDate", "cik", "reportedCurrency"}
    all_keys = set()
    for item in items:
        for key in item:
            if key not in metadata:
                all_keys.add(key)

    # Pivot
    pivoted = []
    for metric in sorted(all_keys):
        row = {"metric": metric, "symbol": items[0].get("symbol", "")}
        for col in columns:
            year, period = col.rsplit(" ", 1)
            data_point = next(
                (item for item in items
                 if str(item.get("calendarYear") or item.get("fiscalYear", "")) == year
                 and item.get("period", "FY") == period),
                None,
            )
            row[col] = data_point.get(metric) if data_point and metric in data_point else None
        pivoted.append(row)

    return pivoted


# ============================================================
# ESTIMATES PIVOT
# From: Earnings_Estimate.js (40 lines)
# Input: FMP /analyst-estimates response
# Output: List of dicts { metric, symbol, [dates]: values }
# ============================================================

def pivot_estimates(fmp_estimates: list) -> list[dict]:
    """Pivot analyst estimates: dates as items → metrics as rows."""
    if not fmp_estimates:
        return []

    dates = sorted(set(item.get("date", "") for item in fmp_estimates if item.get("date")))

    metadata = {"date", "symbol"}
    all_keys = set()
    for item in fmp_estimates:
        for key in item:
            if key not in metadata:
                all_keys.add(key)

    pivoted = []
    for metric in sorted(all_keys):
        row = {"metric": metric, "symbol": fmp_estimates[0].get("symbol", "")}
        for date in dates:
            data_point = next((item for item in fmp_estimates if item.get("date") == date), None)
            row[date] = data_point.get(metric) if data_point else None
        pivoted.append(row)

    return pivoted


# ============================================================
# SURPRISES PIVOT
# From: Earning_Surprises1.js (40 lines)
# Input: FMP /earnings-surprises response
# Output: List of dicts { metric, symbol, [dates]: values }
# ============================================================

def pivot_surprises(fmp_surprises: list) -> list[dict]:
    """Pivot earnings surprises: dates as items → metrics as rows."""
    if not fmp_surprises:
        return []

    # Sort dates descending (newest first) then take up to 16
    dates = sorted(
        set(item.get("date", "") for item in fmp_surprises if item.get("date")),
        reverse=True,
    )[:16]

    metadata = {"date", "symbol"}
    all_keys = set()
    for item in fmp_surprises:
        for key in item:
            if key not in metadata:
                all_keys.add(key)

    pivoted = []
    for metric in sorted(all_keys):
        row = {"metric": metric, "symbol": fmp_surprises[0].get("symbol", "")}
        for date in dates:
            data_point = next((item for item in fmp_surprises if item.get("date") == date), None)
            row[date] = data_point.get(metric) if data_point else None
        pivoted.append(row)

    return pivoted


# ============================================================
# OWNER EARNINGS PIVOT
# From: Owner_Earnings.js (37 lines)
# Input: SEC API /financials response → owner_earnings array
# Output: List of dicts { metric, symbol, [dates]: values }
# ============================================================

def pivot_owner_earnings(sec_financials: dict) -> list[dict]:
    """Pivot owner earnings data by date."""
    oe_data = sec_financials.get("owner_earnings", [])
    if not oe_data:
        return []

    dates = sorted(set(item.get("date", "") for item in oe_data if item.get("date")), reverse=True)

    metadata = {"date", "symbol"}
    all_keys = set()
    for item in oe_data:
        for key in item:
            if key not in metadata:
                all_keys.add(key)

    pivoted = []
    for metric in sorted(all_keys):
        row = {"metric": metric, "symbol": oe_data[0].get("symbol", "")}
        for date in dates:
            data_point = next((item for item in oe_data if item.get("date") == date), None)
            row[date] = data_point.get(metric) if data_point else None
        pivoted.append(row)

    return pivoted


# ============================================================
# SEGMENT NAME NORMALIZER
# From: Clean_Segments.js (197 lines)
# Normalizes XBRL segment names for consistency across years
# ============================================================

# Bracket ISO code → country/region name
BRACKET_MAP = {
    "US": "United States", "BR": "Brazil", "CA": "Canada",
    "CH": "Switzerland", "CN": "China", "DE": "Germany",
    "IN": "India", "JP": "Japan", "MX": "Mexico", "NL": "Netherlands",
}

# Spaced-out XBRL abbreviations
SPACED_MAP = {
    "E M E A": "EMEA", "C N": "China", "U S": "United States",
}

# Single-word ALL CAPS countries
SINGLE_WORD_COUNTRIES = {
    "CANADA": "Canada", "CHINA": "China", "JAPAN": "Japan",
    "GERMANY": "Germany", "FRANCE": "France", "MEXICO": "Mexico",
    "BRAZIL": "Brazil", "INDIA": "India", "AUSTRALIA": "Australia",
    "IRELAND": "Ireland", "SWITZERLAND": "Switzerland", "SINGAPORE": "Singapore",
    "NETHERLANDS": "Netherlands", "ITALY": "Italy", "MALAYSIA": "Malaysia",
    "PHILIPPINES": "Philippines", "CHILE": "Chile", "POLAND": "Poland",
    "ARGENTINA": "Argentina", "EGYPT": "Egypt", "INDONESIA": "Indonesia",
    "THAILAND": "Thailand", "HUNGARY": "Hungary", "BULGARIA": "Bulgaria",
    "CALIFORNIA": "California", "FLORIDA": "Florida", "TEXAS": "Texas",
}

# Suffixes to strip
STRIP_SUFFIXES = [
    " Geographic Region [Domain]", " Geographic Region",
    " Geographic Segment", " Operations Segment",
    " Group Segment", " [member]", " [Member]",
    " [domain]", " [Domain]", " Segment", " Member",
]


def _normalize_segment_name(name: str) -> str:
    """Normalize a single segment/geography name."""
    s = name.strip()

    # 0a. Strip XBRL namespace prefixes (e.g., "us-gaap:EMEA" → "EMEA")
    if ":" in s:
        s = s.split(":", 1)[1]

    # 0b. Split CamelCase into words (NorthAmerica → North America,
    #     GreaterChina → Greater China, NonUs → Non Us)
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s)

    # 1. Spaced-out abbreviations
    if s in SPACED_MAP:
        return SPACED_MAP[s]

    # 2. Bracket ISO codes: "U [S]" → "US" → lookup
    bracket_match = re.match(r"^(\w)\s*\[(\w+)\]$", s)
    if bracket_match:
        code = (bracket_match.group(1) + bracket_match.group(2)).upper()
        return BRACKET_MAP.get(code, code)

    # 3. Strip suffixes (longest first)
    for suffix in STRIP_SUFFIXES:
        if s.lower().endswith(suffix.lower()):
            s = s[:-len(suffix)].strip()
            break

    # 4. Single-word ALL CAPS country names
    if s in SINGLE_WORD_COUNTRIES:
        return SINGLE_WORD_COUNTRIES[s]

    # 5. Title Case normalization
    is_all_caps = s == s.upper() and len(s) > 2
    is_all_lower = s == s.lower() and len(s) > 2
    has_space = " " in s

    if (is_all_caps and has_space) or is_all_lower:
        def title_word(m):
            txt = m.group(0)
            if len(txt) <= 2 and is_all_caps:
                return txt.upper()
            return txt[0].upper() + txt[1:].lower()
        s = re.sub(r"\w\S*", title_word, s)

    # 6. Normalize connector words
    s = re.sub(r" [Aa][Nn][Dd]( |$)", r" And\1", s)
    s = re.sub(r" [Oo][Ff]( |$)", r" Of\1", s)
    s = re.sub(r" [Tt][Hh][Ee]( |$)", r" The\1", s)

    return s


def _normalize_split_dict(split_obj: dict) -> dict:
    """Normalize all keys in a segment split dictionary, summing duplicates."""
    normalized = {}
    for name, revenue in split_obj.items():
        norm_name = _normalize_segment_name(name)
        if norm_name in normalized:
            normalized[norm_name] = (normalized[norm_name] or 0) + (revenue or 0)
        else:
            normalized[norm_name] = revenue
    return normalized


# ── Product segment name aliases ─────────────────────────────
# XBRL member names produce ugly CamelCase-split results for product
# segments (spelled-out numbers, concatenated names, verbose descriptors).
# This dict maps the normalized-but-ugly name → clean display name.
# Only needed for S&P 500 companies whose XBRL names are garbled.

_PRODUCT_SEGMENT_ALIASES: dict[str, str] = {
    # ── MSFT ──
    "Microsoft Three Six Five Commercial Products And Cloud Services": "Microsoft 365 Commercial",
    "Microsoft Three Six Five Consumer Products And Cloud Services": "Microsoft 365 Consumer",
    "Linked In Corporation": "LinkedIn",
    "Server Products And Cloud Services": "Server Products & Cloud",
    "Search And News Advertising": "Search & News Advertising",
    "Dynamics Products And Cloud Services": "Dynamics 365",
    "Enterprise And Partner Services": "Enterprise Services",
    "Other Products And Services": "Other",
    "Windows And Devices": "Windows",
    # ── GOOGL ──
    "Google Advertising Revenue": "Google Advertising",
    "Google Search Other": "Google Search & Other",
    "Subscriptions Platforms And Devices Revenue": "Subscriptions, Platforms & Devices",
    "You Tube Advertising Revenue": "YouTube Advertising",
    # ── AAPL ──
    "i Phone": "iPhone",
    "i Pad": "iPad",
    "i Mac": "iMac",
    "Wearables Home and Accessories": "Wearables, Home & Accessories",
    "Wearables Home And Accessories": "Wearables, Home & Accessories",
    # ── NVDA ──
    "OEM And Other": "OEM & Other",
    # ── TSLA ──
    "Energy Generation And Storage": "Energy Generation & Storage",
    "Services And Other": "Services & Other",
    "Energy Generation And Storage Sales": "Energy Storage Sales",
    "Energy Generation And Storage Leasing": "Energy Storage Leasing",
    # ── AMZN ──
    "Third Party Seller Services": "Third-Party Seller Services",
    "Amazon Web Services": "AWS",
    # ── JNJ (concatenated XBRL names) ──
    "CONTACTLENSESOTHER": "Contact Lenses & Other",
    "INVEGASUSTENNAXEPLIONTRINZATREVICTA": "Invega/Sustenna/Trevicta",
    "SPINESPORTSOTHER": "Spine, Sports & Other",
    "PREZISTAPREZCOBIXREZOLSTASYMTUZA": "Prezista/Prezcobix/Symtuza",
    "RYBREVANTLAZCLUZE": "Rybrevant/Lazcluze",
    "OTHERNEUROSCIENCE": "Other Neuroscience",
    "ELECTROPHYSIOLOGY": "Electrophysiology",
    "EDURAN Trilpivirine": "Edurant (Rilpivirine)",
    "Simponi Simponi Aria": "Simponi/Simponi Aria",
}


# Product segment parent→children containment (like geo dedup).
# If a parent segment AND any of its children both appear, drop the parent
# to avoid double-counting.
_PRODUCT_CONTAINMENT: dict[str, set[str]] = {
    # NVDA: "Data Center" = Compute + Networking
    "data center": {"compute", "networking"},
    # TSLA: "Automotive" / "Automotive Revenues" = Sales + Credits + Leasing
    "automotive": {"automotive sales", "automotive regulatory credits",
                   "automotive leasing"},
    "automotive revenues": {"automotive sales", "automotive regulatory credits",
                            "automotive leasing"},
    # TSLA: Energy parent contains sub-segments
    "energy generation & storage": {"energy storage sales",
                                    "energy storage leasing"},
    # GOOGL: "Google Advertising" = Search + YouTube + Network
    "google advertising": {"google search & other", "youtube advertising",
                           "google network"},
}


def _apply_product_aliases(seg_dict: dict) -> dict:
    """Apply product-specific name aliases and dedup parent/child overlaps.

    Runs AFTER generic normalization. Maps ugly XBRL names to clean
    display names, merging duplicates that map to the same alias,
    then removes parent segments when children are present.
    """
    # Step 1: Apply aliases
    result: dict = {}
    for name, val in seg_dict.items():
        clean = _PRODUCT_SEGMENT_ALIASES.get(name, name)
        if clean in result:
            result[clean] = (result[clean] or 0) + (val or 0)
        else:
            result[clean] = val

    # Step 2: Containment-based parent removal
    if len(result) > 1:
        lower_names = {k.lower() for k in result}
        parents_to_drop: set[str] = set()
        for name in list(result.keys()):
            children = _PRODUCT_CONTAINMENT.get(name.lower())
            if children and children & lower_names:
                parents_to_drop.add(name)
        for p in parents_to_drop:
            result.pop(p, None)

    return result


# ── Geographic segment deduplication ──────────────────────────
# Companies report geography in overlapping hierarchies via XBRL
# (e.g., "North America" AND "United States" as separate line items).
# This produces >100% totals. We resolve by keeping the most granular
# level and dropping parent-level aggregates when children exist.

# Aliases: different names for the same thing (merge into canonical)
_GEO_ALIASES: dict[str, str] = {
    # ISO codes (title-cased for .title() fallback matching)
    "Cn": "China", "Jp": "Japan", "Us": "United States",
    "Tw": "Taiwan", "Gb": "United Kingdom", "De": "Germany",
    "In": "India", "Br": "Brazil", "Ca": "Canada",
    "Mx": "Mexico", "Fr": "France", "Kr": "South Korea",
    "Sg": "Singapore", "Au": "Australia", "It": "Italy",
    "Nl": "Netherlands", "Ch": "Switzerland", "Se": "Sweden",
    "Il": "Israel", "Ie": "Ireland", "Hk": "Hong Kong",
    # US variants
    "U.S.": "United States", "U.S": "United States",
    "United States Of America": "United States",
    # International / Non-US variants
    "Non Us": "International", "Non-Us": "International",
    "NonUs": "International", "Nonus": "International",
    "Non-U.S.": "International", "Non United States": "International",
    "Total International": "International",
    # Composite regions
    "US Canada": "United States & Canada",
    "Us Canada": "United States & Canada",
    "US And Canada": "United States & Canada",
    "Us And Canada": "United States & Canada",
    "Asia Pacific Africa": "Asia-Pacific & Africa",
    "Western Hemisphere Excluding US": "Americas ex-US",
    "Americas Excluding United States": "Americas ex-US",
    "China Including Hong Kong": "Greater China",
    # Catch-alls
    "All Other Countries": "Other Countries",
    "Rest Of World": "Other Countries",
    "All Other": "Other",
    "Other Countries And Corporate": "Other Countries",
    "Corporate And Other": "Other",
}

# Parent → children: if ANY child is present, drop the parent.
# Keys/values must be lowercase for matching.
_GEO_CONTAINMENT: dict[str, set[str]] = {
    "north america": {"united states", "canada", "mexico"},
    "americas": {"north america", "united states", "canada", "mexico",
                 "latin america", "south america", "brazil"},
    "united states & canada": {"united states", "canada"},
    "asia pacific": {"greater china", "china", "japan", "taiwan", "other asia",
                     "southeast asia", "india", "australia", "korea",
                     "south korea", "hong kong"},
    "asia-pacific & africa": {"greater china", "china", "japan", "india",
                              "australia", "korea", "south korea", "africa"},
    "asia": {"greater china", "china", "japan", "other asia",
             "southeast asia", "india", "korea", "south korea"},
    "greater china": {"china", "hong kong"},
    "emea": {"europe", "middle east", "africa", "united kingdom",
             "germany", "france", "italy", "netherlands", "switzerland",
             "sweden", "ireland"},
    "europe": {"united kingdom", "germany", "france", "italy",
               "netherlands", "switzerland", "sweden", "ireland"},
    "international": {"europe", "asia", "asia pacific", "emea",
                      "greater china", "china", "japan", "other asia",
                      "other countries", "canada", "latin america",
                      "americas ex-us", "asia-pacific & africa",
                      "united kingdom", "germany", "taiwan"},
    # "Other Countries" is a catch-all; drop it when specific non-US regions
    # (China, Japan, EMEA, Latin America, etc.) are present alongside it.
    "other countries": {"china", "japan", "other asia", "greater china",
                        "europe", "emea", "asia pacific", "latin america",
                        "india", "korea", "south korea", "brazil",
                        "southeast asia", "middle east", "africa",
                        "united kingdom", "germany", "taiwan"},
}


def _deduplicate_geo_dict(geo: dict) -> dict:
    """Remove overlapping parent-child geographic segments.

    1. Apply aliases (CN → China, Non Us → International).
    2. If a parent region (North America) has any of its children
       (United States) present, drop the parent.
    3. If International/Non-US exists AND there are already ≥2 non-US
       regions that sum to a similar amount, drop the aggregate.
    """
    if not geo or len(geo) <= 1:
        return geo

    # Step 1: Apply aliases, merge duplicates
    merged: dict[str, float | None] = {}
    for name, val in geo.items():
        canonical = _GEO_ALIASES.get(name, name)
        # Also try title case lookup
        if canonical == name:
            canonical = _GEO_ALIASES.get(name.title(), name)
        if canonical in merged:
            merged[canonical] = (merged[canonical] or 0) + (val or 0)
        else:
            merged[canonical] = val

    # Step 2: Containment-based parent removal
    lower_names = {k.lower() for k in merged}
    parents_to_drop: set[str] = set()

    for name in list(merged.keys()):
        children = _GEO_CONTAINMENT.get(name.lower())
        if children and children & lower_names:
            # This parent has at least one child present → drop parent
            parents_to_drop.add(name)

    for p in parents_to_drop:
        merged.pop(p, None)

    return merged


def normalize_segments(data: dict) -> dict:
    """Normalize segment names across all annual financials and peers.

    Operates on the full quantitative data dict containing
    annual_financials, quarterly_financials, and peers arrays.

    After pivot_annual(), each row is: {"metric": "Geographic Rev Split",
    "2021 FY": {seg_dict}, "2022 FY": {seg_dict}, ...}. So we match by
    the "metric" value, then normalize each year-column's segment dict.

    Args:
        data: Dict with annual_financials list (pivoted metric rows)

    Returns:
        Same dict with normalized segment names
    """
    _SPLIT_METRICS = frozenset(["Segment Rev Split", "Geographic Rev Split"])

    # Normalize annual financials (pivoted rows)
    for row in data.get("annual_financials", []):
        metric_name = row.get("metric", "")

        if metric_name in _SPLIT_METRICS:
            # Pivoted format: {"metric": "Geographic Rev Split",
            #                  "2021 FY": {seg_dict}, ...}
            is_geo = metric_name == "Geographic Rev Split"
            for col_key, col_val in row.items():
                if col_key in ("metric", "symbol") or not isinstance(col_val, dict):
                    continue
                normed = _normalize_split_dict(col_val)
                if is_geo:
                    row[col_key] = _deduplicate_geo_dict(normed)
                else:
                    row[col_key] = _apply_product_aliases(normed)
        else:
            # Legacy/direct-key format (pre-pivot or non-pivoted data)
            for split_key in _SPLIT_METRICS:
                val = row.get(split_key)
                if not isinstance(val, dict):
                    continue

                is_geo = split_key == "Geographic Rev Split"
                first_val = next(iter(val.values()), None) if val else None

                if isinstance(first_val, dict):
                    # Year-keyed: { "2025 FY": { "Google Cloud": 123, ... } }
                    for year, splits in val.items():
                        if isinstance(splits, dict):
                            normed = _normalize_split_dict(splits)
                            if is_geo:
                                val[year] = _deduplicate_geo_dict(normed)
                            else:
                                val[year] = _apply_product_aliases(normed)
                else:
                    # Flat split object: { "Google Cloud": 123, ... }
                    normed = _normalize_split_dict(val)
                    if is_geo:
                        row[split_key] = _deduplicate_geo_dict(normed)
                    else:
                        row[split_key] = _apply_product_aliases(normed)

    # Normalize peer data (peers are NOT pivoted — direct-key format)
    for peer in data.get("peers", []):
        for split_key in _SPLIT_METRICS:
            val = peer.get(split_key)
            if isinstance(val, dict):
                is_geo = split_key == "Geographic Rev Split"
                normed = _normalize_split_dict(val)
                if is_geo:
                    peer[split_key] = _deduplicate_geo_dict(normed)
                else:
                    peer[split_key] = _apply_product_aliases(normed)

    return data


# ============================================================
# CURRENCY INJECTOR
# From: Currency_Injector.js (30 lines)
# ============================================================

def inject_currency(data: dict, sec_financials: dict, fmp_profile: dict) -> dict:
    """Add reported currency to data metadata.

    Checks SEC financials first, then FMP profile, defaults to USD.
    """
    currency = "USD"

    # Try SEC financials first
    for stmt_key in ("income_statement", "balance_sheet"):
        arr = sec_financials.get(stmt_key, [])
        if isinstance(arr, list) and arr:
            rc = arr[0].get("reportedCurrency")
            if rc:
                currency = rc
                break

    # Fallback to FMP profile
    if currency == "USD" and fmp_profile:
        fc = fmp_profile.get("currency")
        if fc:
            currency = fc

    if "_meta" not in data:
        data["_meta"] = {}
    data["_meta"]["reported_currency"] = currency

    return data


# ============================================================
# FULL QUANTITATIVE AGGREGATOR
# From: Full_Quantitative.js (40 lines)
# Combines all pivoted data into a single dict for the fact sheet
# ============================================================

def aggregate_quantitative(
    annual: list[dict],
    quarterly: list[dict],
    estimates: list[dict],
    surprises: list[dict],
    owner_earnings: list[dict],
    peers: list[dict],
) -> dict:
    """Combine all pivoted data silos into one structure.

    This replaces the Full Quantitative n8n code node.
    """
    return {
        "annual_financials": annual,
        "quarterly_financials": quarterly,
        "estimates": estimates,
        "surprises": surprises,
        "owner_earnings": owner_earnings,
        "peers": peers,
        "metadata": {
            "total_items_processed": (
                len(annual) + len(quarterly) + len(estimates)
                + len(surprises) + len(owner_earnings) + len(peers)
            ),
        },
    }
