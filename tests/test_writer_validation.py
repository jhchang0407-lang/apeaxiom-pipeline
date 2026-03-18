"""Website-mode writer validation (expensive LLM calls)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from pipeline.orchestrator import run_pipeline

TICKERS = ["JPM", "O"]


def _write_json(path: Path, obj):
    path.write_text(json.dumps(obj, indent=2, default=str))


async def run_one(ticker: str):
    out_dir = Path("test_output/writer_validation") / ticker
    out_dir.mkdir(parents=True, exist_ok=True)

    res = await run_pipeline(
        ticker,
        mode="website",
        years=5,
        quarters=8,
        output_dir=str(out_dir),
        peer_selection_enabled=True,
    )

    _write_json(out_dir / "section_inputs.json", res.section_inputs or {})
    _write_json(out_dir / "scorecard.json", res.scorecard_json or {})
    _write_json(out_dir / "errors.json", {
        "errors": res.errors,
        "stage_timings": res.stage_timings,
        "assembly_ok": res.assembly_ok,
    })

    section_outputs = res.section_outputs or {}
    structured_map = section_outputs.get("structured_map") or {}
    _write_json(out_dir / "structured_section_10.json", structured_map.get("section_10") or {})
    _write_json(out_dir / "structured_section_11.json", structured_map.get("section_11") or {})

    (out_dir / "markdown.md").write_text(res.markdown or "")
    print(f"✓ {ticker}: assembly_ok={res.assembly_ok} errors={len(res.errors)} wrote {out_dir}")


async def main():
    for t in TICKERS:
        await run_one(t)


if __name__ == "__main__":
    asyncio.run(main())
