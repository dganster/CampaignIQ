"""Presentation-only month choices; identifiers and publication checks stay unchanged."""
from datetime import date
from calendar import month_name


def next_month(published_months, *, today=None):
    """Suggest the next month after published history, or current month when empty."""
    periods = [date.fromisoformat(value + '-01') for value in published_months]
    if not periods:
        current = today or date.today()
        return current.year, current.month
    latest = max(periods)
    if latest.year == 2200 and latest.month == 12:
        raise ValueError('No later month is supported')
    return latest.year + (latest.month == 12), latest.month % 12 + 1


def month_option(draft, published):
    return f'{month_name[draft.month]} {draft.year} · ' + ('Published' if published else 'Draft')
