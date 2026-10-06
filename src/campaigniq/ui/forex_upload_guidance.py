"""Distinguish Thinkorswim date selection from the printed report period."""
from datetime import date


def _display(day):
    return f"{day:%B} {day.day}, {day.year}"


def forex_upload_guidance(period_start, period_end):
    next_month = period_end + date.resolution
    printed_start = period_start - date.resolution
    return (
        "In Thinkorswim, select "
        f"{_display(period_start)} through {_display(next_month)} "
        "to generate the FOREX Transaction Report. Before uploading, check "
        "the printed report dates: CampaignIQ requires "
        f"{_display(printed_start)} through {_display(period_end)}. "
        "If the printed range differs, adjust the export dates until it matches. "
        "This guidance applies only to the FOREX Transaction Report; "
        "use the date ranges shown for the other documents."
    )
