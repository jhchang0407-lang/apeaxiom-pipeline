"""Output Formatters — Stage 5 of the pipeline.

Ports the Formatter.js (168 lines) quarterly section builder to Python
and adds full-memo markdown, HTML, Discord scorecard, and financial
appendix formatters.

Source n8n nodes -> Python functions:
  Formatter.js               -> format_quarterly_section()
  (new)                      -> format_markdown()
  (new)                      -> format_html()
  (new)                      -> format_discord_scorecard()
  (new)                      -> build_financial_appendix()
"""

from __future__ import annotations

import html as html_mod
import re
from typing import Any


# ====================================================================
# SHARED HELPERS
# ====================================================================

def _clean_multiline(text: str) -> str:
    """Collapse runs of 3+ newlines down to 2 and strip edges."""
    cleaned = re.sub(r"\n{3,}", "\n\n", text)
    return cleaned.strip()


def _safe(val: Any, fallback: str = "\u2014") -> str:
    """Return *val* as a string, or *fallback* if None/empty."""
    if val is None or val == "":
        return fallback
    return str(val)


def _normalize_score(val: Any) -> float | None:
    """Normalize a score to 0-100 scale.

    LLM outputs often return scores on a 1-10 scale; moat uses 0-100.
    If the value is <= 10, multiply by 10 to normalize to 0-100.
    """
    if val is None:
        return None
    try:
        n = float(val)
    except (TypeError, ValueError):
        return None
    # If it looks like a 1-10 scale, normalize to 0-100
    if 0 < n <= 10:
        return round(n * 10, 1)
    return round(n, 1)


# ====================================================================
# TABLE BUILDERS (ported from Formatter.js)
# ====================================================================

def _build_results_table(rows: list[dict]) -> str:
    """Metric | Actual | YoY Change markdown table."""
    if not rows:
        return ""
    lines = [
        "| Metric | Actual | YoY Change |",
        "| --- | --- | --- |",
    ]
    for r in rows:
        lines.append(
            f"| {r.get('metric', '')} "
            f"| {_safe(r.get('actual'))} "
            f"| {_safe(r.get('yoy_change'))} |"
        )
    return "\n".join(lines)


def _build_beat_miss_table(rows: list[dict]) -> str:
    """Metric | Actual | Consensus | Beat/Miss markdown table."""
    if not rows:
        return ""
    lines = [
        "| Metric | Actual | Consensus | Beat/Miss |",
        "| --- | --- | --- | --- |",
    ]
    for r in rows:
        lines.append(
            f"| {r.get('metric', '')} "
            f"| {_safe(r.get('actual'))} "
            f"| {_safe(r.get('consensus'))} "
            f"| {_safe(r.get('beat_miss'))} |"
        )
    return "\n".join(lines)


def _build_segment_table(rows: list[dict]) -> str:
    """Segment | Revenue | % of Total | YoY Growth markdown table."""
    if not rows:
        return ""
    lines = [
        "| Segment | Revenue | % of Total | YoY Growth |",
        "| --- | --- | --- | --- |",
    ]
    for r in rows:
        lines.append(
            f"| {r.get('segment', '')} "
            f"| {_safe(r.get('revenue'))} "
            f"| {_safe(r.get('pct_of_total'))} "
            f"| {_safe(r.get('yoy_growth'))} |"
        )
    return "\n".join(lines)


def _build_source_block(sources: list[dict]) -> str:
    """Render source citations as a markdown block."""
    if not sources:
        return ""
    block = "\n\n## Sources\n\n"
    for src in sources:
        block += f"[{src.get('id', '')}] {src.get('name', '')}\n"
        block += f"    {src.get('url', '')}\n\n"
    return block


# ====================================================================
# QUARTERLY SECTION FORMATTER (direct port of Formatter.js v4)
# ====================================================================

