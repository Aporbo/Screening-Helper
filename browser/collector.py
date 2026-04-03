"""
browser/collector.py — Scrapes the Unite Us search results pages.

Two phases:
  1. collect_page_raw()      — fast table scrape, no clicks needed
  2. get_uuid_for_single()   — clicks one specific row to extract its UUID
"""
import time
import state
from config import SEARCH_URL
from browser.helpers import extract_uuid_from_url, calc_age, AuthExpiredError, is_auth_url
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError


# ── Phase 1: fast table scrape ────────────────────────────

def collect_page_raw(page, surname: str, page_num: int) -> list | None:
    url = SEARCH_URL.format(page=page_num, surname=surname)
    page.goto(url, wait_until="domcontentloaded")

    try:
        page.wait_for_selector("table tbody tr", timeout=8000)
    except PlaywrightTimeoutError:
        return None

    rows = page.query_selector_all("table tbody tr")
    if not rows:
        return None

    items = []
    for idx, row in enumerate(rows):
        cells     = row.query_selector_all("td")
        name      = cells[0].inner_text().strip() if len(cells) > 0 else ""
        member_id = cells[1].inner_text().strip() if len(cells) > 1 else ""
        phone     = cells[2].inner_text().strip() if len(cells) > 2 else ""
        dob       = cells[3].inner_text().strip() if len(cells) > 3 else ""
        if name:
            items.append({
                "name":      name,
                "member_id": member_id or "—",
                "phone":     phone,
                "dob":       dob,
                "page_num":  page_num,
                "page_idx":  idx,
            })

    print(f"  Page {page_num}: {len(items)} rows")
    return items


# ── Single UUID fetch ─────────────────────────────────────

def get_uuid_for_single(page, surname: str, item: dict) -> str | None:
    url = SEARCH_URL.format(page=item["page_num"], surname=surname)

    # Retry up to 3 times loading the search page
    for attempt in range(3):
        if state.stop_flag:
            return None

        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(600)

        if is_auth_url(page.url):
            raise AuthExpiredError("Redirected to auth page during UUID fetch")

        try:
            page.wait_for_selector("table tbody tr", timeout=15000)
            break
        except PlaywrightTimeoutError:
            print(f"  {item['name']} → search page timeout (attempt {attempt + 1}/3), waiting 3s...")
            if attempt < 2:
                time.sleep(3)
            else:
                print(f"  {item['name']} → ❌ search page failed after 3 attempts")
                return None

    rows   = page.query_selector_all("table tbody tr")
    target = None

    # First try: match by exact name
    for row in rows:
        cells = row.query_selector_all("td")
        if cells and cells[0].inner_text().strip() == item["name"]:
            target = row
            break

    # Fallback: positional index
    if not target and item["page_idx"] < len(rows):
        target = rows[item["page_idx"]]

    if not target:
        print(f"  {item['name']} → ❌ row not found")
        return None

    uuid = None
    for click_attempt in range(3):
        if state.stop_flag:
            return None
        try:
            with page.expect_navigation(timeout=14000):
                target.click()
            page.wait_for_load_state("domcontentloaded", timeout=12000)
            page.wait_for_timeout(800)

            uuid = extract_uuid_from_url(page.url)
            if not uuid:
                # Sometimes navigation finishes before URL updates — wait longer
                page.wait_for_timeout(2500)
                uuid = extract_uuid_from_url(page.url)

            if not uuid:
                # Last resort: try waiting for /facesheet/ in URL
                try:
                    page.wait_for_url("**/facesheet/**", timeout=6000)
                    uuid = extract_uuid_from_url(page.url)
                except PlaywrightTimeoutError:
                    pass

            if uuid:
                print(f"  {item['name']} → {uuid}")
                return uuid

            print(f"  {item['name']} → no UUID in URL yet (attempt {click_attempt + 1}/3), retrying...")
            # Navigate back and try clicking again
            page.go_back(wait_until="domcontentloaded", timeout=10000)
            page.wait_for_timeout(800)
            rows   = page.query_selector_all("table tbody tr")
            target = None
            for row in rows:
                cells = row.query_selector_all("td")
                if cells and cells[0].inner_text().strip() == item["name"]:
                    target = row
                    break
            if not target and item["page_idx"] < len(rows):
                target = rows[item["page_idx"]]
            if not target:
                break

        except Exception as e:
            print(f"  {item['name']} → ❌ click attempt {click_attempt + 1}: {e}")
            if click_attempt < 2:
                time.sleep(2)
            else:
                break

    print(f"  {item['name']} → ❌ no UUID after all attempts")
    return None


# ── Profile stub factory ──────────────────────────────────

def _make_profile(item: dict, uuid: str | None) -> dict:
    """Create a profile dict stub from a raw scraped row and a UUID."""
    return {
        "name":            item["name"],
        "uuid":            uuid,
        "raw_phone":       item["phone"],
        "member_id":       "—",
        "dob":             item["dob"],
        "age":             calc_age(item["dob"]),
        "page":            item["page_num"],
        "checked":         False,
        "any_enrolled":    False,
        "zero_screening":  True,
        "screening_count": "0 Screening Submissions",
        "error":           None,
    }
