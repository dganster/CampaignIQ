"""Validate report membership without rewriting broker settlement dates.

Schwab's selected report period can include first-next-business-day assignment
or settlement rows. The importer deliberately retains those rows. Saved results
must preserve them while rejecting dates unrelated to the selected report.
"""
from datetime import date
from campaigniq.domain.realized_lot_attributor import RealizedLotAttributor


def validate_broker_report_date(closed_date,*,period_start,period_end):
    if any(type(value) is not date for value in (closed_date,period_start,period_end)) or period_end<period_start:
        raise ValueError('Valid broker report dates required')
    if period_start<=closed_date<=period_end:return
    # Reuse the import/attribution calendar, including weekends and market
    # holidays. This is neither a hard-coded date exception nor a date rewrite.
    if closed_date==RealizedLotAttributor._next_business_day(period_end):return
    raise ValueError('Realized record falls outside the selected report and settlement boundary')
