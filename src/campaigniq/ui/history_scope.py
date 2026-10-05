"""Filter history by event dates while preserving saved positions and row identity."""
from datetime import datetime


def scope_history(entries, lifecycle_rows, start, end, *, all_history=False):
    if all_history:
        return list(entries), list(lifecycle_rows)
    return ([row for row in entries if start <= row["occurred_at"][:7] <= end],
            [row for row in lifecycle_rows if start <= row["Date"].strftime("%Y-%m") <= end])


def history_coverage(entries, lifecycle_rows):
    dates = [datetime.fromisoformat(row["occurred_at"]).date() for row in entries]
    dates.extend(row["Date"] for row in lifecycle_rows)
    if not dates:
        return "No retained position activity in this history range."
    return f"Displayed activity: {min(dates):%b %d, %Y} – {max(dates):%b %d, %Y}."
