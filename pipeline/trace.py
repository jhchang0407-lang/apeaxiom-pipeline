"""Pipeline trace / checkpoint system.

Dumps a JSON snapshot at each pipeline stage so you can inspect
exactly what data is present, missing, or wrong — like clicking on
an n8n node to see its input/output.

Usage in orchestrator.py:
    trace = PipelineTrace(ticker, output_dir)
    trace.checkpoint("stage_name", {
        "section_key": data_dict,
        ...
    })
    # At the end:
    trace.write_summary()

Output structure (test_output/AAPL_website/trace/):
    00_fetch.json
    01_transform.json
    02_quantitative.json
    03_format_registry.json
    04_peer_selection.json
    05_peer_rebuild.json
    06_agents.json
    07_writers.json
    08_assembly.json
    09_scorecard.json
    _summary.json          ← full pipeline overview
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any


# ── Keys we validate at each stage ─────────────────────────────
# Maps stage_name → list of (dotted_path, description) to check.
# A dotted path like "s12_peer_benchmarking.valuation_comps.0.price_to_earnings"
# means: data["s12_peer_benchmarking"]["valuation_comps"][0]["price_to_earnings"]
VALIDATIONS: dict[str, list[tuple[str, str]]] = {
    "fetch": [
        ("sec_annual_count", "SEC annual financial periods"),
        ("sec_quarterly_count", "SEC quarterly financial periods"),
        ("fmp_profile.price", "FMP real-time stock price"),
        ("fmp_profile.marketCap", "FMP market cap"),
        ("sec_segments.product_segments", "SEC segment data"),
    ],
    "quantitative": [
        ("s11_income_statement.revenue_usd_m", "Revenue time series"),
        ("s11_income_statement.ebitda_usd_m", "EBITDA time series"),
        ("s11_income_statement.sbc_pct_of_revenue", "SBC % of revenue"),
        ("s11_income_statement.operating_margin_pct", "Operating margin"),
        ("s11_cash_flow.free_cash_flow_usd_m", "Free cash flow"),
        ("s11_cash_flow.fcf_margin_pct", "FCF margin"),
        ("s11_cash_flow.capex_pct_of_revenue", "CapEx % revenue"),
        ("s11_balance_sheet.total_assets_usd_m", "Total assets"),
        ("s11_returns.roic_pct", "ROIC"),
        ("s11_returns.roe_pct", "ROE"),
        ("s5_share_data.sbc_pct_of_revenue", "SBC % (share data)"),
        ("s12_peer_benchmarking.profitability_comps", "Profitability comps table"),
        ("s12_peer_benchmarking.valuation_comps", "Valuation comps table"),
        ("s12_peer_benchmarking.efficiency_comps", "Efficiency comps table"),
        ("s2_s4_revenue_splits.segment_revenue_usd_m", "Segment revenue breakdown"),
    ],
    "format_registry": [
        ("s13_valuation._current_pe", "Current P/E (real-time)"),
        ("s13_valuation._current_ev_ebitda", "Current EV/EBITDA (real-time)"),
        ("s13_valuation._current_p_fcf", "Current P/FCF (real-time)"),
        ("s13_valuation._current_market_cap_b", "Current market cap"),
        ("s13_valuation.ev_to_sales", "EV/Sales time series"),
        ("s13_valuation.ev_to_ebitda", "EV/EBITDA time series"),
        ("_sec_sector_kpis.kpis", "Sector-specific KPIs"),
    ],
    "tables": [
        ("fin_tables.revenue_growth.populated", "S10 Revenue table populated cells"),
        ("fin_tables.margins.populated", "S10 Margins table populated cells"),
        ("fin_tables.cash_flow.populated", "S10 Cash flow table populated cells"),
        ("fin_tables.returns.populated", "S10 Returns table populated cells"),
        ("fin_tables.leverage.populated", "S10 Leverage table populated cells"),
        ("fin_tables.capital_allocation.populated", "S10 CapAlloc table populated cells"),
        ("peer_tables.valuation_comps.rows", "S12 Valuation comps peer count"),
        ("peer_tables.profitability_comps.rows", "S12 Profitability comps peer count"),
        ("peer_tables.efficiency_comps.rows", "S12 Efficiency comps peer count"),
    ],
    "peer_rebuild": [
        ("subject_overrides.price_to_earnings", "Subject P/E override"),
        ("subject_overrides.ev_to_ebitda", "Subject EV/EBITDA override"),
        ("subject_overrides.sbc_to_revenue_pct", "Subject SBC/Rev override"),
        ("subject_overrides.market_cap_usd_b", "Subject mkt cap override"),
        ("valuation_comps_subject.price_to_earnings", "Final val comps P/E"),
        ("valuation_comps_subject.ev_to_ebitda", "Final val comps EV/EBITDA"),
        ("efficiency_comps_subject.sbc_to_revenue_pct", "Final eff comps SBC"),
    ],
    "scorecard": [
        ("pe_ratio", "Scorecard P/E"),
        ("ev_ebitda", "Scorecard EV/EBITDA"),
        ("p_fcf", "Scorecard P/FCF"),
        ("revenue", "Scorecard revenue"),
        ("roic_pct", "Scorecard ROIC"),
        ("roe_pct", "Scorecard ROE"),
        ("moat_classification", "Moat classification"),
        ("investment_thesis", "Investment thesis"),
    ],
}


def _resolve_path(data: Any, path: str) -> Any:
    """Resolve a dotted path like 'a.b.0.c' against nested dicts/lists."""
    parts = path.split(".")
    current = data
    for part in parts:
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, (list, tuple)):
            try:
                current = current[int(part)]
            except (IndexError, ValueError):
                return None
        else:
            return None
    return current


def _summarize_value(val: Any, max_len: int = 200) -> str:
    """Produce a short human-readable summary of a value."""
    if val is None:
        return "❌ NULL"
    if isinstance(val, dict):
        if not val:
            return "❌ EMPTY DICT"
        keys = list(val.keys())
        # For year-keyed series, show latest value
        if all(isinstance(k, str) and len(k) == 4 and k.isdigit() for k in keys):
            latest_yr = max(keys)
            return f"✅ series {min(keys)}–{latest_yr}, latest={val[latest_yr]}"
        preview = ", ".join(f"{k}" for k in keys[:6])
        if len(keys) > 6:
            preview += f"... ({len(keys)} keys)"
        return f"✅ dict({preview})"
    if isinstance(val, list):
        if not val:
            return "❌ EMPTY LIST"
        return f"✅ list[{len(val)} items]"
    if isinstance(val, str):
        if not val.strip():
            return "❌ EMPTY STRING"
        if len(val) > max_len:
            return f"✅ str({len(val)} chars): {val[:80]}..."
        return f"✅ {val!r}"
    if isinstance(val, (int, float)):
        return f"✅ {val}"
    return f"✅ {type(val).__name__}"


def _truncate_for_json(obj: Any, max_str: int = 500, max_list: int = 3, depth: int = 0) -> Any:
    """Recursively truncate a data structure for readable JSON dumps."""
    if depth > 6:
        return "..."
    if obj is None or isinstance(obj, (bool, int, float)):
        return obj
    if isinstance(obj, str):
        return obj[:max_str] + "..." if len(obj) > max_str else obj
    if isinstance(obj, list):
        if not obj:
            return []
        truncated = [_truncate_for_json(item, max_str, max_list, depth + 1) for item in obj[:max_list]]
        if len(obj) > max_list:
            truncated.append(f"... ({len(obj) - max_list} more items)")
        return truncated
    if isinstance(obj, dict):
        result = {}
        for i, (k, v) in enumerate(obj.items()):
            if i >= 20 and depth > 2:
                result["..."] = f"({len(obj) - i} more keys)"
                break
            result[str(k)] = _truncate_for_json(v, max_str, max_list, depth + 1)
        return result
    return str(obj)[:max_str]


@dataclass
class PipelineTrace:
    """Records pipeline stage checkpoints for debugging."""

    ticker: str
    output_dir: str
    enabled: bool = True
    _checkpoints: list[dict] = field(default_factory=list)
    _step_counter: int = 0

    def __post_init__(self):
        if self.enabled:
            trace_dir = os.path.join(self.output_dir, "trace")
            os.makedirs(trace_dir, exist_ok=True)
            # Clear old trace files
            for f in os.listdir(trace_dir):
                if f.endswith(".json"):
                    os.remove(os.path.join(trace_dir, f))

    def checkpoint(self, stage_name: str, data: dict, extra_notes: str = "") -> None:
        """Record a pipeline checkpoint.

        Args:
            stage_name: e.g. "fetch", "quantitative", "peer_rebuild"
            data: dict of key→value to snapshot (will be truncated for JSON)
            extra_notes: optional free-text notes
        """
        if not self.enabled:
            return

        ts = time.time()

        # Run validations for this stage
        validations = []
        stage_validators = VALIDATIONS.get(stage_name, [])
        for path, description in stage_validators:
            val = _resolve_path(data, path)
            validations.append({
                "field": path,
                "description": description,
                "status": "MISSING" if val is None else (
                    "EMPTY" if (isinstance(val, (dict, list, str)) and not val)
                    else "OK"
                ),
                "summary": _summarize_value(val),
            })

        # Count pass/fail
        ok_count = sum(1 for v in validations if v["status"] == "OK")
        total_count = len(validations)
        issues = [v for v in validations if v["status"] != "OK"]

        checkpoint_record = {
            "step": self._step_counter,
            "stage": stage_name,
            "timestamp": ts,
            "ticker": self.ticker,
            "validation_summary": f"{ok_count}/{total_count} checks passed",
            "issues": [
                f"⚠ {v['description']} ({v['field']}): {v['summary']}"
                for v in issues
            ],
            "validations": validations,
            "data_snapshot": _truncate_for_json(data),
        }
        if extra_notes:
            checkpoint_record["notes"] = extra_notes

        self._checkpoints.append(checkpoint_record)

        # Write individual stage file
        trace_dir = os.path.join(self.output_dir, "trace")
        filename = f"{self._step_counter:02d}_{stage_name}.json"
        with open(os.path.join(trace_dir, filename), "w") as f:
            json.dump(checkpoint_record, f, indent=2, default=str)

        self._step_counter += 1

        # Print compact summary to console
        status = "✅" if not issues else "⚠️"
        print(f"  {status} TRACE [{stage_name}] {ok_count}/{total_count} checks passed", end="")
        if issues:
            print(f"  ← ISSUES:")
            for issue in issues:
                print(f"      {issue}")
        else:
            print()

    def write_summary(self, stage_timings: dict | None = None) -> str:
        """Write the final summary file. Returns the trace directory path."""
        if not self.enabled:
            return ""

        trace_dir = os.path.join(self.output_dir, "trace")

        # Build pipeline flow diagram
        flow_lines = []
        all_issues = []
        for cp in self._checkpoints:
            stage = cp["stage"]
            ok = cp["validation_summary"]
            issues = cp.get("issues", [])
            timing = f" ({stage_timings[stage]:.1f}s)" if stage_timings and stage in stage_timings else ""
            status = "✅" if not issues else "⚠️"
            flow_lines.append(f"  {status} {cp['step']:02d}. {stage}{timing} — {ok}")
            for issue in issues:
                flow_lines.append(f"         {issue}")
                all_issues.append({"stage": stage, "issue": issue})

        summary = {
            "ticker": self.ticker,
            "total_stages": len(self._checkpoints),
            "total_issues": len(all_issues),
            "pipeline_flow": "\n".join(flow_lines),
            "all_issues": all_issues,
            "stage_files": [
                f"{cp['step']:02d}_{cp['stage']}.json"
                for cp in self._checkpoints
            ],
        }

        with open(os.path.join(trace_dir, "_summary.json"), "w") as f:
            json.dump(summary, f, indent=2, default=str)

        # Also write a human-readable text summary
        with open(os.path.join(trace_dir, "_summary.txt"), "w") as f:
            f.write(f"Pipeline Trace: {self.ticker}\n")
            f.write(f"{'='*60}\n\n")
            f.write("PIPELINE FLOW:\n")
            f.write(summary["pipeline_flow"])
            f.write(f"\n\n{'='*60}\n")
            if all_issues:
                f.write(f"\n⚠ {len(all_issues)} ISSUE(S) FOUND:\n\n")
                for issue in all_issues:
                    f.write(f"  Stage [{issue['stage']}]: {issue['issue']}\n")
            else:
                f.write("\n✅ All checks passed.\n")

        return trace_dir
