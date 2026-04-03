"""
browser/scanner.py — Orchestrates a full surname scan.

Phase 2 now runs profile checks in parallel.

Fix for "Cannot switch to a different thread":
  sync_playwright() uses greenlets internally and is NOT thread-safe.
  A browser/page created in one thread cannot be touched by another.
  Solution: each worker thread starts its own sync_playwright() + browser
  and processes its entire batch of profiles inside that one browser.
  This means CONCURRENCY browsers run simultaneously, each handling
  (total / CONCURRENCY) profiles serially — same logic as before, just
  N lanes in parallel.
"""

import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from playwright.sync_api import sync_playwright

import state
import persistence
from browser.auth      import do_login
from browser.helpers   import AuthExpiredError
from browser.collector import collect_page_raw, get_uuid_for_single, _make_profile
from browser.checker   import check_profile

# Hard ceiling — never exceed this regardless of profile count.
MAX_CONCURRENCY = 4

def _get_concurrency(total: int) -> int:
    """Scale workers up to MAX_CONCURRENCY based on how many profiles there are."""
    if total <= 3:
        return 1   # not worth the overhead of multiple browsers
    elif total <= 8:
        return 2
    elif total <= 12:
        return 3
    else:
        return MAX_CONCURRENCY

# Prevent simultaneous re-logins from multiple workers.
_relogin_lock = threading.Lock()


# ── Public entry point ────────────────────────────────────

def run_scan(page, surname: str):
    cache_key = surname.lower().strip()
    if cache_key in state.scan_cache:
        _load_from_cache(cache_key, surname)
        return page
    return _run_live_scan(page, surname, cache_key)


# ── Cache path (unchanged) ────────────────────────────────

def _load_from_cache(cache_key: str, surname: str) -> None:
    print(f"⚡ Loading '{surname}' from cache")
    cached = state.scan_cache[cache_key]
    cached["from_cache"] = True
    state.scan_results   = cached
    state.status_message = (
        f"Done — loaded {cached['total']} profiles for '{surname}' (cached)"
    )
    phone_groups  = cached.get("phone_groups") or {}
    family_count  = len([v for v in phone_groups.values() if len(v) > 1])
    enrolled_zero = len([
        p for p in cached.get("profiles", [])
        if p.get("any_enrolled") and p.get("zero_screening")
    ])
    persistence.add_to_history(
        surname,
        cached.get("total", 0),
        family_count,
        enrolled_zero,
        len(cached.get("failed", [])),
    )


# ── Live scan path ────────────────────────────────────────

def _run_live_scan(page, surname: str, cache_key: str):
    state.is_searching    = True
    state.stop_flag       = False
    state.failed_profiles = []
    state.current_surname = surname

    # ── Phase 1: collection (completely unchanged) ───────
    all_raw = _collect_all_raw(page, surname)
    if state.stop_flag:
        state.is_searching   = False
        state.status_message = f"Stopped during collection for '{surname}'"
        return page

    skipped = [p for p in all_raw if p["phone"] == "(000) 000-0000"]
    all_raw  = [p for p in all_raw if p["phone"] != "(000) 000-0000"]
    total    = len(all_raw)
    print(f"Collected {total} raw profiles ({len(skipped)} skipped)")
    for p in skipped:
        print(f"  ⏭️  Skipped: {p['name']} — (000) 000-0000")

    phone_groups = _build_phone_groups(all_raw)
    ordered_raw  = _reorder_family_first(all_raw, phone_groups)

    state.scan_results = {
        "surname":      surname,
        "profiles":     [],
        "total":        total,
        "scanned":      0,
        "phone_groups": phone_groups,
        "failed":       state.failed_profiles,
        "from_cache":   False,
    }

    # ── Phase 2: parallel batches ────────────────────────
    # Pre-allocate result slots so each worker writes by index.
    all_profiles = [None] * total
    lock         = threading.Lock()

    concurrency = _get_concurrency(total)
    batches = _make_batches(list(enumerate(ordered_raw)), concurrency)
    print(f"🚀 Running {concurrency} parallel workers for {total} profiles")

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {
            pool.submit(
                _run_batch,
                surname, batch,
                all_profiles, phone_groups, total, lock
            ): i
            for i, batch in enumerate(batches)
        }

        for future in as_completed(futures):
            if state.stop_flag:
                for f in futures:
                    f.cancel()
                break
            try:
                future.result()
            except Exception as exc:
                worker_idx = futures[future]
                print(f"  ❌ Worker {worker_idx} raised: {exc}")

    # ── Wrap up ───────────────────────────────────────────
    state.is_searching = False

    final_profiles = [
        p if p is not None else _make_profile(ordered_raw[i], None)
        for i, p in enumerate(all_profiles)
    ]

    if state.stop_flag:
        state.status_message = (
            f"Stopped — scanned {state.scan_results.get('scanned', 0)} of {total} for '{surname}'"
        )
        return page

    state.status_message = f"Done — scanned all {total} profiles for '{surname}'"
    print(state.status_message)

    final = {
        "surname":      surname,
        "profiles":     final_profiles,
        "total":        total,
        "scanned":      total,
        "phone_groups": phone_groups,
        "failed":       state.failed_profiles,
        "from_cache":   False,
    }
    state.scan_cache[cache_key] = final
    persistence.save_cache()

    enrolled_zero = len([
        p for p in final_profiles
        if p and p.get("any_enrolled") and p.get("zero_screening")
    ])
    persistence.add_to_history(
        surname, total,
        len([v for v in phone_groups.values() if len(v) > 1]),
        enrolled_zero,
        len(state.failed_profiles),
    )
    return page


