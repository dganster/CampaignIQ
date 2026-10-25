"""A review plan must bind exact inputs while leaving publication blocked."""
import calendar
import copy
from dataclasses import replace
from datetime import date,datetime
from decimal import Decimal
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import pytest

from campaigniq.historical_backfill_boundary_plan import BoundaryPlan,build_boundary_plan,verify_boundary_plan,canonical
from campaigniq.historical_backfill_bundle import encode
from campaigniq.historical_backfill_downstream_audit import audit_boundary
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.persistence.lot_book_store import serialize_lot_book
from campaigniq.persistence.position_journal import serialize_position_journal,deserialize_position_journal
from campaigniq.persistence.monthly_publication import PROTOCOL_FILE,_serialize_publication_protocol,_serialize_finalized_month_marker


@pytest.fixture
def inputs(monkeypatch):
    spec=importlib.util.spec_from_file_location('year_helpers',Path(__file__).with_name('test_whole_year_backfill_preflight.py'))
    h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
    from campaigniq.importers.schwab.position_snapshot import SchwabPositionSnapshotRow,to_lots
    import campaigniq.importers.schwab.position_snapshot_reader as reader
    snapshot='retained opening statement text';at=datetime(2025,12,31,23,59,59,999999)
    rows=(SchwabPositionSnapshotRow('ORCL',Decimal(500),at,basis_total=Decimal('12345.67')),)
    monkeypatch.setattr(reader,'read_position_snapshot_section',lambda lines,snapshot_at:rows)
    prior=LotBook();prior.seed(Lot('derived',Instrument('ORCL'),Decimal(500),datetime(2025,12,19),None,campaign_id='HISTORICAL'))
    published=LotBook()
    for lot in to_lots(rows):published.seed(replace(lot,campaign_id='PUBLISHED'))
    b=h.bundle(range(1,13));b['artifacts']={PROTOCOL_FILE:_serialize_publication_protocol(first_period_end=date(2026,1,31))}
    provenance=json.dumps(dict(format='campaigniq.monthly_import_provenance',version=1,period_start='2026-01-01',period_end='2026-01-31',inputs=[dict(role='schwab_opening_position_snapshot',sha256=sha256(snapshot.encode()).hexdigest(),byte_size=len(snapshot.encode()))]))
    b['artifacts']['2026-01-import-provenance.json']=provenance
    edges=[];previous=prior
    for m in range(1,10):
        start=date(2026,m,1);end=date(2026,m,calendar.monthrange(2026,m)[1]);key=f'2026-{m:02}'
        journal=serialize_position_journal(h.result(published),period_start=start,period_end=end)
        b['artifacts'][key+'-lot-book.json']=serialize_lot_book(period_end=end,lot_book=published)
        b['artifacts'][key+'-finalized.json']=_serialize_finalized_month_marker(period_end=end)
        b['artifacts'][key+'-position-journal.json']=journal
        audit=audit_boundary(previous,deserialize_position_journal(journal,period_start=start,period_end=end),previous_end=start-date.resolution,
            opening_snapshot_text=snapshot if m==1 else None,provenance_text=provenance if m==1 else None)
        edges.append(dict(month=key,mapping_and_basis_audit=audit));previous=published
    months=[]
    for draft in b['drafts']:
        docs=[{k:d[k] for k in ('id','role','filename','revision','sha256')} for d in b['documents'] if d['draft_id']==draft['id'] and not d['superseded']]
        months.append(dict(month=f"2025-{draft['month']:02}",draft_id=draft['id'],revision=draft['revision'],documents=docs,
            blocking_findings=[],continuous_preview=dict(closing_reconciled=True),independent_preview=dict(closing_reconciled=True),
            prepared_source_sha256=dict(schwab_closing_position_snapshot=sha256(snapshot.encode()).hexdigest())))
    from campaigniq.historical_backfill_preflight import _state
    months[-1]['closing_state_sha256']=sha256(_state(prior,date(2025,12,31)).encode()).hexdigest()
    report=dict(workspace=b['workspace'],capture_sha256=sha256(encode(b)).hexdigest(),months=months,downstream=edges,
        all_2025_drafts_passed=True,identity_preserving_chain_verified=True,publication_authorized=False,full_portfolio_continuity_verified=False,
        protected_artifact_sha256={k:sha256(v.encode()).hexdigest() for k,v in b['artifacts'].items() if k.startswith('2026-')})
    return b,report,prior,snapshot


def build(inputs):
    b,r,book,text=inputs
    return build_boundary_plan(b,r,book,opening_snapshot_text=text,development_only=True)


