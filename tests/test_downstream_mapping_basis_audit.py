"""Candidates explain published boundaries without approving or repairing them."""
import copy
from datetime import date, datetime
from decimal import Decimal as D
from hashlib import sha256
import json
import pytest

from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.historical_backfill_downstream_audit import audit_boundary, number
from campaigniq.persistence.lot_book_store import serialize_lot_book


END=date(2026,4,30)


def book(rows):
    result=LotBook()
    for lid,q,cid,basis in rows:
        result.seed(Lot(lid,Instrument('ORCL'),D(q),datetime(2026,4,21),
            None if basis is None else D(basis),'BROKER' if basis is not None else None,cid))
    return result


def entries(rows,opened='2026-04-21T00:00:00'):
    return [dict(action='Carried position',instrument=dict(type='instrument',symbol='ORCL'),
        quantity_change=q,campaign_id=cid,opening_refs=[dict(lot_id=lid,quantity=q,opened_at=opened)])
        for lid,q,cid in rows]


def run(prior,published,end=END,**kw):
    before=serialize_lot_book(period_end=end,lot_book=prior);original=copy.deepcopy(published)
    result=audit_boundary(prior,published,previous_end=end,**kw)
    assert before==serialize_lot_book(period_end=end,lot_book=prior) and original==published
    assert not result['mapping_approved'] and not result['basis_continuity_verified'] and not result['publication_authorized']
    return result


def test_exact_lot_has_unknown_opening_basis_even_when_prior_basis_is_known():
    r=run(book([('a','500','OLD','100')]),entries([('a','500','OLD')]))
    c=r['candidates'][0]
    assert c['classification']=='exact_lots' and r['complete_candidate_inventory_match']
    assert c['prior_lots'][0]['basis_total']=='100'
    assert c['published_opening_lots'][0]['basis_total'] is None


def test_campaign_relabel_is_a_boundary_candidate_only():
    r=run(book([('a','500','OLD',None)]),entries([('a','500.000','NEW')]))
    assert r['candidates'][0]['classification']=='same_lots_campaign_labels'
    assert r['candidate_campaign_relations']==[dict(prior='OLD',published='NEW')]


def test_may_split_preserves_quantity_and_original_date_without_inventing_basis():
    r=run(book([('a','500','OLD','125')]),entries([('a','400','NEW'),('child','100','OLD')]))
    assert r['complete_candidate_inventory_match']
    assert r['candidates'][0]['classification']=='single_origin_split_candidate'
    assert all(row['basis_total'] is None for row in r['candidates'][0]['published_opening_lots'])


def test_two_indistinguishable_origins_do_not_receive_arbitrary_split_allocations():
    r=run(book([('a','200','A',None),('b','300','B',None)]),entries([('x','100','C'),('y','400','D')]))
    assert not r['complete_candidate_inventory_match']
    assert r['candidates'][0]['classification']=='ambiguous_multiple_origin_regrouping'


@pytest.mark.parametrize('qty',['499','-500'])
def test_changed_quantity_or_direction_is_not_mapped(qty):
    r=run(book([('a','500','A',None)]),entries([('a',qty,'B')]))
    assert not r['complete_candidate_inventory_match']


def test_changed_origin_date_is_not_mapped_outside_snapshot_boundary():
    r=run(book([('a','500','A',None)]),entries([('a','500','B')],opened='2026-04-22T00:00:00'))
    assert not r['complete_candidate_inventory_match']


def test_prior_campaign_to_multiple_published_campaigns_is_flagged():
    prior=book([('a','200','OLD',None),('b','300','OLD',None)])
    # Distinct origin timestamps create independently matched candidate groups.
    prior._lots[Instrument('ORCL')][1]=Lot('b',Instrument('ORCL'),D(300),datetime(2026,4,22),None,campaign_id='OLD')
    published=entries([('a','200','NEW1')])+entries([('b','300','NEW2')],opened='2026-04-22T00:00:00')
    r=run(prior,published)
    assert r['campaign_relation_conflicts'][0]['kind']=='one_prior_to_multiple_published_campaigns'


