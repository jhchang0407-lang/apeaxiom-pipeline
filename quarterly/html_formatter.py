"""Quarterly HTML Formatter — Dark-themed earnings card.

Produces a self-contained HTML file with embedded CSS for the quarterly
earnings report. Lighter than the full memo dashboard — focused on
tables + prose analysis.
"""

from __future__ import annotations

import html
from typing import Any


def _esc(val: Any) -> str:
    """HTML-escape a value."""
    if val is None:
        return "—"
    return html.escape(str(val))


def _beat_miss_color(val: str | None) -> str:
    """Return CSS color for a beat/miss value."""
    if not val or val == "—":
        return "#94a3b8"
    if val.startswith("-") or val.startswith("($"):
        return "#f87171"
    return "#4ade80"


def build_quarterly_html(
    writer_output: dict,
    precomputed: dict,
    facts: dict,
    profile: dict,
    sector_family: str = "",
    pipeline_duration_s: float = 0.0,
) -> str:
    """Build a self-contained HTML quarterly earnings card.

    Args:
        writer_output: Patched writer output dict with prose paragraphs.
        precomputed: Dict with precomputed tables and sources.
        facts: Enriched facts from fact_extract.
        profile: FMP company profile.
        sector_family: Canonical sector family.
        pipeline_duration_s: Total pipeline runtime.

    Returns:
        Complete HTML string.
    """
    # Unwrap writer data
    s = writer_output
    if isinstance(s, dict) and "patched_output" in s:
        s = s["patched_output"]
    if not isinstance(s, dict):
        s = {}

    ticker = profile.get("symbol", "?")
    company = profile.get("companyName", ticker)
    sector = profile.get("sector", "")
    industry = profile.get("industry", "")

    quarter_info = facts.get("quarter", {})
    quarter_label = quarter_info.get("reported", "Latest Quarter")
    earnings_date = quarter_info.get("earnings_date", "")

    headline = facts.get("headline", {})
    margins = facts.get("margins", {})

    results_table = precomputed.get("precomputed_results_table") or []
    beat_miss_table = precomputed.get("precomputed_beat_miss_table") or []
    segment_table = precomputed.get("precomputed_segment_table") or []
    sector_kpi_table = precomputed.get("precomputed_sector_kpi_table") or []
    sources = precomputed.get("sources") or []

    # ── Revenue beat/miss indicator ─────────────────────────────
    rev_beat = headline.get("revenue_beat_miss_pct")
    if rev_beat is not None:
        if rev_beat > 0:
            beat_badge = f'<span class="badge beat">BEAT +{rev_beat:.2f}%</span>'
        elif rev_beat < 0:
            beat_badge = f'<span class="badge miss">MISS {rev_beat:.2f}%</span>'
        else:
            beat_badge = '<span class="badge inline">INLINE</span>'
    else:
        beat_badge = ""

    # ── Build HTML tables ───────────────────────────────────────
    def _html_table(rows: list[dict], columns: list[tuple[str, str]]) -> str:
        if not rows:
            return ""
        header = "".join(f"<th>{col[1]}</th>" for col in columns)
        body = ""
        for r in rows:
            cells = ""
            for key, _ in columns:
                val = r.get(key) or "—"
                # Color beat/miss values
                if key == "beat_miss":
                    color = _beat_miss_color(str(val))
                    cells += f'<td style="color:{color};font-weight:600">{_esc(val)}</td>'
                elif key == "yoy_change" or key == "yoy_growth":
                    v = str(val)
                    color = "#4ade80" if v and not v.startswith("-") and v != "—" else "#f87171" if v.startswith("-") else "#94a3b8"
                    cells += f'<td style="color:{color}">{_esc(val)}</td>'
                else:
                    cells += f"<td>{_esc(val)}</td>"
            body += f"<tr>{cells}</tr>\n"
        return f"<table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>"

    results_html = _html_table(results_table, [
        ("metric", "Metric"), ("actual", "Actual"), ("yoy_change", "YoY Change"),
    ])

    beat_miss_html = _html_table(beat_miss_table, [
        ("metric", "Metric"), ("actual", "Actual"),
        ("consensus", "Consensus"), ("beat_miss", "Beat/Miss"),
    ])

    segment_html = _html_table(segment_table, [
        ("segment", "Segment"), ("revenue", "Revenue"),
        ("pct_of_total", "% of Total"), ("yoy_growth", "YoY Growth"),
    ])

    sector_kpi_html = _html_table(sector_kpi_table, [
        ("metric", "Metric"), ("value", "Value"),
    ])

    # ── Prose sections ──────────────────────────────────────────
    def _prose_section(title: str, key: str) -> str:
        text = s.get(key)
        if not text:
            return ""
        return f"""
        <div class="prose-section">
            <h3>{_esc(title)}</h3>
            <p>{_esc(text)}</p>
        </div>"""

    # ── Sources ─────────────────────────────────────────────────
    sources_html = ""
    if sources:
        items = ""
        for src in sources:
            sid = _esc(src.get("id", "?"))
            name = _esc(src.get("name", ""))
            url = _esc(src.get("url", ""))
            items += f'<div class="source">[{sid}] {name}<br><a href="{url}" target="_blank">{url}</a></div>\n'
        sources_html = f'<div class="sources-block"><h3>Sources</h3>{items}</div>'

    # ── Assemble HTML ───────────────────────────────────────────
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_esc(ticker)} — {_esc(quarter_label)} Quarterly Report</title>
<style>
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: linear-gradient(160deg, #0a0e1a 0%, #111827 40%, #0f172a 100%);
    color: #e2e8f0;
    min-height: 100vh;
    padding: 2rem;
}}

