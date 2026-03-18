"""Quarterly Formatter — Assemble final markdown + JSON payload.

Combines precomputed tables with patched prose to produce
the final quarterly output. Also builds the JSON payload
for R2 upload.
"""

from __future__ import annotations

from typing import Any


def _build_results_table(rows: list[dict]) -> str:
    """Build Results Table (Metric | Actual | YoY Change)."""
    if not rows:
        return ""
    lines = [
        "| Metric | Actual | YoY Change |",
        "| --- | --- | --- |",
    ]
    for r in rows:
        lines.append(
            f"| {r.get('metric', '—')} | {r.get('actual') or '—'} "
            f"| {r.get('yoy_change') or '—'} |"
        )
    return "\n".join(lines)


def _build_beat_miss_table(rows: list[dict]) -> str:
    """Build Beat/Miss Table."""
    if not rows:
        return ""
    lines = [
        "| Metric | Actual | Consensus | Beat/Miss |",
        "| --- | --- | --- | --- |",
    ]
    for r in rows:
        lines.append(
            f"| {r.get('metric', '—')} | {r.get('actual') or '—'} "
            f"| {r.get('consensus') or '—'} | {r.get('beat_miss') or '—'} |"
        )
    return "\n".join(lines)


def _build_segment_table(rows: list[dict]) -> str:
    """Build Segment Table."""
    if not rows:
        return ""
    lines = [
        "| Segment | Revenue | % of Total | YoY Growth |",
        "| --- | --- | --- | --- |",
    ]
    for r in rows:
        lines.append(
            f"| {r.get('segment', '—')} | {r.get('revenue') or '—'} "
            f"| {r.get('pct_of_total') or '—'} | {r.get('yoy_growth') or '—'} |"
        )
    return "\n".join(lines)


def _build_sector_kpi_table(rows: list[dict]) -> str:
    """Build Sector KPI Table."""
    if not rows:
        return ""
    lines = [
        "| Metric | Value |",
        "| --- | --- |",
    ]
    for r in rows:
        lines.append(f"| {r.get('metric', '—')} | {r.get('value') or '—'} |")
    return "\n".join(lines)


def _build_guidance_table(guidance: dict) -> str:
    """Build Forward Guidance table from guidance data."""
    if not guidance:
        return ""

    rows: list[tuple[str, str]] = []

    # Next-quarter revenue
    nq_mid = guidance.get("next_q_revenue_mid")
    nq_low = guidance.get("next_quarter_revenue_low_m")
    nq_high = guidance.get("next_quarter_revenue_high_m")
    if nq_mid is not None:
        mid_str = f"${nq_mid / 1000:.1f}B" if nq_mid >= 1000 else f"${nq_mid:,.0f}M"
        if nq_low and nq_high:
            low_str = f"${nq_low / 1000:.1f}B" if nq_low >= 1000 else f"${nq_low:,.0f}M"
            high_str = f"${nq_high / 1000:.1f}B" if nq_high >= 1000 else f"${nq_high:,.0f}M"
            rows.append(("Next-Quarter Revenue", f"{mid_str} ({low_str} – {high_str})"))
        else:
            rows.append(("Next-Quarter Revenue", mid_str))
    elif nq_low and nq_high:
        low_str = f"${nq_low / 1000:.1f}B" if nq_low >= 1000 else f"${nq_low:,.0f}M"
        high_str = f"${nq_high / 1000:.1f}B" if nq_high >= 1000 else f"${nq_high:,.0f}M"
        rows.append(("Next-Quarter Revenue", f"{low_str} – {high_str}"))

    # Full-year revenue
    fy_mid = guidance.get("full_year_revenue_mid")
    fy_low = guidance.get("full_year_revenue_low_m")
    fy_high = guidance.get("full_year_revenue_high_m")
    if fy_mid is not None:
        mid_str = f"${fy_mid / 1000:.1f}B" if fy_mid >= 1000 else f"${fy_mid:,.0f}M"
        if fy_low and fy_high:
            low_str = f"${fy_low / 1000:.1f}B" if fy_low >= 1000 else f"${fy_low:,.0f}M"
            high_str = f"${fy_high / 1000:.1f}B" if fy_high >= 1000 else f"${fy_high:,.0f}M"
            rows.append(("Full-Year Revenue", f"{mid_str} ({low_str} – {high_str})"))
        else:
            rows.append(("Full-Year Revenue", mid_str))
    elif fy_low and fy_high:
        low_str = f"${fy_low / 1000:.1f}B" if fy_low >= 1000 else f"${fy_low:,.0f}M"
        high_str = f"${fy_high / 1000:.1f}B" if fy_high >= 1000 else f"${fy_high:,.0f}M"
        rows.append(("Full-Year Revenue", f"{low_str} – {high_str}"))

    # Direction
    direction = guidance.get("direction")
    if direction:
        rows.append(("Direction", direction.capitalize()))

    if not rows:
        return ""

    lines = [
        "| Guidance Metric | Outlook |",
        "| --- | --- |",
    ]
    for label, val in rows:
        lines.append(f"| {label} | {val} |")
    return "\n".join(lines)


