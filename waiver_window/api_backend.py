"""Backend built on the official Yahoo Fantasy Sports API."""

from __future__ import annotations

import logging

from . import parse, picks as picklist, resolve, transactions
from .backend import Backend, Outcome, Target
from .client import YahooClient
from .resolve import League

log = logging.getLogger(__name__)


class ApiBackend(Backend):
    name = "api"

    def __init__(self, client: YahooClient):
        self.client = client

    def prepare(self, league: League, league_picks: list[picklist.Pick]) -> list[Target]:
        roster = resolve.roster_player_keys(self.client, league.team_key)
        targets: list[Target] = []

        for pick in league_picks:
            drop_key = roster.get(resolve._normalise(pick.drop_player))
            if not drop_key:
                log.error(
                    "%s: %r is not on your roster in %s — skipping.",
                    pick, pick.drop_player, league.alias,
                )
                continue
            try:
                add_key = resolve.resolve_player(self.client, league.league_key, pick.add_player)
            except resolve.ResolutionError as exc:
                log.error("%s: %s", pick, exc)
                continue

            targets.append(Target(pick, add_key, drop_key))
            log.info("Ready: %s  (+%s / -%s)", pick, add_key, drop_key)

        return targets

    def ownership(self, league: League, target: Target) -> str:
        payload = self.client.player_status(league.league_key, [target.add_ref])
        for player in parse.players(payload):
            if player.get("player_key") == target.add_ref:
                return parse.ownership_type(player)
        return ""

    def add_drop(self, league: League, target: Target, *, dry_run: bool) -> Outcome:
        status = self.ownership(league, target)
        result = transactions.submit_add_drop(
            self.client,
            league.league_key,
            league.team_key,
            target.add_ref,
            target.drop_ref,
            ownership_type=status,
            player_name=target.pick.add_player,
            dry_run=dry_run,
        )
        return Outcome(ok=result.ok, detail=result.detail, lost_race=result.lost_race)
