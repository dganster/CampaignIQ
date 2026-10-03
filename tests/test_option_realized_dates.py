from datetime import date,datetime
from decimal import Decimal as D
from dataclasses import replace
import pytest
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.option_leg import OptionLeg
from campaigniq.domain.instrument_leg import InstrumentLeg
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.domain.execution import Execution
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
from campaigniq.domain.realized_lot_attributor import RealizedLotAttributor
CONTRACT=OptionContract('TEST',date(2026,10,2),D('140'),OptionType.PUT)

def seed(instrument=CONTRACT,qty='-2'):
    b=LotBook();b.seed(Lot('opening',instrument,D(qty),datetime(2026,8,31),None,campaign_id='original'))
    return b

def close(day=date(2026,9,8),qty='2',instrument=CONTRACT):
    kwargs=dict(side=Side.BUY,position_effect=PositionEffect.CLOSE,executions=(Execution(D(qty),D('2.21'),datetime.combine(day,datetime.min.time())),))
    leg=OptionLeg(contract=instrument,broker_strategy='DIAGONAL',**kwargs) if isinstance(instrument,OptionContract) else InstrumentLeg(instrument=instrument,**kwargs)
    return Trade(legs=(leg,))

def record(day=date(2026,9,8),qty='2',instrument=CONTRACT):
    return RealizedGainLossRecord(day,instrument,D(qty),D('2.21'),D('1218.63'),D('443.32'),D('775.31'),'FIFO','SHORT TERM')

@pytest.mark.parametrize('day',[date(2026,9,8),date(2026,9,9)])
def test_trade_or_settlement_date_matches(day):
    a=RealizedLotAttributor(seed()).attribute([close()],[record(day)])[0]
    assert a.allocated_quantity==2 and a.basis_reconciled and a.gain_loss_reconciled
    assert a.allocations[0].lot_id=='opening'
    assert a.allocations[0].campaign_id=='original'

def test_split_broker_records_preserve_execution_date():
    rows=[record(qty='1'),record(qty='1')]
    result=RealizedLotAttributor(seed()).attribute([close()],rows)
    assert len(result)==2 and all(a.allocated_quantity==1 for a in result)

def test_partial_fills_can_match_single_record():
    result=RealizedLotAttributor(seed()).attribute([close(qty='1'),close(qty='1')],[record()])
    assert result[0].allocated_quantity==2

@pytest.mark.parametrize('day',[date(2026,9,7),date(2026,9,10)])
def test_other_dates_not_accepted(day):
    with pytest.raises(ValueError,match='No CampaignIQ closing'):
        RealizedLotAttributor(seed()).attribute([close()],[record(day)])

def test_other_strike_not_accepted():
    with pytest.raises(ValueError,match='No CampaignIQ closing'):
        RealizedLotAttributor(seed()).attribute([close()],[record(instrument=replace(CONTRACT,strike=D('145')))])

def test_record_cannot_exceed_actual_closed_quantity():
    with pytest.raises(ValueError,match='remains unmatched'):
        RealizedLotAttributor(seed()).attribute([close()],[record(qty='3')])

def test_equity_date_matching_not_broadened():
    instrument=Instrument('TEST')
    with pytest.raises(ValueError,match='No CampaignIQ closing'):
        RealizedLotAttributor(seed(instrument)).attribute([close(instrument=instrument)],[record(date(2026,9,9),instrument=instrument)])

def test_ambiguous_adjacent_day_contract_requires_review():
    with pytest.raises(ValueError,match='Ambiguous option closing date'):
        RealizedLotAttributor(seed(qty='-4')).attribute([close(),close(date(2026,9,9))],[record(date(2026,9,9))])

def test_weekend_settlement_still_matches():
    assert RealizedLotAttributor(seed()).attribute([close(date(2026,9,11))],[record(date(2026,9,14))])[0].allocated_quantity==2
