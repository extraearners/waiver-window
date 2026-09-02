"""Tolerant helpers for Yahoo's Fantasy JSON.

Yahoo returns deeply nested structures that mix dicts keyed by numeric
strings with lists of single-key dicts, and the exact shape varies by
endpoint. Chasing fixed indices into that is brittle, so everything here
walks the structure and collects what it needs by key name instead.
"""

from __future__ import annotations

from typing import Any, Iterator


def walk(node: Any) -> Iterator[dict]:
    """Yield every dict nested anywhere inside node, depth first."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from walk(item)


def collect_merged(payload: Any, marker: str) -> list[dict]:
    """Collect entities identified by `marker` (e.g. 'player_key').

    Yahoo splits one logical entity across several sibling dicts, so the
    fragments around each marker are merged into a single flat dict.
    """
    found: dict[str, dict] = {}

    for node in walk(payload):
        if marker not in node:
            continue
        key = node[marker]
        if not isinstance(key, str):
            continue
        found.setdefault(key, {}).update(
            {k: v for k, v in node.items() if not isinstance(v, (dict, list))}
        )

    # Second pass: attach the nested blocks (name, ownership) to their owner.
    for node in walk(payload):
        if marker not in node:
            continue
        key = node[marker]
        if not isinstance(key, str) or key not in found:
            continue
        for field in ("name", "ownership", "selected_position"):
            if isinstance(node.get(field), dict):
                found[key][field] = node[field]

    return list(found.values())


def _sibling_blocks(payload: Any, marker: str) -> dict[str, dict]:
    """Map entity key -> the flat fields of every sibling fragment."""
    merged: dict[str, dict] = {}
    for container in walk(payload):
        values = [v for v in container.values()] if isinstance(container, dict) else []
        for value in values:
            if not isinstance(value, list):
                continue
            key = None
            flat: dict = {}
            for fragment in value:
                for node in walk(fragment):
                    if marker in node and isinstance(node[marker], str):
                        key = node[marker]
                    flat.update(
                        {k: v for k, v in node.items() if not isinstance(v, (dict, list))}
                    )
                    for field in ("name", "ownership"):
                        if isinstance(node.get(field), dict):
                            flat[field] = node[field]
            if key:
                merged.setdefault(key, {}).update(flat)
    return merged


def players(payload: Any) -> list[dict]:
    """Every player in a response, flattened."""
    merged = _sibling_blocks(payload, "player_key")
    for entry in collect_merged(payload, "player_key"):
        merged.setdefault(entry["player_key"], {}).update(entry)
    return list(merged.values())


def full_name(player: dict) -> str:
    name = player.get("name")
    if isinstance(name, dict):
        return name.get("full", "")
    return str(name or "")


def ownership_type(player: dict) -> str:
    """'freeagents', 'waivers', 'team', or '' when Yahoo did not say.

    Requires the endpoint to have been called with `out=ownership`.
    """
    ownership = player.get("ownership")
    if isinstance(ownership, dict):
        return str(ownership.get("ownership_type", ""))
    return ""


def teams(payload: Any) -> list[dict]:
    merged = _sibling_blocks(payload, "team_key")
    for entry in collect_merged(payload, "team_key"):
        merged.setdefault(entry["team_key"], {}).update(entry)
    return list(merged.values())


def leagues(payload: Any) -> list[dict]:
    merged = _sibling_blocks(payload, "league_key")
    for entry in collect_merged(payload, "league_key"):
        merged.setdefault(entry["league_key"], {}).update(entry)
    return list(merged.values())
