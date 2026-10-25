"""Explicit per-contract alias matching preserves original broker facts."""
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
import pytest
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_event import PositionChange, PositionEvent
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
from campaigniq.domain.realized_lot_attributor import RealizedLotAttributor
from campaigniq.domain.value_objects.instrument import Instrument


def contract(root='SPX', **kwargs):
    return replace(OptionContract(root,date(2025,8,13),Decimal('6470'),OptionType.CALL),**kwargs)


def book(qty='-100', second_root=False):
    b=LotBook()
    b.seed(Lot('SPX-original',contract(),Decimal(qty),datetime(2025,8,13,10),None,
        campaign_id='ORIGINAL-CAMPAIGN'))
    if second_root:
        b.seed(Lot('SPXW-separate',contract('SPXW'),Decimal('-100'),datetime(2025,8,13,10),None))
    return b


def record(qty='100', **kwargs):
    return replace(RealizedGainLossRecord(date(2025,8,13),contract('SPXW'),Decimal(qty),
        Decimal('.75'),Decimal('7532.90'),Decimal('0'),Decimal('7532.90'),'FIFO','SHORT'),**kwargs)


def event(qty='100', day=14):
    return PositionEvent(kind=PositionEventKind.EXPIRATION,
        changes=(PositionChange(contract(),Decimal(qty)),),occurred_at=datetime(2025,8,day,23))


def attributor(b=None, aliases=None):
    return RealizedLotAttributor(b if b is not None else book(),
        contract_aliases=aliases if aliases is not None else {contract('SPXW'):contract()})


def test_opt_in_expiration_match_preserves_record_identity_basis_and_campaign():
    original=record()
    result,=attributor().attribute([], [original], [event()])
    assert result.record is original
    assert result.record.instrument.underlying=='SPXW'
    assert result.allocated_quantity==Decimal('100')
    assert result.allocations[0].lot_id=='SPX-original'
    assert result.allocations[0].campaign_id=='ORIGINAL-CAMPAIGN'
    assert result.basis_reconciled and result.gain_loss_reconciled


def test_default_behavior_does_not_infer_alias():
    with pytest.raises(ValueError,match='No CampaignIQ closing activity'):
        RealizedLotAttributor(book()).attribute([], [record()], [event()])


@pytest.mark.parametrize('wrong', [
    {'expiration':date(2025,8,14)}, {'strike':Decimal('6475')}, {'option_type':OptionType.PUT}])
def test_contract_details_must_agree(wrong):
    with pytest.raises(ValueError,match='must match exactly'):
        attributor(aliases={contract('SPXW'):contract(**wrong)})


@pytest.mark.parametrize('root', ['SPY','XSP','SPXW','SPX.A'])
def test_other_roots_cannot_be_mapped(root):
    with pytest.raises(ValueError,match='Only explicit SPXW-to-SPX'):
        attributor(aliases={contract('SPXW'):contract(root)})


def test_stock_instrument_alias_is_rejected():
    with pytest.raises(ValueError,match='full option contracts'):
        attributor(aliases={Instrument('SPXW'):Instrument('SPX')})


@pytest.mark.parametrize('strike', ['NaN','Infinity','0','-1'])
def test_nonfinite_or_nonpositive_strike_is_rejected(strike):
    with pytest.raises(ValueError,match='finite and positive'):
        attributor(aliases={contract('SPXW',strike=Decimal(strike)):contract(strike=Decimal(strike))})


def test_both_roots_in_inventory_are_ambiguous_before_any_mutation():
    b=book(second_root=True)
    before=[(i,b.lots(i)) for i in b.instruments()]
    with pytest.raises(ValueError,match='Ambiguous SPX/SPXW'):
        attributor(b).attribute([], [record()], [event()])
    assert before==[(i,b.lots(i)) for i in b.instruments()]


def test_mixed_root_broker_records_cannot_double_consume_series():
    with pytest.raises(ValueError,match='Ambiguous SPX/SPXW'):
        attributor().attribute([], [record(),record(instrument=contract())], [event()])


def test_quantity_mismatch_is_not_waived():
    with pytest.raises(ValueError,match='remains unmatched'):
        attributor().attribute([], [record('101')], [event()])


def test_unrelated_close_date_is_not_waived():
    with pytest.raises(ValueError,match='No CampaignIQ closing activity'):
        attributor().attribute([], [record(closed_date=date(2025,8,11))], [event()])


def test_alias_cannot_match_a_different_contract_in_record():
    with pytest.raises(ValueError,match='No CampaignIQ closing activity'):
        attributor().attribute([], [record(instrument=contract('SPXW',strike=Decimal('6475')))], [event()])


def test_caller_mapping_is_frozen():
    aliases={contract('SPXW'):contract()}
    a=attributor(aliases=aliases)
    aliases.clear()
    assert a.attribute([], [record()], [event()])[0].allocated_quantity==Decimal('100')


def test_split_realized_rows_consume_each_allocation_only_once():
    result=attributor().attribute([], [record('60'),record('40')], [event()])
    assert [r.allocated_quantity for r in result]==[Decimal('60'),Decimal('40')]
    assert sum(r.allocated_quantity for r in result)==Decimal('100')


def test_opt_in_keeps_ordinary_option_settlement_date_rules():
    from campaigniq.domain.execution import Execution
    from campaigniq.domain.option_leg import OptionLeg
    from campaigniq.domain.position_effect import PositionEffect
    from campaigniq.domain.side import Side
    from campaigniq.domain.trade import Trade
    trade=Trade(legs=(OptionLeg(contract=contract(),side=Side.BUY,broker_strategy="SINGLE",
        position_effect=PositionEffect.CLOSE,executions=(Execution(
            quantity=Decimal('100'),execution_price=Decimal('.20'),
            executed_at=datetime(2025,8,13,14)),)),))
    original=record(closed_date=date(2025,8,14),cost_basis=Decimal('2000'),gain_loss=Decimal('5532.90'))
    result,=attributor().attribute([trade],[original])
    assert result.record is original
    assert result.allocated_quantity==Decimal('100')
    assert result.basis_reconciled and result.gain_loss_reconciled
