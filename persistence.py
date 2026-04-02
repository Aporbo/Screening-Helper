"""
persistence.py — Handles all disk I/O: cache, search history, session cookies,
                  profile notes, global notepad, and autoscan list.
"""

import json
import os
import time
from datetime import datetime

import state
from config import CACHE_FILE, HISTORY_FILE, SESSION_FILE, HISTORY_MAX_AGE_SECS

NOTES_FILE    = "profile_notes.json"
NOTEPAD_FILE  = "global_notepad.txt"
AUTOSCAN_FILE = "autoscan_list.json"


# ── Cache ─────────────────────────────────────────────────────

def load_cache() -> None:
    if not os.path.exists(CACHE_FILE):
        return
    try:
        with open(CACHE_FILE, "r") as f:
            state.scan_cache = json.load(f)
        print(f"✅ Loaded cache: {len(state.scan_cache)} surnames")
    except Exception as e:
        print(f"⚠️  Cache load failed: {e}")
        state.scan_cache = {}


def save_cache() -> None:
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(state.scan_cache, f)
    except Exception as e:
        print(f"⚠️  Cache save failed: {e}")


def clear_cache() -> None:
    state.scan_cache = {}
    save_cache()


# ── History ───────────────────────────────────────────────────

def load_history() -> None:
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
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(state.search_history, f)
    except Exception as e:
        print(f"⚠️  History save failed: {e}")


def add_to_history(surname: str, total: int, families: int,
                   enrolled: int, failed: int) -> None:
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


# ── Session cookies ───────────────────────────────────────────

def load_session_cookies() -> list | None:
    if not os.path.exists(SESSION_FILE):
        return None
    try:
        with open(SESSION_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return None


def save_session_cookies(cookies: list) -> None:
    try:
        with open(SESSION_FILE, "w") as f:
            json.dump(cookies, f)
        print("  💾 Session saved")
    except Exception as e:
        print(f"  ⚠️  Could not save session: {e}")


def delete_session() -> None:
    if os.path.exists(SESSION_FILE):
        os.remove(SESSION_FILE)


# ── Profile Notes ─────────────────────────────────────────────

def load_notes() -> None:
    if not os.path.exists(NOTES_FILE):
        return
    try:
        with open(NOTES_FILE, "r") as f:
            state.profile_notes = json.load(f)
        print(f"✅ Loaded notes: {len(state.profile_notes)} entries")
    except Exception as e:
        print(f"⚠️  Notes load failed: {e}")
        state.profile_notes = {}


def save_notes() -> None:
    try:
        with open(NOTES_FILE, "w") as f:
            json.dump(state.profile_notes, f)
    except Exception as e:
        print(f"⚠️  Notes save failed: {e}")


# ── Global Notepad ────────────────────────────────────────────

def load_notepad() -> None:
    if not os.path.exists(NOTEPAD_FILE):
        return
    try:
        with open(NOTEPAD_FILE, "r") as f:
            state.global_notepad = f.read()
        print("✅ Loaded global notepad")
    except Exception as e:
        print(f"⚠️  Notepad load failed: {e}")
        state.global_notepad = ""


def save_notepad() -> None:
    try:
        with open(NOTEPAD_FILE, "w") as f:
            f.write(state.global_notepad)
    except Exception as e:
        print(f"⚠️  Notepad save failed: {e}")


# ── Auto Scanner List ─────────────────────────────────────────

def load_autoscan() -> None:
    if not os.path.exists(AUTOSCAN_FILE):
        return
    try:
        with open(AUTOSCAN_FILE, "r") as f:
            state.autoscan_list = json.load(f)
        print(f"✅ Loaded autoscan list: {len(state.autoscan_list)} surnames")
    except Exception as e:
        print(f"⚠️  Autoscan list load failed: {e}")
        state.autoscan_list = []


def save_autoscan() -> None:
    try:
        with open(AUTOSCAN_FILE, "w") as f:
            json.dump(state.autoscan_list, f)
    except Exception as e:
        print(f"⚠️  Autoscan list save failed: {e}")


def add_autoscan_result(surname: str, total: int, families: int,
                        enrolled: int, failed: int) -> None:
    state.autoscan_results.insert(0, {
        "surname":  surname,
        "ts":       time.time(),
        "time_str": datetime.now().strftime("%b %d, %Y %I:%M %p"),
        "total":    total,
        "families": families,
        "enrolled": enrolled,
        "failed":   failed,
    })
