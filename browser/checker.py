"""
browser/checker.py
"""


import time
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from browser.helpers import extract_uuid_from_url, calc_age, AuthExpiredError, is_auth_url

from config import FACESHEET_URL, PROFILE_RETRY_COUNT, PROFILE_RETRY_DELAY


def check_profile(page, uuid: str | None) -> dict:
    result = {
        "checked":         True,
        "any_enrolled":    False,
        "member_id":       "—",
        "coverages":       [],
        "zero_screening":  True,
        "screening_count": "0 Screening Submissions",
        "error":           None,
    }

    if not uuid:
        result["error"] = "No UUID"
        return result

    base = FACESHEET_URL.format(uuid=uuid)

    for attempt in range(PROFILE_RETRY_COUNT + 1):
        try:
            result = _fetch_profile_data(page, base, uuid, result.copy())
            result["error"] = None
            return result
        except Exception as e:
            err = str(e)[:120]
            print(f"    ❌ Attempt {attempt + 1}/{PROFILE_RETRY_COUNT + 1} failed for {uuid}: {err}")
            if attempt < PROFILE_RETRY_COUNT:
                print(f"    🔄 Retrying in {PROFILE_RETRY_DELAY}s...")
                time.sleep(PROFILE_RETRY_DELAY)
            else:
                result["error"] = err

    return result


def _wait_for_page(page, url: str, uuid: str) -> None:
    page.goto(url)

    # ── OPTIMISED: networkidle → domcontentloaded ──────────
    # Why this is safe:
    #   - "networkidle" waits for ALL network requests to stop for 500ms.
    #     On a React SPA like Unite Us that makes continuous background API calls,
    #     this easily takes 3–8 seconds per page — sometimes hitting the 20s timeout.
    #   - "domcontentloaded" fires as soon as the HTML is parsed (~0.5–1.5s).
    #   - The REAL readiness check happens immediately after this function returns:
    #       • For /profile:   wait_for_selector("text=Member ID") is the guard
    #       • For /screenings: wait_for_selector("text=/\d+ Screening Submission/") is the guard
    #     Both of those wait for the ACTUAL DATA we need, not just "page loaded".
    #     They are safer readiness signals than networkidle ever was.
    # Saves: 2–8s per call, called twice per profile = 4–16s per profile.
    page.wait_for_load_state("domcontentloaded", timeout=15000)
    page.wait_for_timeout(800)

    if is_auth_url(page.url):
        raise AuthExpiredError(f"Redirected to auth page: {page.url}")
    if uuid not in page.url:
        raise RuntimeError(
            f"Unexpected redirect — expected UUID {uuid} in URL, got: {page.url}"
        )


def _fetch_profile_data(page, base: str, uuid: str, result: dict) -> dict:

    # ── /profile tab ──────────────────────────────────────
    _wait_for_page(page, base + "/profile", uuid)

    # Member ID — this selector wait IS the content-readiness guard.
    # It replaces what networkidle used to do: we wait until "Member ID"
    # actually appears in the DOM before reading it.
    try:
        page.wait_for_selector("text=Member ID", timeout=5000)
        page.wait_for_timeout(100)
    except PlaywrightTimeoutError:
        pass  # non-Medicaid profile — Member ID simply absent

    # ── DATA LOGIC COMPLETELY UNCHANGED ──────────────────
    member_id = page.evaluate("""() => {
        const lines = document.body.innerText.split('\\n').map(l => l.trim()).filter(Boolean);
        for (let i = 0; i < lines.length; i++) {
            if (lines[i] === 'Member ID' && lines[i+1]) return lines[i+1];
        }
        return null;
    }""")
    result["member_id"] = member_id or "—"
    print(f"    Member ID: {result['member_id']}")

    # ── Scroll to trigger lazy-loaded sections ────────────
    # iterations 10, pause 150ms (from previous optimisation)
    prev_height = 0
    for _ in range(10):
        page.evaluate("window.scrollBy(0, 600)")
        page.wait_for_timeout(150)
        curr_height = page.evaluate("document.body.scrollHeight")
        if curr_height == prev_height:
            break
        prev_height = curr_height

    # 1500ms React render wait — unchanged, protects coverage accuracy
    page.wait_for_timeout(1500)

    # ── Social Care Coverage ──────────────────────────────
    # OPTIMISED: timeout 8000 → 5000ms
    # Why safe: the 1500ms React wait above already let the page settle.
    # If "Enrolled" hasn't appeared in 5s after that, it genuinely isn't there.
    # Saves: 3s per profile that has NO enrollment data (non-enrolled profiles).
    coverage_in_dom = False
    try:
        page.wait_for_selector("text=Enrolled", state="attached", timeout=5000)
        coverage_in_dom = True
    except PlaywrightTimeoutError:
        pass

    if coverage_in_dom:
        page.wait_for_timeout(200)
        coverages = _scrape_coverages(page)

        # Safety net — unchanged (only fires when pills failed to load first time)
        if not coverages:
            print("    No coverage pills on first read — waiting 3s and retrying...")
            page.wait_for_timeout(3000)
            coverages = _scrape_coverages(page)

        result["coverages"]    = coverages or []
        result["any_enrolled"] = any(p["status"] == "Enrolled" for p in result["coverages"])
        print(f"    Coverages: {len(result['coverages'])} plans | enrolled={result['any_enrolled']}")
    else:
        print("    No enrollment data found for this profile")

    return _check_screenings(page, base, uuid, result)