def format_quarterly_section(
    writer_output: dict[str, Any],
    precomputed: dict[str, Any],
) -> str:
    """Assemble the LATEST FINANCIAL HIGHLIGHTS quarterly section.

    Combines precomputed tables (from the Distributor) with prose
    paragraphs (from the Writer, after fact-check patching).

    Args:
        writer_output: Patched writer output dict. Expected keys:
            opening_paragraph, segment_performance, margin_analysis,
            guidance_and_management, market_reaction,
            investment_implications.
        precomputed: Dict with precomputed_results_table,
            precomputed_beat_miss_table, precomputed_segment_table,
            sources.

    Returns:
        Full markdown string for the quarterly highlights section.
    """
    # ── Unwrap writer data (handles various shapes) ──────────────
    s = writer_output
    if isinstance(s, str):
        import json
        try:
            s = json.loads(s)
        except (json.JSONDecodeError, ValueError):
            pass
    if isinstance(s, dict) and "output" in s:
        out = s["output"]
        if isinstance(out, str):
            import json
            try:
                s = json.loads(out)
            except (json.JSONDecodeError, ValueError):
                s = out
        else:
            s = out
    if isinstance(s, dict) and "parsed" in s:
        p = s["parsed"]
        if isinstance(p, str):
            import json
            try:
                s = json.loads(p)
            except (json.JSONDecodeError, ValueError):
                pass
        else:
            s = p

    # Ensure s is a dict for .get() calls below
    if not isinstance(s, dict):
        s = {}

    results_table = precomputed.get("precomputed_results_table") or []
    beat_miss_table = precomputed.get("precomputed_beat_miss_table") or []
    segment_table = precomputed.get("precomputed_segment_table") or []
    sources = precomputed.get("sources") or []

    # ── Assemble parts ───────────────────────────────────────────
    parts: list[str] = ["# LATEST FINANCIAL HIGHLIGHTS", ""]

    # Opening paragraph
    opening = s.get("opening_paragraph")
    if opening:
        parts.extend([opening, ""])

    # Results table (precomputed)
    rt = _build_results_table(results_table)
    if rt:
        parts.extend([rt, ""])

    # Beat/Miss table -- horizontal rule separates from results
    bmt = _build_beat_miss_table(beat_miss_table)
    if bmt:
        parts.extend(["---", "", bmt, ""])

    # Segment performance -- table first, then prose
    if segment_table or s.get("segment_performance"):
        parts.extend(["## Segment Performance", ""])
        st = _build_segment_table(segment_table)
        if st:
            parts.extend([st, ""])
        seg_prose = s.get("segment_performance")
        if seg_prose:
            parts.extend([seg_prose, ""])

    # Margin analysis
    if s.get("margin_analysis"):
        parts.extend(["## Margin Analysis", "", s["margin_analysis"], ""])

    # Guidance and management
    if s.get("guidance_and_management"):
        parts.extend(["## Guidance & Management Commentary", "", s["guidance_and_management"], ""])

    # Market reaction
    if s.get("market_reaction"):
        parts.extend(["## Market Reaction", "", s["market_reaction"], ""])

    # Investment implications
    if s.get("investment_implications"):
        parts.extend(["## Investment Implications", "", s["investment_implications"], ""])

    # Sources
    source_block = _build_source_block(sources)
    if source_block:
        parts.extend(["---", source_block])

    # ── Clean and return ─────────────────────────────────────────
    cleaned = "\n".join(p for p in parts if p is not None)
    return _clean_multiline(cleaned)


# ====================================================================
# FULL MEMO MARKDOWN
# ====================================================================

# Canonical section ordering and titles (sections 1-14)
_SECTION_META: list[tuple[int, str]] = [
    (1, "Executive Summary & Investment Thesis"),
    (2, "Company Overview & Capital Structure"),
    (3, "Industry & Competitive Landscape"),
    (4, "Revenue Analysis"),
    (5, "Profitability & Margin Analysis"),
    (6, "Competitive Positioning"),
    (7, "Working Capital & Cash Conversion"),
    (8, "Management Assessment"),
    (9, "Capital Allocation & Shareholder Returns"),
    (10, "Guidance & Forward Estimates"),
    (11, "Financial Statements Deep Dive"),
    (12, "Peer Benchmarking"),
    (13, "Valuation"),
    (14, "Conclusion"),
]


