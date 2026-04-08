"""
persistence.py — Handles all disk I/O: cache, search history, session cookies.

Changes:
  - Cache entries now carry a `cached_at` Unix timestamp
  - Cache entries older than 48 hours are purged automatically on load
  - Cache is only written on full scan completion — no partial/incremental saves
"""

import json
import os
import time
from datetime import datetime

import state
from config import CACHE_FILE, HISTORY_FILE, SESSION_FILE, HISTORY_MAX_AGE_SECS


# ── Cache ─────────────────────────────────────────────────

def load_cache() -> None:
    """Load surname scan cache from disk, dropping entries older than 48h."""
    if not os.path.exists(CACHE_FILE):
        return
    try:
        with open(CACHE_FILE, "r") as f:
            raw = json.load(f)
        cutoff = time.time() - HISTORY_MAX_AGE_SECS   # 48 hours ago
        # cached_at=0 default means old entries (no timestamp) are expired
        state.scan_cache = {
            k: v for k, v in raw.items()
            if v.get("cached_at", 0) > cutoff
        }
        expired = len(raw) - len(state.scan_cache)
        print(f"✅ Loaded cache: {len(state.scan_cache)} surnames "
              f"({expired} expired entries removed)")
    except Exception as e:
        print(f"⚠️  Cache load failed: {e}")
        state.scan_cache = {}


def save_cache() -> None:
    """Persist state.scan_cache to disk."""
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(state.scan_cache, f)
    except Exception as e:
        print(f"⚠️  Cache save failed: {e}")


def clear_cache() -> None:
    """Wipe cache from memory and disk."""
    state.scan_cache = {}
    save_cache()


# ── History ───────────────────────────────────────────────

def load_history() -> None:
    """Load search history from disk, pruning entries older than 48 hours."""
    if not os.path.exists(HISTORY_FILE):
        return
    try:
        with open(HISTORY_FILE, "r") as f:
            raw = json.load(f)
        cutoff = time.time() - HISTORY_MAX_AGE_SECS
        state.search_history = [h for h in raw if h.get("ts", 0) > cutoff]
    except Exception as e:
        print(f"⚠️  History load failed: {e}")
        state.search_history = []


def save_history() -> None:
    """Persist state.search_history to disk."""
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(state.search_history, f)
    except Exception as e:
        print(f"⚠️  History save failed: {e}")


def add_to_history(surname: str, total: int, families: int,
                   enrolled: int, failed: int) -> None:
    """Prepend a new entry to history, pruning old entries, then save."""
    cutoff = time.time() - HISTORY_MAX_AGE_SECS
    state.search_history = [h for h in state.search_history if h.get("ts", 0) > cutoff]
    state.search_history.insert(0, {
        "surname":  surname,
        "ts":       time.time(),
        "time_str": datetime.now().strftime("%b %d, %Y %I:%M %p"),
        "total":    total,
        "families": families,
        "enrolled": enrolled,
        "failed":   failed,
    })
    save_history()


# ── Session cookies ───────────────────────────────────────

def load_session_cookies() -> list | None:
    """Return saved cookies list, or None if no session file exists."""
    if not os.path.exists(SESSION_FILE):
        return None
    try:
        with open(SESSION_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return None


def save_session_cookies(cookies: list) -> None:
    """Write browser cookies to disk for session reuse."""
    try:
        with open(SESSION_FILE, "w") as f:
            json.dump(cookies, f)
        print("  💾 Session saved")
    except Exception as e:
        print(f"  ⚠️  Could not save session: {e}")


def delete_session() -> None:
    """Remove the session file to force a fresh login."""
    if os.path.exists(SESSION_FILE):
        os.remove(SESSION_FILE)
