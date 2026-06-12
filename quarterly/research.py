"""Quarterly Research Agent — Web-search-powered earnings data extraction.

Uses the OpenAI Responses API with the built-in web_search tool to find
the most recent quarterly earnings data and extract it into structured JSON.

Sector-aware: injects sector-specific research focus, extraction rules,
and KPI schema fields into the prompt.
"""

from __future__ import annotations

import json

from config.settings import OPENAI_API_KEY, RESEARCH_AGENT_MODEL
from quarterly.sector_prompts import get_sector_config


# ═══════════════════════════════════════════════════════════════════════
# COMMON RESEARCH SCHEMA (shared across all sectors)
# ═══════════════════════════════════════════════════════════════════════

_COMMON_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "quarter_reported": {
            "type": ["string", "null"],
            "description": "Quarter label, e.g. Q1 FY2026",
        },
        "quarter_end_date": {
            "type": ["string", "null"],
            "description": "Fiscal quarter end date, e.g. 2025-12-28",
        },
        "earnings_date": {
            "type": ["string", "null"],
            "description": "Earnings release date, e.g. 2025-01-30",
        },
        "headline": {
            "type": "object",
            "properties": {
                "revenue_actual_m": {
                    "type": ["number", "null"],
                    "description": "Reported revenue in $M. Use sector-specific definition per extraction rules.",
                },
                "revenue_consensus_m": {
                    "type": ["number", "null"],
                    "description": "Consensus revenue estimate in $M",
                },
                "revenue_yoy_growth_pct": {
                    "type": ["number", "null"],
                    "description": "Revenue YoY growth %",
                },
                "eps_actual": {
                    "type": ["number", "null"],
                    "description": "Reported diluted EPS in $. Prefer GAAP. If adjusted only, set eps_basis to adjusted.",
                },
                "eps_consensus": {
                    "type": ["number", "null"],
                    "description": "Consensus EPS estimate in $. Must match same basis as eps_actual.",
                },
                "eps_yoy_growth_pct": {
                    "type": ["number", "null"],
                    "description": "EPS YoY growth % on same basis as eps_actual",
                },
                "eps_basis": {
                    "type": ["string", "null"],
                    "enum": ["GAAP", "adjusted", None],
                    "description": "Whether EPS figures are GAAP or adjusted",
                },
            },
            "required": [
                "revenue_actual_m", "revenue_consensus_m", "revenue_yoy_growth_pct",
                "eps_actual", "eps_consensus", "eps_yoy_growth_pct", "eps_basis",
            ],
            "additionalProperties": False,
        },
        "margins": {
            "type": "object",
            "properties": {
                "gross_margin_pct": {"type": ["number", "null"], "description": "Current quarter gross margin %"},
                "gross_margin_prior_year_pct": {"type": ["number", "null"], "description": "Same quarter prior year gross margin %"},
                "operating_margin_pct": {"type": ["number", "null"], "description": "Current quarter operating margin %"},
                "operating_margin_prior_year_pct": {"type": ["number", "null"], "description": "Same quarter prior year operating margin %"},
                "net_margin_pct": {"type": ["number", "null"], "description": "Current quarter net margin %"},
                "net_margin_prior_year_pct": {"type": ["number", "null"], "description": "Same quarter prior year net margin %"},
            },
            "required": [
                "gross_margin_pct", "gross_margin_prior_year_pct",
                "operating_margin_pct", "operating_margin_prior_year_pct",
                "net_margin_pct", "net_margin_prior_year_pct",
            ],
            "additionalProperties": False,
        },
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Segment name as reported"},
                    "revenue_m": {"type": ["number", "null"], "description": "Segment revenue in $M"},
                    "yoy_growth_pct": {"type": ["number", "null"], "description": "Segment YoY revenue growth %"},
                    "sequential_growth_pct": {"type": ["number", "null"], "description": "Segment QoQ revenue growth %"},
                },
                "required": ["name", "revenue_m", "yoy_growth_pct", "sequential_growth_pct"],
                "additionalProperties": False,
            },
            "minItems": 1,
            "maxItems": 5,
        },
        "guidance": {
            "type": "object",
            "properties": {
                "next_quarter_revenue_low_m": {"type": ["number", "null"]},
                "next_quarter_revenue_high_m": {"type": ["number", "null"]},
                "full_year_revenue_low_m": {"type": ["number", "null"]},
                "full_year_revenue_high_m": {"type": ["number", "null"]},
                "direction": {
                    "type": ["string", "null"],
                    "enum": ["raised", "lowered", "maintained", "initiated", None],
                },
                "commentary": {"type": ["string", "null"], "description": "Key guidance language, verbatim quotes preferred."},
            },
            "required": [
                "next_quarter_revenue_low_m", "next_quarter_revenue_high_m",
                "full_year_revenue_low_m", "full_year_revenue_high_m",
                "direction", "commentary",
            ],
            "additionalProperties": False,
        },
        "management": {
            "type": "object",
            "properties": {
                "tone": {"type": ["string", "null"], "enum": ["confident", "cautious", "mixed", "defensive", None]},
                "key_quote": {"type": ["string", "null"], "description": "Most significant verbatim quote from CEO or CFO"},
                "key_quote_speaker": {"type": ["string", "null"], "description": "Full name and title"},
                "key_theme_1": {"type": ["string", "null"]},
                "key_theme_2": {"type": ["string", "null"]},
            },
            "required": ["tone", "key_quote", "key_quote_speaker", "key_theme_1", "key_theme_2"],
            "additionalProperties": False,
        },
        "market_reaction": {
            "type": "object",
            "properties": {
                "stock_move_pct": {"type": ["number", "null"], "description": "Stock % change on earnings day"},
                "stock_move_direction": {"type": ["string", "null"], "enum": ["up", "down", None]},
                "close_price": {"type": ["number", "null"], "description": "Closing price on earnings day $"},
            },
            "required": ["stock_move_pct", "stock_move_direction", "close_price"],
            "additionalProperties": False,
        },
        "analysts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "firm": {"type": "string"},
                    "action": {"type": ["string", "null"], "enum": ["upgraded", "downgraded", "maintained", "initiated", "reiterated", None]},
                    "rating": {"type": ["string", "null"]},
                    "price_target": {"type": ["number", "null"]},
                    "comment": {"type": ["string", "null"]},
                },
                "required": ["firm", "action", "rating", "price_target", "comment"],
                "additionalProperties": False,
            },
            "minItems": 0,
            "maxItems": 4,
        },
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "url": {"type": "string"},
                },
                "required": ["name", "url"],
                "additionalProperties": False,
            },
            "minItems": 1,
            "maxItems": 6,
        },
    },
    "required": [
        "quarter_reported", "quarter_end_date", "earnings_date",
        "headline", "margins", "segments", "guidance",
        "management", "market_reaction", "analysts", "sources",
    ],
    "additionalProperties": False,
}