# ── Batch worker ──────────────────────────────────────────
#
# Each call to _run_batch runs in its own thread.
# It starts its OWN sync_playwright() + browser so there is
# zero cross-thread Playwright usage — that is what fixes the
# greenlet "Cannot switch to a different thread" error.
# Inside the batch, profiles are processed one at a time using
# the exact same logic as the original serial loop.

def _run_batch(
    surname: str,
    batch: list,                 # list of (index, item) pairs
    all_profiles: list,
    phone_groups: dict,
    total: int,
    lock: threading.Lock,
) -> None:
    """Process one batch of profiles inside a dedicated playwright browser."""

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-extensions",
                "--disable-background-networking",
                "--disable-default-apps",
                "--no-first-run",
            ],
        )
        # Read cookies once per batch instead of once per profile.
        cookies = persistence.load_session_cookies()
        try:
            for index, item in batch:
                if state.stop_flag:
                    break

                # Fresh context per profile — same isolation as the
                # original code's context-recycle approach.
                ctx  = browser.new_context()
                page = ctx.new_page()
                if cookies:
                    ctx.add_cookies(cookies)

                # Block images, fonts, media — we only scrape text.
                page.route("**/*", lambda route: (
                    route.abort()
                    if route.request.resource_type in ("image", "media", "font", "stylesheet")
                    else route.continue_()
                ))

                try:
                    _process_one(
                        page, surname, item, index,
                        all_profiles, phone_groups, total, lock
                    )
                finally:
                    ctx.close()

        finally:
            browser.close()


# ── Single profile processor ──────────────────────────────
#
# This is the original inner loop body from _run_live_scan,
# moved here verbatim. The only change: results are written
# by index instead of all_profiles.append / all_profiles[-1].

