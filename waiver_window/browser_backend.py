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

    # ------------------------------------------------------------------ util

    def _url(self, template_key: str, **kwargs: str) -> str:
        return self.sel[template_key].format(**kwargs)

    def _rows(self) -> list:
        return self.page.query_selector_all(self.sel["player_row"])

    def _find_player_row(self, name: str):
        """Return (row, player_id) for an exact name match, or (None, None)."""
        target = _normalise(name)
        for row in self._rows():
            link = row.query_selector(self.sel["player_name_cell"])
            if not link:
                continue
            if _normalise(link.inner_text()) != target:
                continue
            match = PLAYER_ID_RE.search(link.get_attribute("href") or "")
            if match:
                return row, match.group(1)
        return None, None

    def _read_status(self, row) -> str:
        """Map Yahoo's on-page status label onto the API's vocabulary.

        Anything unrecognised returns '' — which the free-agent gate treats as
        "not confirmed free", so an unfamiliar label fails closed.
        """
        cell = row.query_selector(self.sel["player_status_cell"])
        text = (cell.inner_text().strip() if cell else "").upper()
        for label, status in self.sel["status_text_map"].items():
            if label.upper() in text:
                return status
        # A team abbreviation in the status column means somebody owns them.
        if text and text.isalpha() and len(text) <= 4:
            return "team"
        return ""

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
            _normalise(el.inner_text())
            for el in self.page.query_selector_all(self.sel["roster_player_link"])
            if el.inner_text().strip()
        }

        targets: list[Target] = []
        for pick in league_picks:
            if _normalise(pick.drop_player) not in roster_names:
                log.error(
                    "%s: %r is not on your roster in %s — skipping.",
                    pick, pick.drop_player, league.alias,
                )
                continue

            self.page.goto(
                self._url("players_page", league_id=league.league_id,
                          query=pick.add_player.replace(" ", "+")),
                wait_until="domcontentloaded",
            )
            row, player_id = self._find_player_row(pick.add_player)
            if not player_id:
                log.error("%s: no exact match for %r on Yahoo's player page.",
                          pick, pick.add_player)
                continue

            # The drop is identified by name on the add page, not by id here.
            targets.append(Target(pick, add_ref=player_id, drop_ref=pick.drop_player))
            log.info("Ready: %s  (player id %s)", pick, player_id)

        return targets

    def ownership(self, league: League, target: Target) -> str:
        self.page.goto(
            self._url("players_page", league_id=league.league_id,
                      query=target.pick.add_player.replace(" ", "+")),
            wait_until="domcontentloaded",
        )
        row, _ = self._find_player_row(target.pick.add_player)
        if row is None:
            return ""
        return self._read_status(row)

    def add_drop(self, league: League, target: Target, *, dry_run: bool) -> Outcome:
        status = self.ownership(league, target)
        # Same gate as the API path. Raises WaiverGuard on anything but a
        # confirmed free agent, including an unreadable status.
        transactions.assert_free_agent(status, target.pick.add_player)

        if dry_run:
            log.info(
                "[dry-run] would add %s (id %s) and drop %s in %s",
                target.pick.add_player, target.add_ref,
                target.pick.drop_player, league.alias,
            )
            return Outcome(ok=True, detail="dry run — nothing submitted")

        self.page.goto(
            self._url("add_page", league_id=league.league_id, player_id=target.add_ref),
            wait_until="domcontentloaded",
        )

        drop_choice = self._select_drop(target.pick.drop_player)
        if not drop_choice:
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

        body = self.page.inner_text("body")[:600].lower()
        if any(m in body for m in ("no longer", "not available", "already been added")):
            return Outcome(ok=False, detail="lost the race — " + body[:200], lost_race=True)
        if "error" in body or "unable" in body:
            return Outcome(ok=False, detail=body[:300])

        return Outcome(ok=True, detail="submitted via browser")

    def _select_drop(self, drop_player: str):
        """Pick the radio/checkbox matching the named drop player."""
        target = _normalise(drop_player)
        for row in self._rows():
            if target not in _normalise(row.inner_text()):
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
