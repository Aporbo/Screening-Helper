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
    """
    Scrape a single search results page without clicking anything.
    Returns a list of raw dicts, or None if the page has no rows (end of results).

    Each item includes page_idx (row position within the page) so
    get_uuid_for_single can fall back to positional clicking when two
    profiles on the same page share the same name.
    """
    url = SEARCH_URL.format(page=page_num, surname=surname)
    page.goto(url, wait_until="domcontentloaded")

    try:
        page.wait_for_selector("table tbody tr", timeout=8000)
    except PlaywrightTimeoutError:
        return None  # no more pages

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
                "page_idx":  idx,       # row index within this page for fallback clicking
            })

    print(f"  Page {page_num}: {len(items)} rows")
    return items


# ── Single UUID fetch ─────────────────────────────────────

def get_uuid_for_single(page, surname: str, item: dict) -> str | None:
    url = SEARCH_URL.format(page=item["page_num"], surname=surname)

    # Retry up to 3 times — browser can be sluggish after heavy scrolling
    for attempt in range(3):
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(500)

        if is_auth_url(page.url):                      # ← new: bail immediately, don't retry
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

    for row in rows:
        cells = row.query_selector_all("td")
        if cells and cells[0].inner_text().strip() == item["name"]:
            target = row
            break

    if not target and item["page_idx"] < len(rows):
        target = rows[item["page_idx"]]

    if not target:
        print(f"  {item['name']} → ❌ row not found")
        return None

    uuid = None
    try:
        with page.expect_navigation(timeout=12000):
            target.click()
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        page.wait_for_timeout(500)
        uuid = extract_uuid_from_url(page.url)
        if not uuid:
            page.wait_for_timeout(2000)
            uuid = extract_uuid_from_url(page.url)
        print(f"  {item['name']} → {uuid or '❌ no UUID'}")
    except Exception as e:
        print(f"  {item['name']} → ❌ {e}")

    return uuid


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
