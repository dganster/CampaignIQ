from copy import deepcopy
from calendar import monthrange
from datetime import date,datetime
from decimal import Decimal as D
import json
import pytest
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.domain.value_objects.forex_pair import ForexPair
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.persistence.lot_book_store import serialize_lot_book,deserialize_lot_book,_serialize_instrument
from campaigniq.historical_backfill_continuity_links import source_boundary_audit
from campaigniq.historical_backfill_downstream_audit import audit_boundary
from campaigniq.persistence.position_journal import deserialize_position_journal


def sources(month=8,typed=False):
    start=date(2025,month,1);prior=date(2025,month-1,monthrange(2025,month-1)[1])
    end=date(2025,month,31 if month==8 else 30);opened=datetime(2025,6,25,12,34,5)
    book=LotBook();entries=[]
    instruments=[ForexPair('EUR','USD') if typed else Instrument('EUR/USD'),OptionContract('ISRG',date(2025,9,19),D('555'),OptionType.PUT)]
    for i,ins in enumerate(instruments):
        q=D('-50000') if i==0 else D('-3');lid='LOT-'+str(i);cid='2025-01:'+('EUR%2FUSD:FX-CAMP-1' if i==0 else 'ISRG:CAMP-13')
        book.seed(Lot(lid,ins,q,opened,None,campaign_id=cid))
        entries.append(dict(occurred_at=start.isoformat()+'T00:00:00',instrument=_serialize_instrument(ins),action='Carried position',quantity_change=str(q),price=None,campaign_id=cid,position_after=str(q),lots=[dict(lot_id=lid,quantity=str(abs(q)))],fills=0,opened_at=opened.isoformat(),opening_refs=[dict(lot_id=lid,quantity=str(q),opened_at=opened.isoformat())]))
    return serialize_lot_book(period_end=prior,lot_book=book),json.dumps(dict(format='campaigniq.position_journal',version=1,period_start=start.isoformat(),period_end=end.isoformat(),entries=entries)),prior,start,end


@pytest.mark.parametrize('month',[8,9])
def test_legacy_graph_encoding_survives_forex_subtype_recovery(month):
    raw,journal,prior,start,end=sources(month);before=(raw,journal)
    recovered=deserialize_lot_book(raw).lot_book
    assert any(isinstance(i,ForexPair) for i in recovered.instruments())
    ordinary=audit_boundary(recovered,deserialize_position_journal(journal,period_start=start,period_end=end),previous_end=prior)
    assert ordinary['candidates'][0]['instrument']['type']=='forex_pair'
    fixed=source_boundary_audit(raw,journal,previous_end=prior,start=start,end=end)
    assert fixed['complete_candidate_inventory_match']
    # The original encoding sorted the option before the generic fiat symbol.
    assert [g['instrument']['type'] for g in fixed['candidates']]==['option','instrument']
    assert fixed['candidates'][1]['instrument']==dict(type='instrument',symbol='EUR/USD')
    assert (raw,journal)==before


def test_new_typed_sources_keep_typed_graph_and_default_audit_behavior():
    raw,journal,prior,start,end=sources(typed=True)
    ordinary=audit_boundary(deserialize_lot_book(raw).lot_book,deserialize_position_journal(journal,period_start=start,period_end=end),previous_end=prior)
    fixed=source_boundary_audit(raw,journal,previous_end=prior,start=start,end=end)
    assert fixed==ordinary


def test_mixed_conflicting_source_encodings_are_rejected():
    raw,journal,prior,start,end=sources();data=json.loads(journal)
    data['entries'][0]['instrument']=_serialize_instrument(ForexPair('EUR','USD'))
    with pytest.raises(ValueError,match='ambiguous source instrument encodings'):
        source_boundary_audit(raw,json.dumps(data),previous_end=prior,start=start,end=end)


def test_changed_quantity_still_fails_boundary_conservation():
    raw,journal,prior,start,end=sources();data=json.loads(journal);row=data['entries'][0]
    row['quantity_change']=row['position_after']='-40000';row['opening_refs'][0]['quantity']='-40000';row['lots'][0]['quantity']='40000'
    result=source_boundary_audit(raw,json.dumps(data),previous_end=prior,start=start,end=end)
    assert not result['complete_candidate_inventory_match']

@pytest.mark.parametrize('typed',[False,True])
def test_full_graph_builder_accepts_exact_source_boundary_and_checks_partition(typed):
    from campaigniq.historical_backfill_continuity_links import build_links,verify_partition
    raw,journal,prior,start,end=sources(typed=typed)
    audit=source_boundary_audit(raw,journal,previous_end=prior,start=start,end=end)
    plan=dict(existing_artifact_sha256={},boundaries=[dict(month='2025-08',review_groups=audit['candidates'])])
    graph=build_links(plan,{})
    assert len(graph['groups'])==2 and len(graph['links'])==4
    assert [g['instrument']['type'] for g in graph['groups']]==(['forex_pair','option'] if typed else ['option','instrument'])
    assert verify_partition(graph)['exact_once_record_partition_verified']
    assert graph==build_links(plan,{})
    altered=deepcopy(plan)
    altered['boundaries'][0]['review_groups'][0]['instrument']=dict(type='instrument',symbol='UNRELATED')
    with pytest.raises(ValueError,match='mixes instruments'):build_links(altered,{})

def test_source_graph_bytes_are_stable_across_python_hash_seeds():
    import os,subprocess,sys
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    code="""from tests.test_historical_graph_source_encoding import sources
from campaigniq.historical_backfill_continuity_links import source_boundary_audit,build_links
from campaigniq.historical_backfill_publication_package import canonical,digest
raw,journal,prior,start,end=sources()
audit=source_boundary_audit(raw,journal,previous_end=prior,start=start,end=end)
plan=dict(existing_artifact_sha256={},boundaries=[dict(month='2025-08',review_groups=audit['candidates'])])
print(digest(canonical(build_links(plan,{}))))
"""
    results=[]
    for seed in ('1','97531'):
        env=dict(os.environ,PYTHONHASHSEED=seed,PYTHONPATH=str(root/'src')+os.pathsep+str(root))
        results.append(subprocess.check_output([sys.executable,'-c',code],cwd=root,env=env,text=True))
    assert results[0]==results[1]