def _scrape_coverages(page) -> list:
    # ── DATA LOGIC COMPLETELY UNCHANGED ──────────────────
    return page.evaluate("""() => {
        const plans = [];
        const seen  = new Set();
        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        let node;
        while (node = walker.nextNode()) {
            const txt = node.textContent.trim();
            const isEnrolled    = txt === 'Enrolled';
            const isNotEnrolled = txt === 'Not enrolled' || txt === 'Not Enrolled';
            if (!isEnrolled && !isNotEnrolled) continue;
            let planName = 'Unknown';
            let parent = node.parentElement;
            for (let i = 0; i < 14; i++) {
                if (!parent) break;
                const inner = parent.innerText || '';
                if (inner.includes('Plan Name')) {
                    const lines = inner.split('\\n').map(l => l.trim()).filter(Boolean);
                    const idx = lines.findIndex(l => l === 'Plan Name');
                    if (idx !== -1 && lines[idx + 1]) planName = lines[idx + 1];
                    break;
                }
                parent = parent.parentElement;
            }
            const key = planName + '|' + txt;
            if (!seen.has(key)) {
                seen.add(key);
                plans.push({ name: planName, status: isEnrolled ? 'Enrolled' : 'Not enrolled' });
            }
        }
        return plans;
    }""")


def _check_screenings(page, base: str, uuid: str, result: dict) -> dict:
    _wait_for_page(page, base + "/screenings", uuid)

    page.evaluate("window.scrollBy(0, 800)")
    page.wait_for_timeout(800)

    # OPTIMISED: timeout 8000 → 5000ms
    # Why safe: the selector is either there within a second or two of page load,
    # or we fall through to the evaluate() fallback which reads innerText directly.
    # The fallback path is unchanged and covers slow-loading pages.
    # Saves: 3s on any profile that hits the timeout path.
    try:
        count_el = page.wait_for_selector(
            "text=/\\d+ Screening Submission/", state="attached", timeout=5000
        )
        result["screening_count"] = count_el.inner_text().strip()
        result["zero_screening"]  = result["screening_count"].startswith("0 ")
        print(f"    Screenings: {result['screening_count']}")
        # ── OPTIMISED: about:blank removed ─────────────────
        # The browser context is fully recycled in scanner.py at the start
        # of each profile (page.context.close() → new_context()). Going to
        # about:blank was a redundant extra page load that served no purpose.
        # Saves: ~250ms per profile.
        return result
    except PlaywrightTimeoutError:
        pass

    # ── FALLBACK — data logic completely unchanged ────────
    count_text = page.evaluate("""() => {
        const match = document.body.innerText.match(/(\\d+) Screening Submission/);
        return match ? match[0] : null;
    }""")

    if count_text:
        result["screening_count"] = count_text
        result["zero_screening"]  = count_text.startswith("0 ")
    else:
        result["zero_screening"]  = True
        result["screening_count"] = "0 Screening Submissions"

    print(f"    Screenings (fallback): {result['screening_count']}")
    # ── about:blank removed here too (same reason as above) ──
    return result
