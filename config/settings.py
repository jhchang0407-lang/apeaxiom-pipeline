"""Ape Axiom Pipeline Configuration.

All credentials and deployment-specific values come from environment
variables (or a local .env file). Nothing personal or account-specific
is hardcoded here — see .env.template for the full list of variables.
"""

import os
from pathlib import Path

# Load .env file if present (local development)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass


# --- SEC EDGAR ---
# SEC requires a User-Agent identifying you with a contact email, e.g.
# "Jane Doe jane@example.com". Required for any SEC data fetching.
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "")

# --- FMP (estimates, surprises, market data, peers) ---
FMP_API_KEY = os.getenv("FMP_API_KEY", "")
FMP_BASE_URL = "https://financialmodelingprep.com/stable"

# --- LLM ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# Default models for each stage
WRITER_MODEL = os.getenv("WRITER_MODEL", "gpt-5-mini")
RESEARCH_AGENT_MODEL = os.getenv("RESEARCH_AGENT_MODEL", "gpt-5-mini")
PEER_SELECTION_MODEL = os.getenv("PEER_SELECTION_MODEL", "gpt-5-mini")

# --- Cloudflare R2 (website mode) ---
# Endpoint looks like https://<account-id>.r2.cloudflarestorage.com
CF_R2_ENDPOINT = os.getenv("CF_R2_ENDPOINT", "")
CF_R2_ACCESS_KEY = os.getenv("CF_R2_ACCESS_KEY", "")
CF_R2_SECRET_KEY = os.getenv("CF_R2_SECRET_KEY", "")
CF_R2_BUCKET = os.getenv("CF_R2_BUCKET", "")

# --- Discord ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

# --- Cache ---
CACHE_DIR = os.getenv("CACHE_DIR", str(Path(__file__).resolve().parent.parent / "cache"))

# --- Pipeline ---
DEFAULT_ANNUAL_YEARS = 5
DEFAULT_QUARTERLY_PERIODS = 8

# Banks report revenue differently (net interest income vs. gross interest
# income); these tickers get bank-specific revenue handling in quantitative
# analysis and peer selection.
BANK_TICKERS = frozenset({
    "JPM", "BAC", "WFC", "GS", "MS", "C", "USB", "PNC", "TFC",
    "COF", "BK", "STT", "SCHW", "MTB", "RF", "CFG", "FITB",
    "HBAN", "KEY", "WBS", "ALLY",
})
