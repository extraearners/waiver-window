"""Entry point: prepare, poll, add.

Waiver claims lock at 11:59pm PT Tuesday and process shortly after. Players
nobody claimed convert to free agents and go first-come. This runs across that
boundary, watches the targets in the pick list, and adds the first one that
turns free.

    python -m waiver_window.run --backend browser --dry-run
    python -m waiver_window.run --backend api
"""

from __future__ import annotations

import argparse
import logging
import time

from . import picks as picklist, resolve
from .backend import Backend, Target
from .config import Config
from .notify import push, setup_logging
from .transactions import WaiverGuard

log = logging.getLogger(__name__)


def race_for_league(
    backend: Backend,
    league: resolve.League,
    targets: list[Target],
    interval_s: float,
    dry_run: bool,
) -> list[str]:
    """Poll a league's targets and add the first one that turns free.

    Every target is checked each pass rather than blocking on the first, so a
    lower-priority player who frees up early is not missed while waiting on one
    that never clears.
    """
    if not targets:
        return [f"{league.alias}: nothing ready to attempt"]

    deadline = time.time() + max(t.pick.max_wait_min for t in targets) * 60
    remaining = list(targets)
    results: list[str] = []

    while remaining and time.time() < deadline:
        # The browser backend reads the whole available list in one request and
        # answers every target from it, so refresh once per pass, not per target.
        refresh = getattr(backend, "refresh_available", None)
        if refresh is not None:
            refresh(league)

        for target in list(remaining):
            status = backend.ownership(league, target)

            if status == "waivers" or not status:
                continue  # not ours to take, and unknown fails closed

            if status == "team":
                log.info("%s was claimed by another manager — dropping this target.",
                         target.pick.add_player)
                results.append(f"CLAIMED {target} — someone won them on waivers")
                remaining.remove(target)
                continue

            log.info("%s is a free agent — submitting.", target.pick.add_player)
            try:
                outcome = backend.add_drop(league, target, dry_run=dry_run)
            except WaiverGuard as exc:
                results.append(f"BLOCKED {target} — {exc}")
                remaining.remove(target)
                continue

            if outcome.ok:
                results.append(f"WON {target}")
                return results  # one add/drop per league per run

            results.append(f"LOST {target} — {outcome.detail}")
            remaining.remove(target)
            if not outcome.lost_race:
                return results  # a real fault, stop touching this league

        time.sleep(interval_s)

    results.extend(
        f"TIMEOUT {t} — never became a free agent in the window" for t in remaining
    )
    return results


def build_backend(kind: str, config: Config, headless: bool) -> tuple[Backend, dict]:
    """Return the chosen backend and the league map it should use."""
    league_map = resolve.load_league_map()

    if kind == "api":
        from .api_backend import ApiBackend
        from .client import YahooClient

        config.validate()
        client = YahooClient(config)
        client.refresh()
        if not client.has_write_scope():
            log.warning(
                "Token scope is %r — no fspt-w. Reads work; submitting will be "
                "refused. Use --backend browser, or --dry-run.", client.scopes,
            )
        return ApiBackend(client), resolve.build_leagues(client, league_map)

    from .browser_backend import BrowserBackend

    return BrowserBackend(headless=headless), resolve.build_leagues_offline(league_map)


def run(backend_kind: str, dry_run: bool, headless: bool) -> int:
    config = Config()

    all_picks = picklist.load(config.picks_source)
    grouped = picklist.by_league(all_picks)
    log.info("Loaded %d picks across %d leagues.", len(all_picks), len(grouped))

    backend, leagues = build_backend(backend_kind, config, headless)
    log.info("Backend: %s", backend.name)

    unknown = set(grouped) - set(leagues)
    if unknown:
        log.error("Pick list names leagues absent from leagues.json: %s", unknown)
        backend.close()
        return 1

    results: list[str] = []
    try:
        for alias, league_picks in grouped.items():
            league = leagues[alias]
            targets = backend.prepare(league, league_picks)
            results.extend(
                race_for_league(
                    backend, league, targets, config.poll_interval_ms / 1000, dry_run
                )
            )
    finally:
        backend.close()

    summary = "\n".join(results) or "nothing attempted"
    log.info("Run complete.\n%s", summary)
    push(config.ntfy_topic, "Waiver Window", summary)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="waiver-window")
    parser.add_argument(
        "--backend", choices=("api", "browser"), default="browser",
        help="how to reach Yahoo (default: browser, which needs no API approval)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="run the full resolve and poll path, log the action, submit nothing",
    )
    parser.add_argument(
        "--headed", action="store_true",
        help="browser backend: show the browser window instead of running headless",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    setup_logging(args.verbose)
    try:
        return run(args.backend, args.dry_run, headless=not args.headed)
    except (picklist.PickListError, resolve.ResolutionError, RuntimeError) as exc:
        log.error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
