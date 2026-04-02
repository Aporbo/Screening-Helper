"""
scheduler.py — Surname scanner with manual execution only.

- All surnames managed directly in surnames.json (add to top via UI)
- Configurable batch size (asked at runtime via UI, saved in scan_settings.json)
- NO automatic trigger — manual only via "Start Auto Scan" button
- Daily reminder: email (Gmail SMTP) + browser notification flag
"""

import json
import os
import time
import threading
import smtplib
import ssl
from email.message import EmailMessage
from datetime import datetime, date

import state

# ── File paths ────────────────────────────────────────────
SURNAMES_FILE      = "surnames.json"
AUTO_PROGRESS_FILE = "auto_scan_progress.json"
AUTO_RESULTS_FILE  = "auto_scan_results.json"
SETTINGS_FILE      = "scan_settings.json"

# ── Defaults ──────────────────────────────────────────────
DEFAULT_BATCH_SIZE    = 10
DEFAULT_REMINDER_HOUR = 15   # 3 PM server local time
DEFAULT_REMINDER_MIN  = 0
WAIT_BETWEEN_SCANS    = 10   # seconds between surnames in a batch

# Scan timeout constants (seconds)
SCAN_START_TIMEOUT    = 60   # max time to wait for a scan to begin
SCAN_FINISH_TIMEOUT   = 480  # max time to wait for a scan to finish (8 min)

# Email — set in .env / environment variables
SMTP_EMAIL    = os.environ.get("REMINDER_EMAIL_FROM", "")
SMTP_PASSWORD = os.environ.get("REMINDER_EMAIL_PASS", "")
REMINDER_TO   = os.environ.get("REMINDER_EMAIL_TO", "jeuga1978@gmail.com")
SMTP_HOST     = "smtp.gmail.com"
SMTP_PORT     = 465


# ═══════════════════════════════════════════════════════
#  SETTINGS
# ═══════════════════════════════════════════════════════

def load_settings() -> dict:
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "batch_size":    DEFAULT_BATCH_SIZE,
        "reminder_hour": DEFAULT_REMINDER_HOUR,
        "reminder_min":  DEFAULT_REMINDER_MIN,
    }


def save_settings(settings: dict) -> None:
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings, f, indent=2)
    except Exception as e:
        print(f"⚠️  Could not save settings: {e}")


def get_batch_size() -> int:
    return int(load_settings().get("batch_size", DEFAULT_BATCH_SIZE))


# ═══════════════════════════════════════════════════════
#  SURNAMES.JSON  — single source of truth
# ═══════════════════════════════════════════════════════

def load_surnames() -> list:
    """Returns list of surname strings in order."""
    if not os.path.exists(SURNAMES_FILE):
        return []
    try:
        with open(SURNAMES_FILE) as f:
            data = json.load(f)
        return [entry["surname"] for entry in data if "surname" in entry]
    except Exception as e:
        print(f"⚠️  Could not load surnames: {e}")
        return []


def save_surnames(surnames: list) -> None:
    """Save a list of surname strings back to surnames.json."""
    try:
        with open(SURNAMES_FILE, "w") as f:
            json.dump([{"surname": s} for s in surnames], f, indent=2)
    except Exception as e:
        print(f"⚠️  Could not save surnames: {e}")


def surnames_add_top(name: str) -> dict:
    """Add a surname to position #1 in surnames.json (removes duplicates)."""
    name = name.strip()
    if not name:
        return {"ok": False, "error": "Empty name"}
    surnames = load_surnames()
    surnames = [s for s in surnames if s.lower() != name.lower()]
    surnames.insert(0, name)
    save_surnames(surnames)
    return {"ok": True, "surnames": surnames}


def surnames_reorder(new_order: list) -> dict:
    """Replace the surnames list with a new order (from drag-and-drop in UI)."""
    all_surnames = load_surnames()
    lower_map = {s.lower(): s for s in all_surnames}
    reordered_lower = [s.lower() for s in new_order]
    tail = [s for s in all_surnames if s.lower() not in reordered_lower]
    final = [lower_map.get(s.lower(), s) for s in new_order] + tail
    save_surnames(final)
    return {"ok": True, "surnames": final}


def surnames_delete(name: str) -> dict:
    """Remove a surname from surnames.json."""
    surnames = load_surnames()
    surnames = [s for s in surnames if s.lower() != name.lower()]
    save_surnames(surnames)
    return {"ok": True, "surnames": surnames}


# ═══════════════════════════════════════════════════════
#  PROGRESS & RESULTS
# ═══════════════════════════════════════════════════════

def load_progress() -> dict:
    if os.path.exists(AUTO_PROGRESS_FILE):
        try:
            with open(AUTO_PROGRESS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"scanned": [], "failed": [], "last_run": None, "total_run": 0}


def save_progress(progress: dict) -> None:
    try:
        with open(AUTO_PROGRESS_FILE, "w") as f:
            json.dump(progress, f, indent=2)
    except Exception as e:
        print(f"⚠️  Could not save progress: {e}")