@pytest.mark.parametrize('value',['NaN','Infinity',float('nan'),1.2,True])
def test_nonexact_or_nonfinite_numeric_values_are_rejected(value):
    with pytest.raises(ValueError):number(value)


def test_duplicate_journal_lot_ids_are_rejected():
    with pytest.raises(ValueError,match='Duplicate'):
        run(book([('a','500','A',None)]),entries([('a','400','B'),('a','100','B')]))


def test_inconsistent_journal_reference_total_is_rejected():
    rows=entries([('a','500','B')]);rows[0]['quantity_change']='499'
    with pytest.raises(ValueError,match='disagree'):run(book([('a','500','A',None)]),rows)


def test_future_opening_is_rejected():
    with pytest.raises(ValueError,match='after the boundary'):
        run(book([('a','500','A',None)]),entries([('a','500','A')],opened='2026-05-01T00:00:00'))


def test_non_month_end_is_rejected():
    with pytest.raises(ValueError,match='month-end'):run(book([]),[],end=date(2026,4,29))


def snapshot_inputs():
    text='original extracted snapshot text'
    provenance=dict(format='campaigniq.monthly_import_provenance',version=1,period_start='2026-01-01',period_end='2026-01-31',
        inputs=[dict(role='schwab_opening_position_snapshot',sha256=sha256(text.encode()).hexdigest(),byte_size=len(text.encode()))])
    lid='SCHWAB-SNAPSHOT:2025-12-31T23:59:59.999999:EQUITY:ORCL'
    prior=LotBook();prior.seed(Lot('derived',Instrument('ORCL'),D(500),datetime(2025,12,19),None,campaign_id='OLD'))
    return text,provenance,lid,prior,entries([(lid,'500','NEW')],opened='2025-12-31T23:59:59.999999')


def test_january_source_hash_recovers_broker_basis_without_claiming_prior_basis_equality(monkeypatch):
    text,p,lid,prior,published=snapshot_inputs()
    from campaigniq.importers.schwab.position_snapshot import SchwabPositionSnapshotRow
    import campaigniq.importers.schwab.position_snapshot_reader as reader
    monkeypatch.setattr(reader,'read_position_snapshot_section',lambda lines,snapshot_at:[
        SchwabPositionSnapshotRow('ORCL',D(500),snapshot_at,basis_total=D('12345.67'))])
    r=run(prior,published,end=date(2025,12,31),opening_snapshot_text=text,provenance_text=json.dumps(p))
    source=r['retained_opening_snapshot']
    assert source['source_hash_verified'] and source['opening_basis_recovered']
    assert source['known_basis_lots']==1 and source['rows'][0]['basis_total']=='12345.67'
    assert r['candidates'][0]['classification']=='snapshot_regrouping_candidate'
    assert r['candidates'][0]['prior_lots'][0]['basis_total'] is None


@pytest.mark.parametrize('change',['hash','bytes','duplicate','period','format'])
def test_wrong_or_ambiguous_source_provenance_is_rejected(change):
    text,p,_,prior,published=snapshot_inputs()
    if change=='hash':p['inputs'][0]['sha256']='f'*64
    if change=='bytes':p['inputs'][0]['byte_size']+=1
    if change=='duplicate':p['inputs']*=2
    if change=='period':p['period_start']='2026-02-01'
    if change=='format':p['format']='unrelated'
    with pytest.raises(ValueError):
        run(prior,published,end=date(2025,12,31),opening_snapshot_text=text,provenance_text=json.dumps(p))


def test_unknown_and_zero_basis_remain_distinct():
    r=run(book([('a','200','A',None),('b','300','B','0')]),entries([('a','200','A'),('b','300','B')]))
    rows=r['candidates'][0]['prior_lots']
    assert rows[0]['basis_total'] is None and not rows[0]['basis_recorded']
    assert rows[1]['basis_total']=='0' and rows[1]['basis_recorded']
