"""Regression tests for explicitly documented Schwab SPXW expirations."""
from datetime import date
from decimal import Decimal

import pytest

from campaigniq.domain.option_type import OptionType
from campaigniq.importers.schwab.option_expiration_reader import read_option_expiration_events


STATEMENT = '''
03/27 Other         Expired Long    SPXW         CALL S & P 500 INDEX        $5790                   10.0000
       Activity                      03/26/2025   EXP 03/26/25
                                     5790.00 C
       Other         Expired Short   SPXW         CALL S & P 500 INDEX        $5800                 (10.0000)
       Activity                      03/26/2025   EXP 03/26/25
                                     5800.00 C
       Other         Expired Short   SPXW         CALL S & P 500 INDEX        $5730                 (10.0000)
       Activity                      03/27/2025   EXP 03/27/25
                                     5730.00 C
03/28 Other          Expired Long      SPXW          CALL S & P 500 INDEX        $5740                   10.0000
      Activity                         03/27/2025    EXP 03/27/25
                                       5740.00 C
       Other         Expired Long      SPXW          PUT S & P 500 INDEX         $5675                   10.0000
       Activity                        03/27/2025    EXP 03/27/25
                                       5675.00 P
       Other         Expired Short     SPXW          PUT S & P 500 INDEX         $5685                 (10.0000)
       Activity                        03/27/2025    EXP 03/27/25
                                       5685.00 P
'''


def test_six_spx_offsets_from_march_statement():
    events = read_option_expiration_events(STATEMENT.splitlines(), start=date(2025, 3, 1), end=date(2025, 3, 31))
    actual = {(e.changes[0].instrument.underlying,
               e.changes[0].instrument.expiration,
               e.changes[0].instrument.strike,
               e.changes[0].instrument.option_type,
               e.changes[0].quantity) for e in events}
    expected = {
        ('SPX', date(2025, 3, 26), Decimal('5790'), OptionType.CALL, Decimal('10')),
        ('SPX', date(2025, 3, 26), Decimal('5800'), OptionType.CALL, Decimal('-10')),
        ('SPX', date(2025, 3, 27), Decimal('5730'), OptionType.CALL, Decimal('-10')),
        ('SPX', date(2025, 3, 27), Decimal('5740'), OptionType.CALL, Decimal('10')),
        ('SPX', date(2025, 3, 27), Decimal('5675'), OptionType.PUT, Decimal('10')),
        ('SPX', date(2025, 3, 27), Decimal('5685'), OptionType.PUT, Decimal('-10')),
    }
    assert actual == expected
    assert len(events) == 6
    assert events[0].occurred_at.date() == date(2025, 3, 27)
    assert events[-1].occurred_at.date() == date(2025, 3, 28)
    assert not read_option_expiration_events(STATEMENT.splitlines(), start=date(2025, 4, 1), end=date(2025, 4, 30))


def test_fails_closed_on_contract_mismatch():
    corrupted = STATEMENT.replace('5790.00 C', '5791.00 C')
    with pytest.raises(ValueError, match='disagrees'):
        read_option_expiration_events(corrupted.splitlines(), start=date(2025, 3, 1), end=date(2025, 3, 31))


def test_trades_do_not_create_expirations():
    assert not read_option_expiration_events([
        '03/27 Sale Short Sale SPXW CALL S & P 500 INDEX $5790 (10.0000)',
        '       Activity 03/26/2025 EXP 03/26/25',
        '       5790.00 C',
    ], start=date(2025, 3, 1), end=date(2025, 3, 31))


def test_andrew_ppg_two_line_expiration_from_september_2026_statement():
    from pathlib import Path
    # Representative literal lines from Poppler pdftotext -layout of Schwab PDF.
    lines = '''09/21 Other           Expired Long          PPG 09/18/2026 CALL PPG INDS INC                 $105        EXP             1.0000
      Activity                              105.00 C       09/18/26
09/24 Sale            Short Sale            PPG 11/20/2026 CALL PPG INDS INC                 $100        EXP            (1.0000)'''.splitlines()
    events = read_option_expiration_events(lines, start=date(2026, 9, 1), end=date(2026, 9, 30))
    assert len(events) == 1
    event = events[0]
    assert event.occurred_at.date() == date(2026, 9, 21)
    change = event.changes[0]
    assert change.instrument.underlying == 'PPG'
    assert change.instrument.expiration == date(2026, 9, 18)
    assert change.instrument.strike == Decimal('105')
    assert change.instrument.option_type == OptionType.CALL
    assert change.quantity == Decimal('1')


def test_unrecognized_expiration_fails_closed():
    with pytest.raises(ValueError, match='Unrecognized Schwab option expiration'):
        read_option_expiration_events(['09/21 Other Expired Long PPG UNKNOWN'], start=date(2026, 9, 1), end=date(2026, 9, 30))


def test_new_layout_rejects_mismatched_expiration():
    lines = ['09/21 Other Expired Long PPG 09/18/2026 CALL PPG INDS INC $105 EXP 1.0000',
             '      Activity 105.00 C 09/19/26']
    with pytest.raises(ValueError, match='disagrees'):
        read_option_expiration_events(lines, start=date(2026, 9, 1), end=date(2026, 9, 30))
