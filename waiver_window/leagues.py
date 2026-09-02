"""List the league and team keys on the authenticated account.

    python -m waiver_window.leagues

Read-only. Run this once during setup to find the values for LEAGUE_KEYS.
"""

from __future__ import annotations

import json

from .client import YahooClient
from .config import Config


def main() -> int:
    config = Config()
    config.validate()

    client = YahooClient(config)
    client.refresh()

    payload = client.leagues(game="nfl")
    print(json.dumps(payload, indent=2))
    print(
        "\nLeague keys look like 461.l.123456. Copy the ones you want into "
        "LEAGUE_KEYS in .env, and map your pick-list aliases to them."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
