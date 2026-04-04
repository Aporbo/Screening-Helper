"""
main.py — Entry point. Wires all modules together and runs the main loop.
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
    persistence.load_notes()
    persistence.load_notepad()
    persistence.load_autoscan()

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

                elif task == "autoscan":
                    # Run scan and record results in autoscan_results
                    page = run_scan(page, surname)
                    # Capture result from the most recent history entry
                    if state.search_history:
                        h = state.search_history[0]
                        persistence.add_autoscan_result(
                            h["surname"], h["total"], h["families"],
                            h["enrolled"], h["failed"]
                        )
                    # If queue is now empty of autoscan tasks, mark done
                    remaining = [t for t, _ in state.search_queue if t == "autoscan"]
                    if not remaining:
                        state.autoscan_running = False

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
