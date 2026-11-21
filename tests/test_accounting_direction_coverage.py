from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from campaigniq.ui.campaign_interactions import accounting_close_sides


def record(day):
    return SimpleNamespace(record=SimpleNamespace(closed_date=date(2026, day, 27)))


def test_exact_journal_sides_do_not_read_missing_archives():
    with patch('campaigniq.ui.roll_evidence.closed_side_from_journal', side_effect=['Short', 'Long']), patch('campaigniq.ui.campaign_interactions.read_closing_trades') as read:
        assert accounting_close_sides('/missing', [record(9), record(10)], []) == (('Short', 'Long'), False)
        read.assert_not_called()


def test_fallback_reads_only_dates_without_exact_journal_evidence():
    rows = [record(9), record(10)]
    with patch('campaigniq.ui.roll_evidence.closed_side_from_journal', side_effect=['Short', 'Unavailable']), patch('campaigniq.ui.campaign_interactions.read_closing_trades', return_value=(('trade',), False)) as read, patch('campaigniq.ui.campaign_interactions.closed_position_side', return_value='Long') as fallback:
        assert accounting_close_sides('/archive', rows, []) == (('Short', 'Long'), False)
        read.assert_called_once_with('/archive', [date(2026, 10, 27)])
        fallback.assert_called_once_with(rows[1].record, ('trade',))


def test_missing_fallback_preserves_journal_side_and_warns_for_unresolved_record():
    with patch('campaigniq.ui.roll_evidence.closed_side_from_journal', side_effect=['Short', 'Unavailable']):
        assert accounting_close_sides('/missing', [record(9), record(10)], []) == (('Short', 'Unavailable'), True)


def test_readable_archive_without_matching_trade_keeps_side_unavailable():
    with patch('campaigniq.ui.roll_evidence.closed_side_from_journal', return_value='Unavailable'), patch('campaigniq.ui.campaign_interactions.read_closing_trades', return_value=((), False)):
        assert accounting_close_sides('/archive', [record(10)], []) == (('Unavailable',), False)


def test_no_closes_does_not_read_archives():
    with patch('campaigniq.ui.campaign_interactions.read_closing_trades') as read:
        assert accounting_close_sides('/missing', [], []) == ((), False)
        read.assert_not_called()
