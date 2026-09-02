"""One-time OAuth2 consent.

Run once to authorise the application against your own Yahoo account:

    python -m waiver_window.auth

Opens the Yahoo consent page, takes the code it gives back, and stores the
resulting tokens locally in tokens.json (mode 0600). Nothing here is
transmitted anywhere except to Yahoo's token endpoint.
"""

from __future__ import annotations

import json
import time
import webbrowser
from pathlib import Path
from urllib.parse import urlencode

import requests

from .config import AUTH_URL, SCOPE_WRITE, TOKEN_URL, Config


def main() -> int:
    config = Config()
    config.validate()

    query = urlencode(
        {
            "client_id": config.client_id,
            "redirect_uri": config.redirect_uri,
            "response_type": "code",
            "scope": SCOPE_WRITE,
            "language": "en-us",
        }
    )
    url = f"{AUTH_URL}?{query}"

    print("Opening the Yahoo consent page.")
    print("If it does not open, paste this into a browser:\n")
    print(url, "\n")
    webbrowser.open(url)

    code = input("Paste the authorisation code from Yahoo: ").strip()
    if not code:
        print("No code entered — nothing was saved.")
        return 1

    response = requests.post(
        TOKEN_URL,
        data={
            "client_id": config.client_id,
            "client_secret": config.client_secret,
            "redirect_uri": config.redirect_uri,
            "code": code,
            "grant_type": "authorization_code",
        },
        timeout=20,
    )
    response.raise_for_status()

    tokens = response.json()
    tokens["obtained_at"] = time.time()

    path = Path(config.token_path)
    path.write_text(json.dumps(tokens, indent=2), encoding="utf-8")
    path.chmod(0o600)

    granted = tokens.get("scope", "")
    print(f"\nTokens saved to {path} (mode 0600).")
    print(f"Granted scope: {granted or 'unreported'}")
    if "fspt-w" not in granted:
        print(
            "\nNote: this token is read-only. Transactions cannot be submitted "
            "until Yahoo grants read/write access to the application."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
