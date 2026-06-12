"""Parse revenue segmentation (product + geographic) from XBRL filing instance."""

from __future__ import annotations

import re
import time
from datetime import date as _date

import httpx

from sec.client import get_submissions, require_sec_user_agent


def _get_latest_filing_url(ticker: str, form_type: str = "10-K") -> str | None:
    """Get the XBRL instance document URL for the latest filing."""
    subs = get_submissions(ticker)

    recent = subs.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])

    cik = str(subs.get("cik", "")).lstrip("0")

    for i, form in enumerate(forms):
        if form == form_type:
            accn = accessions[i].replace("-", "")
            primary = primary_docs[i]
            # XBRL instance is usually {primary_without_ext}_htm.xml
            base_name = primary.rsplit(".", 1)[0]
            instance_url = (
                f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn}/{base_name}_htm.xml"
            )
            return instance_url

    return None


def _parse_xbrl_instance(url: str) -> tuple[dict, list]:
    """Parse XBRL instance document and return (contexts, facts).

    Returns:
        contexts: dict mapping context_id -> {id, dimensions, end/instant, start}
        facts: list of (concept, context_id, value) tuples
    """
    time.sleep(0.15)  # rate limit
    headers = {"User-Agent": require_sec_user_agent()}
    try:
        r = httpx.get(url, headers=headers, timeout=60, follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPError:
        time.sleep(1.0)
        r = httpx.get(url, headers=headers, timeout=60, follow_redirects=True)
        r.raise_for_status()
    text = r.text

    # Parse contexts
    contexts = {}
    for match in re.finditer(
        r'<context[^>]*id="([^"]+)">(.*?)</context>', text, re.DOTALL
    ):
        cid = match.group(1)
        body = match.group(2)

        period_match = re.search(
            r"<startDate>([^<]+)</startDate>\s*<endDate>([^<]+)</endDate>", body
        )
        instant_match = re.search(r"<instant>([^<]+)</instant>", body)
        members = re.findall(
            r'<xbrldi:explicitMember[^>]*dimension="([^"]+)">([^<]+)</xbrldi:explicitMember>',
            body,
        )

        ctx = {"id": cid, "dimensions": dict(members)}
        if period_match:
            ctx["start"] = period_match.group(1)
            ctx["end"] = period_match.group(2)
        elif instant_match:
            ctx["instant"] = instant_match.group(1)

        contexts[cid] = ctx

    # Parse all numeric facts (us-gaap namespace)
    facts = []
    for match in re.finditer(
        r'<(?:us-gaap|[a-z]+):(\w+)[^>]*contextRef="([^"]+)"[^>]*>([^<]+)<',
        text,
    ):
        concept = match.group(1)
        ctx_id = match.group(2)
        value = match.group(3).strip()
        try:
            val = float(value)
            facts.append((concept, ctx_id, val))
        except (ValueError, TypeError):
            pass

    return contexts, facts


def _clean_segment_name(name: str) -> str:
    """Clean XBRL segment member name to human-readable."""
    # Remove namespace prefixes (including hyphenated like "us-gaap:")
    name = re.sub(r"^[a-z][a-z0-9-]*:", "", name)
    # Remove common suffixes
    name = name.replace("SegmentMember", "")
    name = name.replace("Member", "")
    # CamelCase to spaces (but keep consecutive capitals together like "IPad")
    name = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", name)
    # Fix common product names
    name = name.replace("I Phone", "iPhone")
    name = name.replace("I Pad", "iPad")
    name = name.replace("I Mac", "iMac")
    name = name.replace("Homeand", "Home and")
    return name.strip()


def get_segments(ticker: str) -> dict:
    """Get revenue segmentation data (product + geographic) from SEC filings.

    Returns dict with product_segments and geographic_segments, each containing
    yearly breakdowns.
    """
    url = _get_latest_filing_url(ticker, "10-K")
    if url is None:
        return {"product_segments": {}, "geographic_segments": {}, "error": "No 10-K filing found"}

    try:
        contexts, facts = _parse_xbrl_instance(url)
    except Exception as e:
        return {"product_segments": {}, "geographic_segments": {}, "error": str(e)}

    # Revenue concept names to look for
    revenue_concepts = {
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    }

    # Some filers (e.g. XOM) tag segment revenue with custom concepts
    # (SalesAndOtherOperatingRevenue) that aren't in the standard set.
    # Broaden the match for facts that carry a business-segment axis —
    # any concept containing "revenue" or "sales" qualifies.
    def _is_revenue_like(concept_name: str) -> bool:
        lower = concept_name.lower()
        return any(kw in lower for kw in ("revenue", "sales", "totalsegment"))

    # Product/Service axis dimension
    product_axis = "srt:ProductOrServiceAxis"
    # Geographic axis dimension
    geo_axis = "srt:StatementGeographicalAxis"
    # Business segment axis
    biz_axis = "us-gaap:StatementBusinessSegmentsAxis"

    product_data: dict[str, dict[str, float]] = {}  # {year: {segment: value}}
    geo_data: dict[str, dict[str, float]] = {}
    biz_segment_data: dict[str, dict[str, float]] = {}  # business segments

    # Track context durations so ~annual values (>300 days) are preferred
    # over quarterly/YTD contexts ending in the same year.
    product_durations: dict[tuple[str, str], int | None] = {}
    geo_durations: dict[tuple[str, str], int | None] = {}

    def _ctx_duration_days(ctx: dict) -> int | None:
        start = ctx.get("start")
        end = ctx.get("end")
        if not start or not end:
            return None
        try:
            return (_date.fromisoformat(end) - _date.fromisoformat(start)).days
        except ValueError:
            return None

    def _set_segment_value(
        data: dict[str, dict[str, float]],
        seg_durations: dict[tuple[str, str], int | None],
        year: str,
        segment_name: str,
        value: float,
        duration: int | None,
    ) -> None:
        if year not in data:
            data[year] = {}
        existing = seg_durations.get((year, segment_name))
        if segment_name in data[year] and existing is not None and existing > 300:
            if duration is None or duration <= 300:
                return  # keep the annual value already stored
        data[year][segment_name] = value
        seg_durations[(year, segment_name)] = duration

    for concept, ctx_id, value in facts:
        ctx = contexts.get(ctx_id, {})
        dims = ctx.get("dimensions", {})
        end_date = ctx.get("end", "")
        year = end_date[:4] if end_date else ""
        if not year:
            continue

        duration = _ctx_duration_days(ctx)
        is_standard_rev = concept in revenue_concepts

        # Product segments — skip aggregate categories (Product/Service)
        # to avoid double-counting with detailed breakdowns (iPhone/Mac/iPad)
        if is_standard_rev and product_axis in dims:
            raw_member = dims[product_axis]
            # Skip us-gaap aggregate members
            if raw_member.startswith("us-gaap:"):
                pass
            else:
                segment_name = _clean_segment_name(raw_member)
                _set_segment_value(
                    product_data, product_durations, year, segment_name, value, duration
                )

        # Geographic segments (from StatementGeographicalAxis)
        if is_standard_rev and geo_axis in dims and biz_axis not in dims:
            segment_name = _clean_segment_name(dims[geo_axis])
            _set_segment_value(
                geo_data, geo_durations, year, segment_name, value, duration
            )

        # Business segments (StatementBusinessSegmentsAxis) — collect as
        # a first-class segment source, not just a fallback.
        # Accept facts with biz_axis even if geo_axis is present (common
        # for companies like XOM that cross-tab segment × geography).
        # Skip facts that also carry ProductOrServiceAxis or
        # ConsolidationItemsAxis to avoid double-counting sub-breakdowns.
        if biz_axis in dims and product_axis not in dims:
            consolidation_axis = "srt:ConsolidationItemsAxis"
            if consolidation_axis not in dims:
                if is_standard_rev or _is_revenue_like(concept):
                    segment_name = _clean_segment_name(dims[biz_axis])
                    if year not in biz_segment_data:
                        biz_segment_data[year] = {}
                    # Aggregate across geo sub-breakdowns by summing
                    biz_segment_data[year][segment_name] = (
                        biz_segment_data[year].get(segment_name, 0) + value
                    )

    # ── Decide which source to use for product segments ──────────
    # Some filers (e.g. XOM) report income-statement-level
    # classifications on the ProductOrServiceAxis (e.g. "Sales And
    # Other Operating Revenue", "Income From Equity Affiliates").
    # These are NOT actual operating segments.  Detect this by
    # checking if the names look like income-statement line items
    # and prefer StatementBusinessSegmentsAxis data when available.
    _INCOME_LINE_KEYWORDS = {
        "revenue", "income from", "interest", "other income",
        "gain", "loss", "fee", "commission",
    }

    def _looks_like_income_items(seg_dict: dict[str, dict[str, float]]) -> bool:
        """Return True if most segment names look like income statement items."""
        if not seg_dict:
            return False
        latest_year = max(seg_dict.keys())
        names = list(seg_dict[latest_year].keys())
        if not names:
            return False
        matches = sum(
            1 for n in names
            if any(kw in n.lower() for kw in _INCOME_LINE_KEYWORDS)
        )
        return matches / len(names) > 0.5

    if product_data and _looks_like_income_items(product_data) and biz_segment_data:
        # ProductOrServiceAxis has income-line items; use business
        # segments as the true product/operating segment source
        product_data = biz_segment_data
    elif not product_data and biz_segment_data:
        # No ProductOrServiceAxis data at all; use business segments
        product_data = biz_segment_data

    # Use remaining business segments as geographic fallback for years
    # where no geographic-axis data was found.
    for year, biz_segments in biz_segment_data.items():
        if not geo_data.get(year):
            geo_data[year] = biz_segments

    # Convert to sorted lists for output
    def _format_segments(data: dict) -> list[dict]:
        result = []
        for year in sorted(data.keys(), reverse=True):
            segments = data[year]
            total = sum(segments.values())
            entry = {"year": year, "total": total, "segments": {}}
            for seg_name, seg_val in sorted(segments.items(), key=lambda x: -x[1]):
                entry["segments"][seg_name] = {
                    "value": seg_val,
                    "percentage": round(seg_val / total * 100, 1) if total else 0,
                }
            result.append(entry)
        return result

    return {
        "product_segments": _format_segments(product_data),
        "geographic_segments": _format_segments(geo_data),
    }
