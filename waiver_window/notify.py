"""Run reporting: a log line per attempt, plus one optional push summary."""

from __future__ import annotations

import logging
from pathlib import Path

import requests

log = logging.getLogger(__name__)


def setup_logging(verbose: bool = False) -> None:
    Path("logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler("logs/waiver-window.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def push(topic: str, title: str, body: str) -> None:
    """Send a summary via ntfy.sh. Silently degrades to log-only if unset."""
    if not topic:
        log.info("Summary (no push configured): %s — %s", title, body)
        return
    try:
        requests.post(
            f"https://ntfy.sh/{topic}",
            data=body.encode("utf-8"),
            headers={"Title": title},
            timeout=10,
        )
    except requests.RequestException as exc:
        log.warning("Could not send push notification: %s", exc)
