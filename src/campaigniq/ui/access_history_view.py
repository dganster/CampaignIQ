"""Readable access timestamps in the viewer's browser timezone."""

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def resolve_viewer_timezone(name):
    """Use a browser IANA zone, falling back explicitly to UTC."""
    if isinstance(name, str) and name:
        try:
            return ZoneInfo(name), False
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return ZoneInfo("UTC"), True


def local_access_rows(rows, zone):
    """Preserve chronological input order and identities without mutating records."""
    result = []
    for row in rows:
        value = row.get("Visited (UTC)")
        display = "Unavailable"
        try:
            stamp = datetime.fromisoformat(value)
            if stamp.tzinfo is not None:
                local = stamp.astimezone(zone)
                display = (
                    f"{local:%b} {local.day}, {local.year}, "
                    f"{local.hour % 12 or 12}:{local:%M:%S %p %Z}"
                )
        except (ValueError, TypeError, OverflowError):
            pass
        result.append({"Visited": display, **{
            key: value for key, value in row.items() if key != "Visited (UTC)"
        }})
    return result
