"""
main.py — Entry point. Wires all modules together and runs the main loop.

Start the app:
    python main.py

Make sure you have a .env file with:
    UNITEUS_EMAIL=your@email.com
    UNITEUS_PASSWORD=yourpassword
"""

import threading

from playwright.sync_api import sync_playwright

import state
import persistence
import server as http_server
from config import PORT
from browser.auth    import do_login
from browser.scanner import run_scan


def main():
    # ── Load persisted data ───────────────────────────────
    persistence.load_cache()
    persistence.load_history()

    # ── Start HTTP server ─────────────────────────────────
    http_server.start_server()
    print(f"🌐 Open port {PORT} in your browser (or Codespaces Ports tab)\n")

    # ── Launch browser and run job loop ───────────────────
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page    = context.new_page()

        do_login(page, state.credentials["email"], state.credentials["password"])

        while True:
            if state.search_queue:
                task, surname = state.search_queue.pop(0)

                if task == "scan":
                    page = run_scan(page, surname)

                elif task == "relogin":
                    persistence.delete_session()
                    do_login(
                        page,
                        state.credentials["email"],
                        state.credentials["password"],
                    )
            else:
                threading.Event().wait(0.5)


if __name__ == "__main__":
    main()
