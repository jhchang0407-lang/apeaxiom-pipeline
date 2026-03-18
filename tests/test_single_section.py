"""Run a single section of the pipeline to save token costs.

Usage:
    python test_single_section.py AAPL 10        # Section 10 for AAPL
    python test_single_section.py JPM 10 11      # Sections 10 and 11 for JPM
    python test_single_section.py PLD             # Defaults to section 10
"""

import asyncio
import json
import sys
import time


async def main():
    ticker = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    section_nums = [int(x) for x in sys.argv[2:]] if len(sys.argv) > 2 else [10]

    print(f"\n{'='*60}")
    print(f"  Single Section Test")
    print(f"  Ticker: {ticker}  |  Sections: {section_nums}")
    print(f"{'='*60}\n")

    from pipeline.orchestrator import run_pipeline

    # Stage 1-2: fetch + transform + distribute (no LLM calls)
    t0 = time.time()
    result = await run_pipeline(
        ticker=ticker,
        mode="personal",
        stop_after="distribute",
    )
    print(f"  Stages 1-2 (data + distribute): {time.time() - t0:.1f}s")

    if not result.section_inputs:
        print("  ERROR: No section inputs produced")
        return

    # Build agent prompts (needed for qualitative injection)
    from pipeline.orchestrator import _build_agent_prompts
    from pipeline.data_fetcher import fetch_all_data
    from pipeline.writers import write_section

    # Run only the requested sections
    t_write = time.time()
    for num in section_nums:
        key = f"section_{num}"
        if key not in result.section_inputs:
            print(f"  WARNING: {key} not found in section_inputs")
            continue

        inp = result.section_inputs[key].copy()
        print(f"\n  Writing section {num}...")
        t_sec = time.time()
        try:
            sec_result = await write_section(inp)
            dur = time.time() - t_sec
            out = sec_result.get("output", {})
            out_str = json.dumps(out, default=str) if isinstance(out, dict) else str(out)
            print(f"  Section {num} done: {len(out_str):,} chars in {dur:.1f}s")

            # Show subsection keys for S10 to verify schema coverage
            if num == 10 and isinstance(out, dict):
                skip = {"section_number", "section_thesis", "opening_paragraph",
                        "financial_quality_flags", "synthesis", "quality_score"}
                subsections = [k for k in out.keys() if k not in skip]
                print(f"  S10 subsections: {subsections}")
                for sub_key in subsections:
                    val = out[sub_key]
                    if isinstance(val, dict):
                        has_intro = bool(val.get("intro"))
                        has_analysis = bool(val.get("analysis"))
                        print(f"    {sub_key}: intro={'yes' if has_intro else 'NO'}, analysis={'yes' if has_analysis else 'NO'}")
                    elif val is None:
                        print(f"    {sub_key}: null (skipped by LLM)")

            # Also render to markdown to check assembly
            if num == 10 and isinstance(out, dict):
                try:
                    from pipeline.assembly import assemble_memo
                    # Build minimal section_outputs for assembly
                    fake_outputs = {key: sec_result}
                    memo = assemble_memo(
                        section_outputs=fake_outputs,
                        fact_sheet=result.formatted_facts,
                        mode="personal",
                    )
                    # Extract just S10 from rendered markdown
                    lines = memo.split("\n")
                    s10_start = None
                    s10_end = None
                    for i, line in enumerate(lines):
                        if "# 10." in line or "# Financial Analysis" in line:
                            s10_start = i
                        elif s10_start and line.startswith("# ") and "10." not in line:
                            s10_end = i
                            break
                    if s10_start:
                        s10_md = "\n".join(lines[s10_start:(s10_end or len(lines))])
                        print(f"\n  Rendered S10 markdown ({len(s10_md):,} chars):")
                        # Show headers only
                        for line in s10_md.split("\n"):
                            if line.startswith("##"):
                                print(f"    {line}")
                except Exception as e:
                    print(f"  Assembly preview failed: {e}")

        except Exception as e:
            import traceback
            print(f"  Section {num} FAILED: {e}")
            traceback.print_exc()

    total = time.time() - t0
    print(f"\n  Total time: {total:.1f}s (writing: {time.time() - t_write:.1f}s)")


if __name__ == "__main__":
    asyncio.run(main())
