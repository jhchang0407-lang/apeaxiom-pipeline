"""Full end-to-end pipeline test — WEBSITE mode.

Runs the complete pipeline for AAPL including:
- Data fetch (SEC + FMP)
- Quantitative engine
- Peer selection agent
- Section writers (LLM)
- Assembly
- Scorecard JSON + Discord

Usage:
    python tests/test_full_pipeline.py [ticker]
    python tests/test_full_pipeline.py AAPL
"""

import asyncio
import json
import os
import sys
import time


async def main():
    ticker = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    mode = "website"

    print(f"\n{'='*60}")
    print(f"  Ape Axiom Full Pipeline Test")
    print(f"  Ticker: {ticker}  |  Mode: {mode}")
    print(f"{'='*60}\n")

    from pipeline.orchestrator import run_pipeline

    # Output dir for trace + results
    _here = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.join(_here, "test_output", f"{ticker}_{mode}")
    os.makedirs(out_dir, exist_ok=True)

    t0 = time.time()
    result = await run_pipeline(ticker=ticker, mode=mode, output_dir=out_dir)
    total = time.time() - t0

    print(f"\n{'='*60}")
    print(f"  RESULTS")
    print(f"{'='*60}")
    print(f"  Total time: {total:.1f}s")
    print(f"  Stage timings:")
    for stage, dur in result.stage_timings.items():
        print(f"    {stage}: {dur:.1f}s")

    print(f"\n  Assembly OK: {getattr(result, 'assembly_ok', 'N/A')}")
    print(f"\n  Errors ({len(result.errors)}):")
    for e in result.errors:
        print(f"    ⚠ {e[:200]}")

    print(f"\n  Sections written: {len(result.section_outputs)}")
    for key in sorted(result.section_outputs.keys()):
        sec = result.section_outputs[key]
        out = sec.get("output", {})
        size = len(json.dumps(out, default=str)) if isinstance(out, dict) else len(str(out))
        print(f"    {key}: {size:,} chars")

    print(f"\n  Memo body: {len(result.memo_body):,} chars")
    print(f"  Markdown: {len(result.markdown):,} chars")
    print(f"  HTML: {len(result.html):,} chars")
    print(f"  PDF: {len(result.pdf):,} bytes")

    # Scorecard JSON
    print(f"\n  Scorecard JSON:")
    sc = result.scorecard_json
    if sc:
        print(f"    Ticker: {sc.get('ticker')}")
        print(f"    Company: {sc.get('company_name')}")
        print(f"    Price: {sc.get('current_price')}")
        print(f"    Mkt Cap: {sc.get('market_cap_b')}B")
        print(f"    P/E: {sc.get('pe_ratio')}")
        print(f"    EV/EBITDA: {sc.get('ev_ebitda')}")
        print(f"    P/FCF: {sc.get('p_fcf')}")
        print(f"    Moat: {sc.get('moat_score')} ({sc.get('moat_classification')})")
        print(f"    Growth: {sc.get('growth_score')}")
        print(f"    Quality: {sc.get('quality_score')}")
        print(f"    Fair Value: {sc.get('fair_value')}")
        print(f"    Margin of Safety: {sc.get('margin_of_safety_pct')}%")
        print(f"    Thesis: {(sc.get('investment_thesis') or '')[:150]}...")
    else:
        print("    (empty)")

    # Discord scorecard
    print(f"\n  Discord scorecard ({len(result.discord_scorecard)} chars):")
    print(f"  {'─'*40}")
    for line in result.discord_scorecard.split("\n")[:15]:
        print(f"  {line}")
    if result.discord_scorecard.count("\n") > 15:
        print(f"  ... ({result.discord_scorecard.count(chr(10)) - 15} more lines)")
    print(f"  {'─'*40}")

    # Save outputs
    with open(f"{out_dir}/scorecard.json", "w") as f:
        json.dump(sc, f, indent=2, default=str)
    with open(f"{out_dir}/memo.md", "w") as f:
        f.write(result.markdown)
    with open(f"{out_dir}/memo.html", "w") as f:
        f.write(result.html)
    with open(f"{out_dir}/discord.txt", "w") as f:
        f.write(result.discord_scorecard)
    # Persist errors for debugging (full tracebacks, not truncated)
    if result.errors:
        with open(f"{out_dir}/errors.json", "w") as f:
            json.dump(result.errors, f, indent=2, default=str)
        print(f"\n  ⚠ Errors saved to {out_dir}/errors.json")
    if result.pdf:
        with open(f"{out_dir}/memo.pdf", "wb") as f:
            f.write(result.pdf)

    print(f"\n  Outputs saved to {out_dir}/")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