.container {{
    max-width: 900px;
    margin: 0 auto;
}}

/* ── Header ──────────────────────────────────── */
.header {{
    background: rgba(30, 41, 59, 0.6);
    backdrop-filter: blur(12px);
    border: 1px solid rgba(148, 163, 184, 0.1);
    border-radius: 16px;
    padding: 2rem;
    margin-bottom: 1.5rem;
}}

.header-top {{
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 1rem;
    flex-wrap: wrap;
}}

.ticker-block {{
    flex: 1;
}}

.ticker {{
    font-size: 2.5rem;
    font-weight: 800;
    color: #f8fafc;
    letter-spacing: -0.02em;
}}

.company-name {{
    font-size: 1rem;
    color: #94a3b8;
    margin-top: 0.25rem;
}}

.quarter-block {{
    text-align: right;
}}

.quarter-label {{
    font-size: 1.5rem;
    font-weight: 700;
    color: #38bdf8;
}}

.earnings-date {{
    font-size: 0.85rem;
    color: #64748b;
    margin-top: 0.25rem;
}}

.sector-tag {{
    display: inline-block;
    background: rgba(56, 189, 248, 0.1);
    border: 1px solid rgba(56, 189, 248, 0.2);
    border-radius: 6px;
    padding: 0.25rem 0.75rem;
    font-size: 0.8rem;
    color: #38bdf8;
    margin-top: 0.75rem;
}}

.badge {{
    display: inline-block;
    padding: 0.3rem 0.8rem;
    border-radius: 6px;
    font-size: 0.85rem;
    font-weight: 700;
    margin-top: 0.5rem;
}}

.badge.beat {{
    background: rgba(74, 222, 128, 0.15);
    color: #4ade80;
    border: 1px solid rgba(74, 222, 128, 0.3);
}}

.badge.miss {{
    background: rgba(248, 113, 113, 0.15);
    color: #f87171;
    border: 1px solid rgba(248, 113, 113, 0.3);
}}

.badge.inline {{
    background: rgba(148, 163, 184, 0.15);
    color: #94a3b8;
    border: 1px solid rgba(148, 163, 184, 0.3);
}}

/* ── Cards ───────────────────────────────────── */
.card {{
    background: rgba(30, 41, 59, 0.5);
    backdrop-filter: blur(10px);
    border: 1px solid rgba(148, 163, 184, 0.08);
    border-radius: 12px;
    padding: 1.5rem;
    margin-bottom: 1.25rem;
}}

.card h2 {{
    font-size: 0.85rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: #64748b;
    margin-bottom: 1rem;
    padding-bottom: 0.5rem;
    border-bottom: 1px solid rgba(148, 163, 184, 0.1);
}}

