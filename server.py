"""
server.py — HTTP server and request handler.

Includes:
1. Cookie-based Authentication (via ACCESS_TOKEN)
2. Static file serving for style.css
3. JSON API for live scan data
4. /export-data endpoint — returns full scan data for client-side PDF generation
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import state
import persistence
from config import HOST, PORT, ACCESS_TOKEN

# ── Load HTML template ────────────────────────────────────
_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
_HTML_PATH = os.path.join(_TEMPLATE_DIR, "index.html")
_CSS_PATH = os.path.join(_TEMPLATE_DIR, "style.css")

def _load_html() -> bytes:
    if not os.path.exists(_HTML_PATH):
        return b"Error: templates/index.html not found."
    with open(_HTML_PATH, "rb") as f:
        return f.read()

# ── Request handler ───────────────────────────────────────

class Handler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        pass  # suppress default access log noise

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path
        qs     = parse_qs(parsed.query)

        # 1. Security Check: Look for key in URL or Cookie
        user_key = qs.get("key", [None])[0]
        cookie_header = self.headers.get('Cookie', '')
        has_valid_cookie = f"access_token={ACCESS_TOKEN}" in cookie_header

        # --- ROUTING LOGIC ---

        # Main Entry Point
        if path == "/":
            if user_key == ACCESS_TOKEN:
                self.send_response(200)
                self.send_header("Set-Cookie", f"access_token={ACCESS_TOKEN}; Path=/; Max-Age=86400; HttpOnly")
                self.send_header("Content-Type", "text/html; charset=utf-8")
                body = _load_html()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            elif has_valid_cookie:
                self._html(_load_html())
                return
            else:
                self._unauthorized()
                return

        # Serve CSS
        elif path == "/style.css":
            if os.path.exists(_CSS_PATH):
                self.send_response(200)
                self.send_header("Content-Type", "text/css")
                with open(_CSS_PATH, "rb") as f:
                    content = f.read()
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
            else:
                self.send_response(404)
                self.end_headers()
            return

        # --- PROTECTED API ENDPOINTS ---
        if not (has_valid_cookie or user_key == ACCESS_TOKEN):
            self._unauthorized()
            return

        if path == "/data":
            scan = state.scan_results if state.scan_results else None

            # If no live scan_results in memory (e.g. after server restart),
            # seed from the most-recently-cached entry so a page refresh
            # still shows the last complete scan results.
            if not scan and state.scan_cache:
                try:
                    latest = max(
                        state.scan_cache.values(),
                        key=lambda v: v.get("cached_at", 0),
                    )
                    state.scan_results = latest
                    scan = latest
                    if not state.status_message or state.status_message == "Waiting for scan request...":
                        total   = latest.get("total", 0)
                        surname = latest.get("surname", "")
                        state.status_message = (
                            f"Done — loaded {total} profiles for '{surname}' (cached)"
                        )
                except Exception:
                    pass

            self._json({
                "status":    state.status_message,
                "searching": state.is_searching,
                "scan":      scan,
            })

        elif path == "/scan":
            surname = unquote(qs.get("q", [""])[0]).strip()
            if surname:
                state.scan_results = {}
                # Remove this surname from cache so a fresh live scan is forced.
                # Without this, run_scan() sees the cache entry and returns
                # stale/partial results instead of starting a new scan.
                cache_key = surname.lower().strip()
                if cache_key in state.scan_cache:
                    del state.scan_cache[cache_key]
                    persistence.save_cache()
                state.search_queue.append(("scan", surname))
            self._json({"ok": True})

        elif path == "/stop":
            # Set flag immediately — scanner checks this between profiles
            # and inside _wait_for_page, so stops within ~1-2 seconds
            print("⛔ Scan stopped from browser.")
            state.stop_flag    = True
            state.is_searching = False
            self._json({"ok": True})

        elif path == "/creds":
            self._json({"email": state.credentials["email"]})

        elif path == "/history":
            self._json({"history": state.search_history})

        elif path == "/clear-cache":
            persistence.clear_cache()
            self._json({"ok": True})

        elif path == "/delete-cache":
            surname = unquote(qs.get("q", [""])[0]).strip()
            if surname:
                cache_key = surname.lower().strip()
                if cache_key in state.scan_cache:
                    del state.scan_cache[cache_key]
                    persistence.save_cache()
                # Also remove from history
                state.search_history = [
                    h for h in state.search_history
                    if h.get("surname", "").lower() != cache_key
                ]
                persistence.save_history()
            self._json({"ok": True})

        elif path == "/export-data":
            # Returns full current scan data for client-side PDF generation.
            # Includes profiles, phone_groups, and partial flag.
            self._json({
                "scan":    state.scan_results if state.scan_results else {},
                "status":  state.status_message,
            })

        else:
            self.send_response(404)
            self.end_headers()

    # ── Helpers ───────────────────────────────────────────

    def _unauthorized(self):
        self.send_response(401)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Unauthorized: Please use your secret link to access this tool.")

    def _json(self, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type",   "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type",   "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ── Start server in background thread ────────────────────

def start_server() -> None:
    """Launch the HTTP server on HOST:PORT in a daemon thread."""
    server = HTTPServer((HOST, PORT), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    print(f"🌐 Server running on http://{HOST}:{PORT}")
    return server
