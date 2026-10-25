from copy import deepcopy
from datetime import date
from decimal import Decimal as D
import json
import pytest

from campaigniq.historical_backfill_basis_consumption import trace_group, carry_forward, verify_consumption, MONTHS
from campaigniq.historical_backfill_basis_provenance import key
from campaigniq.historical_backfill_publication_package import canonical, digest
from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
from campaigniq.domain.lot_attribution import RealizedAttribution
from campaigniq.domain.lot_allocation import LotAllocation
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.persistence.realized_attribution_store import serialize_realized_attributions


@pytest.fixture
def stock():
    ins=dict(type='instrument',symbol='XYZ')
    group=dict(instrument=ins,quantity='500',aggregate_basis_total='1000',retained_opening_lots=[dict(lot_id='R',quantity='500')])
    def lot(q='500',lid='R'):return dict(instrument=ins,lot_id=lid,quantity=q)
    carried=dict(action='Carried position',instrument=ins,quantity_change='500',lots=[dict(lot_id='R',quantity='500')],
        opening_refs=[dict(lot_id='R',quantity='500')])
    close=dict(action='Close / reduce',instrument=ins,quantity_change='-500',lots=[dict(lot_id='R',quantity='500')])
    artifacts={}
    for m in MONTHS:
        n=int(m[-2:]);entries=[deepcopy(carried)] if n<=2 else []
        if n==2:entries.append(deepcopy(close))
        artifacts[m+'-position-journal.json']=json.dumps(dict(entries=entries))
        artifacts[m+'-lot-book.json']=json.dumps(dict(lots=[lot()] if n==1 else []))
        attrs=[]
        if n==2:
            record=RealizedGainLossRecord(date(2026,2,20),Instrument('XYZ'),D(500),D('2.2'),D(1100),D(1000),D(100),'FIFO','ST',D(0))
            attrs=[RealizedAttribution(record,(LotAllocation('R',D(500),D(1000),campaign_id='C'),))]
        artifacts[m+'-realized-attributions.json']=serialize_realized_attributions(period_start=date(2026,n,1),period_end=date(2026,n,28),attributions=attrs)
    edge=dict(review_groups=[dict(instrument=ins,classification='exact_lots',prior_lots=[lot()],published_opening_lots=[lot()])])
    return group,artifacts,{'2026-02':edge}


def mutate(artifacts,name,fn):
    obj=json.loads(artifacts[name]);fn(obj);artifacts[name]=json.dumps(obj)


def test_complete_stock_basis_consumption_preserves_sources(stock):
    before=deepcopy(stock);result=trace_group(*stock)
    assert result['complete_quantity_and_monetary_chain_verified']
    assert result['actual_consumption_usd']=='1000'
    assert len(result['realized_allocations'])==1
    assert stock==before


@pytest.mark.parametrize('change',['missing_record','double_record','wrong_basis','wrong_instrument','missing_close','overclose','wrong_direction','missing_carry','unconsumed_book'])
def test_changed_closure_or_money_is_rejected(stock,change):
    group,a,b=stock
    realized='2026-02-realized-attributions.json';journal='2026-02-position-journal.json'
    if change=='missing_record':mutate(a,realized,lambda o:o.update(attributions=[]))
    if change=='double_record':mutate(a,realized,lambda o:o['attributions'].append(deepcopy(o['attributions'][0])))
    if change=='wrong_basis':
        def alter(o):
            r=o['attributions'][0];r['record']['cost_basis']='1001';r['record']['proceeds']='1101';r['allocations'][0]['broker_basis']='1001'
        mutate(a,realized,alter)
    if change=='wrong_instrument':mutate(a,realized,lambda o:o['attributions'][0]['record']['instrument'].update(symbol='OTHER'))
    if change=='missing_close':mutate(a,journal,lambda o:o['entries'].pop())
    if change=='overclose':mutate(a,journal,lambda o:o['entries'][-1]['lots'][0].update(quantity='501'))
    if change=='wrong_direction':mutate(a,journal,lambda o:o['entries'][-1].update(quantity_change='500'))
    if change=='missing_carry':mutate(a,journal,lambda o:o['entries'].pop(0))
    if change=='unconsumed_book':mutate(a,'2026-02-lot-book.json',lambda o:o.update(lots=[dict(instrument=group['instrument'],lot_id='R',quantity='1')]))
    with pytest.raises((ValueError,KeyError)):trace_group(group,a,b)


def test_single_parent_split_preserves_exact_group_quantity(stock):
    group,_,edges=stock;edge=edges['2026-02'];edge['review_groups'][0]['classification']='single_origin_split_candidate'
    edge['review_groups'][0]['published_opening_lots']=[dict(lot_id='R',quantity='400'),dict(lot_id='CHILD',quantity='100')]
    assert carry_forward({(key(group['instrument']),'R'):D(500)},edge)=={(key(group['instrument']),'R'):D(400),(key(group['instrument']),'CHILD'):D(100)}