def _build_sources_block(sources: list[dict]) -> str:
    """Build sources reference block."""
    if not sources:
        return ""
    parts = ["\n\n## Sources\n"]
    for src in sources:
        parts.append(f"[{src.get('id', '?')}] {src.get('name', '')}\n    {src.get('url', '')}\n")
    return "\n".join(parts)


def format_quarterly_output(
    writer_output: dict,
    precomputed: dict,
    facts: dict | None = None,
) -> str:
    """Assemble the LATEST FINANCIAL HIGHLIGHTS quarterly section.

    Args:
        writer_output: Patched writer output dict with prose paragraphs.
        precomputed: Dict with precomputed tables and sources.
        facts: Enriched facts dict (for guidance data, etc.).

    Returns:
        Full markdown string.
    """
    facts = facts or {}

    # Unwrap writer data
    s = writer_output
    if isinstance(s, dict) and "output" in s:
        s = s["output"]
    if isinstance(s, dict) and "parsed" in s:
        s = s["parsed"]
    if isinstance(s, dict) and "patched_output" in s:
        s = s["patched_output"]
    if not isinstance(s, dict):
        s = {}

    results_table = precomputed.get("precomputed_results_table") or []
    beat_miss_table = precomputed.get("precomputed_beat_miss_table") or []
    segment_table = precomputed.get("precomputed_segment_table") or []
    sector_kpi_table = precomputed.get("precomputed_sector_kpi_table") or []
    sources = precomputed.get("sources") or []
    guidance = facts.get("guidance") or {}

    # ── Assemble parts ───────────────────────────────────────────
    parts: list[str] = ["# LATEST FINANCIAL HIGHLIGHTS", ""]

    # Opening paragraph
    opening = s.get("opening_paragraph")
    if opening:
        parts.extend([opening, ""])

    # ── Quarterly Results ─────────────────────────────────────────
    rt = _build_results_table(results_table)
    if rt:
        parts.extend(["## Quarterly Results", "", rt, ""])

    # Beat/Miss table
    bmt = _build_beat_miss_table(beat_miss_table)
    if bmt:
        parts.extend(["### Consensus Comparison", "", bmt, ""])

    # Results commentary — explains what drove the beat/miss
    results_commentary = s.get("results_commentary")
    if results_commentary:
        parts.extend([results_commentary, ""])

    # Sector KPI table (if sector-specific)
    skt = _build_sector_kpi_table(sector_kpi_table)
    if skt:
        parts.extend(["## Key Sector Metrics", "", skt, ""])

    # ── Segment performance ───────────────────────────────────────
    if segment_table or s.get("segment_performance"):
        parts.extend(["## Segment Performance", ""])
        st = _build_segment_table(segment_table)
        if st:
            parts.extend([st, ""])
        if s.get("segment_performance"):
            parts.extend([s["segment_performance"], ""])

    # ── Margin analysis ───────────────────────────────────────────
    if s.get("margin_analysis"):
        parts.extend(["## Margin Analysis", "", s["margin_analysis"], ""])

    # ── Guidance & Management Commentary ──────────────────────────
    has_guidance_table = bool(guidance.get("next_q_revenue_mid")
                             or guidance.get("next_quarter_revenue_low_m")
                             or guidance.get("full_year_revenue_mid")
                             or guidance.get("full_year_revenue_low_m"))
    has_guidance_prose = bool(s.get("guidance_and_management"))

    if has_guidance_table or has_guidance_prose:
        parts.extend(["## Guidance & Management Commentary", ""])
        # Guidance table first
        gt = _build_guidance_table(guidance)
        if gt:
            parts.extend([gt, ""])
        # Then prose
        if has_guidance_prose:
            parts.extend([s["guidance_and_management"], ""])

    # ── Market reaction ───────────────────────────────────────────
    if s.get("market_reaction"):
        parts.extend(["## Market Reaction", "", s["market_reaction"], ""])

    # ── Investment implications ───────────────────────────────────
    if s.get("investment_implications"):
        parts.extend(["## Investment Implications", "", s["investment_implications"], ""])

    # Sources
    source_block = _build_sources_block(sources)
    if source_block:
        parts.extend(["---", source_block])

    # Clean up
    cleaned = "\n".join(p for p in parts if p is not None)
    cleaned = cleaned.replace("\n\n\n", "\n\n").strip()

    return cleaned


