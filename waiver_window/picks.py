"""Loading and validating the pre-approved pick list.

The pick list is the only thing that decides what this tool does. It is
authored by hand before the waiver deadline. Nothing here infers, ranks, or
substitutes a player that the author did not write down.
"""

from __future__ import annotations

import csv
import io
import time
from dataclasses import dataclass
from pathlib import Path

import requests

REQUIRED_COLUMNS = {"league", "priority", "add_player"}
OPTIONAL_COLUMNS = {"drop_player", "max_wait_min"}


@dataclass(frozen=True)
class Pick:
    league: str
    priority: int
    add_player: str
    drop_player: str = ""
    max_wait_min: int = 10

    @property
    def needs_drop(self) -> bool:
        """False when the pickup is meant to fill an already-open roster slot."""
        return bool(self.drop_player)

    def __str__(self) -> str:
        tail = f" / -{self.drop_player}" if self.drop_player else " (open slot)"
        return f"[{self.league} #{self.priority}] +{self.add_player}{tail}"


class PickListError(ValueError):
    """The pick list is missing, malformed, or internally inconsistent."""


def _read_source(source: str, timeout: int = 20) -> str:
    """Read the pick list from a local path or a published-CSV URL."""
    if source.startswith(("http://", "https://")):
        # Google serves published sheets through a cache, and an edit made
        # shortly before the run can otherwise be missed. A unique parameter
        # and no-cache headers ask for the current version.
        separator = "&" if "?" in source else "?"
        url = f"{source}{separator}_cb={int(time.time())}"
        response = requests.get(
            url,
            timeout=timeout,
            headers={"Cache-Control": "no-cache", "Pragma": "no-cache"},
        )
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
                # Blank means "only if a slot is already open". The tool will
                # not choose a drop on its own in that case.
                drop_player=(row.get("drop_player") or "").strip(),
                max_wait_min=int(row.get("max_wait_min") or 10),
            )
        except (TypeError, ValueError) as exc:
            raise PickListError(f"Row {line_no} is malformed: {exc}") from exc

        if not pick.add_player:
            raise PickListError(f"Row {line_no}: add_player is required.")
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