def format_markdown(memo_result: Any) -> str:
    """Render a full investment memo as markdown.

    If ``memo_body`` is available (set by ``assemble_memo``), we use it
    directly — it already contains rendered tables, footnotes, and
    formatted section headers from the ``_SectionRenderer``.

    Falls back to raw section-output extraction only when assembly
    hasn't run (e.g. unit tests, partial pipeline runs).

    Args:
        memo_result: A ``MemoResult`` dataclass (or dict-like) with at
            least ``memo_body`` (preferred) or ``section_outputs``,
            plus ``ticker``, ``formatted_facts``, ``data_block``, ``scores``.

    Returns:
        Complete markdown string with all sections.
    """
    # Support both dataclass attribute access and dict access
    def _get(obj: Any, key: str, default: Any = None) -> Any:
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    ticker = _get(memo_result, "ticker", "")
    memo_body = _get(memo_result, "memo_body", "")
    section_outputs = _get(memo_result, "section_outputs", {})
    fact_sheet = _get(memo_result, "formatted_facts", {})
    data_block = _get(memo_result, "data_block", {})
    scores = _get(memo_result, "scores", {})

    parts: list[str] = []

    # Title
    company = ""
    if isinstance(fact_sheet, dict):
        meta = fact_sheet.get("_meta", {})
        company = meta.get("company_name") or ticker
    parts.append(f"# {company or ticker} Investment Memo")
    parts.append("")

    # ── PRIMARY PATH: Use assembly-rendered memo_body ──────────────
    # memo_body is produced by assemble_memo() → _SectionRenderer which
    # renders structured JSON into markdown WITH embedded tables,
    # footnotes, and proper formatting.  This is the high-quality path.
    # Guard: also check assembly_ok flag to avoid treating an appendix-only
    # memo_body (from a failed assembly) as real content.
    assembly_ok = _get(memo_result, "assembly_ok", False)
    if memo_body and len(memo_body.strip()) > 500 and assembly_ok:
        parts.append(memo_body)

        # Sources appendix (may already be in memo_body via section 15)
        sources_appendix = ""
        if isinstance(fact_sheet, dict):
            sources_appendix = fact_sheet.get("_sources_appendix", "")
        if sources_appendix and "SOURCES & CITATIONS" not in memo_body:
            parts.append("---")
            parts.append("")
            parts.append(sources_appendix)

        return _clean_multiline("\n".join(parts))

    # ── FALLBACK: Extract from raw section outputs ─────────────────
    # Used only when assemble_memo() hasn't run (partial pipeline, tests).

    # Scores summary (if available)
    if scores:
        parts.append("## Scorecard")
        parts.append("")
        for score_key, score_val in scores.items():
            parts.append(f"- **{score_key}:** {score_val}")
        parts.append("")

    # Quarterly highlights (if embedded in section_outputs)
    quarterly = section_outputs.get("quarterly") or section_outputs.get("section_quarterly")
    if quarterly:
        q_output = quarterly.get("output", {})
        precomputed = quarterly.get("precomputed", {})
        if precomputed:
            parts.append(format_quarterly_section(q_output, precomputed))
            parts.append("")

    # Main sections
    for num, default_title in _SECTION_META:
        key = f"section_{num}"
        section = section_outputs.get(key)
        if not section:
            continue

        title = section.get("section_title", default_title)
        output = section.get("output", {})

        parts.append(f"## Section {num}: {title}")
        parts.append("")

        # Extract text from structured output
        if isinstance(output, dict):
            text = (
                output.get("raw_text")
                or output.get("section_text")
                or output.get("content")
                or ""
            )
            if not text:
                # Concatenate all string values (skip metadata keys)
                skip = {"section_title", "section_thesis", "section_number"}
                for k, v in output.items():
                    if k in skip:
                        continue
                    if isinstance(v, str) and len(v) > 20:
                        parts.append(v)
                        parts.append("")
                text = None  # already appended
            if text:
                parts.append(text)
                parts.append("")
        else:
            parts.append(str(output))
            parts.append("")

    # Financial appendix (if fact sheet available)
    if fact_sheet:
        appendix = build_financial_appendix(fact_sheet)
        if appendix:
            parts.append(appendix)

    # Sources appendix
    sources_appendix = ""
    if isinstance(fact_sheet, dict):
        sources_appendix = fact_sheet.get("_sources_appendix", "")
    if sources_appendix:
        parts.append("---")
        parts.append("")
        parts.append(sources_appendix)

    return _clean_multiline("\n".join(parts))


# ====================================================================
# HTML RENDERING
# ====================================================================

_CSS = """
<style>
  :root {
    --bg: #ffffff;
    --fg: #1a1a2e;
    --accent: #0f3460;
    --border: #ddd;
    --table-stripe: #f8f9fa;
    --code-bg: #f4f4f5;
  }
  body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    color: var(--fg);
    background: var(--bg);
    max-width: 900px;
    margin: 2rem auto;
    padding: 0 1.5rem;
    line-height: 1.65;
    font-size: 15px;
  }
  h1 { font-size: 1.8rem; color: var(--accent); border-bottom: 2px solid var(--accent); padding-bottom: .4rem; margin-top: 2rem; }
  h2 { font-size: 1.35rem; color: var(--accent); margin-top: 2rem; }
  h3 { font-size: 1.1rem; margin-top: 1.4rem; }
  hr { border: none; border-top: 1px solid var(--border); margin: 2rem 0; }
  table { width: 100%; border-collapse: collapse; margin: 1rem 0; font-size: 14px; }
  th { background: var(--accent); color: #fff; text-align: left; padding: 8px 12px; font-weight: 600; }
  td { padding: 6px 12px; border-bottom: 1px solid var(--border); }
  tr:nth-child(even) td { background: var(--table-stripe); }
  p { margin: 0.8rem 0; }
  strong { font-weight: 600; }
  code { background: var(--code-bg); padding: 2px 5px; border-radius: 3px; font-size: 13px; }
  .scorecard { background: var(--code-bg); border-radius: 8px; padding: 1rem 1.5rem; margin: 1rem 0; }
  .scorecard li { list-style: none; padding: 0.2rem 0; }
  .source-block { font-size: 13px; color: #666; }
  @media print {
    body { max-width: 100%; margin: 0; padding: 1cm; font-size: 11pt; }
    h1, h2 { page-break-after: avoid; }
    table { page-break-inside: avoid; }
  }
</style>
"""