def build_quarterly_payload(
    ticker: str,
    formatted_markdown: str,
    facts: dict,
    precomputed: dict,
    profile: dict,
    sector_family: str,
    pipeline_duration_s: float = 0.0,
    fact_check_meta: dict | None = None,
) -> dict:
    """Build the full JSON payload for R2 upload and storage.

    Args:
        ticker: Stock ticker.
        formatted_markdown: Final markdown output.
        facts: Enriched facts from fact_extract.
        precomputed: Distributor output.
        profile: FMP company profile.
        sector_family: Canonical sector family.
        pipeline_duration_s: Total pipeline runtime.
        fact_check_meta: Fact check metadata (patches, verified, suspicious).

    Returns:
        JSON-serializable dict.
    """
    from datetime import datetime

    now = datetime.now()

    return {
        "ticker": ticker.upper(),
        "company_name": profile.get("companyName", ticker.upper()),
        "sector": profile.get("sector", ""),
        "industry": profile.get("industry", ""),
        "sector_family": sector_family,
        "quarter": facts.get("quarter", {}),
        "generated_at": now.isoformat(),
        "pipeline_duration_s": round(pipeline_duration_s, 1),
        # Structured data
        "headline": facts.get("headline", {}),
        "margins": facts.get("margins", {}),
        "segments": facts.get("segments", {}),
        "guidance": facts.get("guidance", {}),
        "management": facts.get("management", {}),
        "market_reaction": facts.get("market_reaction", {}),
        "analysts": facts.get("analysts", []),
        "sector_kpis": facts.get("sector_kpis", {}),
        "sources": facts.get("sources", []),
        # Precomputed tables
        "precomputed_results_table": precomputed.get("precomputed_results_table", []),
        "precomputed_beat_miss_table": precomputed.get("precomputed_beat_miss_table", []),
        "precomputed_segment_table": precomputed.get("precomputed_segment_table", []),
        "precomputed_sector_kpi_table": precomputed.get("precomputed_sector_kpi_table", []),
        # Formatted output
        "formatted_section": formatted_markdown,
        "word_count": len(formatted_markdown.split()),
        # Quality
        "fact_check": fact_check_meta or {},
    }
