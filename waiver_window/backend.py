"""The interface both execution paths implement.

Two ways to reach Yahoo:

* `api_backend` — the official Fantasy Sports API. Fast, stable, and the
  preferred path, but submitting a transaction needs the `fspt-w` scope, which
  Yahoo grants by manual review.
* `browser_backend` — a real browser session driving the Yahoo Fantasy web UI.
  Slower and more fragile, but available without waiting on that review.

Both are handed the same validated pick list and are subject to the same
free-agent gate, so the decision of *what* to do never depends on *how*.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from . import picks as picklist
from .resolve import League


@dataclass
class Target:
    """One pick, prepared for execution by a specific backend.

    `add_ref` and `drop_ref` are whatever that backend needs to act — Yahoo
    player keys for the API, player-page URLs for the browser.
    """

    pick: picklist.Pick
    add_ref: str
    drop_ref: str

    def __str__(self) -> str:
        return str(self.pick)


@dataclass
class Outcome:
    ok: bool
    detail: str
    lost_race: bool = False


class Backend(ABC):
    name: str = "backend"

    @abstractmethod
    def prepare(self, league: League, league_picks: list[picklist.Pick]) -> list[Target]:
        """Resolve names and verify the roster, before the window opens."""

    @abstractmethod
    def ownership(self, league: League, target: Target) -> str:
        """Current ownership: 'freeagents', 'waivers', 'team', or '' if unknown."""

    @abstractmethod
    def add_drop(self, league: League, target: Target, *, dry_run: bool) -> Outcome:
        """Add the target's player, dropping its paired player.

        Implementations must call `transactions.assert_free_agent` first. The
        caller checks ownership too, but the gate belongs in both places: the
        status can change between the check and the act.
        """

    def close(self) -> None:
        """Release any resources. Safe to call more than once."""
