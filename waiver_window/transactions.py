"""Submitting a pre-approved add/drop transaction.

This is the only module in the project that writes anything to Yahoo. It
accepts a fully-resolved pair of player keys that came from the author's own
pick list, and does nothing else.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .client import YahooClient

log = logging.getLogger(__name__)

ADD_DROP_TEMPLATE = """\
<fantasy_content>
  <transaction>
    <type>add/drop</type>
    <players>
      <player>
        <player_key>{add_key}</player_key>
        <transaction_data>
          <type>add</type>
          <destination_team_key>{team_key}</destination_team_key>
        </transaction_data>
      </player>
      <player>
        <player_key>{drop_key}</player_key>
        <transaction_data>
          <type>drop</type>
          <source_team_key>{team_key}</source_team_key>
        </transaction_data>
      </player>
    </players>
  </transaction>
</fantasy_content>
"""


@dataclass
class Result:
    ok: bool
    detail: str

    @property
    def lost_race(self) -> bool:
        """True when the transaction failed because someone else got there first."""
        markers = ("not available", "already owned", "no longer a free agent")
        return not self.ok and any(marker in self.detail.lower() for marker in markers)


def submit_add_drop(
    client: YahooClient,
    league_key: str,
    team_key: str,
    add_key: str,
    drop_key: str,
    *,
    dry_run: bool = False,
) -> Result:
    """Submit one add/drop. Returns a Result rather than raising on rejection.

    A rejection is an expected outcome here — in a first-come-first-served
    league another manager may simply have been faster. The caller advances to
    the next priority in that case.
    """
    body = ADD_DROP_TEMPLATE.format(add_key=add_key, drop_key=drop_key, team_key=team_key)

    if dry_run:
        log.info("[dry-run] would POST add %s / drop %s in %s", add_key, drop_key, league_key)
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
        log.info("Transaction accepted: +%s / -%s in %s", add_key, drop_key, league_key)
        return Result(ok=True, detail="accepted")

    detail = response.text[:400].replace("\n", " ").strip()
    log.warning("Transaction rejected (%s): %s", response.status_code, detail)
    return Result(ok=False, detail=f"HTTP {response.status_code}: {detail}")