def _md_table_to_html(md: str) -> str:
    """Convert a markdown table string to an HTML <table>."""
    lines = [ln.strip() for ln in md.strip().split("\n") if ln.strip()]
    if len(lines) < 2:
        return f"<p>{html_mod.escape(md)}</p>"

    # Header row
    header_cells = [c.strip() for c in lines[0].split("|") if c.strip()]
    # Skip separator row (lines[1])
    body_rows = lines[2:]

    out = ["<table>", "<thead><tr>"]
    for cell in header_cells:
        out.append(f"  <th>{html_mod.escape(cell)}</th>")
    out.append("</tr></thead>")
    out.append("<tbody>")
    for row_line in body_rows:
        cells = [c.strip() for c in row_line.split("|") if c.strip()]
        out.append("<tr>")
        for cell in cells:
            out.append(f"  <td>{html_mod.escape(cell)}</td>")
        out.append("</tr>")
    out.append("</tbody></table>")
    return "\n".join(out)


def _md_to_html_body(md: str) -> str:
    """Minimal markdown-to-HTML converter for memo content."""
    lines = md.split("\n")
    html_parts: list[str] = []
    in_table = False
    table_lines: list[str] = []
    in_list = False

    def flush_table():
        nonlocal in_table, table_lines
        if table_lines:
            html_parts.append(_md_table_to_html("\n".join(table_lines)))
            table_lines = []
        in_table = False

    def flush_list():
        nonlocal in_list
        if in_list:
            html_parts.append("</ul>")
            in_list = False

    for line in lines:
        stripped = line.strip()

        # Table line
        if stripped.startswith("|"):
            if not in_table:
                flush_list()
                in_table = True
            table_lines.append(stripped)
            continue
        else:
            if in_table:
                flush_table()

        # Horizontal rule
        if stripped == "---" or stripped == "***":
            flush_list()
            html_parts.append("<hr>")
            continue

        # Headers
        if stripped.startswith("# "):
            flush_list()
            html_parts.append(f"<h1>{html_mod.escape(stripped[2:])}</h1>")
            continue
        if stripped.startswith("## "):
            flush_list()
            html_parts.append(f"<h2>{html_mod.escape(stripped[3:])}</h2>")
            continue
        if stripped.startswith("### "):
            flush_list()
            html_parts.append(f"<h3>{html_mod.escape(stripped[4:])}</h3>")
            continue

        # List items
        if stripped.startswith("- "):
            if not in_list:
                html_parts.append("<ul>")
                in_list = True
            content = stripped[2:]
            # Bold
            content = re.sub(
                r"\*\*(.+?)\*\*",
                lambda m: f"<strong>{html_mod.escape(m.group(1))}</strong>",
                content,
            )
            html_parts.append(f"<li>{content}</li>")
            continue
        else:
            flush_list()

        # Empty line
        if not stripped:
            continue

        # Regular paragraph -- apply inline formatting
        escaped = html_mod.escape(stripped)
        escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
        escaped = re.sub(r"\*(.+?)\*", r"<em>\1</em>", escaped)
        html_parts.append(f"<p>{escaped}</p>")

    # Flush remaining
    if in_table:
        flush_table()
    flush_list()

    return "\n".join(html_parts)


def format_html(memo_result: Any) -> str:
    """Render the full memo as a self-contained HTML document.

    Converts the markdown output to styled HTML with embedded CSS.

    Args:
        memo_result: A ``MemoResult`` dataclass or dict with memo data.

    Returns:
        Complete HTML string.
    """
    md = format_markdown(memo_result)
    body = _md_to_html_body(md)

    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
        '<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        "<title>Investment Memo</title>\n"
        f"{_CSS}\n"
        "</head>\n<body>\n"
        f"{body}\n"
        "</body>\n</html>"
    )


# ====================================================================
# DISCORD SCORECARD (2000-char limit)
# ====================================================================

