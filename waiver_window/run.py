"""Entry point: prepare, poll, add.

Waivers process shortly after the Tuesday 11:59pm PT deadline. Players nobody
claimed convert to free agents and become first-come. This runs across that
boundary, watches the targets in the pick list, and adds the first one that
turns free.

    python -m waiver_window.run --dry-run
    python -m waiver_window.run
"""

from __future__ import annotations

import argparse
import logging
import time

from . import parse
from . import picks as picklist
from . import resolve
from .client import RequestBudgetExceeded, YahooClient
from .config import Config
from .notify import push, setup_logging
from .transactions import Result, WaiverGuard, submit_add_drop

log = logging.getLogger(__name__)


class Target:
    """One pick, with its Yahoo keys resolved and verified."""

    def __init__(self, pick: picklist.Pick, add_key: str, drop_key: str):
        self.pick = pick
        self.add_key = add_key
        self.drop_key = drop_key

    def __str__(self) -> str:
        return str(self.pick)


def prepare(
    client: YahooClient, league: resolve.League, league_picks: list[picklist.Pick]
) -> list[Target]:
    """Resolve every name to a key before the window opens.

    A failure here is reported now, while there is still time to fix the sheet,
    rather than discovered mid-race.
    """
    roster = resolve.roster_player_keys(client, league.team_key)
    targets: list[Target] = []

    for pick in league_picks:
        drop_key = roster.get(resolve._normalise(pick.drop_player))
        if not drop_key:
            log.error(
                "%s: %r is not on your roster in %s — skipping this row.",
                pick,
                pick.drop_player,
                league.alias,
            )
            continue
        try:
            add_key = resolve.resolve_player(client, league.league_key, pick.add_player)
        except resolve.ResolutionError as exc:
            log.error("%s: %s", pick, exc)
            continue

        targets.append(Target(pick, add_key, drop_key))
        log.info("Ready: %s  (+%s / -%s)", pick, add_key, drop_key)

    return targets


def ownership_of(client: YahooClient, league_key: str, player_key: str) -> str:
    payload = client.player_status(league_key, [player_key])
    for player in parse.players(payload):
        if player.get("player_key") == player_key:
            return parse.ownership_type(player)
    return ""


def race_for_league(
    client: YahooClient,
    league: resolve.League,
    targets: list[Target],
    interval_s: float,
    dry_run: bool,
) -> list[str]:
    """Poll the league's targets and add the first one that turns free.

    Polls all targets in priority order each pass rather than blocking on the
    first, so a lower target that frees up early is not missed while waiting on
    one that never clears.
    """
    results: list[str] = []
    if not targets:
        return [f"{league.alias}: nothing ready to attempt"]

    deadline = time.time() + max(t.pick.max_wait_min for t in targets) * 60
    remaining = list(targets)

    while remaining and time.time() < deadline:
        for target in list(remaining):
            status = ownership_of(client, league.league_key, target.add_key)

            if status != "freeagents":
                continue

            log.info("%s is now a free agent — submitting.", target.pick.add_player)
            try:
                outcome = submit_add_drop(
                    client,
                    league.league_key,
                    league.team_key,
                    target.add_key,
                    target.drop_key,
                    ownership_type=status,
                    player_name=target.pick.add_player,
                    dry_run=dry_run,
                )
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

    for target in remaining:
        results.append(f"TIMEOUT {target} — never became a free agent in the window")
    return results


def run(dry_run: bool = False, league_map_path: str = "leagues.json") -> int:
    config = Config()
    config.validate()

    all_picks = picklist.load(config.picks_source)
    grouped = picklist.by_league(all_picks)
    log.info("Loaded %d picks across %d leagues.", len(all_picks), len(grouped))

    client = YahooClient(config)
    client.refresh()

    if not dry_run and not client.has_write_scope():
        log.error(
            "Token scope is %r — no fspt-w, so nothing can be submitted. "
            "Re-run with --dry-run, or use the browser backend.",
            client.scopes,
        )
        return 2

    leagues = resolve.build_leagues(client, resolve.load_league_map(league_map_path))

    unknown = set(grouped) - set(leagues)
    if unknown:
        log.error("Pick list names leagues absent from %s: %s", league_map_path, unknown)
        return 1

    results: list[str] = []
    for alias, league_picks in grouped.items():
        league = leagues[alias]
        targets = prepare(client, league, league_picks)
        results.extend(
            race_for_league(
                client, league, targets, config.poll_interval_ms / 1000, dry_run
            )
        )

    summary = "\n".join(results) or "nothing attempted"
    log.info("Run complete.\n%s", summary)
    push(config.ntfy_topic, "Waiver Window", summary)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="waiver-window")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="run the full resolve and poll path, log the transaction, submit nothing",
    )
    parser.add_argument("--leagues", default="leagues.json", help="path to the league map")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    setup_logging(args.verbose)
    try:
        return run(dry_run=args.dry_run, league_map_path=args.leagues)
    except (
        picklist.PickListError,
        resolve.ResolutionError,
        RequestBudgetExceeded,
        RuntimeError,
    ) as exc:
        log.error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
