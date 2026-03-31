"""
browser/helpers.py — Pure utility functions used across browser modules.
No side effects, no imports from state or config — safe to reuse anywhere.
"""
class AuthExpiredError(Exception):
    """Raised when the browser is silently redirected to the Unite Us auth page."""

def is_auth_url(url: str) -> bool:
    return "auth.uniteus.io" in url

import re
from datetime import date


def extract_uuid_from_url(url: str | None) -> str | None:
    """
    Pull the 36-char UUID out of a Unite Us facesheet URL.
    e.g. https://app.uniteus.io/facesheet/abc123.../profile → 'abc123...'
    Returns None if not found.
    """
    if not url:
        return None
    m = re.search(r'/facesheet/([a-f0-9\-]{36})', url)
    return m.group(1) if m else None


def calc_age(dob_str: str | None) -> int | None:
    """
    Calculate current age from a MM/DD/YYYY string.
    Returns None if the string is missing or malformed.
    """
    if not dob_str:
        return None
    try:
        parts = dob_str.strip().split('/')
        if len(parts) != 3:
            return None
        birth = date(int(parts[2]), int(parts[0]), int(parts[1]))
        today = date.today()
        age = today.year - birth.year - (
            (today.month, today.day) < (birth.month, birth.day)
        )
        return age if age >= 0 else None
    except Exception:
        return None