def test_plan_is_deterministic_detached_and_does_not_mutate_inputs(inputs):
    before=copy.deepcopy(inputs[:2]);p=build(inputs);q=build(inputs)
    assert p.sha256==q.sha256 and inputs[:2]==before
    payload=p.to_dict();payload['constraints']['existing_basis']='overwrite'
    assert p.to_dict()['constraints']['existing_basis']=='preserve'
    b,r,book,text=inputs
    assert verify_boundary_plan(p,b,r,book,opening_snapshot_text=text,development_only=True)


def test_plan_preserves_both_basis_views_and_original_campaign_ids(inputs):
    d=build(inputs).to_dict();first=d['boundaries'][0]
    assert first['review_groups'][0]['prior_lots'][0]['basis_total'] is None
    assert first['retained_opening_snapshot']['rows'][0]['basis_total']=='12345.67'
    assert first['review_groups'][0]['prior_lots'][0]['campaign_id']=='HISTORICAL'
    assert first['review_groups'][0]['published_opening_lots'][0]['campaign_id']=='PUBLISHED'
    assert not first['mapping_approved'] and not first['basis_replacement_authorized']
    assert not d['publication_authorized'] and not d['full_portfolio_continuity_verified']
    assert not d['constraints']['global_campaign_aliases_allowed']
    assert not d['constraints']['publication_protocol_change_authorized']


@pytest.mark.parametrize('field',['all_2025_drafts_passed','identity_preserving_chain_verified'])
def test_incomplete_2025_checks_block_plan(inputs,field):
    inputs[1][field]=False
    with pytest.raises(ValueError,match='checks'):build(inputs)


@pytest.mark.parametrize('field',['publication_authorized','full_portfolio_continuity_verified'])
def test_false_claims_of_authority_are_rejected(inputs,field):
    inputs[1][field]=True
    with pytest.raises(ValueError,match='diagnostic'):build(inputs)


@pytest.mark.parametrize('change',['revision','documents','monthly_blocker','opening_change','closing_change','missing_month'])
def test_changed_or_incomplete_monthly_evidence_is_rejected(inputs,change):
    r=inputs[1];row=r['months'][1]
    if change=='revision':row['revision']+=1
    if change=='documents':row['documents'][0]['sha256']='f'*64
    if change=='monthly_blocker':row['blocking_findings']=['unresolved']
    if change=='opening_change':row['opening_state_changes']=['changed']
    if change=='closing_change':row['closing_quantity_differences']=['changed']
    if change=='missing_month':r['months'].pop()
    with pytest.raises(ValueError):build(inputs)


@pytest.mark.parametrize('change',['changed','removed','added'])
def test_protected_artifacts_cannot_change_or_disappear(inputs,change):
    b,r,_,_=inputs
    key='2026-01-lot-book.json'
    if change=='changed':b['artifacts'][key]+='\n'
    if change=='removed':del b['artifacts'][key]
    if change=='added':b['artifacts']['2026-10-lot-book.json']='new'
    # Even updating the outer capture hash cannot conceal changed protected bytes.
    r['capture_sha256']=sha256(encode(b)).hexdigest()
    with pytest.raises(ValueError,match='Protected'):build(inputs)


def test_candidate_tampering_is_detected_by_recomputing_from_published_artifacts(inputs):
    inputs[1]['downstream'][4]['mapping_and_basis_audit']['candidates'][0]['published_quantity']='499'
    with pytest.raises(ValueError,match='recomputed'):build(inputs)


def test_changed_december_book_is_rejected(inputs):
    inputs[2].reassign_campaign('derived','OTHER')
    with pytest.raises(ValueError,match='December state'):build(inputs)


def test_changed_retained_statement_is_rejected(inputs):
    b,r,book,text=inputs
    with pytest.raises(ValueError,match='snapshot'):
        build_boundary_plan(b,r,book,opening_snapshot_text=text+'changed',development_only=True)


def test_tampered_plan_is_rejected_even_with_a_new_valid_json_checksum(inputs):
    b,r,book,text=inputs;d=build(inputs).to_dict();d['constraints']['production_writes_authorized']=True
    altered=BoundaryPlan(canonical(d))
    with pytest.raises(ValueError,match='differs'):
        verify_boundary_plan(altered,b,r,book,opening_snapshot_text=text,development_only=True)


@pytest.mark.parametrize('flag',[False,1,None])
def test_explicit_development_flag_is_required(inputs,flag):
    b,r,book,text=inputs
    with pytest.raises(ValueError,match='explicit local'):
        build_boundary_plan(b,r,book,opening_snapshot_text=text,development_only=flag)


@pytest.mark.parametrize('key',['RENDER','RENDER_SERVICE_ID'])
def test_plan_cannot_run_on_render(inputs,monkeypatch,key):
    monkeypatch.setenv(key,'present')
    with pytest.raises(ValueError,match='explicit local'):build(inputs)
