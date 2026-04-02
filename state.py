"""
state.py — Single source of truth for all shared mutable state.
"""

from config import DEFAULT_EMAIL, DEFAULT_PASSWORD

# ── Scan state ────────────────────────────────────────────────
scan_results: dict  = {}
status_message: str = "Waiting for search..."
is_searching: bool  = False
stop_flag: bool     = False
current_surname: str = ""

# ── Job queue ─────────────────────────────────────────────────
search_queue: list = []

# ── Credentials ───────────────────────────────────────────────
credentials: dict = {
    "email":    DEFAULT_EMAIL,
    "password": DEFAULT_PASSWORD,
}

# ── Persisted data (loaded at startup by persistence.py) ──────
scan_cache: dict     = {}
search_history: list = []
failed_profiles: list = []

# ── Per-profile notes (uuid -> text, max 100 chars) ───────────
profile_notes: dict = {}

# ── Global notepad ────────────────────────────────────────────
global_notepad: str = ""

# ── Auto Scanner ─────────────────────────────────────────────
autoscan_list: list    = []   # ordered list of surnames to scan
autoscan_results: list = []   # [ { surname, ts, time_str, total, families, enrolled, failed } ]
autoscan_running: bool = False
