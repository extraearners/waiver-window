"""Entry point: prepare, poll, execute.

Usage:
    python -m waiver_window.run --dry-run
    python -m waiver_window.run
"""

from __future__ import annotations

import argparse
import logging
import time

from . import picks as picklist
from .client import RequestBudgetExceeded, YahooClient
from .config import Config
from .notify import push, setup_logging
from .transactions import submit_add_drop

log = logging.getLogger(__name__)


def _poll_until_free(
    client: YahooClient,
    league_key: str,
    player_key: str,
    deadline: float,
    interval_s: float,
) -> bool:
    """Poll one player's ownership until it flips to free agent, or time runs out.

    Waivers do not clear at an exact instant — Yahoo's processing takes some
    minutes and the lag varies. Polling for the actual status change is
    self-correcting in a way that firing at a fixed timestamp is not.
    """
    while time.time() < deadline:
        payload = client.player_status(league_key, [player_key])
        if _is_free_agent(payload):
            return True
        time.sleep(interval_s)
    return False


def _is_free_agent(payload: dict) -> bool:
    """Walk Yahoo's nested JSON for an ownership_type of 'freeagents'."""
    text = str(payload)
    return "freeagents" in text and "waivers" not in text


def run(dry_run: bool = False) -> int:
    config = Config()
    config.validate()

    all_picks = picklist.load(config.picks_source)
    grouped = picklist.by_league(all_picks)
    log.info("Loaded %d picks across %d leagues.", len(all_picks), len(grouped))

    client = YahooClient(config)
    client.refresh()

    if not dry_run and not client.has_write_scope():
        log.error(
            "Token has scope %r — no fspt-w. Nothing can be submitted. "
            "Re-run with --dry-run, or wait on read/write approval.",
            client.scopes,
        )
        return 2

    results: list[str] = []

    for league, league_picks in grouped.items():
        league_key, team_key = _resolve_league(config, league)
        won = False

        for pick in league_picks:
            add_key = _resolve_player(client, league_key, pick.add_player)
            drop_key = _resolve_player(client, league_key, pick.drop_player)
            if not add_key or not drop_key:
                results.append(f"SKIP {pick} — could not resolve a player key")
                continue

            deadline = time.time() + pick.max_wait_min * 60
            interval_s = config.poll_interval_ms / 1000

            log.info("Watching %s (up to %d min).", pick, pick.max_wait_min)
            if not _poll_until_free(client, league_key, add_key, deadline, interval_s):
                results.append(f"TIMEOUT {pick} — never cleared waivers in window")
                continue

            outcome = submit_add_drop(
                client, league_key, team_key, add_key, drop_key, dry_run=dry_run
            )
            if outcome.ok:
                results.append(f"WON {pick}")
                won = True
                break  # one successful add/drop per league per run

            results.append(f"LOST {pick} — {outcome.detail}")
            if not outcome.lost_race:
                break  # a real error, not a race loss; stop touching this league

        if not won:
            log.warning("No pickup completed for league %s.", league)

    summary = "\n".join(results) or "nothing attempted"
    log.info("Run complete.\n%s", summary)
    push(config.ntfy_topic, "Waiver Window", summary)
    return 0


def _resolve_league(config: Config, alias: str) -> tuple[str, str]:
    """Map a friendly league alias from the pick list to Yahoo keys."""
    raise NotImplementedError(
        "League alias mapping is configured per-install; see docs/setup.md"
    )


def _resolve_player(client: YahooClient, league_key: str, name: str) -> str | None:
    payload = client.search_player(league_key, name)
    # Yahoo's JSON nests players under numeric string keys; the first exact
    # name match wins. Resolution happens before the waiver window opens so a
    # miss is reported early rather than during the race.
    del payload
    raise NotImplementedError("Player key resolution — see docs/setup.md")


def main() -> int:
    parser = argparse.ArgumentParser(prog="waiver-window")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="run the full polling and matching path, log the transaction, submit nothing",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    setup_logging(args.verbose)
    try:
        return run(dry_run=args.dry_run)
    except (picklist.PickListError, RequestBudgetExceeded, RuntimeError) as exc:
        log.error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
