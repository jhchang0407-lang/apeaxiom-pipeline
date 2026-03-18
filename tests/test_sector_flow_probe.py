"""Deterministic probe: verify sector-family dispatch + data flow into Section 10/11 inputs.

Runs orchestrator up to `stop_after='distribute'` (no writers) for a
representative ticker per sector, and emits a JSON report.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from pipeline.orchestrator import run_pipeline


TESTS = {
    "technology": "MSFT",
    "banking": "JPM",
    "insurance": "PRU",
    "reits": "O",
    "retail": "WMT",
    "energy": "XOM",
    "healthcare": "UNH",
    "industrials": "CAT",
    "consumer_staples": "PG",
    "utilities": "NEE",
    "telecom": "T",
    "materials": "NUE",
    "consumer_disc": "TSLA",
    # Generic fallback example: non-bank financial (often sparse sector KPIs)
    "generic": "V",
}


def _table_shape(table):
    if not isinstance(table, list):
        return {"rows": 0, "cols": []}
    cols = []
    if table and isinstance(table[0], dict):
        cols = list(table[0].keys())
    return {"rows": len(table), "cols": cols}


async def probe_one(ticker: str) -> dict:
    res = await run_pipeline(ticker, mode="personal", stop_after="distribute", peer_selection_enabled=False)
    fs = res.formatted_facts or {}
    ident = fs.get("s1_identity") or {}
    s10 = res.section_inputs.get("section_10") or {}
    s11 = res.section_inputs.get("section_11") or {}

    out = {
        "ticker": ticker,
        "sector": ident.get("sector"),
        "industry": ident.get("industry"),
        "sector_family": (s10.get("facts") or {}).get("sector_family"),
        "sector_kpi_coverage": (s10.get("facts") or {}).get("sector_kpi_coverage"),
        "s10_title": s10.get("section_title"),
        "s11_title": s11.get("section_title"),
        "s10_schema_keys": list((s10.get("schema") or {}).get("properties", {}).keys()),
        "s11_schema_keys": list((s11.get("schema") or {}).get("properties", {}).keys()),
        "s10_precomputed": {},
        "s11_precomputed": {},
        "s11_primary_valuation_cols": [],
    }

    for k, v in s10.items():
        if isinstance(k, str) and k.startswith("precomputed_"):
            out["s10_precomputed"][k] = _table_shape(v)

    for k, v in s11.items():
        if isinstance(k, str) and k.startswith("precomputed_"):
            out["s11_precomputed"][k] = _table_shape(v)

    vtab = s11.get("precomputed_valuation_comps")
    if isinstance(vtab, list) and vtab and isinstance(vtab[0], dict):
        out["s11_primary_valuation_cols"] = list(vtab[0].keys())

    # quick “emptiness” checks
    out["checks"] = {
        "s10_has_sector_kpis": bool((s10.get("facts") or {}).get("sector_kpis")),
        "s10_has_sector_kpi_recent": bool((s10.get("facts") or {}).get("sector_kpi_recent")),
        "s11_has_peer_medians": bool((s11.get("facts") or {}).get("peer_medians")),
        "s11_valuation_rows": len(vtab) if isinstance(vtab, list) else 0,
    }

    return out


async def main():
    report = {
        "tests": TESTS,
        "results": {},
    }

    for sector, ticker in TESTS.items():
        try:
            report["results"][sector] = await probe_one(ticker)
        except Exception as e:
            report["results"][sector] = {"ticker": ticker, "error": f"{type(e).__name__}: {e}"}

    out_path = Path("test_output/sector_flow_report.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))
    print(f"Wrote: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