def load_auto_results() -> list:
    if os.path.exists(AUTO_RESULTS_FILE):
        try:
            with open(AUTO_RESULTS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_auto_result(surname: str, scan_data: dict) -> None:
    results = load_auto_results()
    results = [r for r in results if r.get("surname", "").lower() != surname.lower()]
    results.insert(0, {
        "surname":       surname,
        "scanned_at":    datetime.now().strftime("%b %d, %Y %I:%M %p"),
        "ts":            time.time(),
        "total":         scan_data.get("total", 0),
        "families":      len([v for v in (scan_data.get("phone_groups") or {}).values() if len(v) > 1]),
        "enrolled_zero": len([
            p for p in scan_data.get("profiles", [])
            if p.get("any_enrolled") and p.get("zero_screening")
        ]),
        "failed":        len(scan_data.get("failed", [])),
    })
    try:
        with open(AUTO_RESULTS_FILE, "w") as f:
            json.dump(results, f, indent=2)
    except Exception as e:
        print(f"⚠️  Could not save auto result: {e}")


def get_next_batch(progress: dict, all_surnames: list, n: int) -> list:
    """Return next N surnames not yet scanned or failed."""
    done = set(s.lower() for s in progress.get("scanned", []) + progress.get("failed", []))
    return [s for s in all_surnames if s.lower() not in done][:n]


# ═══════════════════════════════════════════════════════
#  REMINDER SYSTEM
# ═══════════════════════════════════════════════════════

_reminder_sent_date: str       = ""
_browser_reminder_pending: bool = False


def check_and_send_reminder() -> None:
    global _reminder_sent_date, _browser_reminder_pending
    s      = load_settings()
    r_hour = int(s.get("reminder_hour", DEFAULT_REMINDER_HOUR))
    r_min  = int(s.get("reminder_min",  DEFAULT_REMINDER_MIN))
    now    = datetime.now()
    today  = date.today().isoformat()

    if _reminder_sent_date == today:
        return
    if not (now.hour == r_hour and now.minute == r_min):
        return
    if load_progress().get("last_run") == today:
        _reminder_sent_date = today
        return

    _reminder_sent_date       = today
    _browser_reminder_pending = True
    print("🔔 Reminder triggered — no batch run today")
    threading.Thread(target=_send_email_reminder, daemon=True).start()


def _send_email_reminder() -> None:
    if not SMTP_EMAIL or not SMTP_PASSWORD:
        print("⚠️  Email reminder skipped — set REMINDER_EMAIL_FROM and REMINDER_EMAIL_PASS in .env")
        return
    try:
        s         = load_settings()
        progress  = load_progress()
        all_sn    = load_surnames()
        scanned   = len(progress.get("scanned", []))
        remaining = len(all_sn) - scanned - len(progress.get("failed", []))
        preview   = get_next_batch(progress, all_sn, 5)

        msg = EmailMessage()
        msg["Subject"] = "🤖 Screening Helper — Time to Run Your Scan Batch"
        msg["From"]    = SMTP_EMAIL
        msg["To"]      = REMINDER_TO
        msg.set_content(f"""Hi,

Daily reminder to run your Screening Helper scan batch.

📊 Status:
  Scanned:    {scanned} of {len(all_sn)} surnames
  Remaining:  {remaining}
  Batch size: {s.get("batch_size", DEFAULT_BATCH_SIZE)}

📋 Next up: {", ".join(preview) if preview else "All done!"}

👉 Open the tool and click "▶ Start Auto Scan".

— Screening Helper
""")
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx) as smtp:
            smtp.login(SMTP_EMAIL, SMTP_PASSWORD)
            smtp.send_message(msg)
        print(f"✉️  Reminder email sent to {REMINDER_TO}")
    except Exception as e:
        print(f"⚠️  Email reminder failed: {e}")


def get_browser_reminder() -> dict:
    global _browser_reminder_pending
    today     = date.today().isoformat()
    ran_today = load_progress().get("last_run") == today
    result    = {
        "pending":   _browser_reminder_pending and not ran_today,
        "ran_today": ran_today,
    }
    if _browser_reminder_pending:
        _browser_reminder_pending = False
    return result


# ═══════════════════════════════════════════════════════
#  BATCH RUNNER  (manual only)
# ═══════════════════════════════════════════════════════

_batch_running = False


def run_batch_now(batch_size: int = None) -> dict:
    global _batch_running
    if _batch_running:
        return {"ok": False, "error": "A batch is already running"}
    if batch_size is None:
        batch_size = get_batch_size()
    s = load_settings()
    s["batch_size"] = max(1, int(batch_size))
    save_settings(s)
    threading.Thread(target=_run_batch, args=(int(batch_size),), daemon=True).start()
    return {"ok": True, "message": f"Batch of {batch_size} started"}


def is_batch_running() -> bool:
    return _batch_running


