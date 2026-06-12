"""SEC Filing Text Extraction.

Extracts key sections from 10-K and 10-Q filings using edgartools.
Returns structured text for feeding to research agents instead of web search.

Sections extracted from 10-K:
  - Item 1: Business
  - Item 1A: Risk Factors
  - Item 7: MD&A (Management's Discussion and Analysis)
  - Item 7A: Market Risk
  - Item 8: Financial Statements (footnotes)

Sections extracted from 10-Q:
  - Item 1: Financial Statements (footnotes)
  - Item 2: MD&A
  - Item 1A: Risk Factors (if updated)
"""

import json
import os
import re
from pathlib import Path
from typing import Optional

import edgar
from edgar import Company

from sec.client import require_sec_user_agent


# Cache directory
CACHE_DIR = Path(os.getenv("CACHE_DIR", str(Path(__file__).resolve().parent.parent / "cache")))
FILINGS_CACHE = CACHE_DIR / "filings"

_identity_set = False


def _ensure_identity() -> None:
    """Set the edgartools SEC identity once, lazily."""
    global _identity_set
    if not _identity_set:
        edgar.set_identity(require_sec_user_agent())
        _identity_set = True


def _cache_path(ticker: str, form: str, accession: str) -> Path:
    """Generate cache file path for a filing."""
    safe_accession = accession.replace("-", "")
    return FILINGS_CACHE / f"{ticker}_{form}_{safe_accession}.json"


def _extract_section(markdown: str, start_item: str, end_items: list[str]) -> str:
    """Extract text between two Item headings in filing markdown.

    Args:
        markdown: Full filing markdown text
        start_item: Item pattern to start extraction (e.g., "Item 1.")
        end_items: List of Item patterns where extraction should stop
    """
    # Build start pattern - match "Item 1." or "Item 1 " or "Item 1A."
    start_pattern = re.compile(
        rf"^#+\s*{re.escape(start_item)}\s",
        re.MULTILINE | re.IGNORECASE,
    )

    start_match = start_pattern.search(markdown)
    if not start_match:
        return ""

    start_pos = start_match.end()

    # Find earliest end position
    end_pos = len(markdown)
    for end_item in end_items:
        end_pattern = re.compile(
            rf"^#+\s*{re.escape(end_item)}\s",
            re.MULTILINE | re.IGNORECASE,
        )
        end_match = end_pattern.search(markdown, start_pos)
        if end_match and end_match.start() < end_pos:
            end_pos = end_match.start()

    section_text = markdown[start_pos:end_pos].strip()

    # Clean up: remove excessive whitespace and HTML tags
    section_text = re.sub(r"<[^>]+>", "", section_text)
    section_text = re.sub(r"\n{3,}", "\n\n", section_text)

    return section_text


def get_10k_sections(ticker: str) -> dict:
    """Extract key sections from the latest 10-K filing.

    Returns:
        Dictionary with keys:
        - filing_date: Filing date string
        - period: Period of report
        - accession_no: SEC accession number
        - business: Item 1 text
        - risk_factors: Item 1A text
        - mda: Item 7 (MD&A) text
        - market_risk: Item 7A text
        - financial_statements_notes: Item 8 text (truncated to footnotes)
        - full_text_length: Total character count of full filing
    """
    ticker = ticker.upper()

    _ensure_identity()
    company = Company(ticker)
    filings = company.get_filings(form="10-K")

    if not filings or len(filings) == 0:
        return {"error": f"No 10-K filings found for {ticker}"}

    filing = filings[0]

    accession = filing.accession_no
    filing_date = str(filing.filing_date)

    # Check cache
    cache_file = _cache_path(ticker, "10-K", accession)
    if cache_file.exists():
        with open(cache_file) as f:
            return json.load(f)

    # Fetch and parse
    md = filing.markdown()

    result = {
        "ticker": ticker,
        "form": "10-K",
        "filing_date": filing_date,
        "accession_no": accession,
        "period": str(getattr(filing, "report_date", filing_date)),
        "business": _extract_section(md, "Item 1.", ["Item 1A.", "Item 1B."]),
        "risk_factors": _extract_section(md, "Item 1A.", ["Item 1B.", "Item 1C.", "Item 2."]),
        "mda": _extract_section(md, "Item 7.", ["Item 7A.", "Item 8."]),
        "market_risk": _extract_section(md, "Item 7A.", ["Item 8."]),
        "financial_statements_notes": _extract_section(
            md, "Item 8.", ["Item 9.", "Item 9A."]
        )[:50000],  # Truncate — notes can be very long
        "full_text_length": len(md),
    }

    # Cache result
    FILINGS_CACHE.mkdir(parents=True, exist_ok=True)
    with open(cache_file, "w") as f:
        json.dump(result, f)

    return result