/* ── Tables ──────────────────────────────────── */
table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.9rem;
}}

th {{
    text-align: left;
    padding: 0.6rem 0.75rem;
    font-weight: 600;
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: #64748b;
    border-bottom: 1px solid rgba(148, 163, 184, 0.15);
}}

td {{
    padding: 0.6rem 0.75rem;
    border-bottom: 1px solid rgba(148, 163, 184, 0.06);
    color: #e2e8f0;
}}

tr:last-child td {{
    border-bottom: none;
}}

/* ── Prose ───────────────────────────────────── */
.prose-section {{
    margin-bottom: 0.5rem;
}}

.prose-section h3 {{
    font-size: 0.8rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: #38bdf8;
    margin-bottom: 0.75rem;
}}

.prose-section p {{
    font-size: 0.92rem;
    line-height: 1.7;
    color: #cbd5e1;
}}

/* ── Sources ─────────────────────────────────── */
.sources-block {{
    margin-top: 0.5rem;
}}

.sources-block h3 {{
    font-size: 0.8rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: #64748b;
    margin-bottom: 0.75rem;
}}

.source {{
    font-size: 0.78rem;
    color: #64748b;
    margin-bottom: 0.5rem;
    line-height: 1.4;
}}

.source a {{
    color: #38bdf8;
    text-decoration: none;
    word-break: break-all;
}}

.source a:hover {{
    text-decoration: underline;
}}

/* ── Footer ──────────────────────────────────── */
.footer {{
    text-align: center;
    font-size: 0.75rem;
    color: #475569;
    margin-top: 2rem;
    padding-top: 1rem;
    border-top: 1px solid rgba(148, 163, 184, 0.08);
}}

/* ── Responsive ──────────────────────────────── */
@media (max-width: 640px) {{
    body {{ padding: 1rem; }}
    .ticker {{ font-size: 2rem; }}
    .quarter-label {{ font-size: 1.2rem; }}
    table {{ font-size: 0.8rem; }}
    th, td {{ padding: 0.4rem 0.5rem; }}
}}
</style>
</head>
<body>
<div class="container">

    <!-- Header -->
    <div class="header">
        <div class="header-top">
            <div class="ticker-block">
                <div class="ticker">{_esc(ticker)}</div>
                <div class="company-name">{_esc(company)}</div>
                <div class="sector-tag">{_esc(sector)} / {_esc(industry)}</div>
            </div>
            <div class="quarter-block">
                <div class="quarter-label">{_esc(quarter_label)}</div>
                <div class="earnings-date">{_esc(earnings_date) if earnings_date else ''}</div>
                {beat_badge}
            </div>
        </div>
    </div>

    <!-- Opening Analysis -->
    {_prose_section("Analysis", "opening_paragraph")}

    <!-- Financial Results -->
    {'<div class="card"><h2>Financial Results</h2>' + results_html + '</div>' if results_html else ''}

    <!-- Beat / Miss -->
    {'<div class="card"><h2>Consensus Comparison</h2>' + beat_miss_html + '</div>' if beat_miss_html else ''}

    <!-- Sector KPIs -->
    {'<div class="card"><h2>Key Sector Metrics</h2>' + sector_kpi_html + '</div>' if sector_kpi_html else ''}

    <!-- Segment Performance -->
    {'<div class="card"><h2>Segment Performance</h2>' + segment_html + _prose_section("", "segment_performance").replace('<h3></h3>', '') + '</div>' if segment_html or s.get("segment_performance") else ''}

    <!-- Analysis Sections -->
    <div class="card">
        {_prose_section("Margin Analysis", "margin_analysis")}
        {_prose_section("Guidance & Management", "guidance_and_management")}
        {_prose_section("Market Reaction", "market_reaction")}
        {_prose_section("Investment Implications", "investment_implications")}
    </div>

    <!-- Sources -->
    {'<div class="card">' + sources_html + '</div>' if sources_html else ''}

    <!-- Footer -->
    <div class="footer">
        Generated in {pipeline_duration_s:.0f}s | {_esc(sector_family.upper())} sector pipeline
    </div>

</div>
</body>
</html>"""
