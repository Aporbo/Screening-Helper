"""
server.py — HTTP server and request handler.

Includes:
1. Cookie-based Authentication (via ACCESS_TOKEN)
2. Static file serving for style.css
3. JSON API for live scan data
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
                # Valid key in URL -> Set cookie and show page
                self.send_response(200)
                self.send_header("Set-Cookie", f"access_token={ACCESS_TOKEN}; Path=/; Max-Age=86400; HttpOnly")
                self.send_header("Content-Type", "text/html; charset=utf-8")
                body = _load_html()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            elif has_valid_cookie:
                # Already has cookie -> Show page
                self._html(_load_html())
                return
            else:
                # No credentials -> 401 Unauthorized
                self._unauthorized()
                return

        # Serve CSS (Always allowed so login/error screens look right)
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
        # All background calls (/data, /scan, etc.) require the cookie or key
        if not (has_valid_cookie or user_key == ACCESS_TOKEN):
            self._unauthorized()
            return

        if path == "/data":
            self._json({
                "status":    state.status_message,
                "searching": state.is_searching,
                "scan":      state.scan_results if state.scan_results else None,
            })

        elif path == "/scan":
            surname = unquote(qs.get("q", [""])[0]).strip()
            if surname:
                # Clear previous results before starting new scan
                state.scan_results = {} 
                state.search_queue.append(("scan", surname))
            self._json({"ok": True})

        elif path == "/stop":
            state.stop_flag = True
            self._json({"ok": True})

        elif path == "/creds":
            self._json({"email": state.credentials["email"]})

        elif path == "/history":
            self._json({"history": state.search_history})

        elif path == "/clear-cache":
            persistence.clear_cache()
            self._json({"ok": True})

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