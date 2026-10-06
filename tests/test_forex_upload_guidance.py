from datetime import date
import pytest
from campaigniq.ui.forex_upload_guidance import forex_upload_guidance


@pytest.mark.parametrize("start,end,selection_end,printed_start", [
    (date(2026,9,1), date(2026,9,30), "October 1, 2026", "August 31, 2026"),
    (date(2026,1,1), date(2026,1,31), "February 1, 2026", "December 31, 2025"),
    (date(2026,12,1), date(2026,12,31), "January 1, 2027", "November 30, 2026"),
    (date(2028,2,1), date(2028,2,29), "March 1, 2028", "January 31, 2028"),
])
def test_month_specific_export_and_printed_dates(start,end,selection_end,printed_start):
    text = forex_upload_guidance(start,end)
    selection, printed = text.split("Before uploading,")
    assert selection_end in selection
    assert printed_start in printed
    assert f"{end:%B} {end.day}, {end.year}" in printed


def test_printed_period_is_required_and_other_documents_keep_their_ranges():
    text = forex_upload_guidance(date(2026,9,1), date(2026,9,30))
    assert "adjust the export dates until it matches" in text
    assert "applies only to the FOREX Transaction Report" in text
