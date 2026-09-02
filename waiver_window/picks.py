"""Loading and validating the pre-approved pick list.

The pick list is the only thing that decides what this tool does. It is
authored by hand before the waiver deadline. Nothing here infers, ranks, or
substitutes a player that the author did not write down.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path

import requests

REQUIRED_COLUMNS = {"league", "priority", "add_player", "drop_player"}


@dataclass(frozen=True)
class Pick:
    league: str
    priority: int
    add_player: str
    drop_player: str
    max_wait_min: int = 10

    def __str__(self) -> str:
        return f"[{self.league} #{self.priority}] +{self.add_player} / -{self.drop_player}"


class PickListError(ValueError):
    """The pick list is missing, malformed, or internally inconsistent."""


def _read_source(source: str, timeout: int = 20) -> str:
    """Read the pick list from a local path or a published-CSV URL."""
    if source.startswith(("http://", "https://")):
        response = requests.get(source, timeout=timeout)
        response.raise_for_status()
        return response.text

    path = Path(source)
    if not path.exists():
        raise PickListError(f"Pick list not found: {source}")
    return path.read_text(encoding="utf-8")


def load(source: str) -> list[Pick]:
    """Parse the pick list and return it ordered by league, then priority."""
    raw = _read_source(source)
    reader = csv.DictReader(io.StringIO(raw))

    header = set(reader.fieldnames or [])
    missing = REQUIRED_COLUMNS - header
    if missing:
        raise PickListError(f"Pick list is missing columns: {', '.join(sorted(missing))}")

    picks: list[Pick] = []
    for line_no, row in enumerate(reader, start=2):
        if not any((row.get(column) or "").strip() for column in REQUIRED_COLUMNS):
            continue  # blank spacer row

        try:
            pick = Pick(
                league=row["league"].strip(),
                priority=int(row["priority"]),
                add_player=row["add_player"].strip(),
                drop_player=row["drop_player"].strip(),
                max_wait_min=int(row.get("max_wait_min") or 10),
            )
        except (TypeError, ValueError) as exc:
            raise PickListError(f"Row {line_no} is malformed: {exc}") from exc

        if not pick.add_player or not pick.drop_player:
            raise PickListError(
                f"Row {line_no}: both add_player and drop_player are required. "
                "Every pickup must name the player it replaces."
            )
        picks.append(pick)

    if not picks:
        raise PickListError(
            "Pick list is empty. Waiver Window does nothing without an explicit list."
        )

    _check_duplicate_priorities(picks)
    return sorted(picks, key=lambda p: (p.league, p.priority))


def _check_duplicate_priorities(picks: list[Pick]) -> None:
    seen: set[tuple[str, int]] = set()
    for pick in picks:
        key = (pick.league, pick.priority)
        if key in seen:
            raise PickListError(
                f"Duplicate priority {pick.priority} in league {pick.league}. "
                "Fallback order must be unambiguous."
            )
        seen.add(key)


def by_league(picks: list[Pick]) -> dict[str, list[Pick]]:
    grouped: dict[str, list[Pick]] = {}
    for pick in picks:
        grouped.setdefault(pick.league, []).append(pick)
    return grouped
