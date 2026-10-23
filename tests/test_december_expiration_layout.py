from datetime import date
from decimal import Decimal
import pytest
from campaigniq.importers.schwab.option_expiration_reader import read_option_expiration_events

BLOCKS = ['12/15 Other         Expired Long    VRT 12/12/2025 CALL VERTIV HLDGS CO           $175                     5.0000\n       Activity                      175.00 C       EXP 12/12/25', '12/22 Other         Expired Long    GOOG            PUT ALPHABET INC            $300                           5.0000\n       Activity                      12/19/2025      EXP 12/19/25\n                                     300.00 P', '12/22 Other         Expired Long    AAPL            PUT APPLE INC             $265         EXP                 5.0000\n       Activity                      12/19/2025      12/19/25\n                                     265.00 P', '12/22 Other         Expired Long    CMI 12/19/2025 PUT CUMMINS INC              $480        EXP                5.0000\n       Activity                      480.00 P       12/19/25', '12/22 Other         Expired Long    DE 12/19/2025   PUT DEERE & CO             $465         EXP                5.0000\n       Activity                      465.00 P        12/19/25', '12/22 Other         Expired Long    GE 12/19/2025   PUT GE AEROSPACE              $290                         5.0000\n       Activity                      290.00 P        EXP 12/19/25', '12/22 Other          Expired Long    JPM 12/19/2025 PUT JPMORGAN CHASE & CO $305                                5.0000\n      Activity                       305.00 P       EXP 12/19/25', '12/22 Other         Expired Long    MA 12/19/2025     PUT MASTERCARD INC             $545                      5.0000\n       Activity                      545.00 P          EXP 12/19/25']

def parse(lines):
    return read_option_expiration_events(lines, start=date(2025,12,1), end=date(2025,12,31))

@pytest.mark.parametrize('block,symbol,strike', list(zip(BLOCKS, ['VRT','GOOG','AAPL','CMI','DE','GE','JPM','MA'], ['175','300','265','480','465','290','305','545'])))
def test_december_literal_layout(block,symbol,strike):
    events=parse(block.splitlines())
    assert len(events)==1
    change=events[0].changes[0]
    assert change.instrument.underlying==symbol
    assert change.instrument.strike==Decimal(strike)
    assert change.instrument.expiration==date(2025,12,12 if symbol=='VRT' else 19)
    assert change.quantity==Decimal('5')
    assert events[0].occurred_at.date()==date(2025,12,15 if symbol=='VRT' else 22)

@pytest.mark.parametrize('block',BLOCKS)
def test_missing_contract_detail_rejected(block):
    lines=block.splitlines()
    lines[1]='Activity unreadable'
    with pytest.raises(ValueError): parse(lines)

@pytest.mark.parametrize('block',BLOCKS)
def test_conflicting_strike_rejected(block):
    lines=block.splitlines()
    import re
    lines[-1]=re.sub(r'(\d+\.00) ([CP])',r'9999.00 \2',lines[-1])
    with pytest.raises(ValueError,match='disagrees'): parse(lines)

def test_three_line_conflicting_expiration_rejected():
    with pytest.raises(ValueError,match='disagrees'):
        parse(BLOCKS[1].replace('EXP 12/19/25','EXP 12/20/25').splitlines())

def test_three_line_missing_exp_marker_rejected():
    with pytest.raises(ValueError): parse(BLOCKS[2].replace('EXP','').splitlines())
