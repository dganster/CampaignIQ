from datetime import date
from decimal import Decimal as D
import copy
import json
import pytest
from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.lot_allocation import LotAllocation
from campaigniq.domain.lot_attribution import RealizedAttribution
from campaigniq.persistence.realized_attribution_store import serialize_realized_attributions
from campaigniq.historical_backfill_correction_preview import build_preview, missing_record_proposal, reference_proposal


def record(instrument,quantity,proceeds,basis,closed,price):
    return RealizedGainLossRecord(closed,instrument,D(quantity),D(price),D(proceeds),D(basis),D(proceeds)-D(basis),'FIFO','SHORT')


def encoded(attributions,month):
    return serialize_realized_attributions(period_start=date(2026,month,1),period_end=date(2026,month,28 if month==2 else 30),attributions=attributions)


def entry(instrument,action,quantity,lot_id,campaign,when,price=None):
    return dict(instrument=instrument,action=action,quantity_change=quantity,
        lots=[dict(lot_id=lot_id,quantity=str(abs(D(quantity))))],campaign_id=campaign,
        occurred_at=when,price=price)


@pytest.fixture
def evidence():
    stock=dict(type='instrument',symbol='NFLX')
    call=dict(type='option',underlying='SPOT',expiration='2026-03-20',strike='480',option_type='CALL')
    other=record(Instrument('CSCO'),'500','40984.06','40510.00',date(2026,4,14),'81.97')
    added=record(Instrument('NFLX'),'5000','536187.97','534533.19',date(2026,4,16),'107.24')
    spot=record(OptionContract('SPOT',date(2026,3,20),D(480),OptionType.CALL),'5','5521.68','14603.30',date(2026,2,23),'11.04')
    a={
      '2026-04-realized-attributions.json':encoded([RealizedAttribution(other,(LotAllocation('CSCO',D(500),D('40510.00'),'SCHWAB_REALIZED_GAIN_LOSS','CSCO-CAMPAIGN'),))],4),
      '2026-02-realized-attributions.json':encoded([RealizedAttribution(spot,(LotAllocation('LOT-000057',D(5),D('14603.30'),'SCHWAB_REALIZED_GAIN_LOSS','SPOT-CAMPAIGN'),))],2),
      '2026-04-position-journal.json':json.dumps(dict(format='campaigniq.position_journal',version=1,entries=[entry(stock,'Close / reduce','-5000','NFLX-OPEN','NFLX-CAMPAIGN','2026-04-16T10:52:01','107.24')])),
      '2026-03-lot-book.json':json.dumps(dict(format='campaigniq.lot_book',version=1,period_end='2026-03-31',next_id=100,lots=[dict(instrument=stock,lot_id='NFLX-OPEN',quantity='5000',opened_at='2025-12-31T23:59:59.999999',basis_total='534533.19',basis_source='SCHWAB_POSITION_SNAPSHOT',campaign_id=None)])),
      '2026-02-position-journal.json':json.dumps(dict(format='campaigniq.position_journal',version=1,entries=[
          entry(dict(type='instrument',symbol='SPOT'),'Assignment','200','LOT-000057',None,'2026-02-05T00:00:00'),
          entry(call,'Open / add','-5','LOT-000059','SPOT-CAMPAIGN','2026-02-06T10:47:06','11.05'),
          entry(call,'Roll: close','5','LOT-000059','SPOT-CAMPAIGN','2026-02-20T08:36:58','29.20')]))}
    return a,[other,added]


def test_only_missing_record_and_one_reference_change_with_no_source_mutation(evidence):
    artifacts,records=evidence;before=copy.deepcopy(artifacts)
    plan,proposals=build_preview(artifacts,records,'a'*64)
    assert artifacts==before
    assert set(proposals)=={'2026-04-realized-attributions.json','2026-02-realized-attributions.json'}
    old=json.loads(artifacts['2026-04-realized-attributions.json'])
    new=json.loads(proposals['2026-04-realized-attributions.json'])
    assert new['attributions'][0]==old['attributions'][0]
    assert new['attributions'][1]['record']['cost_basis']=='534533.19'
    original=json.loads(artifacts['2026-02-realized-attributions.json'])
    expected=copy.deepcopy(original);expected['attributions'][0]['allocations'][0]['lot_id']='LOT-000059'
    assert json.loads(proposals['2026-02-realized-attributions.json'])==expected
    assert plan['corrections_approved'] is False and plan['publication_authorized'] is False
    assert plan['full_portfolio_continuity_verified'] is False


@pytest.mark.parametrize('change',['missing_existing','changed_existing','duplicate','no_addition','extra_addition'])
def test_changed_or_expanded_broker_scope_is_refused(evidence,change):
    artifacts,records=evidence
    if change=='missing_existing':records=records[1:]
    if change=='changed_existing':
        from dataclasses import replace
        records[0]=replace(records[0],proceeds=D('40984.07'))
    if change=='duplicate':records.append(records[0])
    if change=='no_addition':records=records[:1]
    if change=='extra_addition':records.append(record(Instrument('OTHER'),'1','10','9',date(2026,4,16),'10'))
    with pytest.raises(ValueError):missing_record_proposal(artifacts,records)


@pytest.mark.parametrize('change',['basis','quantity','unknown_basis','ambiguous_close','wrong_price','wrong_date'])
def test_missing_sale_requires_exact_lot_and_execution_evidence(evidence,change):
    artifacts,records=evidence
    book=json.loads(artifacts['2026-03-lot-book.json']);journal=json.loads(artifacts['2026-04-position-journal.json'])
    if change=='basis':book['lots'][0]['basis_total']='534533.20'
    if change=='quantity':book['lots'][0]['quantity']='4999'
    if change=='unknown_basis':book['lots'][0]['basis_total']=None
    if change=='ambiguous_close':journal['entries']*=2
    if change=='wrong_price':journal['entries'][0]['price']='107.25'
    if change=='wrong_date':journal['entries'][0]['occurred_at']='2026-04-17T10:52:01'
    artifacts['2026-03-lot-book.json']=json.dumps(book);artifacts['2026-04-position-journal.json']=json.dumps(journal)
    with pytest.raises(ValueError):missing_record_proposal(artifacts,records)


@pytest.mark.parametrize('change',['ambiguous_open','ambiguous_close','wrong_campaign','wrong_date','wrong_quantity','already_correct'])
def test_reference_change_requires_unique_matching_option_chain(evidence,change):
    artifacts,_=evidence;journal=json.loads(artifacts['2026-02-position-journal.json'])
    if change=='ambiguous_open':journal['entries'].append(copy.deepcopy(journal['entries'][1]))
    if change=='ambiguous_close':journal['entries'].append(copy.deepcopy(journal['entries'][2]))
    if change=='wrong_campaign':journal['entries'][2]['campaign_id']='OTHER'
    if change=='wrong_date':journal['entries'][2]['occurred_at']='2026-02-18T08:36:58'
    if change=='wrong_quantity':journal['entries'][2]['quantity_change']='4'
    if change=='already_correct':
        data=json.loads(artifacts['2026-02-realized-attributions.json']);data['attributions'][0]['allocations'][0]['lot_id']='LOT-000059';artifacts['2026-02-realized-attributions.json']=json.dumps(data)
    artifacts['2026-02-position-journal.json']=json.dumps(journal)
    with pytest.raises(ValueError):reference_proposal(artifacts)
