"""Quarterly Earnings Pipeline — Lightweight, sector-aware quarterly update module.

Runs independently from the full memo pipeline. Designed for frequent execution
after each earnings report.

Pipeline stages:
  1. FMP Profile fetch (sector detection)
  2. Quarterly Research (web search agent, sector-specific prompts)
  3. Fact Extract (deterministic: beat/miss, margin deltas, segment coverage)
  4. Distribute (pre-compute tables + writer prompt)
  5. Write (AI prose)
  6. Fact Check (deterministic number verification)
  7. Format (assemble markdown + JSON payload)
"""
