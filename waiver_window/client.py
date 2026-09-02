"""Thin Yahoo Fantasy Sports API client.

Handles OAuth2 token refresh, request budgeting, and backoff. Every request
this tool makes goes through here so the per-run request cap is enforced in
one place.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import requests

from .config import API_BASE, TOKEN_URL, Config

log = logging.getLogger(__name__)


class RequestBudgetExceeded(RuntimeError):
    """The run hit its hard cap on total Yahoo API requests."""


class YahooClient:
    def __init__(self, config: Config):
        self.config = config
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "waiver-window/0.3 (personal use; 1 user)"
        self._requests_made = 0
        self._tokens = self._load_tokens()

    # ---------------------------------------------------------------- tokens

    def _load_tokens(self) -> dict[str, Any]:
        path = Path(self.config.token_path)
        if not path.exists():
            raise RuntimeError(
                f"No stored tokens at {path}. Run: python -m waiver_window.auth"
            )
        return json.loads(path.read_text(encoding="utf-8"))

    def _save_tokens(self, tokens: dict[str, Any]) -> None:
        path = Path(self.config.token_path)
        path.write_text(json.dumps(tokens, indent=2), encoding="utf-8")
        path.chmod(0o600)

    def refresh(self) -> None:
        """Exchange the refresh token for a fresh access token."""
        response = self.session.post(
            TOKEN_URL,
            data={
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "redirect_uri": self.config.redirect_uri,
                "refresh_token": self._tokens["refresh_token"],
                "grant_type": "refresh_token",
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        payload.setdefault("refresh_token", self._tokens["refresh_token"])
        payload["obtained_at"] = time.time()
        self._tokens = payload
        self._save_tokens(payload)
        log.info("Access token refreshed.")

    def _token_is_stale(self) -> bool:
        obtained = self._tokens.get("obtained_at", 0)
        lifetime = self._tokens.get("expires_in", 3600)
        return time.time() > obtained + lifetime - 300

    @property
    def scopes(self) -> str:
        return self._tokens.get("xoauth_yahoo_guid_scope", self._tokens.get("scope", ""))

    def has_write_scope(self) -> bool:
        return "fspt-w" in self.scopes

    # --------------------------------------------------------------- requests

    def _spend(self) -> None:
        self._requests_made += 1
        if self._requests_made > self.config.max_requests_per_run:
            raise RequestBudgetExceeded(
                f"Hit the per-run cap of {self.config.max_requests_per_run} requests."
            )

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        data: str | None = None,
        retries: int = 3,
    ) -> requests.Response:
        if self._token_is_stale():
            self.refresh()

        url = f"{API_BASE}{path}"
        params = {**(params or {}), "format": "json"}
        headers = {"Authorization": f"Bearer {self._tokens['access_token']}"}
        if data is not None:
            headers["Content-Type"] = "application/xml"

        backoff = 0.5
        for attempt in range(retries + 1):
            self._spend()
            response = self.session.request(
                method, url, params=params, data=data, headers=headers, timeout=15
            )

            if response.status_code == 401 and attempt == 0:
                self.refresh()
                headers["Authorization"] = f"Bearer {self._tokens['access_token']}"
                continue

            # 429 and 5xx are transient; back off rather than hammering Yahoo.
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == retries:
                    response.raise_for_status()
                log.warning(
                    "HTTP %s from Yahoo, backing off %.1fs", response.status_code, backoff
                )
                time.sleep(backoff)
                backoff *= 2
                continue

            return response

        return response  # pragma: no cover - loop always returns or raises

    def get(self, path: str, **kwargs: Any) -> dict[str, Any]:
        return self.request("GET", path, **kwargs).json()

    # ------------------------------------------------------------- endpoints

    def leagues(self, game: str = "nfl") -> dict[str, Any]:
        return self.get(f"/users;use_login=1/games;game_keys={game}/leagues")

    def league_settings(self, league_key: str) -> dict[str, Any]:
        return self.get(f"/league/{league_key}/settings")

    def roster(self, team_key: str) -> dict[str, Any]:
        return self.get(f"/team/{team_key}/roster")

    def player_status(self, league_key: str, player_keys: list[str]) -> dict[str, Any]:
        """Ownership status for specific players — the poll this tool runs on."""
        keys = ",".join(player_keys)
        return self.get(f"/league/{league_key}/players;player_keys={keys};out=ownership")

    def search_player(self, league_key: str, name: str) -> dict[str, Any]:
        return self.get(f"/league/{league_key}/players;search={requests.utils.quote(name)}")
