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
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except PlaywrightTimeoutError:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    page.wait_for_timeout(2000)
    if is_auth_url(page.url):                          # ← new: catch session expiry first
        raise AuthExpiredError(f"Redirected to auth page: {page.url}")
    if uuid not in page.url:
        raise RuntimeError(
            f"Unexpected redirect — expected UUID {uuid} in URL, got: {page.url}"
        )


def _fetch_profile_data(page, base: str, uuid: str, result: dict) -> dict:

    # ── /profile tab ──────────────────────────────────────
    _wait_for_page(page, base + "/profile", uuid)

    # Member ID — best-effort, absent on non-Medicaid profiles
    try:
        page.wait_for_selector("text=Member ID", timeout=5000)
        page.wait_for_timeout(300)
    except PlaywrightTimeoutError:
        pass

    member_id = page.evaluate("""() => {
        const lines = document.body.innerText.split('\\n').map(l => l.trim()).filter(Boolean);
        for (let i = 0; i < lines.length; i++) {
            if (lines[i] === 'Member ID' && lines[i+1]) return lines[i+1];
        }
        return null;
    }""")
    result["member_id"] = member_id or "—"
    print(f"    Member ID: {result['member_id']}")

    # ── Scroll fully to trigger all lazy-loaded sections ──
    # Do NOT scroll back to top — stay at bottom so coverage pills
    # remain in the DOM and are reachable by the scraper
    prev_height = 0
    for _ in range(20):
        page.evaluate("window.scrollBy(0, 600)")
        page.wait_for_timeout(250)
        curr_height = page.evaluate("document.body.scrollHeight")
        if curr_height == prev_height:
            break
        prev_height = curr_height

    # Extra wait for React to finish rendering after scroll
    page.wait_for_timeout(1500)

    # ── Social Care Coverage ──────────────────────────────
    # Use state="attached" — checks DOM presence, not visibility.
    # This works even when the coverage section is scrolled off screen.
    coverage_in_dom = False
    try:
        page.wait_for_selector("text=Enrolled", state="attached", timeout=8000)
        coverage_in_dom = True
    except PlaywrightTimeoutError:
        pass

    if coverage_in_dom:
        page.wait_for_timeout(400)
        coverages = _scrape_coverages(page)

        # Safety net — wait longer and retry once if still empty
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
    page.wait_for_timeout(1500)

    try:
        count_el = page.wait_for_selector(
            "text=/\\d+ Screening Submission/", state="attached", timeout=8000
        )
        result["screening_count"] = count_el.inner_text().strip()
        result["zero_screening"]  = result["screening_count"].startswith("0 ")
        print(f"    Screenings: {result['screening_count']}")
        page.goto("about:blank", wait_until="commit")   # ← clear stuck state
        return result
    except PlaywrightTimeoutError:
        pass

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
    page.goto("about:blank", wait_until="commit")   # ← clear stuck state
    return result