def _process_one(
    page,
    surname: str,
    item: dict,
    index: int,
    all_profiles: list,
    phone_groups: dict,
    total: int,
    lock: threading.Lock,
) -> None:
    position   = index + 1
    stub       = None
    stub_added = False
    worker_id  = f"W{(index % MAX_CONCURRENCY) + 1}"

    for login_attempt in range(2):
        if state.stop_flag:
            break
        try:
            state.status_message = f"[{worker_id}] Getting profile {position}/{total}: {item['name']}..."
            uuid = get_uuid_for_single(page, surname, item)

            if state.stop_flag:
                break

            if not stub_added:
                stub = _make_profile(item, uuid)
                stub_added = True
            else:
                stub["uuid"] = uuid

            _write(index, stub, all_profiles, phone_groups, lock)

            state.status_message = f"[{worker_id}] Checking {position}/{total}: {item['name']}..."
            print(state.status_message)

            if state.stop_flag:
                stub["checked"] = False
                _write(index, stub, all_profiles, phone_groups, lock, update_scanned=True)
                break

            info = check_profile(page, uuid)

            if state.stop_flag:
                stub.update(info)
                _write(index, stub, all_profiles, phone_groups, lock, update_scanned=True)
                break

            stub.update(info)
            _write(index, stub, all_profiles, phone_groups, lock, update_scanned=True)

            if info.get("error") and info["error"] != "No UUID":
                state.failed_profiles.append({
                    "name":  item["name"],
                    "uuid":  uuid,
                    "error": info["error"],
                })
                state.scan_results["failed"] = state.failed_profiles
                time.sleep(1)

            time.sleep(0.8)
            break  # success

        except AuthExpiredError:
            if state.stop_flag:
                break
            if login_attempt == 0:
                # Lock so only one worker triggers a re-login at a time.
                with _relogin_lock:
                    print(f"  🔑 Session expired (profile {position}) — re-logging in...")
                    state.status_message = "Session expired — re-logging in..."
                    do_login(page, state.credentials["email"], state.credentials["password"])
                    print(f"  ✅ Re-logged in — retrying {item['name']}")
            else:
                err = "Session expired and re-login failed"
                print(f"  ❌ {err} for {item['name']}")
                if not stub_added:
                    stub = _make_profile(item, None)
                    stub_added = True
                stub["error"]   = err
                stub["checked"] = True
                state.failed_profiles.append({"name": item["name"], "uuid": None, "error": err})
                state.scan_results["failed"] = state.failed_profiles
                _write(index, stub, all_profiles, phone_groups, lock, update_scanned=True)


# ── Thread-safe result push ───────────────────────────────

def _write(
    index: int,
    stub: dict,
    all_profiles: list,
    phone_groups: dict,
    lock: threading.Lock,
    update_scanned: bool = False,
) -> None:
    with lock:
        all_profiles[index] = stub
        filled = [p for p in all_profiles if p is not None]
        _push(filled, phone_groups)
        if update_scanned:
            _push(filled, phone_groups,
                  scanned=sum(1 for p in filled if p.get("checked")))


# ── Helpers (completely unchanged) ───────────────────────

def _push(profiles: list, phone_groups: dict, scanned: int | None = None) -> None:
    state.scan_results["profiles"]     = list(profiles)
    state.scan_results["phone_groups"] = phone_groups
    if scanned is not None:
        state.scan_results["scanned"] = scanned


def _collect_all_raw(page, surname: str) -> list:
    all_raw = []
    pg = 1
    while True:
        if state.stop_flag:
            break
        state.status_message = (
            f"Collecting profile list for '{surname}'... ({len(all_raw)} found, page {pg})"
        )
        raw = collect_page_raw(page, surname, pg)
        if raw is None:
            break
        all_raw.extend(raw)
        pg += 1
    return all_raw


def _build_phone_groups(all_raw: list) -> dict:
    groups = defaultdict(list)
    for item in all_raw:
        if item["phone"]:
            groups[item["phone"]].append(item["name"])
    return {k: v for k, v in groups.items() if len(v) > 1}


def _reorder_family_first(all_raw: list, phone_groups: dict) -> list:
    family_phones  = set(phone_groups.keys())
    family_buckets = {}

    for item in all_raw:
        if item["phone"] in family_phones:
            family_buckets.setdefault(item["phone"], []).append(item)

    solos = [item for item in all_raw if item["phone"] not in family_phones]

    ordered = []
    for phone, members in family_buckets.items():
        ordered.extend(members)
        print(f"  Family [{phone}]: {[m['name'] for m in members]}")

    ordered.extend(solos)

    family_people = len(ordered) - len(solos)
    print(
        f"Scan order: {len(family_buckets)} family groups "
        f"({family_people} people) → then {len(solos)} solos"
    )
    return ordered


def _make_batches(indexed_items: list, n: int) -> list:
    """Split a list of (index, item) pairs into n roughly equal batches."""
    if not indexed_items:
        return []
    k, m = divmod(len(indexed_items), n)
    batches = []
    start = 0
    for i in range(n):
        end = start + k + (1 if i < m else 0)
        if start < end:
            batches.append(indexed_items[start:end])
        start = end
    return batches