def format_discord_scorecard(data_block: dict[str, Any]) -> str:
    """Build a compact scorecard for Discord (max 2000 chars).

    Args:
        data_block: Dict with keys like ticker, company_name, scores,
            headline (revenue, eps, beat/miss), recommendation, and
            price_target.

    Returns:
        Discord-formatted string under 2000 characters.
    """
    ticker = data_block.get("ticker", "???")
    company = data_block.get("company_name", ticker)
    scores = data_block.get("scores", {})
    headline = data_block.get("headline", {})
    recommendation = data_block.get("recommendation", "")
    price_target = data_block.get("price_target", "")
    thesis = data_block.get("thesis", "")
    quarter_label = data_block.get("quarter_label", "")

    lines: list[str] = []
    lines.append(f"**{company} ({ticker})**")
    if quarter_label:
        lines.append(f"_{quarter_label}_")
    lines.append("")

    # Headline numbers
    if headline:
        rev = headline.get("revenue", "")
        eps = headline.get("eps", "")
        beat = headline.get("beat_miss", "")
        if rev:
            lines.append(f"Revenue: {rev}")
        if eps:
            lines.append(f"EPS: {eps}")
        if beat:
            lines.append(f"Beat/Miss: {beat}")
        lines.append("")

    # Scores
    if scores:
        lines.append("**Scores**")
        for k, v in scores.items():
            label = k.replace("_", " ").title()
            lines.append(f"  {label}: {v}")
        lines.append("")

    # Recommendation
    if recommendation:
        lines.append(f"**Recommendation:** {recommendation}")
    if price_target:
        lines.append(f"**Price Target:** {price_target}")
    if thesis:
        lines.append("")
        lines.append(f"_{thesis}_")

    result = "\n".join(lines)

    # Hard cap at 2000 characters for Discord
    if len(result) > 2000:
        result = result[:1997] + "..."

    return result


# ====================================================================
# FINANCIAL APPENDIX (NEW)
# ====================================================================

def _year_dict_to_table(
    data: dict[str, Any],
    label: str,
) -> str:
    """Convert a year-keyed dict ``{ "2021 FY": val, ... }`` to a
    single markdown table row: ``| label | val1 | val2 | ... |``

    Returns (header_years, row_str) or empty string if no data.
    """
    if not isinstance(data, dict):
        return ""
    year_items = sorted(
        ((k, v) for k, v in data.items() if re.match(r"^\d{4}", k)),
        key=lambda t: t[0],
    )
    if not year_items:
        return ""
    cells = " | ".join(_safe(v) for _, v in year_items)
    return f"| {label} | {cells} |"


def _year_headers(data: dict[str, Any]) -> list[str]:
    """Extract sorted year-key headers from a year-keyed dict."""
    if not isinstance(data, dict):
        return []
    return sorted(k for k in data if re.match(r"^\d{4}", k))


def _build_statement_table(
    section: dict[str, Any],
    metrics: list[tuple[str, str]],
    title: str,
) -> str:
    """Build a full markdown table from a fact-sheet section.

    Args:
        section: Dict like s11_income_statement.
        metrics: List of (key, display_label) tuples.
        title: Table heading.

    Returns:
        Markdown string with heading + table.
    """
    if not isinstance(section, dict):
        return ""

    # Find year columns from the first available metric
    years: list[str] = []
    for key, _ in metrics:
        val = section.get(key)
        if isinstance(val, dict):
            years = _year_headers(val)
            if years:
                break

    if not years:
        return ""

    # Build header
    header = "| Metric | " + " | ".join(years) + " |"
    separator = "| --- " + "| --- " * len(years) + "|"

    rows: list[str] = [f"### {title}", "", header, separator]

    for key, label in metrics:
        val = section.get(key)
        if val is None:
            continue
        if isinstance(val, dict):
            # Skip rows where ALL year values are None/empty (e.g. R&D for utilities)
            if not any(val.get(y) is not None for y in years):
                continue
            cells = " | ".join(_safe(val.get(y)) for y in years)
            rows.append(f"| {label} | {cells} |")
        elif isinstance(val, (str, int, float)):
            # Scalar -- repeat across columns or show once
            rows.append(f"| {label} | {_safe(val)} |")

    if len(rows) <= 4:
        return ""  # header only, no data rows

    return "\n".join(rows)


