"""Backend that drives the Yahoo Fantasy web UI in a real browser.

This exists because the official API's write scope is granted by manual review
and may not arrive in time, or at all. It uses a browser session the author
authenticated themselves — see `login.py`. No password is ever handled by this
code; it only reuses cookies that Yahoo issued to a human sign-in.

Yahoo's markup is not a contract and changes without notice, so every selector
lives in `selectors.json` and can be re-derived with `python -m
waiver_window.probe`. The logic here is deliberately dumb about layout: find a
row, read a status, click a link.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from . import picks as picklist, transactions
from .backend import Backend, Outcome, Target
from .resolve import League, ResolutionError, _normalise

log = logging.getLogger(__name__)

PLAYER_ID_RE = re.compile(r"/nfl/players/(\d+)")

# Yahoo draws its badges with a private-use icon font. Those codepoints carry
# no meaning here and would otherwise pollute any text we read.
PRIVATE_USE = re.compile("[\\ue000-\\uf8ff]")


def clean(text: str) -> str:
    return PRIVATE_USE.sub("", text or "").strip()


class BrowserUnavailable(RuntimeError):
    """Playwright or the saved session is missing."""


def load_selectors(path: str = "selectors.json") -> dict:
    file = Path(path)
    if not file.exists():
        raise BrowserUnavailable(f"No selector config at {path}.")
    return json.loads(file.read_text(encoding="utf-8"))


class BrowserBackend(Backend):
    name = "browser"

    def __init__(
        self,
        storage_state: str = "storage_state.json",
        selectors_path: str = "selectors.json",
        headless: bool = True,
    ):
        if not Path(storage_state).exists():
            raise BrowserUnavailable(
                f"No saved Yahoo session at {storage_state}. "
                "Run: python -m waiver_window.login"
            )
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover
            raise BrowserUnavailable(
                "Playwright is not installed. Run:\n"
                "  pip install playwright && playwright install chromium"
            ) from exc

        self.sel = load_selectors(selectors_path)
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=headless)
        self._context = self._browser.new_context(storage_state=storage_state)
        self.page = self._context.new_page()
        self._warm = False
        self._available: dict[str, dict] = {}

    # ------------------------------------------------------------------ util

    def _url(self, template_key: str, **kwargs: str) -> str:
        return self.sel[template_key].format(**kwargs)

    def _rows(self) -> list:
        return self.page.query_selector_all(self.sel["player_row"])

    def _read_status(self, row, league: League) -> str:
        """Ownership for one row: 'freeagents', 'waivers', 'team', or ''.

        Yahoo shows ownership in a dedicated column: an owned player carries a
        link to the owning fantasy team, everyone else carries a text label.
        The link is the reliable signal, so it is checked first. Anything not
        recognised returns '' — which the free-agent gate treats as unsafe.
        """
        cell = row.query_selector(self.sel["player_status_cell"])
        if cell is None:
            return ""

        owner = cell.query_selector(
            self.sel["owner_team_link"].format(league_id=league.league_id)
        )
        if owner is not None:
            return "team"

        text = clean(cell.inner_text()).upper()
        for label, status in self.sel["status_text_map"].items():
            if text.startswith(label.upper()):
                return status
        return ""

    def _index_page(self, league: League) -> dict[str, dict]:
        """Map normalised name -> {player_id, status, add_url} for the loaded page."""
        index: dict[str, dict] = {}
        for row in self._rows():
            link = row.query_selector(self.sel["player_name_cell"])
            if not link:
                continue
            name = clean(link.inner_text())
            if not name:
                continue
            match = PLAYER_ID_RE.search(link.get_attribute("href") or "")
            if not match:
                continue
            index[_normalise(name)] = {
                "name": name,
                "player_id": match.group(1),
                "status": self._read_status(row, league),
                "add_url": self._row_add_url(row),
            }
        return index

    def _row_add_url(self, row) -> str:
        """The row's own Add link.

        Yahoo signs action links with a per-session `crumb` token, so the URL
        has to be taken from the page rather than constructed. A row with no
        Add link simply has no add path — which is the case for anyone who is
        not currently addable.
        """
        for link in row.query_selector_all("a[href*='addplayer']"):
            href = link.get_attribute("href") or ""
            if "addplayerwatch" in href:
                continue  # that is the watch list, not an acquisition
            return href
        return ""

    def _scan(self, league: League, page_key: str) -> dict[str, dict]:
        """Walk the paginated player list and merge every page into one index."""
        merged: dict[str, dict] = {}
        page_size = int(self.sel.get("page_size", 120))
        for page_no in range(int(self.sel.get("max_scan_pages", 6))):
            url = self.sel[page_key].format(
                league_id=league.league_id, offset=page_no * page_size
            )
            self.page.goto(url, wait_until="domcontentloaded")
            if "login" in self.page.url or "signin" in self.page.url:
                raise BrowserUnavailable(
                    "The saved Yahoo session is no longer valid — Yahoo redirected "
                    "to sign-in. Re-run: python -m waiver_window.login"
                )
            page_index = self._index_page(league)
            if not page_index:
                break  # ran off the end of the list
            merged.update(page_index)
            if len(page_index) < page_size:
                break
        return merged

    def warm_up(self, league: League) -> None:
        """Load the league once before the window so the session is live."""
        self.page.goto(self._url("roster_page", league_id=league.league_id,
                                 team_id=league.team_id), wait_until="domcontentloaded")
        if "login" in self.page.url or "signin" in self.page.url:
            raise BrowserUnavailable(
                "The saved Yahoo session is no longer valid — Yahoo redirected to "
                "sign-in. Re-run: python -m waiver_window.login"
            )
        self._warm = True

    # --------------------------------------------------------------- backend

    def prepare(self, league: League, league_picks: list[picklist.Pick]) -> list[Target]:
        if not self._warm:
            self.warm_up(league)

        roster_names = {
            _normalise(clean(el.inner_text()))
            for el in self.page.query_selector_all(self.sel["player_name_cell"])
            if clean(el.inner_text())
        }

        # Scanned once, before the window opens, purely to catch a misspelled
        # name while there is still time to fix the sheet.
        known = self._scan(league, "all_players_page")

        targets: list[Target] = []
        for pick in league_picks:
            if _normalise(pick.drop_player) not in roster_names:
                log.error(
                    "%s: %r is not on your roster in %s — skipping.",
                    pick, pick.drop_player, league.alias,
                )
                continue

            entry = known.get(_normalise(pick.add_player))
            if entry is None:
                log.error(
                    "%s: %r did not appear anywhere in %s's player list. "
                    "Check the spelling against Yahoo.",
                    pick, pick.add_player, league.alias,
                )
                continue

            targets.append(
                Target(pick, add_ref=entry["player_id"], drop_ref=pick.drop_player)
            )
            log.info("Ready: %s  (player id %s, currently %s)",
                     pick, entry["player_id"], entry["status"] or "unknown")

        return targets

    def refresh_available(self, league: League) -> None:
        """One scan of the available list, serving every target in the league.

        Yahoo ignores search parameters on this page, so a per-player lookup is
        not possible. Scanning once per poll is also simply fewer requests than
        one lookup per target would have been.
        """
        self._available = self._scan(league, "available_page")

    def ownership(self, league: League, target: Target) -> str:
        entry = getattr(self, "_available", {}).get(_normalise(target.pick.add_player))
        if entry is None:
            # Absent from the available list means somebody holds them.
            return "team"
        return entry["status"]

    def add_drop(self, league: League, target: Target, *, dry_run: bool) -> Outcome:
        status = self.ownership(league, target)
        # Same gate as the API path. Raises WaiverGuard on anything but a
        # confirmed free agent, including an unreadable status.
        transactions.assert_free_agent(status, target.pick.add_player)

        entry = self._available.get(_normalise(target.pick.add_player), {})
        add_url = entry.get("add_url") or ""
        if not add_url:
            return Outcome(
                ok=False,
                detail=(
                    f"{target.pick.add_player} is listed free but the page offered "
                    "no Add link. Yahoo signs those links per session, so one "
                    "cannot be constructed — treating this as not addable."
                ),
            )

        if dry_run:
            log.info(
                "[dry-run] would follow the Add link for %s (id %s) and drop %s in %s",
                target.pick.add_player, target.add_ref,
                target.pick.drop_player, league.alias,
            )
            log.debug("[dry-run] add url: %s", add_url)
            return Outcome(ok=True, detail="dry run — nothing submitted")

        self.page.goto(self._absolute(add_url), wait_until="domcontentloaded")

        if not self._select_drop(target.pick.drop_player):
            return Outcome(
                ok=False,
                detail=f"Could not find a drop control for {target.pick.drop_player!r}.",
            )

        submit = self.page.query_selector(self.sel["submit_add"])
        if not submit:
            return Outcome(ok=False, detail="Could not find the submit control.")
        submit.click()
        self.page.wait_for_load_state("domcontentloaded")

        confirm = self.page.query_selector(self.sel["confirm_add"])
        if confirm:
            confirm.click()
            self.page.wait_for_load_state("domcontentloaded")

        return self._read_result(target)

    def _absolute(self, href: str) -> str:
        if href.startswith("http"):
            return href
        return "https://football.fantasysports.yahoo.com" + href

    def _read_result(self, target: Target) -> Outcome:
        body = clean(self.page.inner_text("body"))[:800].lower()

        lost = ("no longer", "not available", "already been added", "is owned")
        if any(marker in body for marker in lost):
            return Outcome(ok=False, detail="lost the race — " + body[:180],
                           lost_race=True)
        if any(marker in body for marker in ("error", "unable", "cannot")):
            return Outcome(ok=False, detail=body[:250])
        if target.pick.add_player.split()[-1].lower() in body:
            return Outcome(ok=True, detail="submitted via browser")
        return Outcome(
            ok=False,
            detail=(
                "Submitted, but the result page did not confirm the move. "
                "Check the roster by hand: " + body[:150]
            ),
        )

    def _select_drop(self, drop_player: str):
        """Pick the radio/checkbox matching the named drop player."""
        target = _normalise(drop_player)
        for row in self._rows():
            if target not in _normalise(clean(row.inner_text())):
                continue
            control = row.query_selector("input[type='radio'], input[type='checkbox']")
            if control:
                control.check()
                return control
        return None

    def close(self) -> None:
        for closer in (
            getattr(self, "_context", None),
            getattr(self, "_browser", None),
        ):
            try:
                closer and closer.close()
            except Exception:  # pragma: no cover - best effort teardown
                pass
        try:
            self._pw.stop()
        except Exception:  # pragma: no cover
            pass
