"""Calibrate selectors against the live Yahoo page.

    python -m waiver_window.probe "Jaylen Wright"

Yahoo's markup is not a contract. This loads the player search page with the
saved session, reports what the configured selectors actually matched, and
dumps the status labels it saw — so `selectors.json` can be corrected without
guessing. Read-only: it clicks nothing and submits nothing.
"""

from __future__ import annotations

import sys

from .browser_backend import BrowserBackend, BrowserUnavailable
from .resolve import build_leagues_offline, load_league_map


def main() -> int:
    query = sys.argv[1] if len(sys.argv) > 1 else "Justin Jefferson"

    try:
        backend = BrowserBackend(headless=True)
    except BrowserUnavailable as exc:
        print(exc)
        return 1

    try:
        leagues = build_leagues_offline(load_league_map())
        league = next(iter(leagues.values()))

        url = backend._url(
            "players_page", league_id=league.league_id, query=query.replace(" ", "+")
        )
        print(f"GET {url}\n")
        backend.page.goto(url, wait_until="domcontentloaded")

        if "login" in backend.page.url or "signin" in backend.page.url:
            print("Redirected to sign-in — the saved session has expired.")
            print("Re-run: python -m waiver_window.login")
            return 1

        rows = backend._rows()
        print(f"player_row matched {len(rows)} rows\n")

        for i, row in enumerate(rows[:12]):
            link = row.query_selector(backend.sel["player_name_cell"])
            cell = row.query_selector(backend.sel["player_status_cell"])
            name = link.inner_text().strip() if link else "(no name cell)"
            href = (link.get_attribute("href") or "") if link else ""
            status_raw = cell.inner_text().strip() if cell else "(no status cell)"
            print(f"  [{i}] {name!r:28} status={status_raw!r:14} -> "
                  f"{backend._read_status(row)!r}")
            if href:
                print(f"       href: {href}")

        print("\nIf names or statuses look wrong, edit selectors.json and re-run.")
        return 0
    finally:
        backend.close()


if __name__ == "__main__":
    raise SystemExit(main())