def get_10q_sections(ticker: str) -> dict:
    """Extract key sections from the latest 10-Q filing.

    Returns:
        Dictionary with keys:
        - filing_date: Filing date string
        - period: Period of report
        - accession_no: SEC accession number
        - mda: Part I, Item 2 (MD&A) text
        - risk_factors: Part II, Item 1A (Risk Factors update, if present)
        - financial_statements_notes: Part I, Item 1 text (truncated)
        - full_text_length: Total character count of full filing
    """
    ticker = ticker.upper()

    _ensure_identity()
    company = Company(ticker)
    filings = company.get_filings(form="10-Q")

    if not filings or len(filings) == 0:
        return {"error": f"No 10-Q filings found for {ticker}"}

    filing = filings[0]

    accession = filing.accession_no
    filing_date = str(filing.filing_date)

    # Check cache
    cache_file = _cache_path(ticker, "10-Q", accession)
    if cache_file.exists():
        with open(cache_file) as f:
            return json.load(f)

    # Fetch and parse
    md = filing.markdown()

    # 10-Q structure is different from 10-K:
    # Part I: Financial Information
    #   Item 1: Financial Statements
    #   Item 2: MD&A
    #   Item 3: Quantitative and Qualitative Disclosures About Market Risk
    #   Item 4: Controls and Procedures
    # Part II: Other Information
    #   Item 1: Legal Proceedings
    #   Item 1A: Risk Factors
    #   Item 2: Unregistered Sales
    #   Item 6: Exhibits

    # 10-Q items often don't have unique "Item N" prefixes — they repeat.
    # We look for MD&A by keyword match instead.
    mda_text = ""
    risk_text = ""
    notes_text = ""

    # Try standard item extraction first
    mda_text = _extract_section(md, "Item 2.", ["Item 3.", "Item 4."])

    # If MD&A is too short, it might be a reference — try finding "Management's Discussion"
    if len(mda_text) < 500:
        mda_pattern = re.compile(
            r"(?:Management.s Discussion and Analysis|MD&A)",
            re.IGNORECASE,
        )
        match = mda_pattern.search(md)
        if match:
            # Find next major heading
            next_heading = re.search(r"^#+\s*Item\s+\d", md[match.end():], re.MULTILINE | re.IGNORECASE)
            end = match.end() + next_heading.start() if next_heading else match.end() + 50000
            mda_text = md[match.start():end].strip()
            mda_text = re.sub(r"<[^>]+>", "", mda_text)

    # Risk factors (Part II, Item 1A) — often says "no material changes"
    risk_text = _extract_section(md, "Item 1A.", ["Item 2.", "Item 3.", "Item 5.", "Item 6."])

    # Financial statement notes
    notes_text = _extract_section(md, "Item 1.", ["Item 2."])[:40000]

    result = {
        "ticker": ticker,
        "form": "10-Q",
        "filing_date": filing_date,
        "accession_no": accession,
        "period": str(getattr(filing, "report_date", filing_date)),
        "mda": mda_text,
        "risk_factors": risk_text,
        "financial_statements_notes": notes_text,
        "full_text_length": len(md),
    }

    # Cache result
    FILINGS_CACHE.mkdir(parents=True, exist_ok=True)
    with open(cache_file, "w") as f:
        json.dump(result, f)

    return result


def get_filing_text(
    ticker: str,
    form: str = "10-K",
) -> dict:
    """Unified interface to get filing text.

    Args:
        ticker: Company ticker symbol
        form: "10-K" or "10-Q"

    Returns:
        Dictionary with extracted sections
    """
    if form.upper() == "10-K":
        return get_10k_sections(ticker)
    elif form.upper() == "10-Q":
        return get_10q_sections(ticker)
    else:
        raise ValueError(f"Unsupported form type: {form}. Use '10-K' or '10-Q'.")


def build_agent_context(
    tenk: dict,
    tenq: Optional[dict] = None,
    max_chars: int = 120000,
) -> str:
    """Build a combined filing context string for research agents.

    Combines 10-K and 10-Q sections into a structured text block
    that can be injected into agent prompts.

    Args:
        tenk: 10-K sections dict from get_10k_sections()
        tenq: 10-Q sections dict from get_10q_sections() (optional)
        max_chars: Maximum total characters (default 120K to fit in context)

    Returns:
        Formatted string with labeled sections
    """
    parts = []

    # 10-K sections
    if tenk and "error" not in tenk:
        parts.append(f"=== 10-K ANNUAL REPORT (Filed: {tenk.get('filing_date', 'N/A')}) ===\n")

        if tenk.get("business"):
            biz = tenk["business"][:30000]
            parts.append(f"--- BUSINESS DESCRIPTION (Item 1) ---\n{biz}\n")

        if tenk.get("mda"):
            mda = tenk["mda"][:35000]
            parts.append(f"--- MANAGEMENT DISCUSSION & ANALYSIS (Item 7) ---\n{mda}\n")

        if tenk.get("risk_factors"):
            rf = tenk["risk_factors"][:20000]
            parts.append(f"--- RISK FACTORS (Item 1A) ---\n{rf}\n")

    # 10-Q sections
    if tenq and "error" not in tenq:
        parts.append(f"\n=== 10-Q QUARTERLY REPORT (Filed: {tenq.get('filing_date', 'N/A')}) ===\n")

        if tenq.get("mda"):
            mda = tenq["mda"][:25000]
            parts.append(f"--- QUARTERLY MD&A (Item 2) ---\n{mda}\n")

        if tenq.get("risk_factors") and len(tenq["risk_factors"]) > 100:
            rf = tenq["risk_factors"][:10000]
            parts.append(f"--- UPDATED RISK FACTORS (Item 1A) ---\n{rf}\n")

    combined = "\n".join(parts)

    # Truncate to max_chars if needed
    if len(combined) > max_chars:
        combined = combined[:max_chars] + "\n\n[... truncated for context length ...]"

    return combined
