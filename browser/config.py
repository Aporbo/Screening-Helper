"""
config.py — All constants, file paths, and credentials.
Credentials are loaded from a .env file so they are never hardcoded.
"""

import os
from dotenv import load_dotenv

load_dotenv()  # reads .env in the project root

# ── Server ────────────────────────────────────────────────
HOST = "0.0.0.0"
PORT = 8765

# ── Credentials (set these in your .env file) ────────────
# Create a file called `.env` in the same folder with:
#   UNITEUS_EMAIL=your@email.com
#   UNITEUS_PASSWORD=yourpassword
DEFAULT_EMAIL    = os.getenv("UNITEUS_EMAIL",    "")
DEFAULT_PASSWORD = os.getenv("UNITEUS_PASSWORD", "")
ACCESS_TOKEN     = os.getenv("ACCESS_TOKEN", "")

if not DEFAULT_EMAIL or not DEFAULT_PASSWORD or not ACCESS_TOKEN:
    print("\n❌ ERROR: Missing configuration!")
    print("Ensure .env has UNITEUS_EMAIL, UNITEUS_PASSWORD, and ACCESS_TOKEN.\n")
    exit(1) # Stop the program immediately

# ── File paths ────────────────────────────────────────────
SESSION_FILE = "session_state.json"
CACHE_FILE   = "scan_cache.json"      # switched from .pkl to .json (safer)
HISTORY_FILE = "search_history.json"

# ── Scan settings ─────────────────────────────────────────
PROFILE_RETRY_COUNT  = 2      # how many times to retry a failed profile
PROFILE_RETRY_DELAY  = 2      # seconds between retries
HISTORY_MAX_AGE_SECS = 48 * 3600  # drop history older than 48 hours

# ── Unite Us URLs ─────────────────────────────────────────
BASE_URL     = "https://app.uniteus.io"
SEARCH_URL   = BASE_URL + "/search?model=contact&page={page}&q={surname}&scope=client_name"
FACESHEET_URL = BASE_URL + "/facesheet/{uuid}"