def build_financial_appendix(fact_sheet: dict[str, Any]) -> str:
    """Build a standardised financial statements appendix section.

    Extracts data from the ``s11_*`` sections of the formatted fact
    sheet and renders income statement, cash flow statement, balance
    sheet, and return metrics as markdown tables.

    Args:
        fact_sheet: The formatted quantitative fact sheet dict.

    Returns:
        Markdown string for the financial appendix section.
    """
    if not isinstance(fact_sheet, dict):
        return ""

    income = fact_sheet.get("s11_income_statement", {})
    cash_flow = fact_sheet.get("s11_cash_flow", {})
    balance = fact_sheet.get("s11_balance_sheet", {})
    returns = fact_sheet.get("s11_returns", {})

    parts: list[str] = []
    parts.append("## Financial Statements Appendix")
    parts.append("")

    # ── Income Statement ─────────────────────────────────────────
    income_table = _build_statement_table(
        income,
        [
            ("revenue_usd_m", "Revenue"),
            ("revenue_growth_pct", "Revenue Growth"),
            ("cost_of_revenue_usd_m", "Cost of Revenue"),
            ("gross_profit_usd_m", "Gross Profit"),
            ("gross_margin_pct", "Gross Margin"),
            ("rd_expense_usd_m", "R&D Expense"),
            ("sga_expense_usd_m", "SG&A Expense"),
            ("operating_income_usd_m", "Operating Income"),
            ("operating_margin_pct", "Operating Margin"),
            ("ebitda_usd_m", "EBITDA"),
            ("ebitda_margin_pct", "EBITDA Margin"),
            ("net_income_usd_m", "Net Income"),
            ("net_margin_pct", "Net Margin"),
            ("eps_diluted", "EPS (Diluted)"),
            ("eps_diluted_growth_pct", "EPS Growth"),
            ("sbc_usd_m", "Stock-Based Comp"),
            ("sbc_pct_of_revenue", "SBC % of Revenue"),
            ("interest_expense_usd_m", "Interest Expense"),
            ("effective_tax_rate_pct", "Effective Tax Rate"),
        ],
        "Income Statement",
    )
    if income_table:
        parts.append(income_table)
        parts.append("")

    # ── Cash Flow Statement ──────────────────────────────────────
    cf_table = _build_statement_table(
        cash_flow,
        [
            ("operating_cash_flow_usd_m", "Operating Cash Flow"),
            ("ocf_growth_pct", "OCF Growth"),
            ("ocf_margin_pct", "OCF Margin"),
            ("capex_usd_m", "Capital Expenditure"),
            ("capex_pct_of_revenue", "CapEx % of Revenue"),
            ("free_cash_flow_usd_m", "Free Cash Flow"),
            ("fcf_margin_pct", "FCF Margin"),
            ("fcf_growth_pct", "FCF Growth"),
            ("fcf_conversion_pct", "FCF Conversion"),
            ("da_usd_m", "D&A"),
            ("change_in_working_capital_usd_m", "Change in Working Capital"),
            ("dividends_paid_usd_m", "Dividends Paid"),
            ("share_repurchases_usd_m", "Share Repurchases"),
        ],
        "Cash Flow Statement",
    )
    if cf_table:
        parts.append(cf_table)
        parts.append("")

    # ── Balance Sheet ────────────────────────────────────────────
    bs_table = _build_statement_table(
        balance,
        [
            ("cash_and_equivalents_usd_m", "Cash & Equivalents"),
            ("total_current_assets_usd_m", "Total Current Assets"),
            ("pp_and_e_usd_m", "PP&E"),
            ("goodwill_usd_m", "Goodwill"),
            ("intangible_assets_usd_m", "Intangible Assets"),
            ("total_assets_usd_m", "Total Assets"),
            ("total_current_liabilities_usd_m", "Total Current Liabilities"),
            ("total_debt_usd_m", "Total Debt"),
            ("total_liabilities_usd_m", "Total Liabilities"),
            ("retained_earnings_usd_m", "Retained Earnings"),
            ("total_equity_usd_m", "Total Equity"),
            ("net_debt_usd_m", "Net Debt"),
            ("net_working_capital_usd_m", "Net Working Capital"),
            ("net_debt_to_ebitda", "Net Debt / EBITDA"),
            ("interest_coverage_ratio", "Interest Coverage"),
            ("debt_to_equity_ratio", "Debt / Equity"),
            ("current_ratio", "Current Ratio"),
            ("goodwill_pct_total_assets", "Goodwill % of Assets"),
        ],
        "Balance Sheet",
    )
    if bs_table:
        parts.append(bs_table)
        parts.append("")

    # ── Return Metrics ───────────────────────────────────────────
    returns_table = _build_statement_table(
        returns,
        [
            ("roic_pct", "ROIC"),
            ("roe_pct", "ROE"),
            ("roa_pct", "ROA"),
            ("roce_pct", "ROCE"),
            ("income_quality", "Income Quality"),
        ],
        "Return Metrics",
    )
    if returns_table:
        parts.append(returns_table)
        parts.append("")

    result = "\n".join(parts)
    return _clean_multiline(result) if result.strip() else ""


# ====================================================================
# SCORECARD JSON (website dashboard + internal use)
# ====================================================================