@pytest.mark.parametrize('change',['missing','ambiguous','quantity','duplicate_group'])
def test_unsupported_boundary_cannot_infer_basis(stock,change):
    group,_,edges=stock;edge=edges['2026-02']
    if change=='missing':edge['review_groups']=[]
    if change=='ambiguous':edge['review_groups'][0]['classification']='ambiguous_multiple_origins'
    if change=='quantity':edge['review_groups'][0]['prior_lots'][0]['quantity']='499'
    if change=='duplicate_group':edge['review_groups']*=2
    with pytest.raises(ValueError):carry_forward({(key(group['instrument']),'R'):D(500)},edge)


def test_lot_number_reuse_after_original_closure_does_not_join_history(stock):
    group,a,b=stock
    mutate(a,'2026-03-lot-book.json',lambda o:o.update(lots=[dict(instrument=group['instrument'],lot_id='R',quantity='900')]))
    result=trace_group(group,a,b)
    assert len(result['realized_allocations'])==1 and result['actual_consumption_usd']=='1000'


def test_same_broker_allocation_cannot_fund_two_opening_groups(stock):
    group,a,edges=stock;plan=dict(capture_sha256='capture',boundaries=[dict(month=m,**e) for m,e in edges.items()])
    provenance=dict(format='campaigniq.aggregate_basis_provenance',version=1,aggregate_basis_provenance_verified=True,
        capture_sha256='capture',boundary_plan_sha256=digest(canonical(plan)),source_artifact_sha256={},positions=[group,deepcopy(group)],position_count=2)
    with pytest.raises(ValueError,match='multiple opening'):verify_consumption(provenance,plan,a)


@pytest.mark.parametrize('change',[None,'share_quantity','premium'])
def test_put_assignment_transfers_aggregate_basis_without_invented_fill_allocation(stock,change):
    _,a,_=stock
    ins=dict(type='option',underlying='XYZ',expiration='2026-02-20',strike='100',option_type='PUT')
    shares=dict(type='instrument',symbol='XYZ')
    group=dict(instrument=ins,quantity='-5',aggregate_basis_total='-5000',retained_opening_lots=[dict(lot_id='R',quantity='-5')])
    def carry(instrument,lid,q):return dict(action='Carried position',instrument=instrument,quantity_change=q,
        lots=[dict(lot_id=lid,quantity=str(abs(D(q))))],opening_refs=[dict(lot_id=lid,quantity=q)])
    option=dict(action='Assignment',instrument=ins,quantity_change='5',lots=[dict(lot_id='R',quantity='5')],occurred_at='2026-02-05T00:00:00',campaign_id=None)
    delivery=dict(action='Assignment',instrument=shares,quantity_change='500',lots=[dict(lot_id='S',quantity='500')],occurred_at=option['occurred_at'],campaign_id=None)
    close=dict(action='Close / reduce',instrument=shares,quantity_change='-500',lots=[dict(lot_id='S',quantity='500')])
    for m in MONTHS:
        n=int(m[-2:]);entries=[];lots=[];attrs=[]
        if n<=2:entries=[carry(ins,'R','-5')]
        if n==1:lots=[dict(instrument=ins,lot_id='R',quantity='-5')]
        if n==2:entries += [option,delivery];lots=[dict(instrument=shares,lot_id='S',quantity='500')]
        if n==3:
            entries=[carry(shares,'S','500'),close]
            record=RealizedGainLossRecord(date(2026,3,20),Instrument('XYZ'),D(500),D(92),D(46000),D(45000),D(1000),'FIFO','ST',D(0))
            attrs=[RealizedAttribution(record,(LotAllocation('S',D(500),D(45000),campaign_id='C'),))]
        a[m+'-position-journal.json']=json.dumps(dict(entries=entries))
        a[m+'-lot-book.json']=json.dumps(dict(lots=lots))
        a[m+'-realized-attributions.json']=serialize_realized_attributions(period_start=date(2026,n,1),period_end=date(2026,n,28),attributions=attrs)
    def edge(instrument,lid,q):
        rows=[dict(lot_id=lid,quantity=q)]
        return dict(review_groups=[dict(instrument=instrument,classification='exact_lots',prior_lots=rows,published_opening_lots=deepcopy(rows))])
    edges={'2026-02':edge(ins,'R','-5'),'2026-03':edge(shares,'S','500')}
    if change=='share_quantity':mutate(a,'2026-02-position-journal.json',lambda o:o['entries'][-1].update(quantity_change='499'))
    if change=='premium':group['aggregate_basis_total']='-4999'
    if change:
        with pytest.raises(ValueError):trace_group(group,a,edges)
    else:
        result=trace_group(group,a,edges)
        assert result['actual_consumption_usd']=='45000'
        assert result['method']=='assigned_put_basis_transferred_to_stock'
        assert not result['per_fill_basis_allocation_inferred']
