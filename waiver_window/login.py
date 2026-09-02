"""One-time capture of a Yahoo browser session.

    python -m waiver_window.login

Opens a visible browser at Yahoo Fantasy and waits. You sign in yourself, by
hand, in that window. When you are done, press Enter here and the resulting
cookies are written to storage_state.json.

This script never asks for, receives, stores, or transmits a password. It reads
the cookies Yahoo issued to your own sign-in and nothing else. The saved file is
equivalent to being logged in — it is written 0600 and gitignored. Delete it to
revoke, or sign out of Yahoo everywhere.
"""

from __future__ import annotations

import json
from pathlib import Path

START_URL = "https://football.fantasysports.yahoo.com/"
OUTPUT = "storage_state.json"


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright is not installed. Run:")
        print("  pip install playwright && playwright install chromium")
        return 1

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(START_URL)

        print("\nA browser window is open.")
        print("Sign in to Yahoo there yourself. Nothing you type is visible to")
        print("this script — it only reads the cookies afterwards.\n")
        input("Press Enter once you can see your fantasy teams... ")

        state = context.storage_state()
        cookie_names = {c["name"] for c in state.get("cookies", [])}
        if not any(n in cookie_names for n in ("SSID", "T", "Y", "A1", "A3")):
            print("\nNo Yahoo session cookies found. Did the sign-in complete?")
            browser.close()
            return 1

        path = Path(OUTPUT)
        path.write_text(json.dumps(state), encoding="utf-8")
        path.chmod(0o600)
        browser.close()

    print(f"\nSession saved to {OUTPUT} (mode 0600, gitignored).")
    print("Treat it like a password. Delete the file to revoke it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
