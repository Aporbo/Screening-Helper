"""
server.py — HTTP server and request handler.

Includes:
1. Cookie-based Authentication (via ACCESS_TOKEN)
2. Static file serving for style.css
3. JSON API for live scan data
4. Notes API (per-profile notes stored server-side)
5. Global Notepad API
6. Auto Scanner queue management
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import state
import persistence
from config import HOST, PORT, ACCESS_TOKEN

# ── Load HTML template ────────────────────────────────────────
_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
_HTML_PATH = os.path.join(_TEMPLATE_DIR, "index.html")
_CSS_PATH = os.path.join(_TEMPLATE_DIR, "style.css")

def _load_html() -> bytes:
    if not os.path.exists(_HTML_PATH):
        return b"Error: templates/index.html not found."
    with open(_HTML_PATH, "rb") as f:
        return f.read()

# ── Request handler ───────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        pass  # suppress default access log noise

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path
        qs     = parse_qs(parsed.query)

        user_key = qs.get("key", [None])[0]
        cookie_header = self.headers.get('Cookie', '')
        has_valid_cookie = f"access_token={ACCESS_TOKEN}" in cookie_header

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

        # ── Notes API ─────────────────────────────────────────
        elif path == "/notes":
            self._json({"notes": state.profile_notes})

        elif path == "/save-note":
            uuid = unquote(qs.get("uuid", [""])[0]).strip()
            text = unquote(qs.get("text", [""])[0]).strip()
            if uuid:
                if text:
                    state.profile_notes[uuid] = text[:100]
                else:
                    state.profile_notes.pop(uuid, None)
                persistence.save_notes()
            self._json({"ok": True})

        elif path == "/delete-note":
            uuid = unquote(qs.get("uuid", [""])[0]).strip()
            if uuid:
                state.profile_notes.pop(uuid, None)
                persistence.save_notes()
            self._json({"ok": True})

        # ── Global Notepad API ────────────────────────────────
        elif path == "/global-notepad":
            self._json({"notepad": state.global_notepad})

        elif path == "/save-notepad":
            text = unquote(qs.get("text", [""])[0])
            state.global_notepad = text
            persistence.save_notepad()
            self._json({"ok": True})

        # ── Auto Scanner API ──────────────────────────────────
        elif path == "/autoscan-list":
            self._json({"list": state.autoscan_list})

        elif path == "/autoscan-add-top":
            surname = unquote(qs.get("surname", [""])[0]).strip()
            if surname and surname not in state.autoscan_list:
                state.autoscan_list.insert(0, surname)
                persistence.save_autoscan()
            self._json({"ok": True, "list": state.autoscan_list})

        elif path == "/autoscan-add-bottom":
            surname = unquote(qs.get("surname", [""])[0]).strip()
            if surname and surname not in state.autoscan_list:
                state.autoscan_list.append(surname)
                persistence.save_autoscan()
            self._json({"ok": True, "list": state.autoscan_list})

        elif path == "/autoscan-remove":
            surname = unquote(qs.get("surname", [""])[0]).strip()
            if surname in state.autoscan_list:
                state.autoscan_list.remove(surname)
                persistence.save_autoscan()
            self._json({"ok": True, "list": state.autoscan_list})

        elif path == "/autoscan-start":
            if not state.is_searching and state.autoscan_list:
                state.autoscan_running = True
                state.autoscan_results = []
                for surname in list(state.autoscan_list):
                    state.search_queue.append(("autoscan", surname))
            self._json({"ok": True})

        elif path == "/autoscan-stop":
            state.autoscan_running = False
            state.stop_flag = True
            state.search_queue = [(t, s) for t, s in state.search_queue if t != "autoscan"]
            self._json({"ok": True})

        elif path == "/autoscan-results":
            self._json({
                "results": state.autoscan_results,
                "running": state.autoscan_running,
            })

        else:
            self.send_response(404)
            self.end_headers()

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


def start_server() -> None:
    """Launch the HTTP server on HOST:PORT in a daemon thread."""
    server = HTTPServer((HOST, PORT), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    print(f"🌐 Server running on http://{HOST}:{PORT}")
    return server