def _build_research_schema(sector_family: str) -> dict:
    """Build the full JSON schema, merging common fields with sector-specific KPIs."""
    schema = json.loads(json.dumps(_COMMON_SCHEMA))  # deep copy
    config = get_sector_config(sector_family)
    extra = config.get("extra_schema", {})
    if extra:
        schema["properties"].update(extra)
        # Add sector_kpis to required if it has required fields
        if "sector_kpis" in extra:
            schema["required"].append("sector_kpis")
    return schema


def _build_system_prompt(sector_family: str) -> str:
    """Build the system prompt with sector-specific extraction rules."""
    config = get_sector_config(sector_family)

    base = (
        "You are a financial research analyst. Your job is to find the most recent "
        "quarterly earnings data for the target company using web search and extract "
        "it into a structured JSON format.\n\n"
        "SEARCH STRATEGY\n"
        "Execute these searches in order. Do not skip steps.\n\n"
        '1. "{ticker} Q[X] [year] earnings press release" site:sec.gov OR site:[company IR domain]\n'
        '2. "{company} {ticker} latest quarterly earnings results"\n'
        '3. "{company} {ticker} Q[X] [prior year] earnings results" (required for prior-year margins)\n'
        '4. "{company} {ticker} earnings call transcript"\n'
        '5. "{company} {ticker} analyst reaction earnings"\n\n'
        "EXTRACTION RULES\n\n"
        "- Use ONLY data from the most recent completed quarter.\n"
        "- Source priority: (1) Company IR / SEC 8-K, (2) SEC filing, (3) Bloomberg/Reuters, "
        "(4) WSJ/FT, (5) Seeking Alpha/Barron's.\n"
        "- Revenue in millions (e.g. 124300 not 124.3B). EPS in dollars. Margins as percentages "
        "(e.g. 46.9 not 0.469). Stock move as percentage (e.g. -4.2 not -0.042).\n"
        "- For EPS: use GAAP diluted EPS. If only adjusted available, set eps_basis to 'adjusted'.\n"
        "- Do not round numbers. Use exact figures as reported.\n"
        "- If data cannot be found, set to null. Do not fabricate.\n"
        "- For segment data, use the company's own segment names exactly.\n"
        "- For analyst reactions, only include actions within 72 hours of earnings.\n"
        "- For management quotes, use verbatim language only.\n\n"
    )

    # Sector-specific rules
    sector_rules = config.get("extraction_rules", "")
    if sector_rules:
        base += f"SECTOR-SPECIFIC RULES ({sector_family.upper()})\n{sector_rules}\n\n"

    # Sector-specific focus
    sector_focus = config.get("research_focus", "")
    if sector_focus:
        base += (
            f"SECTOR-SPECIFIC METRICS TO EXTRACT\n"
            f"In addition to the common metrics, extract these sector KPIs: {sector_focus}\n"
            f"Populate the sector_kpis object with these values.\n\n"
        )

    base += "Output your findings as structured JSON matching the provided schema exactly."
    return base


