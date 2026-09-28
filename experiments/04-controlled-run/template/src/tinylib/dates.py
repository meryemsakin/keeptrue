"""Date parsing for the formats found in our data files."""

from __future__ import annotations

from datetime import date


def parse_date(value: str) -> date | None:
    """Parse 'YYYY-MM-DD' or 'DD/MM/YYYY'. Returns None for anything invalid."""
    value = value.strip()
    try:
        if "-" in value:
            year, month, day = (int(part) for part in value.split("-"))
        else:
            day, month, year = (int(part) for part in value.split("/"))
    except ValueError:
        return None
    if not 1 <= month < 12:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None