def build_scorecard_json(
    fact_sheet: dict[str, Any],
    data_block: dict[str, Any],
    section_outputs: dict[str, Any] | None = None,
    mode: str = "personal",
) -> dict[str, Any]:
    """Build a comprehensive JSON scorecard for website dashboard and internal use.

    This is the primary structured output consumed by the Ape Axiom website
    dashboard and the Discord bot.  Includes: identity, pricing, multiples,
    scores, financials, peer data, thesis, and recommendation.

    Args:
        fact_sheet: Formatted quantitative fact sheet.
        data_block: From assembly.build_data_block().
        section_outputs: Dict of section writer outputs.
        mode: "personal" (include pricing/recommendation) or "website" (no pricing).

    Returns:
        JSON-serializable dict.
    """
    ident = fact_sheet.get("s1_identity", {})
    val = fact_sheet.get("s13_valuation") or fact_sheet.get("s12_valuation", {})
    inc = fact_sheet.get("s11_income_statement", {})
    cf = fact_sheet.get("s11_cash_flow", {})
    margins = fact_sheet.get("s5_subject_margins", {})
    returns = fact_sheet.get("s11_returns", {})
    share_data = fact_sheet.get("s5_share_data", {})
    meta = fact_sheet.get("_meta", {})
    latest = meta.get("latest_annual_year", "")
    peers = fact_sheet.get("s12_peer_benchmarking", {})
    fwd = fact_sheet.get("s10_s13_forward_estimates", [])

    section_outputs = section_outputs or {}

    def _ly(d: dict | Any, key: str = "") -> Any:
        """Get latest year value from a year-keyed dict."""
        if key:
            d = d.get(key, {}) if isinstance(d, dict) else {}
        if isinstance(d, dict):
            return d.get(latest)
        return d

    # ── Extract thesis and verdict from LLM outputs ──────────
    s1_out = section_outputs.get("section_1", {}).get("output", {})
    s14_out = section_outputs.get("section_14", {}).get("output", {})
    s5_out = section_outputs.get("section_5", {}).get("output", {})

    thesis = s1_out.get("investment_thesis", "")
    verdict = s14_out.get("the_verdict", "")
    moat_class = (
        _safe_nested(s5_out, "overall_assessment", "classification")
        or data_block.get("moat", "")
    )

    scorecard: dict[str, Any] = {
        # ── Identity ──
        "ticker": ident.get("ticker", ""),
        "company_name": ident.get("company_name", ""),
        "sector": ident.get("sector", ""),
        "industry": ident.get("industry", ""),
        "exchange": ident.get("exchange", ""),
        "description": ident.get("description", "")[:300],
        "data_as_of": meta.get("data_as_of", ""),
        "latest_fiscal_year": latest,

        # ── Pricing ──
        "current_price": val.get("_current_price"),
        "market_cap_b": val.get("_current_market_cap_b"),
        "enterprise_value_b": val.get("_current_ev_b"),
        "price_52w_range": ident.get("range", ""),
        "beta": ident.get("beta"),
        "dividend_yield_pct": val.get("_current_dividend_yield_pct"),

        # ── Valuation Multiples ──
        "pe_ratio": val.get("_current_pe"),
        "ev_ebitda": val.get("_current_ev_ebitda"),
        "ev_sales": _ly(val, "ev_to_sales"),
        "p_fcf": val.get("_current_p_fcf"),
        "p_book": _ly(val, "price_to_book"),
        "fcf_yield_pct": val.get("_current_fcf_yield_pct"),
        "earnings_yield_pct": _ly(val, "earnings_yield_pct"),

        # ── Peer Medians ──
        "peer_median_pe": peers.get("peer_medians", {}).get("price_to_earnings"),
        "peer_median_ev_ebitda": peers.get("peer_medians", {}).get("ev_to_ebitda"),
        "peer_median_ev_sales": peers.get("peer_medians", {}).get("ev_to_sales"),

        # ── Scores (normalize to 0-100 scale) ──
        "moat_classification": moat_class,
        "moat_score": _normalize_score(data_block.get("moat_score")),
        "growth_score": _normalize_score(data_block.get("growth")),
        "quality_score": _normalize_score(data_block.get("quality")),

        # ── Fair Value ──
        "fair_value": data_block.get("fair_value"),
        "fair_value_method": data_block.get("fair_value_method"),
        "fair_value_note": data_block.get("fair_value_note"),

        # ── Key Financials (latest year) ──
        "revenue": _ly(inc, "revenue_usd_m"),
        "revenue_growth_pct": _ly(inc, "revenue_growth_pct"),
        "gross_margin_pct": _ly(margins, "gross_margin_pct"),
        "operating_margin_pct": _ly(margins, "operating_margin_pct"),
        "net_margin_pct": _ly(margins, "net_margin_pct"),
        "roic_pct": _ly(returns, "roic_pct"),
        "roe_pct": _ly(returns, "roe_pct"),
        "fcf_margin_pct": _ly(cf, "fcf_margin_pct"),
        "eps_diluted": _ly(inc, "eps_diluted"),

        # ── Share Data ──
        "shares_diluted_m": _ly(share_data, "shares_diluted_millions"),
        "sbc_pct_revenue": _ly(share_data, "sbc_pct_of_revenue"),

        # ── Thesis & Verdict ──
        "investment_thesis": thesis,
        "verdict": verdict,

        # ── Forward Estimates (if available) ──
        "forward_estimates": fwd[:3] if isinstance(fwd, list) else [],
    }

    # ── Margin of safety (if fair value available) ──
    if scorecard["fair_value"] and scorecard["current_price"]:
        fv = scorecard["fair_value"]
        cp = scorecard["current_price"]
        mos = round(((fv - cp) / fv) * 100, 1)
        scorecard["margin_of_safety_pct"] = mos
        scorecard["upside_pct"] = round(((fv / cp) - 1) * 100, 1)
    else:
        scorecard["margin_of_safety_pct"] = None
        scorecard["upside_pct"] = None

    # ── Mode-specific: strip pricing from website mode ──
    if mode == "website":
        scorecard.pop("fair_value", None)
        scorecard.pop("fair_value_method", None)
        scorecard.pop("fair_value_note", None)
        scorecard.pop("margin_of_safety_pct", None)
        scorecard.pop("upside_pct", None)

    return scorecard


