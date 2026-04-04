"""
browser/scanner.py — Orchestrates a full surname scan in two phases:

  Phase 1 — collect raw:  fast table scrape across all pages (no clicks)
             → builds phone_groups → determines family-first order

  Phase 2 — live loop:    for each profile in family-first order:
               get UUID (1 click) → profile appears in UI
               check profile      → enrollment/screenings fill in immediately
               move to next

Profiles now appear and get checked one at a time so the UI builds up
progressively rather than showing all stubs first and then filling in data.
"""

from collections import defaultdict
import time
import state
import persistence
from browser.auth      import do_login
from browser.helpers   import AuthExpiredError
from browser.collector import collect_page_raw, get_uuid_for_single, _make_profile
from browser.checker   import check_profile


def run_scan(page, surname: str):
    cache_key = surname.lower().strip()
    if cache_key in state.scan_cache:
        _load_from_cache(cache_key, surname)
        return page

    return _run_live_scan(page, surname, cache_key)


# ── Cache path ────────────────────────────────────────────

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

    # ── Phase 1: fast table scrape — no clicks, builds phone groups ──
    all_raw = _collect_all_raw(page, surname)
    if state.stop_flag:
        state.is_searching   = False
        state.status_message = f"Stopped during collection for '{surname}'"
        return page

    skipped = [p for p in all_raw if p["phone"] == "(000) 000-0000"]
    all_raw  = [p for p in all_raw if p["phone"] != "(000) 000-0000"]
    total    = len(all_raw)
    print(f"Collected {total} raw profiles ({len(skipped)} skipped — (000) 000-0000)")
    for p in skipped:
        print(f"  ⏭️  Skipped: {p['name']} — (000) 000-0000")

    phone_groups   = _build_phone_groups(all_raw)
    ordered_raw    = _reorder_family_first(all_raw, phone_groups)

    state.scan_results = {
        "surname":      surname,
        "profiles":     [],
        "total":        total,
        "scanned":      0,
        "phone_groups": phone_groups,
        "failed":       state.failed_profiles,
        "from_cache":   False,
    }

    # ── Phase 2: combined UUID + check loop ──────────────
    all_profiles = []

    for item in ordered_raw:
        if state.stop_flag:
            break

        position   = len(all_profiles) + 1
        stub       = None
        stub_added = False

        # ── Recycle browser context after every profile ───
        print(f"  ♻️  Refreshing context...")
        browser = page.context.browser
        page.context.close()
        new_ctx  = browser.new_context()
        page     = new_ctx.new_page()
        cookies = persistence.load_session_cookies()
        if cookies:
            new_ctx.add_cookies(cookies)
        # OPTIMISED: was 300ms → 100ms
        # Cookie injection is synchronous; 100ms is enough before navigation.
        page.wait_for_timeout(100)

        for login_attempt in range(2):
            try:
                state.status_message = f"Getting profile {position}/{total}: {item['name']}..."
                uuid = get_uuid_for_single(page, surname, item)

                if not stub_added:
                    stub = _make_profile(item, uuid)
                    all_profiles.append(stub)
                    stub_added = True
                else:
                    stub["uuid"] = uuid

                _push(all_profiles, phone_groups)

                state.status_message = f"Checking {position}/{total}: {item['name']}..."
                print(state.status_message)

                info = check_profile(page, uuid)
                stub.update(info)
                all_profiles[-1] = stub
                _push(all_profiles, phone_groups, scanned=position)

                if info.get("error") and info["error"] != "No UUID":
                    state.failed_profiles.append({
                        "name":  item["name"],
                        "uuid":  uuid,
                        "error": info["error"],
                    })
                    state.scan_results["failed"] = state.failed_profiles
                    time.sleep(1)

                # OPTIMISED: was 0.8s → 0.3s
                # Brief cooldown between profiles to avoid hammering the server.
                time.sleep(0.3)
                break

            except AuthExpiredError:
                if login_attempt == 0:
                    print(f"  🔑 Session expired — re-logging in...")
                    state.status_message = "Session expired — re-logging in..."
                    do_login(page, state.credentials["email"], state.credentials["password"])
                    print(f"  ✅ Re-logged in — retrying {item['name']}")
                else:
                    err = "Session expired and re-login failed"
                    print(f"  ❌ {err} for {item['name']}")
                    if not stub_added:
                        stub = _make_profile(item, None)
                        all_profiles.append(stub)
                        stub_added = True
                    stub["error"]   = err
                    stub["checked"] = True
                    all_profiles[-1] = stub
                    state.failed_profiles.append({"name": item["name"], "uuid": None, "error": err})
                    state.scan_results["failed"] = state.failed_profiles
                    _push(all_profiles, phone_groups, scanned=position)


    # ── Wrap up ───────────────────────────────────────────
    state.is_searching = False

    if state.stop_flag:
        state.status_message = (
            f"Stopped — scanned {state.scan_results['scanned']} of {total} for '{surname}'"
        )
        return page

    state.status_message = f"Done — scanned all {total} profiles for '{surname}'"
    print(state.status_message)

    final = {
        "surname":      surname,
        "profiles":     all_profiles,
        "total":        total,
        "scanned":      total,
        "phone_groups": phone_groups,
        "failed":       state.failed_profiles,
        "from_cache":   False,
    }
    state.scan_cache[cache_key] = final
    persistence.save_cache()

    enrolled_zero = len([
        p for p in all_profiles
        if p.get("any_enrolled") and p.get("zero_screening")
    ])
    persistence.add_to_history(
        surname, total,
        len([v for v in phone_groups.values() if len(v) > 1]),
        enrolled_zero,
        len(state.failed_profiles),
    )
    return page


# ── Helpers ───────────────────────────────────────────────

def _push(profiles: list, phone_groups: dict, scanned: int | None = None) -> None:
    """Push the current profiles list to state.scan_results."""
    state.scan_results["profiles"]     = list(profiles)
    state.scan_results["phone_groups"] = phone_groups
    if scanned is not None:
        state.scan_results["scanned"] = scanned


def _collect_all_raw(page, surname: str) -> list:
    """Fast table scrape across all pages. No clicks."""
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
    """Return {phone: [name, ...]} for phones shared by 2+ people."""
    groups = defaultdict(list)
    for item in all_raw:
        if item["phone"]:
            groups[item["phone"]].append(item["name"])
    return {k: v for k, v in groups.items() if len(v) > 1}


def _reorder_family_first(all_raw: list, phone_groups: dict) -> list:
    """
    Return all_raw reordered so that:
      - All members of family group 1 come first (consecutive)
      - Then all members of family group 2, etc.
      - Then all solo profiles at the end
    """
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