def _run_batch(batch_size: int) -> None:
    global _batch_running
    _batch_running = True
    try:
        all_sn   = load_surnames()
        progress = load_progress()
        batch    = get_next_batch(progress, all_sn, batch_size)

        if not batch:
            print("✅ Batch: nothing left to scan")
            return

        today = date.today().isoformat()
        print(f"\n🤖 Batch — {len(batch)} surnames: {batch}\n")

        for surname in batch:
            # ── Wait for the main browser loop to be idle ──
            if not _wait_for_idle():
                print(f"⚠️  Skipping '{surname}' — idle timeout")
                progress["failed"].append(surname)
                save_progress(progress)
                continue

            print(f"🤖 Queuing: {surname}")
            state.search_queue.append(("scan", surname))

            # ── Wait for this scan to actually complete ──
            success = _wait_for_scan_complete(surname)

            if not success:
                print(f"⚠️  '{surname}' scan timed out or failed — marking failed")
                progress["failed"].append(surname)
                save_progress(progress)
                # Clear any stuck state
                if state.is_searching:
                    state.stop_flag = True
                    time.sleep(3)
                continue

            # ── Check cache for results ──
            cache_key = surname.lower()
            if cache_key in state.scan_cache:
                scan_data = state.scan_cache[cache_key]
                total     = scan_data.get("total", 0)
                if total == 0:
                    print(f"🤖 '{surname}' → 0 profiles, marking failed")
                    progress["failed"].append(surname)
                else:
                    save_auto_result(surname, scan_data)
                    progress["scanned"].append(surname)
                    print(f"🤖 '{surname}' done — {total} profiles")
            else:
                print(f"🤖 '{surname}' — not in cache after scan, marking failed")
                progress["failed"].append(surname)

            save_progress(progress)
            time.sleep(WAIT_BETWEEN_SCANS)

        progress["last_run"]  = today
        progress["total_run"] = progress.get("total_run", 0) + len(batch)
        save_progress(progress)
        print(f"\n🤖 Batch complete — {len(batch)} processed\n")

    except Exception as e:
        print(f"⚠️  Batch runner error: {e}")
    finally:
        _batch_running = False


def _wait_for_idle(timeout: int = 120) -> bool:
    """Wait until the main scan loop is not scanning. Max 2 minutes."""
    start = time.time()
    while state.is_searching:
        if time.time() - start > timeout:
            print(f"⚠️  idle wait timeout after {timeout}s")
            return False
        time.sleep(3)
    return True


def _wait_for_scan_complete(surname: str) -> bool:
    """
    Two-phase wait:
      Phase 1 — wait up to SCAN_START_TIMEOUT seconds for the scan to begin.
      Phase 2 — wait up to SCAN_FINISH_TIMEOUT seconds for it to finish.

    Returns True if the scan completed (successfully or with errors).
    Returns False only if it never started or timed out.
    """
    surname_lo = surname.lower()

    # ── Phase 0: immediate cache hit (no scan needed) ─────
    if surname_lo in state.scan_cache:
        print(f"🤖 '{surname}' already in cache — skipping wait")
        return True

    # ── Phase 1: wait for scan to START ───────────────────
    start = time.time()
    scan_started = False
    while time.time() - start < SCAN_START_TIMEOUT:
        # Cache populated → definitely done
        if surname_lo in state.scan_cache:
            return True
        if state.scan_completed_surname.lower() == surname_lo:
            return True
        # Scan is underway for this surname
        if state.is_searching and state.current_surname.lower() == surname_lo:
            scan_started = True
            break
        time.sleep(1)

    if not scan_started:
        # Final check after timeout
        if surname_lo in state.scan_cache or state.scan_completed_surname.lower() == surname_lo:
            return True
        print(f"⚠️  '{surname}' never started within {SCAN_START_TIMEOUT}s")
        return False

    # ── Phase 2: wait for scan to FINISH ──────────────────
    start2 = time.time()
    while time.time() - start2 < SCAN_FINISH_TIMEOUT:
        if surname_lo in state.scan_cache:
            return True
        if state.scan_completed_surname.lower() == surname_lo:
            return True
        if not state.is_searching:
            # Scan stopped but wasn't ours — finished faster than expected
            if surname_lo in state.scan_cache or state.scan_completed_surname.lower() == surname_lo:
                return True
            # May have errored out
            time.sleep(3)
            if surname_lo in state.scan_cache or state.scan_completed_surname.lower() == surname_lo:
                return True
            print(f"⚠️  '{surname}' scan ended without caching")
            return False
        time.sleep(3)

    print(f"⚠️  '{surname}' finish timeout after {SCAN_FINISH_TIMEOUT}s")
    return False


# ═══════════════════════════════════════════════════════
#  BACKGROUND  (reminder checker only)
# ═══════════════════════════════════════════════════════

def start_scheduler() -> None:
    def _loop():
        s = load_settings()
        print(f"🔔 Reminder scheduler — daily at {s.get('reminder_hour',15):02d}:{s.get('reminder_min',0):02d}")
        while True:
            try:
                check_and_send_reminder()
            except Exception as e:
                print(f"⚠️  Reminder error: {e}")
            time.sleep(60)
    threading.Thread(target=_loop, daemon=True).start()
