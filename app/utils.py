"""Miscellaneous utility helpers."""
from __future__ import annotations

import re

IDENTIFIER_RE = re.compile(r"^[A-Z][A-Z0-9_#$]*$")


def quote_identifier(name: str) -> str:
    """Quote an Oracle identifier safely.

    Oracle normalizes unquoted identifiers to uppercase. To keep behavior predictable we
    require callers to provide uppercase names consisting of standard identifier
    characters. The resulting identifier is wrapped in double quotes so that reserved
    words remain valid.
    """

    normalized = name.upper()
    if not IDENTIFIER_RE.match(normalized):
        raise ValueError(f"Invalid Oracle identifier: {name!r}")
    return f'"{normalized}"'


def qualify_identifier(schema: str, table: str) -> str:
    """Return a fully qualified identifier for `schema.table`."""

    return f"{quote_identifier(schema)}.{quote_identifier(table)}"


__all__ = ["quote_identifier", "qualify_identifier"]
