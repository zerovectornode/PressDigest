"""(edition, date) <-> opaque edition_id used in URLs, e.g. "delhi__2025-09-13"."""
from __future__ import annotations

_SEP = "__"


class InvalidEditionId(ValueError):
    pass


def make_edition_id(edition: str, date: str) -> str:
    return f"{edition}{_SEP}{date}"


def split_edition_id(edition_id: str) -> tuple[str, str]:
    if _SEP not in edition_id:
        raise InvalidEditionId(f"malformed edition_id: {edition_id!r}")
    edition, date = edition_id.split(_SEP, 1)
    return edition, date
