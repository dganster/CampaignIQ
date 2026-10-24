"""Admission, stale-source and atomic write-order checks for local restore tests."""
import gzip
import json
from types import SimpleNamespace
import pytest

from tests.test_historical_publication_package import inputs
from tests.test_historical_publication_rehearsal import complete_generation
from campaigniq.historical_backfill_bundle import encode
from campaigniq.historical_backfill_publication_package import build_package, save_package, digest
from campaigniq.historical_backfill_restored_publisher import (
    local_database, load_inputs, stage, InjectedFailure, PROTOCOL_FILE,
)


@pytest.mark.parametrize('name',['campaigniq_production_db','campaigniq_dev','postgres',
    'campaigniq_restore_20261024_194114; DROP DATABASE postgres',
    'campaigniq_restore_20261024_194114 host=example.com','campaigniq_restore_old'])
def test_production_and_ambiguous_database_names_are_refused(name):
    with pytest.raises(ValueError):local_database(name)


@pytest.mark.parametrize('variable',['RENDER','RENDER_SERVICE_ID'])
def test_render_execution_is_refused(monkeypatch,variable):
    monkeypatch.setenv(variable,'present')
    with pytest.raises(ValueError):local_database('campaigniq_restore_20261024_194114')


def test_separately_named_local_restore_is_accepted():
    assert local_database('campaigniq_restore_20261024_194114')=='campaigniq_restore_20261024_194114'


@pytest.fixture
def prepared(inputs,tmp_path):
    bundle,report,collector,rehearsal=inputs
    # A real publication package requires the original protocol artifact.
    bundle['artifacts'][PROTOCOL_FILE]='original protocol'
    h=digest(encode(bundle));report['capture_sha256']=h;report['boundary_plan']['capture_sha256']=h;rehearsal['capture_sha256']=h
    package=build_package(*inputs)
    folder=tmp_path/'package';save_package(folder,package,collector.artifacts)
    raw=encode(bundle);packed=gzip.compress(raw)
    capture=tmp_path/'workspace-capture.json.gz';capture.write_bytes(packed)
    (tmp_path/'capture-verification.json').write_text(json.dumps(dict(capture_sha256=digest(raw),compressed_file_sha256=digest(packed))))
    return folder,capture


def test_complete_source_bound_inputs_load_without_database_access(prepared):
    package,bundle,artifacts=load_inputs(*prepared)
    assert len(artifacts)==108
    assert package['publication_authorized'] is False
    assert PROTOCOL_FILE in bundle['artifacts']


@pytest.mark.parametrize('change',['payload','completion','capture','manifest','overlap','documents','approval','traversal'])
def test_changed_or_unsafe_package_is_rejected_before_connecting(prepared,change):
    folder,capture=prepared
    path=folder/'publication-package.json';p=json.loads(path.read_text())
    if change=='payload':(folder/'candidates'/'2025-01-finalized.json').write_text('{}')
    elif change=='completion':(folder/'package-complete.json').write_text('{}')
    elif change=='capture':capture.write_bytes(gzip.compress(b'{}'))
    else:
        if change=='manifest':p['candidate_artifact_count']=0
        if change=='overlap':p['existing_artifact_sha256']['2025-01-finalized.json']='bad'
        if change=='documents':p['retained_documents_sha256']='bad'
        if change=='approval':p['publication_authorized']=True
        if change=='traversal':p['candidate_artifact_sha256']['../../outside']='bad'
        path.write_text(json.dumps(p))
        (folder/'package-complete.json').write_text(json.dumps(dict(package_sha256=digest(path.read_bytes()),publication_authorized=False)))
    with pytest.raises(ValueError):load_inputs(folder,capture)


class RecordingConnection:
    def __init__(self,rowcount=1):self.calls=[];self.rowcount=rowcount
    def execute(self,query,params):
        self.calls.append((query,params))
        return SimpleNamespace(rowcount=self.rowcount)


def test_markers_and_protocol_follow_all_payloads():
    c=RecordingConnection()
    artifacts={'2025-01-finalized.json':'marker','2025-02-finalized.json':'marker2',
        '2025-01-lot-book.json':'book','2025-02-realized-attributions.json':'records'}
    stage(c,artifacts)
    keys=[params[1] for _,params in c.calls[:-1]]
    assert keys==['2025-01-lot-book.json','2025-02-realized-attributions.json','2025-01-finalized.json','2025-02-finalized.json']
    assert all('ON CONFLICT' not in query for query,_ in c.calls)
    query,params=c.calls[-1]
    assert query.startswith('UPDATE') and params[-1]==PROTOCOL_FILE
    assert json.loads(params[0])['first_protected_period_end']=='2025-01-31'


def test_injected_failure_never_reaches_protocol_update():
    c=RecordingConnection()
    with pytest.raises(InjectedFailure):stage(c,{f'2025-01-{i}.json':'payload' for i in range(5)},inject_failure=True)
    assert len(c.calls)==3 and all(q.startswith('INSERT') for q,_ in c.calls)


def test_missing_protocol_aborts_instead_of_silently_publishing():
    with pytest.raises(ValueError,match='protocol'):stage(RecordingConnection(rowcount=0),{})
