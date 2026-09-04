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
        self.render_timeout_ms = int(self.sel.get("render_timeout_ms", 8000))
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

    def _goto_player_page(self, url: str) -> bool:
        """Load a player list page and wait for its table to populate.

        Two things to get right here, both learned the hard way.

        Yahoo fills the table after domcontentloaded, so indexing immediately
        can read an empty DOM. But the table also contains player links that
        are present and not visible — off in the horizontally scrolled stats
        columns — and Playwright's wait_for_selector defaults to waiting for
        *visibility*. Waiting that way times out on a page that is in fact
        fully loaded. So the wait is on attachment to the DOM, which is what
        actually matters for reading it.

        The return value is advisory, for logging only. The caller indexes the
        page either way and judges it by what it finds, so a wrong answer here
        can no longer empty out a scan.
        """
        self.page.goto(url, wait_until="domcontentloaded")
        if "login" in self.page.url or "signin" in self.page.url:
            raise BrowserUnavailable(
                "The saved Yahoo session is no longer valid — Yahoo redirected "
                "to sign-in. Re-run: python -m waiver_window.login"
            )
        try:
            self.page.wait_for_selector(
                self.sel["player_name_cell"],
                state="attached",
                timeout=self.render_timeout_ms,
            )
            return True
        except Exception:  # noqa: BLE001 - a genuinely empty page also lands here
            log.debug("No player links attached within %dms: %s",
                      self.render_timeout_ms, url)
            return False

    def _scan(self, league: League, page_key: str) -> dict[str, dict]:
        """Walk the paginated player list and merge every page into one index."""
        merged: dict[str, dict] = {}
        page_size = int(self.sel.get("page_size", 25))
        for page_no in range(int(self.sel.get("max_scan_pages", 24))):
            url = self.sel[page_key].format(
                league_id=league.league_id, offset=page_no * page_size
            )
            rendered = self._goto_player_page(url)
            # Indexed regardless of the wait's verdict: the page is judged by
            # the players actually found on it, never by a timeout.
            page_index = self._index_page(league)
            if page_index and not rendered:
                log.debug("Wait reported nothing, but %d players were present.",
                          len(page_index))
            if page_no + 1 == int(self.sel.get("max_scan_pages", 24)) and page_index:
                log.warning(
                    "Scan stopped on the %d-page cap with the list still "
                    "returning players — it was truncated, not exhausted. A "
                    "target further down would not have been found. Raise "
                    "max_scan_pages in selectors.json.",
                    page_no + 1,
                )
            if not page_index:
                if page_no == 0:
                    # An empty first page is far more likely to be a page that
                    # did not render than a league with no players in it.
                    log.warning(
                        "%s returned no players on its first page (rendered=%s). "
                        "Treating the scan as empty.", page_key, rendered,
                    )
                break  # ran off the end of the list
            merged.update(page_index)
            # The table carries spacer and nested rows, so a page is judged by
            # the players actually found on it, never by its row count.
            if len(page_index) < page_size:
                break
        log.debug("Scanned %s: %d players over %d page(s)",
                  page_key, len(merged), page_no + 1)
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
        """Resolve every name to the ids Yahoo's own form posts.

        Runs before the window opens, so a misspelled name or a drop that is
        not on the roster surfaces while the sheet can still be corrected.
        """
        if not self._warm:
            self.warm_up(league)

        # Scanned once, purely to turn add_player names into Yahoo player ids.
        known = self._scan(league, "all_players_page")

        targets: list[Target] = []
        for pick in league_picks:
            entry = known.get(_normalise(pick.add_player))
            if entry is None:
                log.error(
                    "%s: %r did not appear in %s's player list. Check the "
                    "spelling against Yahoo.", pick, pick.add_player, league.alias,
                )
                continue

            provisional = Target(pick, add_ref=entry["player_id"], drop_ref="")
            try:
                self._load_acquisition_page(league, provisional)
            except BrowserUnavailable:
                raise
            except Exception as exc:  # noqa: BLE001
                log.error("%s: could not load the acquisition page: %s", pick, exc)
                continue

            if not pick.needs_drop:
                # Filling an open slot. No drop is resolved, and none will be
                # chosen later either.
                targets.append(Target(pick, add_ref=entry["player_id"], drop_ref=""))
                log.info("Ready: %s  (apid %s, no drop, currently %s)",
                         pick, entry["player_id"], entry["status"] or "unknown")
                continue

            # The dpid list on this page *is* the roster, taken from the form
            # that would post the drop — so it needs no separate roster lookup.
            drops = self.dpid_map()
            drop_id = drops.get(_normalise(pick.drop_player))
            if not drop_id:
                log.error(
                    "%s: %r is not among the droppable players in %s. On the "
                    "roster? Yahoo offered: %s",
                    pick, pick.drop_player, league.alias,
                    ", ".join(sorted(drops)[:6]) or "(none)",
                )
                continue

            targets.append(
                Target(pick, add_ref=entry["player_id"], drop_ref=drop_id)
            )
            log.info(
                "Ready: %s  (apid %s, dpid %s, currently %s)",
                pick, entry["player_id"], drop_id, entry["status"] or "unknown",
            )

        return targets

    def refresh_available(self, league: League) -> None:
        """No-op during the race.

        Scanning the paginated list costs ~24 requests, which is far too slow
        for a first-come window. Status is read from each target's own
        acquisition page instead — see `ownership`.
        """

    def _load_acquisition_page(self, league: League, target: Target) -> str:
        """GET the acquisition page for one target. Returns its lowercased text."""
        url = self.sel["addplayer_page"].format(
            league_id=league.league_id, player_id=target.add_ref
        )
        self.page.goto(url, wait_until="domcontentloaded")
        if "login" in self.page.url or "signin" in self.page.url:
            raise BrowserUnavailable(
                "The saved Yahoo session is no longer valid — Yahoo redirected to "
                "sign-in. Re-run: python -m waiver_window.login"
            )
        # Wait for the acquisition form so the page is judged on its real
        # content. A page that never renders it falls through to classify_page,
        # which returns '' and is refused.
        try:
            # Attachment, not visibility — same reason as the list pages.
            self.page.wait_for_selector(
                self.sel["addplayer_form"],
                state="attached",
                timeout=self.render_timeout_ms,
            )
        except Exception:  # noqa: BLE001
            # Not fatal, and not a decision. classify_page reads the body
            # either way, and an unrecognised page is refused by the gate.
            log.debug("Acquisition form did not attach for %s", url)
        return clean(self.page.inner_text("body")).lower()

    def classify_page(self, body: str) -> str:
        """What kind of acquisition page is this?

        Yahoo names it in the heading: a player still on waivers gets
        'Claim Player From Waivers', a free agent gets an add page. Anything
        unrecognised returns '', which the gate refuses.
        """
        for marker in self.sel["claim_page_markers"]:
            if marker in body:
                return "waivers"
        for marker in self.sel["add_page_markers"]:
            if marker in body:
                return "freeagents"
        return ""

    def ownership(self, league: League, target: Target) -> str:
        """Current status for one target, straight from its acquisition page.

        One request per target per pass, rather than re-walking the whole
        paginated pool. The page is also the thing we are about to act on, so
        this reads the same state the submit would.
        """
        try:
            body = self._load_acquisition_page(league, target)
        except BrowserUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - a failed poll is not fatal
            log.debug("Poll failed for %s: %s", target.pick.add_player, exc)
            return ""

        kind = self.classify_page(body)
        if kind:
            self._last_body = body
            return kind

        if "is on another team" in body or "already owned" in body:
            return "team"
        return ""

    def add_drop(self, league: League, target: Target, *, dry_run: bool) -> Outcome:
        # First gate: the status the caller polled.
        status = self.ownership(league, target)
        transactions.assert_free_agent(status, target.pick.add_player)

        # Second gate, independent of the first: Yahoo names the page it served.
        # `ownership` has just loaded it, so this reads the very page a submit
        # would post from — not a status observed somewhere else earlier.
        body = getattr(self, "_last_body", "")
        kind = self.classify_page(body)
        if kind != "freeagents":
            raise transactions.WaiverGuard(
                f"Yahoo served a {kind or 'unrecognised'} page for "
                f"{target.pick.add_player} — refusing. This tool never submits a "
                "waiver claim."
            )

        drop_control = None
        if target.pick.needs_drop:
            drop_control = self._find_drop_control(
                target.drop_ref, target.pick.drop_player
            )
            if drop_control is None:
                return Outcome(
                    ok=False,
                    detail=(
                        f"No drop control for {target.pick.drop_player!r} on the "
                        "page. Not submitting a partial move."
                    ),
                )

        if dry_run:
            if target.pick.needs_drop:
                log.info(
                    "[dry-run] would add %s (apid %s) and drop %s (dpid %s) in %s",
                    target.pick.add_player, target.add_ref,
                    target.pick.drop_player, target.drop_ref, league.alias,
                )
            else:
                log.info(
                    "[dry-run] would add %s (apid %s) into an open slot in %s, "
                    "selecting no drop",
                    target.pick.add_player, target.add_ref, league.alias,
                )
            return Outcome(ok=True, detail="dry run — nothing submitted")

        if drop_control is not None:
            self._activate(drop_control)
        else:
            # No drop was named, so none is chosen. If Yahoo turns out to
            # require one the submit fails and is reported — the tool does not
            # pick a player to cut in order to get the add through.
            log.info("No drop named for %s; submitting against an open slot.",
                     target.pick.add_player)

        submit = self._find_submit()
        if submit is not None:
            submit.click()
        elif not self._submit_form():
            return Outcome(
                ok=False,
                detail="No submit control and no acquisition form to post.",
            )
        self.page.wait_for_load_state("domcontentloaded")

        confirm = self.page.query_selector(self.sel["confirm_add"])
        if confirm:
            confirm.click()
            self.page.wait_for_load_state("domcontentloaded")

        return self._read_result(target)

    def _find_drop_control(self, drop_id: str, drop_name: str):
        """The control that marks a player for dropping.

        Yahoo puts a scripted trigger button over a hidden input, so the
        trigger is what gets clicked — that lets Yahoo's own handler set the
        form state instead of this code imitating it. The hidden input is only
        a fallback for a layout without the trigger.

        Note the data attribute's value carries a trailing space, hence the
        prefix match.
        """
        if drop_id:
            trigger = self.page.query_selector(
                self.sel["drop_trigger"].format(dpid=drop_id)
            )
            if trigger is not None:
                return trigger
            control = self.page.query_selector(
                f"{self.sel['drop_control']}[value='{drop_id}']"
            )
            if control is not None:
                log.warning("No trigger for dpid %s; using the hidden input.", drop_id)
                return control
            log.warning("dpid %s not on the page; falling back to a name match.", drop_id)

        target = _normalise(drop_name)
        for control in self.page.query_selector_all(self.sel["drop_control"]):
            row = control.evaluate_handle("e => e.closest('tr')").as_element()
            if row and target in _normalise(clean(row.inner_text())):
                return control
        return None

    @staticmethod
    def _activate(control) -> None:
        """Mark a drop. Clicks a trigger button, checks a bare input."""
        tag = control.evaluate("e => e.tagName.toLowerCase()")
        if tag == "input":
            control.check()
        else:
            control.click()

    def _submit_form(self) -> bool:
        """Post the acquisition form.

        The page has no submit control — Yahoo posts from script — but the
        form already holds its own hidden stage, crumb and apid, so submitting
        it directly sends exactly what Yahoo would have sent.
        """
        form = self.page.query_selector(self.sel["addplayer_form"])
        if form is None:
            return False
        form.evaluate("f => f.submit()")
        return True

    def _find_submit(self):
        """The submit control, scoped to the acquisition form.

        The page carries dozens of unrelated icon buttons in its header, so a
        page-wide search finds the wrong thing. Search inside the form only.
        """
        form = self.page.query_selector(self.sel["addplayer_form"])
        scope = form or self.page
        for selector in (
            "input[type='submit']",
            "button[type='submit']",
            "button:not([type])",
        ):
            control = scope.query_selector(selector)
            if control is not None:
                return control
        return None

    def dpid_map(self) -> dict[str, str]:
        """Normalised name -> dpid for every droppable player on the page."""
        mapping: dict[str, str] = {}
        for control in self.page.query_selector_all(self.sel["drop_control"]):
            value = control.get_attribute("value") or ""
            row = control.evaluate_handle("e => e.closest('tr')").as_element()
            if not row or not value:
                continue
            # The row's text leads with an em dash and a tab, so the player
            # link is the only clean source for the name.
            link = row.query_selector(self.sel["player_name_cell"])
            name = clean(link.inner_text()) if link else ""
            if name:
                mapping[_normalise(name)] = value
        return mapping

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

        full = ("must drop", "roster is full", "too many players", "exceed the roster")
        if any(marker in body for marker in full):
            return Outcome(
                ok=False,
                detail=(
                    "Yahoo requires a drop for this add, and the pick named none. "
                    "Add a drop_player for this row — the tool will not choose "
                    "one. Page said: " + body[:150]
                ),
            )
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