def _build_user_prompt(ticker: str, company_name: str, sector_family: str, quarter_hint: str | None = None) -> str:
    """Build the user prompt for the research agent."""
    config = get_sector_config(sector_family)
    hint = f" Focus on {quarter_hint}." if quarter_hint else ""

    prompt = (
        f"Research the most recent quarterly earnings for {company_name} ({ticker}).{hint}\n\n"
        f"Find and extract:\n"
        f"1. Headline results (revenue, EPS) with consensus estimates and YoY comparisons\n"
        f"2. Margin data (gross, operating, net) for current quarter AND same quarter prior year\n"
        f"3. Top 2-5 segment/product line performance with revenue, YoY growth, and QoQ growth\n"
        f"4. Forward guidance (next quarter and/or full year) with direction and exact management language\n"
        f"5. Key management commentary and tone from earnings call — verbatim quotes only\n"
        f"6. Stock price percentage move and closing price on earnings day\n"
        f"7. Analyst rating changes and price target updates within 72 hours of earnings\n"
        f"8. Source URLs for every data point used\n"
    )

    # Sector-specific KPIs
    sector_focus = config.get("research_focus", "")
    if sector_focus:
        prompt += f"\nSECTOR-SPECIFIC ({sector_family.upper()}):\n9. {sector_focus}\n"

    prompt += "\nFill every field you can find. Set fields to null only when data is genuinely unavailable."
    return prompt


# ═══════════════════════════════════════════════════════════════════════
# MAIN RESEARCH FUNCTION
# ═══════════════════════════════════════════════════════════════════════

async def run_quarterly_research(
    ticker: str,
    company_name: str,
    sector_family: str,
    quarter_hint: str | None = None,
) -> dict:
    """Run the quarterly research agent with web search.

    Uses the OpenAI Responses API with built-in web_search tool to find
    live earnings data and extract it into structured JSON.

    Args:
        ticker: Stock ticker symbol.
        company_name: Full company name.
        sector_family: Canonical sector family (e.g., "banking", "technology").
        quarter_hint: Optional hint like "Q4 2025" to disambiguate.

    Returns:
        Structured dict matching the research schema (common + sector KPIs).
    """
    import openai

    client = openai.AsyncOpenAI(api_key=OPENAI_API_KEY, timeout=300)

    schema = _build_research_schema(sector_family)
    system_prompt = _build_system_prompt(sector_family)
    user_prompt = _build_user_prompt(ticker, company_name, sector_family, quarter_hint)

    model = RESEARCH_AGENT_MODEL or "gpt-5-mini"

    response = await client.responses.create(
        model=model,
        instructions=system_prompt,
        input=user_prompt,
        tools=[{"type": "web_search_preview", "search_context_size": "high"}],
        text={
            "format": {
                "type": "json_schema",
                "name": "research_schema",
                "schema": schema,
                "strict": False,
            }
        },
    )

    # Extract text content from response
    raw_text = ""
    for item in response.output:
        if item.type == "message":
            for content in item.content:
                if content.type == "output_text":
                    raw_text = content.text
                    break

    # Parse JSON
    try:
        result = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        result = {"raw_text": raw_text, "_parse_error": True}

    return result
