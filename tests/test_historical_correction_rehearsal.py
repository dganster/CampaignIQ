import copy
from datetime import datetime, timezone
import json
from types import SimpleNamespace
import pytest
from tests.test_historical_correction_preview import evidence
from campaigniq.historical_backfill_correction_preview import build_preview
from campaigniq.historical_backfill_publication_package import digest
from campaigniq.historical_backfill_correction_rehearsal import (
    verified_preview,conditional_update,apply_trial,InjectedFailure,TARGETS,
)


@pytest.fixture
def reviewed(evidence):
    artifacts,records=evidence;pdf=b'%PDF reviewed evidence'
    preview,proposals=build_preview(artifacts,records,digest(pdf))
    preview['read_only_database_verified']=True
    return artifacts,records,preview,proposals,pdf


def test_proposals_rebuild_exactly_without_approving_or_mutating_evidence(reviewed):
    before=copy.deepcopy(reviewed)
    assert verified_preview(*reviewed)==reviewed[3]
    assert reviewed==before
    assert reviewed[2]['corrections_approved'] is False


@pytest.mark.parametrize('change',['source_pdf','source_record','preview_change','original_hash',
    'proposal_bytes','approved','authorized','read_only','extra_target'])
def test_stale_changed_or_approved_inputs_are_refused(reviewed,change):
    artifacts,records,preview,proposals,pdf=reviewed
    if change=='source_pdf':pdf+=b'changed'
    if change=='source_record':records=records[:1]
    if change=='preview_change':preview['changes'][1]['new_lot_id']='WRONG'
    if change=='original_hash':preview['existing_artifact_sha256']['2026-02-realized-attributions.json']='bad'
    if change=='proposal_bytes':proposals['2026-02-realized-attributions.json']+=' '
    if change=='approved':preview['corrections_approved']=True
    if change=='authorized':preview['publication_authorized']=True
    if change=='read_only':preview['read_only_database_verified']=False
    if change=='extra_target':proposals['2026-03-lot-book.json']='{}'
    with pytest.raises(ValueError):verified_preview(artifacts,records,preview,proposals,pdf)


class Connection:
    def __init__(self,rowcount=1):self.calls=[];self.rowcount=rowcount
    def execute(self,query,params):
        self.calls.append((query,params))
        return SimpleNamespace(rowcount=self.rowcount)


def test_compare_and_swap_binds_namespace_original_bytes_and_timestamp():
    connection=Connection();timestamp=datetime(2026,10,24,tzinfo=timezone.utc)
    conditional_update(connection,'2026-02-realized-attributions.json','original','proposed',timestamp)
    query,params=connection.calls[0]
    assert 'AND content=%s AND updated_at=%s' in query
    assert params==('proposed','workspace-dennis','2026-02-realized-attributions.json','original',timestamp)


@pytest.mark.parametrize('rows',[0,2])
def test_failed_compare_and_swap_aborts_the_transaction(rows):
    with pytest.raises(ValueError,match='changed'):
        conditional_update(Connection(rows),'2026-02-realized-attributions.json','before','after',datetime.now(timezone.utc))


def test_unreviewed_update_target_is_refused_before_sql():
    connection=Connection()
    with pytest.raises(ValueError):conditional_update(connection,'2026-03-lot-book.json','a','b',None)
    assert not connection.calls


def test_archive_is_inserted_before_either_correction_and_failure_stops_second_update():
    connection=Connection();stamp=datetime(2026,10,24,tzinfo=timezone.utc)
    originals={key:('original',stamp) for key in TARGETS};proposals={key:'proposed' for key in TARGETS}
    with pytest.raises(InjectedFailure):apply_trial(connection,originals,proposals,'audit','archived original/source',inject=True)
    assert len(connection.calls)==2
    assert connection.calls[0][0].startswith('INSERT')
    assert connection.calls[1][0].startswith('UPDATE')
    assert connection.calls[0][1]==('workspace-dennis','audit','archived original/source')


def test_scope_must_be_exact_before_archiving_or_mutating():
    connection=Connection()
    with pytest.raises(ValueError):apply_trial(connection,{}, {},'audit','source')
    assert connection.calls==[]
