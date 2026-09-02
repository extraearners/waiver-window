"""Runtime configuration, loaded from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

API_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"
AUTH_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"

# Read-only runs need only fspt-r. Submitting a transaction needs fspt-w,
# which requires an approved read/write grant on the Yahoo application.
SCOPE_READ = "fspt-r"
SCOPE_WRITE = "fspt-w"


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


@dataclass
class Config:
    client_id: str = field(default_factory=lambda: os.getenv("YAHOO_CLIENT_ID", ""))
    client_secret: str = field(default_factory=lambda: os.getenv("YAHOO_CLIENT_SECRET", ""))
    redirect_uri: str = field(default_factory=lambda: os.getenv("YAHOO_REDIRECT_URI", "oob"))

    picks_source: str = field(default_factory=lambda: os.getenv("PICKS_SOURCE", "picks.csv"))
    league_keys: list[str] = field(
        default_factory=lambda: [
            k.strip() for k in os.getenv("LEAGUE_KEYS", "").split(",") if k.strip()
        ]
    )

    # Two cadences. Before the clear time there is nothing to catch, so polling
    # slowly avoids hundreds of pointless page loads; once the window opens the
    # tight interval is what wins the race.
    poll_interval_ms: int = field(default_factory=lambda: _int("POLL_INTERVAL_MS", 400))
    poll_slow_ms: int = field(default_factory=lambda: _int("POLL_SLOW_MS", 15000))
    poll_start_lead_min: int = field(default_factory=lambda: _int("POLL_START_LEAD_MIN", 2))
    max_requests_per_run: int = field(default_factory=lambda: _int("MAX_REQUESTS_PER_RUN", 4000))

    ntfy_topic: str = field(default_factory=lambda: os.getenv("NTFY_TOPIC", ""))

    token_path: str = "tokens.json"

    def validate(self) -> None:
        missing = [
            name
            for name, value in (
                ("YAHOO_CLIENT_ID", self.client_id),
                ("YAHOO_CLIENT_SECRET", self.client_secret),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                "Missing required configuration: "
                + ", ".join(missing)
                + ". Copy .env.example to .env and fill it in."
            )
