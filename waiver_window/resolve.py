"""Turning human names into Yahoo keys.

The pick list is written in plain English — "league_A", "Jaylen Wright". Yahoo
wants "461.l.49039.t.7" and "461.p.31883". Everything that bridges the two
lives here, and all of it runs *before* the waiver window opens so a failure
surfaces early rather than during the race.
"""

from __future__ import annotations

import json
import logging
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from . import parse
from .client import YahooClient

log = logging.getLogger(__name__)


class ResolutionError(RuntimeError):
    """A name in the pick list could not be mapped to a Yahoo key."""


@dataclass(frozen=True)
class League:
    alias: str
    league_key: str
    team_key: str


def _normalise(name: str) -> str:
    """Fold case, accents and punctuation so 'A.J. Brown' matches 'AJ Brown'."""
    decomposed = unicodedata.normalize("NFKD", name)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return "".join(c for c in stripped.lower() if c.isalnum())


def load_league_map(path: str = "leagues.json") -> dict[str, dict]:
    file = Path(path)
    if not file.exists():
        raise ResolutionError(
            f"No league map at {path}. Copy leagues.example.json and fill in "
            "your league_id, team_id and waiver_type."
        )
    return json.loads(file.read_text(encoding="utf-8"))


def discover_game_id(client: YahooClient, game: str = "nfl") -> str:
    """Ask Yahoo for the current season's game id rather than hardcoding it."""
    payload = client.leagues(game=game)
    for league in parse.leagues(payload):
        key = league.get("league_key", "")
        if ".l." in key:
            return key.split(".l.")[0]
    raise ResolutionError(
        "Could not determine the current NFL game id from Yahoo. "
        "Is the authenticated account in at least one league this season?"
    )


def build_leagues(client: YahooClient, league_map: dict[str, dict]) -> dict[str, League]:
    """Expand the alias map into fully-qualified Yahoo keys."""
    game_id = discover_game_id(client)
    log.info("Current NFL game id: %s", game_id)

    leagues: dict[str, League] = {}
    for alias, entry in league_map.items():
        league_id = str(entry.get("league_id") or "").strip()
        team_id = str(entry.get("team_id") or "").strip()
        if not league_id:
            raise ResolutionError(f"League '{alias}' has no league_id.")
        if not team_id:
            raise ResolutionError(
                f"League '{alias}' has no team_id. Open your team in that league "
                "and take the last number from the URL."
            )

        league_key = f"{game_id}.l.{league_id}"
        leagues[alias] = League(
            alias=alias,
            league_key=league_key,
            team_key=f"{league_key}.t.{team_id}",
        )
        log.info("%s -> %s (team %s)", alias, league_key, team_id)
    return leagues


def resolve_player(client: YahooClient, league_key: str, name: str) -> str:
    """Find one player's key by name within a league. Exact match required."""
    payload = client.search_player(league_key, name)
    candidates = parse.players(payload)

    if not candidates:
        raise ResolutionError(f"Yahoo returned no players matching {name!r}.")

    target = _normalise(name)
    exact = [p for p in candidates if _normalise(parse.full_name(p)) == target]

    if len(exact) == 1:
        return exact[0]["player_key"]

    if len(exact) > 1:
        raise ResolutionError(
            f"{name!r} matched {len(exact)} players in this league. "
            "Use the full name as Yahoo spells it."
        )

    names = ", ".join(sorted({parse.full_name(p) for p in candidates})[:8])
    raise ResolutionError(
        f"No exact match for {name!r}. Yahoo offered: {names or '(nothing usable)'}"
    )


def roster_player_keys(client: YahooClient, team_key: str) -> dict[str, str]:
    """Map normalised name -> player_key for everyone currently rostered."""
    payload = client.roster(team_key)
    return {
        _normalise(parse.full_name(p)): p["player_key"]
        for p in parse.players(payload)
        if parse.full_name(p)
    }
