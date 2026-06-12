"""Quarterly Pipeline Orchestrator — Main entry point.

Coordinates the lightweight quarterly earnings pipeline:
  Step 1: FMP Profile fetch (sector detection)
  Step 2: Quarterly Research (web search agent, sector-specific)
  Step 3: Fact Extract (deterministic: beat/miss, margins, segments)
  Step 4: Distribute (pre-compute tables + writer prompt)
  Step 5: Write (AI prose)
  Step 6: Fact Check (deterministic number verification)
  Step 7: Format (assemble markdown + JSON payload)

Designed to run in 60-120 seconds.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx

from config.settings import FMP_API_KEY, FMP_BASE_URL


@dataclass
class QuarterlyResult:
    """Container for quarterly pipeline output."""

    ticker: str = ""
    company_name: str = ""
    sector_family: str = ""
    quarter_label: str = ""

    # Structured data
    facts: dict = field(default_factory=dict)
    precomputed: dict = field(default_factory=dict)

    # Final output
    markdown: str = ""
    html: str = ""
    payload: dict = field(default_factory=dict)

    # Timing
    pipeline_duration_s: float = 0.0
    stage_timings: dict = field(default_factory=dict)

    # Errors
    errors: list = field(default_factory=list)


async def _fetch_fmp_profile(ticker: str) -> dict:
    """Fetch FMP company profile."""
    url = f"{FMP_BASE_URL}/profile"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, params={"symbol": ticker, "apikey": FMP_API_KEY})
        resp.raise_for_status()
        data = resp.json()
    # FMP returns a list with one item
    if isinstance(data, list) and data:
        return data[0]
    return data if isinstance(data, dict) else {}


def _detect_sector_family(profile: dict) -> str:
    """Detect sector family from FMP profile using the existing pipeline logic."""
    # Import the existing sector detection function
    import sys
    import os
    # Ensure pipeline is importable
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if base_dir not in sys.path:
        sys.path.insert(0, base_dir)

    try:
        from pipeline.distributors import _get_sector_family
        return _get_sector_family(
            subsector=profile.get("sector", ""),
            sector=profile.get("sector", ""),
            industry=profile.get("industry", ""),
        )
    except ImportError:
        # Fallback: simple mapping
        sector = (profile.get("sector") or "").lower()
        industry = (profile.get("industry") or "").lower()
        if "bank" in sector or "bank" in industry:
            return "banking"
        if "insurance" in sector or "insurance" in industry:
            return "insurance"
        if "real estate" in sector or "reit" in industry:
            return "reits"
        if "technology" in sector:
            return "technology"
        if "energy" in sector:
            return "energy"
        if "healthcare" in sector:
            return "healthcare"
        if "utilities" in sector:
            return "utilities"
        return "generic"


async def run_quarterly_pipeline(
    ticker: str,
    quarter_hint: str | None = None,
) -> QuarterlyResult:
    """Run the full quarterly earnings pipeline.

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL").
        quarter_hint: Optional quarter hint (e.g., "Q4 2025").

    Returns:
        QuarterlyResult with markdown, JSON payload, and metadata.
    """
    t0 = time.time()
    result = QuarterlyResult(ticker=ticker.upper())
    timings: dict[str, float] = {}

    try:
        # ── Step 1: FMP Profile ──────────────────────────────────
        t1 = time.time()
        profile = await _fetch_fmp_profile(ticker)
        timings["fetch_profile"] = round(time.time() - t1, 1)

        result.company_name = profile.get("companyName", ticker.upper())
        print(f"  [1/7] Profile: {result.company_name} ({profile.get('sector', '?')} / {profile.get('industry', '?')})")

        # ── Step 2: Detect Sector ────────────────────────────────
        sector_family = _detect_sector_family(profile)
        result.sector_family = sector_family
        print(f"  [2/7] Sector family: {sector_family}")

        # ── Step 3: Research ─────────────────────────────────────
        t3 = time.time()
        from quarterly.research import run_quarterly_research
        research = await run_quarterly_research(
            ticker=ticker,
            company_name=result.company_name,
            sector_family=sector_family,
            quarter_hint=quarter_hint,
        )
        timings["research"] = round(time.time() - t3, 1)

        if research.get("_parse_error"):
            result.errors.append("Research output could not be parsed as JSON")
            print(f"  [3/7] Research: PARSE ERROR")
        else:
            q_label = research.get("quarter_reported", "?")
            result.quarter_label = q_label or "?"
            print(f"  [3/7] Research: {q_label} — {timings['research']}s")

        # ── Step 4: Fact Extract ─────────────────────────────────
        t4 = time.time()
        from quarterly.fact_extract import extract_quarterly_facts
        facts = extract_quarterly_facts(research, sector_family)
        timings["fact_extract"] = round(time.time() - t4, 2)
        result.facts = facts

        h = facts.get("headline", {})
        beat = h.get("revenue_beat_miss_pct")
        beat_str = f"+{beat:.1f}%" if beat and beat > 0 else f"{beat:.1f}%" if beat else "?"
        print(f"  [4/7] Fact Extract: Revenue beat/miss {beat_str}")

        # ── Step 5: Distribute ───────────────────────────────────
        t5 = time.time()
        from quarterly.distributor import distribute_quarterly
        distributed = distribute_quarterly(facts, sector_family, profile)
        timings["distribute"] = round(time.time() - t5, 2)
        result.precomputed = distributed
        print(f"  [5/7] Distributor: {len(distributed.get('precomputed_results_table', []))} result rows, "
              f"{len(distributed.get('precomputed_segment_table', []))} segments")

        # ── Step 6: Write ────────────────────────────────────────
        t6 = time.time()
        from quarterly.writer import write_quarterly
        writer_output = await write_quarterly(distributed)
        timings["write"] = round(time.time() - t6, 1)

        if writer_output.get("_parse_error"):
            result.errors.append("Writer output could not be parsed as JSON")
            print(f"  [6/7] Writer: PARSE ERROR — {timings['write']}s")
        else:
            print(f"  [6/7] Writer: OK — {timings['write']}s")

        # ── Step 7: Fact Check ───────────────────────────────────
        t7 = time.time()
        try:
            from quarterly.fact_check import fact_check_quarterly as _fc
            fc_result = _fc(
                writer_output=writer_output,
                raw_facts=facts,
                precomputed=distributed,
            )
            fact_check_meta = {
                "patches_applied": fc_result.get("patches_applied", 0),
                "verified_claims": fc_result.get("verified_claims", 0),
                "suspicious_claims": fc_result.get("suspicious_claims", []),
            }
            # Use patched output
            patched = fc_result.get("patched_output", {})
            if isinstance(patched, dict) and patched:
                writer_output = patched
        except Exception as e:
            fact_check_meta = {"error": str(e)}
            result.errors.append(f"Fact check failed: {e}")
        timings["fact_check"] = round(time.time() - t7, 2)
        print(f"  [7/7] Fact Check: {fact_check_meta.get('patches_applied', '?')} patches, "
              f"{fact_check_meta.get('verified_claims', '?')} verified")

        # ── Step 8: Format ───────────────────────────────────────
        from quarterly.formatter import format_quarterly_output, build_quarterly_payload
        from quarterly.html_formatter import build_quarterly_html

        markdown = format_quarterly_output(writer_output, distributed, facts=facts)
        result.markdown = markdown

        result.html = build_quarterly_html(
            writer_output=writer_output,
            precomputed=distributed,
            facts=facts,
            profile=profile,
            sector_family=sector_family,
            pipeline_duration_s=time.time() - t0,
        )

        total = time.time() - t0
        result.pipeline_duration_s = round(total, 1)
        timings["total"] = round(total, 1)
        result.stage_timings = timings

        result.payload = build_quarterly_payload(
            ticker=ticker,
            formatted_markdown=markdown,
            facts=facts,
            precomputed=distributed,
            profile=profile,
            sector_family=sector_family,
            pipeline_duration_s=total,
            fact_check_meta=fact_check_meta,
        )

        print(f"\n  Done: {result.company_name} ({ticker.upper()}) — "
              f"{result.quarter_label} — {total:.0f}s total — "
              f"{len(markdown.split())} words")

    except Exception as e:
        result.errors.append(str(e))
        result.pipeline_duration_s = round(time.time() - t0, 1)
        result.stage_timings = timings
        print(f"\n  ERROR: {e}")
        import traceback
        traceback.print_exc()

    return result
