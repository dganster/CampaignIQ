import copy
from datetime import datetime,timezone
from types import SimpleNamespace
import pytest
from tests.test_historical_publication_rehearsal import complete_generation
from tests.test_historical_continuity_links import graph,boundary
from campaigniq.historical_backfill_publication_package import canonical,digest
from campaigniq.historical_backfill_continuity_links import verify_partition,build_links
from campaigniq.historical_backfill_combined_rehearsal import (
    validate_history_review,stage,InjectedFailure,PROTOCOL_FILE,CONTINUITY_KEY,TARGETS,
)


def reviewed():
    g=graph();review=dict(verify_partition(g),format='campaigniq.linked_history_accounting_review',
        no_database_connection_made=True,continuity_graph_sha256=digest(canonical(g)))
    return g,review


def test_history_review_rebuilds_all_economics_and_preserves_inputs():
    g,r=reviewed();before=copy.deepcopy((g,r))
    validate_history_review(g,r)
    assert (g,r)==before


@pytest.mark.parametrize('change',['graph','total','count','history','authorized','scope','database'])
def test_changed_history_review_is_refused(change):
    g,r=reviewed()
    if change=='graph':g['realized_records'][0]['gain_loss']='21'
    if change=='total':r['totals']['gain_loss']='41'
    if change=='count':r['realized_record_count']=1
    if change=='history':r['histories'][0]['record_ids']=[]
    if change=='authorized':r['publication_authorized']=True
    if change=='scope':r['scope']='full_portfolio'
    if change=='database':r['no_database_connection_made']=False
    with pytest.raises(ValueError):validate_history_review(g,r)


def test_protocol_cutover_does_not_change_lot_links_but_other_source_changes_block(boundary):
    boundary['existing_artifact_sha256']={PROTOCOL_FILE:digest(b'old protocol')}
    links=build_links(boundary,{PROTOCOL_FILE:'new protocol'})
    assert len(links['groups'])==1
    boundary['existing_artifact_sha256']['2026-04-lot-book.json']=digest(b'original lots')
    with pytest.raises(ValueError,match='changed'):
        build_links(boundary,{PROTOCOL_FILE:'new protocol','2026-04-lot-book.json':'different lots'})


class Connection:
    def __init__(self,rowcount=1):self.calls=[];self.rowcount=rowcount
    def execute(self,query,params):
        self.calls.append((query,params));return SimpleNamespace(rowcount=self.rowcount)


def originals():
    return {key:('original '+key,datetime(2026,10,24,tzinfo=timezone.utc)) for key in TARGETS|{PROTOCOL_FILE}}


def test_one_transaction_contains_archive_corrections_candidates_links_and_protocol(complete_generation):
    c=Connection();corrections={key:'corrected' for key in TARGETS}
    stage(c,originals(),complete_generation,corrections,'links','audit','original archive')
    assert c.calls[0][1]==('workspace-dennis','audit','original archive')
    updates=[(query,params) for query,params in c.calls if query.startswith('UPDATE')]
    assert len(updates)==3
    assert c.calls[-1][0].startswith('UPDATE') and c.calls[-1][1][2]==PROTOCOL_FILE
    inserts=[params[1] for query,params in c.calls if query.startswith('INSERT')]
    assert set(inserts)==set(complete_generation)|{'audit',CONTINUITY_KEY}
    positions={key:index for index,key in enumerate(inserts)}
    assert max(positions[k] for k in complete_generation if not k.endswith('-finalized.json'))<min(positions[k] for k in complete_generation if k.endswith('-finalized.json'))
    assert all('ON CONFLICT' not in query for query,_ in c.calls)


def test_injected_failure_stops_before_markers_links_and_protocol(complete_generation):
    c=Connection()
    with pytest.raises(InjectedFailure):stage(c,originals(),complete_generation,{k:'corrected' for k in TARGETS},'links','audit','archive',inject=True)
    assert len(c.calls)==6
    assert all(params[1]!=CONTINUITY_KEY for query,params in c.calls if query.startswith('INSERT'))
    assert not any(query.startswith('UPDATE') and params[2]==PROTOCOL_FILE for query,params in c.calls)


@pytest.mark.parametrize('change',['missing_candidate','extra_update','missing_original','collision'])
def test_invalid_or_expanded_write_scope_is_refused_before_sql(complete_generation,change):
    c=Connection();before=originals();corrections={k:'corrected' for k in TARGETS};audit_key='audit'
    if change=='missing_candidate':del complete_generation['2025-01-finalized.json']
    if change=='extra_update':corrections['2026-03-lot-book.json']='bad'
    if change=='missing_original':del before[PROTOCOL_FILE]
    if change=='collision':audit_key=CONTINUITY_KEY
    with pytest.raises(ValueError):stage(c,before,complete_generation,corrections,'links',audit_key,'archive')
    assert c.calls==[]