def _safe_nested(d: dict | None, *keys: str) -> Any:
    """Nested safe dict access."""
    current = d
    for k in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(k)
    return current


def format_discord_scorecard_v2(scorecard: dict[str, Any]) -> str:
    """Build a rich Discord scorecard from the scorecard JSON.

    Uses the full scorecard dict (from build_scorecard_json) to produce
    a Discord-formatted message under 2000 characters.
    """
    ticker = scorecard.get("ticker", "???")
    company = scorecard.get("company_name", ticker)
    price = scorecard.get("current_price")
    mkt_cap = scorecard.get("market_cap_b")

    lines: list[str] = []
    lines.append(f"**{company} ({ticker})**")

    # Price line
    price_parts = []
    if price:
        price_parts.append(f"${price:.2f}")
    if mkt_cap:
        price_parts.append(f"Mkt Cap: ${mkt_cap:.0f}B")
    if price_parts:
        lines.append(" | ".join(price_parts))
    lines.append("")

    # Multiples
    mult_parts = []
    pe = scorecard.get("pe_ratio")
    ev_ebitda = scorecard.get("ev_ebitda")
    p_fcf = scorecard.get("p_fcf")
    if pe:
        mult_parts.append(f"P/E: {pe:.1f}x")
    if ev_ebitda:
        mult_parts.append(f"EV/EBITDA: {ev_ebitda:.1f}x")
    if p_fcf:
        mult_parts.append(f"P/FCF: {p_fcf:.1f}x")
    if mult_parts:
        lines.append("**Multiples:** " + " | ".join(mult_parts))

    # Revenue & margins
    rev = scorecard.get("revenue")
    rev_g = scorecard.get("revenue_growth_pct")
    gm = scorecard.get("gross_margin_pct")
    om = scorecard.get("operating_margin_pct")
    if rev:
        rev_str = rev if isinstance(rev, str) else f"${rev / 1000:.1f}B"
        parts = [f"Rev: {rev_str}"]
        if rev_g:
            rg_str = rev_g if isinstance(rev_g, str) else f"{rev_g:.1f}%"
            parts.append(f"Growth: {rg_str}")
        lines.append(" | ".join(parts))
    margin_parts = []
    if gm:
        gm_str = gm if isinstance(gm, str) else f"{gm:.1f}%"
        margin_parts.append(f"GM: {gm_str}")
    if om:
        om_str = om if isinstance(om, str) else f"{om:.1f}%"
        margin_parts.append(f"OM: {om_str}")
    if margin_parts:
        lines.append("Margins: " + " | ".join(margin_parts))
    lines.append("")

    # Scores
    moat = scorecard.get("moat_score")
    growth = scorecard.get("growth_score")
    quality = scorecard.get("quality_score")
    moat_class = scorecard.get("moat_classification", "")
    score_parts = []
    if moat is not None:
        score_parts.append(f"Moat: {moat}/100" + (f" ({moat_class})" if moat_class else ""))
    if growth is not None:
        score_parts.append(f"Growth: {growth}/100")
    if quality is not None:
        score_parts.append(f"Quality: {quality}/100")
    if score_parts:
        lines.append("**Scores:** " + " | ".join(score_parts))

    # Fair value
    fv = scorecard.get("fair_value")
    fv_method = scorecard.get("fair_value_method")
    mos = scorecard.get("margin_of_safety_pct")
    upside = scorecard.get("upside_pct")
    if fv:
        fv_line = f"**Fair Value:** ${fv:.2f} ({fv_method})"
        if mos is not None:
            direction = "upside" if upside and upside > 0 else "downside"
            fv_line += f" | {abs(upside):.1f}% {direction}"
        lines.append(fv_line)

    lines.append("")

    # Thesis
    thesis = scorecard.get("investment_thesis", "")
    if thesis:
        lines.append(f"_{thesis[:300]}_")

    result = "\n".join(lines)
    if len(result) > 2000:
        result = result[:1997] + "..."
    return result
