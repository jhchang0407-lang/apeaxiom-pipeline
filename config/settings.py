"""OpenClaw Pipeline Configuration.

API keys and settings. For local use, reads from environment variables.
For Modal deployment, reads from Modal Secrets.
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
# SEC modules are called directly (no HTTP server needed).
# SEC_EDGAR_BASE_URL is no longer required.
SEC_USER_AGENT = os.getenv(
    "SEC_USER_AGENT",
    "OpenClaw Research thomas@openclaw.com",
)

# --- FMP (kept for estimates, surprises, market data, peers) ---
FMP_API_KEY = os.getenv("FMP_API_KEY", "")
FMP_BASE_URL = "https://financialmodelingprep.com/stable"

# --- LLM ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# Default models for each stage
WRITER_MODEL = os.getenv("WRITER_MODEL", "gpt-5-mini")
RESEARCH_AGENT_MODEL = os.getenv("RESEARCH_AGENT_MODEL", "gpt-5-mini")

# --- Cloudflare R2 (website mode) ---
CF_R2_ENDPOINT = "https://f3a5563fca3d8d1165c35edaa8c2cc48.r2.cloudflarestorage.com"
CF_R2_ACCESS_KEY = os.getenv("CF_R2_ACCESS_KEY", "")
CF_R2_SECRET_KEY = os.getenv("CF_R2_SECRET_KEY", "")
CF_R2_BUCKET = os.getenv("CF_R2_BUCKET", "apeaxiom")

# --- Discord ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

# --- Cache ---
CACHE_DIR = os.getenv("CACHE_DIR", str(Path(__file__).resolve().parent.parent / "cache"))

# --- Pipeline ---
DEFAULT_ANNUAL_YEARS = 5
DEFAULT_QUARTERLY_PERIODS = 8
