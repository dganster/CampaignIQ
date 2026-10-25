"""Schwab comma-formatted quantities must not silently omit realized trades."""
from datetime import date
from decimal import Decimal as D
from pathlib import Path
import pytest

from campaigniq.importers.schwab.realized_gain_loss_reader import read_realized_gain_loss_section


@pytest.mark.parametrize('quantity,expected',[
    ('5,000','5000'),('1,000','1000'),('12,345','12345'),
    ('1,234,567','1234567'),('5000','5000'),('500','500'),
])
@pytest.mark.parametrize('instrument',['NFLX','SPXW 05/15/2026 740.00 C'])
def test_valid_thousands_quantities_are_exact(instrument,quantity,expected):
    rows=read_realized_gain_loss_section([
        f'{instrument} 04/16/2026 {quantity} $107.24 FIFO $536,187.97 $534,533.19 +$1,654.78 +$1,654.78'])
    assert len(rows)==1
    assert rows[0].quantity==D(expected)
    assert rows[0].closed_date==date(2026,4,16)
    assert rows[0].proceeds==D('536187.97')
    assert rows[0].cost_basis==D('534533.19')
    assert rows[0].gain_loss==D('1654.78')


@pytest.mark.parametrize('quantity',['5,00','50,00','5,0000','1,23,456','5,,000','5.000','5,000.5'])
def test_invalid_grouping_is_not_coerced_into_a_different_quantity(quantity):
    assert read_realized_gain_loss_section([
        f'NFLX 04/16/2026 {quantity} $107.24 FIFO $536,187.97 $534,533.19 +$1,654.78'])==()


def test_april_nflx_omission_explains_entire_summary_difference():
    # Retained normalized April fixture already includes this stock sale.
    path=Path(__file__).parent/'data/schwab/april_realized_gain_loss.txt'
    lines=path.read_text().splitlines()
    original=read_realized_gain_loss_section(lines)
    rendered=[line.replace('04/16/2026 5000 ', '04/16/2026 5,000 ') for line in lines]
    reparsed=read_realized_gain_loss_section(rendered)
    assert reparsed==original
    nflx=[r for r in reparsed if getattr(r.instrument,'symbol',None)=='NFLX']
    assert len(nflx)==1 and nflx[0].quantity==D(5000)
    assert nflx[0].cost_basis==D('534533.19')
    assert len(reparsed)==23
    assert sum((r.proceeds for r in reparsed),D(0))==D('1313172.01')
    assert sum((r.cost_basis for r in reparsed),D(0))==D('1483980.06')
    assert sum((r.gain_loss for r in reparsed),D(0))==D('-170808.05')
