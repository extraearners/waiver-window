"""Submitting a pre-approved free-agent add/drop.

The only module in the project that writes anything to Yahoo.

**This tool never submits a waiver claim.** Waiver claims are made by hand,
before the Tuesday deadline. The purpose here is the window *after* waivers
process, when unclaimed players convert to free agents and go first-come.
Adding a free agent costs no waiver priority; submitting against a player who
is still on waivers would consume one. So a transaction is only ever built for
a player Yahoo has confirmed as `freeagents`, and `assert_free_agent` below is
the gate that enforces it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .client import YahooClient

log = logging.getLogger(__name__)

_ADD_BLOCK = """\
      <player>
        <player_key>{add_key}</player_key>
        <transaction_data>
          <type>add</type>
          <destination_team_key>{team_key}</destination_team_key>
        </transaction_data>
      </player>"""

_DROP_BLOCK = """
      <player>
        <player_key>{drop_key}</player_key>
        <transaction_data>
          <type>drop</type>
          <source_team_key>{team_key}</source_team_key>
        </transaction_data>
      </player>"""

_TEMPLATE = """\
<fantasy_content>
  <transaction>
    <type>{ttype}</type>
    <players>
{players}
    </players>
  </transaction>
</fantasy_content>
"""

# Rejections that mean "another manager was faster", as opposed to a real fault.
_RACE_MARKERS = (
    "not available",
    "already owned",
    "no longer a free agent",
    "is not a free agent",
    "player is on another team",
)

# Yahoo refusing the move because the roster has no open spot. Distinct from
# losing a race: the pick simply named no player to drop.
_ROSTER_FULL_MARKERS = (
    "must drop",
    "roster is full",
    "too many players",
    "exceed the roster",
)


class WaiverGuard(RuntimeError):
    """Refused to act on a player who is not a confirmed free agent."""


@dataclass
class Result:
    ok: bool
    detail: str

    @property
    def lost_race(self) -> bool:
        if self.ok:
            return False
        lowered = self.detail.lower()
        return any(marker in lowered for marker in _RACE_MARKERS)

    @property
    def roster_full(self) -> bool:
        """The add needed a drop and the pick named none."""
        if self.ok:
            return False
        lowered = self.detail.lower()
        return any(marker in lowered for marker in _ROSTER_FULL_MARKERS)


def assert_free_agent(ownership_type: str, player_name: str) -> None:
    """Refuse anything that would spend a waiver claim.

    An empty ownership_type means Yahoo did not tell us, which is treated the
    same as 'still on waivers' — the tool declines rather than guesses.
    """
    if ownership_type != "freeagents":
        raise WaiverGuard(
            f"{player_name} has ownership status {ownership_type or 'unknown'!r}, "
            "not 'freeagents'. Refusing to submit — this tool never spends a "
            "waiver claim."
        )


def build_payload(team_key: str, add_key: str, drop_key: str = "") -> str:
    """Build the transaction body.

    An empty drop_key means the roster already has an open slot, so the
    transaction is a plain `add` and names no player to cut.
    """
    players = _ADD_BLOCK.format(add_key=add_key, team_key=team_key)
    if drop_key:
        players += _DROP_BLOCK.format(drop_key=drop_key, team_key=team_key)
    return _TEMPLATE.format(
        ttype="add/drop" if drop_key else "add", players=players
    )


def submit_add_drop(
    client: YahooClient,
    league_key: str,
    team_key: str,
    add_key: str,
    drop_key: str = "",
    *,
    ownership_type: str,
    player_name: str = "",
    dry_run: bool = False,
) -> Result:
    """Add a confirmed free agent, dropping a named player to make room.

    Returns a Result rather than raising on rejection. In a first-come league a
    rejection is an expected outcome — another manager may simply have been
    faster — and the caller advances to the next priority.
    """
    assert_free_agent(ownership_type, player_name or add_key)

    body = build_payload(team_key, add_key, drop_key)

    if dry_run:
        log.info(
            "[dry-run] would POST to %s: add %s / drop %s", league_key, add_key, drop_key
        )
        return Result(ok=True, detail="dry run — nothing submitted")

    if not client.has_write_scope():
        return Result(
            ok=False,
            detail=(
                "Stored token lacks the fspt-w scope. Read/write access has not been "
                "granted for this application, so no transaction can be submitted."
            ),
        )

    response = client.request("POST", f"/league/{league_key}/transactions", data=body)

    if response.status_code in (200, 201):
        log.info("Accepted: +%s / -%s in %s", add_key, drop_key, league_key)
        return Result(ok=True, detail="accepted")

    detail = response.text[:400].replace("\n", " ").strip()
    log.warning("Rejected (%s): %s", response.status_code, detail)
    return Result(ok=False, detail=f"HTTP {response.status_code}: {detail}")
