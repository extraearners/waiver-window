"""Calibrate selectors against the live Yahoo page.

Yahoo's markup is not a contract, so rather than guessing at selectors this
reports what the page actually contains and lets `selectors.json` be corrected
from evidence.

    python -m waiver_window.probe                     # what current selectors match
    python -m waiver_window.probe --deep              # full cell structure of a few rows
    python -m waiver_window.probe --urls "Josh Allen" # which URL form actually filters

Read-only throughout: it clicks nothing, checks nothing, and submits nothing.
"""

from __future__ import annotations

import argparse
from .browser_backend import BrowserBackend, BrowserUnavailable, clean
from .resolve import build_leagues_offline, load_league_map

URL_VARIANTS = [
    "https://football.fantasysports.yahoo.com/f1/{lid}/players?status=A&pos=O&cut_type=9&stat1=S_PW_1&sort=AR&sdir=1",
    "https://football.fantasysports.yahoo.com/f1/{lid}/players?status=FA&pos=O&sort=AR",
    "https://football.fantasysports.yahoo.com/f1/{lid}/players?status=ALL&search={q}&pos=O",
    "https://football.fantasysports.yahoo.com/f1/{lid}/players?status=A&search={q}",
    "https://football.fantasysports.yahoo.com/f1/{lid}/players?searchName={q}&status=ALL",
    "https://football.fantasysports.yahoo.com/f1/{lid}/players?status=ALL&pos=O&stat1=S_PW_1&jsenabled=1&search={q}",
]


def show_current(backend: BrowserBackend, league, query: str) -> None:
    url = backend._url("players_page", league_id=league.league_id,
                       query=query.replace(" ", "+"))
    print(f"GET {url}\n")
    backend.page.goto(url, wait_until="domcontentloaded")
    rows = backend._rows()
    print(f"player_row matched {len(rows)} rows\n")
    for i, row in enumerate(rows[:8]):
        link = row.query_selector(backend.sel["player_name_cell"])
        cell = row.query_selector(backend.sel["player_status_cell"])
        name = clean(link.inner_text()) if link else "(no name cell)"
        raw = clean(cell.inner_text()) if cell else "(no status cell)"
        print(f"  [{i}] {name!r:26} status={raw!r:12} -> {backend._read_status(row)!r}")


def show_deep(backend: BrowserBackend, league, query: str, rows_to_dump: int) -> None:
    """Dump every cell of the first few rows, so the real columns are visible."""
    url = backend._url("players_page", league_id=league.league_id,
                       query=query.replace(" ", "+"))
    backend.page.goto(url, wait_until="domcontentloaded")

    rows = backend._rows()
    print(f"Dumping {min(rows_to_dump, len(rows))} of {len(rows)} rows.\n")

    for i, row in enumerate(rows[:rows_to_dump]):
        print(f"=== row {i} " + "=" * 56)
        for j, cell in enumerate(row.query_selector_all("td")):
            text = clean(cell.inner_text())
            classes = cell.get_attribute("class") or ""
            print(f"  td[{j}] class={classes!r}")
            print(f"         text={text[:110]!r}")
            for link in cell.query_selector_all("a")[:3]:
                href = link.get_attribute("href") or ""
                label = clean(link.inner_text())
                if label or "players" in href or "addplayer" in href:
                    print(f"         a: {label[:34]!r} -> {href[:88]}")
            for inp in cell.query_selector_all("input")[:3]:
                print(f"         input: type={inp.get_attribute('type')!r} "
                      f"name={inp.get_attribute('name')!r} "
                      f"value={(inp.get_attribute('value') or '')[:40]!r}")
        print()


def show_urls(backend: BrowserBackend, league, query: str) -> None:
    """Try several player-page URL forms and report which one actually filters."""
    print(f"Testing URL forms with query {query!r}. A working search should")
    print("return a handful of rows, not the whole ranked list.\n")

    for template in URL_VARIANTS:
        url = template.format(lid=league.league_id, q=query.replace(" ", "+"))
        try:
            backend.page.goto(url, wait_until="domcontentloaded", timeout=25000)
            rows = backend._rows()
            names = []
            for row in rows[:4]:
                link = row.query_selector(backend.sel["player_name_cell"])
                if link:
                    names.append(clean(link.inner_text()))
            marker = "  <-- filtered" if 0 < len(rows) <= 15 else ""
            print(f"  {len(rows):>4} rows{marker}")
            print(f"       {url[:120]}")
            print(f"       first: {names}\n")
        except Exception as exc:  # noqa: BLE001 - probe reports, never fails hard
            print(f"  ERROR {type(exc).__name__}: {str(exc)[:90]}")
            print(f"       {url[:120]}\n")


def main() -> int:
    parser = argparse.ArgumentParser(prog="waiver-window probe")
    parser.add_argument("name", nargs="?", default="Justin Jefferson")
    parser.add_argument("--deep", action="store_true",
                        help="dump the full cell structure of the first rows")
    parser.add_argument("--urls", action="store_true",
                        help="try several player-page URL forms and report row counts")
    parser.add_argument("--rows", type=int, default=3, help="rows to dump with --deep")
    args = parser.parse_args()

    try:
        backend = BrowserBackend(headless=True)
    except BrowserUnavailable as exc:
        print(exc)
        return 1

    try:
        league = next(iter(build_leagues_offline(load_league_map()).values()))
        backend.page.goto(
            backend._url("roster_page", league_id=league.league_id,
                         team_id=league.team_id),
            wait_until="domcontentloaded",
        )
        if "login" in backend.page.url or "signin" in backend.page.url:
            print("Redirected to sign-in — the saved session has expired.")
            print("Re-run: python -m waiver_window.login")
            return 1

        if args.urls:
            show_urls(backend, league, args.name)
        elif args.deep:
            show_deep(backend, league, args.name, args.rows)
        else:
            show_current(backend, league, args.name)
        return 0
    finally:
        backend.close()


if __name__ == "__main__":
    raise SystemExit(main())
