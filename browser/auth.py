"""
browser/auth.py — Handles Unite Us login with session cookie reuse.
"""

import state
import persistence
from config import BASE_URL


def do_login(page, email: str, password: str) -> None:
    """
    Log in to Unite Us. Tries to restore a saved session first.
    Falls back to full email/password login and saves the new session.
    Updates state.status_message throughout.
    """
    state.status_message = "Logging in..."

    # ── Try restoring a saved session ────────────────────
    cookies = persistence.load_session_cookies()
    if cookies:
        try:
            page.context.add_cookies(cookies)
            page.goto(BASE_URL + "/dashboard")
            page.wait_for_load_state("domcontentloaded", timeout=8000)
            if "dashboard" in page.url:
                state.status_message = f"✅ Logged in as {email} (session restored)"
                print(state.status_message)
                return
        except Exception as e:
            print(f"  Session restore failed: {e} — doing fresh login...")

    # ── Full login flow ───────────────────────────────────
    page.goto(BASE_URL)
    page.wait_for_selector("input[type='email']", timeout=10000)
    page.fill("input[type='email']", email)
    page.get_by_role("button", name="Next").click()
    page.wait_for_selector("input[type='password']", timeout=10000)
    page.fill("input[type='password']", password)
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url("**/dashboard**", timeout=30000)

    persistence.save_session_cookies(page.context.cookies())

    state.status_message = f"✅ Logged in as {email}"
    print(state.status_message)
