"""
state.py — Single source of truth for all shared mutable state.

Every module imports from here instead of using its own globals.
This makes state changes visible across all modules without circular imports.
"""

from config import DEFAULT_EMAIL, DEFAULT_PASSWORD

# ── Scan state ────────────────────────────────────────────
scan_results: dict  = {}       # live results fed to the UI
status_message: str = "Waiting for search..."
is_searching: bool  = False
stop_flag: bool     = False
current_surname: str = ""

# ── Job queue ─────────────────────────────────────────────
# Tuples of ('scan', surname) or ('relogin', '')
search_queue: list = []

# ── Credentials ───────────────────────────────────────────
credentials: dict = {
    "email":    DEFAULT_EMAIL,
    "password": DEFAULT_PASSWORD,
}

# ── Persisted data (loaded at startup by persistence.py) ──
scan_cache: dict     = {}   # { surname_lower: scan_result_dict }
search_history: list = []   # [ { surname, ts, time_str, total, ... } ]
failed_profiles: list = []  # [ { name, uuid, error } ]
