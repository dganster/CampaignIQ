from copy import deepcopy
import json
import sys
from types import SimpleNamespace
import pytest

from campaigniq.historical_backfill_production_readiness import (
    SERVICE,PREFIX,workspace_manifest,readiness_script,parse_readiness,assess_readiness,
    validate_runtime_paths,make_plan,validate_saved_plan,save,PROTOCOL_FILE,TARGETS,
)
from campaigniq.historical_backfill_publication_package import canonical,digest


@pytest.fixture
def plan():
    return dict(format='campaigniq.guarded_historical_publication_plan',service_id=SERVICE,database='campaigniq_production_db',
        expected_workspace={'reviewed':'state'},required_runtime_source_sha256={'src/campaigniq/__init__.py':'f'*64},
        production_write_implemented=False,publication_authorized=False)


@pytest.fixture
def observed(plan):
    return dict(service_id=SERVICE,database='campaigniq_production_db',workspace_manifest=plan['expected_workspace'],
        runtime_source_sha256=plan['required_runtime_source_sha256'],deployed_commit='a'*40,
        database_read_only_verified=True,publication_authorized=False)


def test_matching_readiness_never_authorizes_or_enables_a_write(plan,observed):
    result=assess_readiness(plan,observed)
    assert result['read_only_preconditions_passed']
    assert not result['production_ready'] and not result['publication_authorized']
    assert not result['production_write_implemented']


@pytest.mark.parametrize('change',['draft','document','artifact','runtime','commit'])
def test_any_stale_precondition_blocks_readiness(plan,observed,change):
    observed=deepcopy(observed)
    if change in ('draft','document','artifact'):observed['workspace_manifest'][change]='changed'
    if change=='runtime':observed['runtime_source_sha256']={}
    if change=='commit':observed['deployed_commit']=None
    result=assess_readiness(plan,observed)
    assert not result['read_only_preconditions_passed'] and result['blocking_findings']


@pytest.mark.parametrize('field,value',[('service_id','wrong'),('database','campaigniq_dev'),('database_read_only_verified',False)])
def test_wrong_remote_identity_is_refused(plan,observed,field,value):
    observed[field]=value
    with pytest.raises(ValueError):assess_readiness(plan,observed)


def response(data,nonce='a'*32):
    return PREFIX+json.dumps(dict(nonce=nonce,sha256=digest(canonical(data)),data=data))


@pytest.mark.parametrize('change',['duplicate','nonce','checksum','service','readwrite'])
def test_remote_response_is_single_source_bound_and_read_only(observed,change):
    if change=='service':observed['service_id']='wrong'
    if change=='readwrite':observed['database_read_only_verified']=False
    text=response(observed)
    if change=='duplicate':text+='\n'+text
    if change=='nonce':text=response(observed,'b'*32)
    if change=='checksum':text=text.replace(digest(canonical(observed)),'0'*64)
    with pytest.raises(ValueError):parse_readiness(text,'a'*32)


@pytest.mark.parametrize('path',['/tmp/code.py','src/campaigniq/../secret.py','src/other/code.py','src/campaigniq/a;bad.py'])
def test_runtime_paths_cannot_escape_the_package(path):
    with pytest.raises(ValueError):validate_runtime_paths([path])


def test_generated_remote_program_executes_only_read_only_database_queries(tmp_path,monkeypatch,capsys):
    monkeypatch.chdir(tmp_path)
    package=tmp_path/'src/campaigniq';package.mkdir(parents=True);(package/'__init__.py').write_text('# runtime')
    queries=[]
    class Connection:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,query,params=None):
            queries.append(query)
            if query.startswith('SELECT current_database'):rows=[('campaigniq_production_db','on','repeatable read')]
            else:rows=[]
            return SimpleNamespace(fetchone=lambda:rows[0],fetchall=lambda:rows)
    def connect(dsn,**options):
        assert 'default_transaction_read_only=on' in options['options']
        assert 'timezone=UTC' in options['options']
        return Connection()
    monkeypatch.setitem(sys.modules,'psycopg',SimpleNamespace(connect=connect))
    monkeypatch.setenv('RENDER_SERVICE_ID',SERVICE);monkeypatch.setenv('RENDER_GIT_COMMIT','a'*40)
    monkeypatch.setenv('CAMPAIGNIQ_DEV_DATABASE_URL','secret-fixture-dsn')
    code=readiness_script('a'*32,['src/campaigniq/__init__.py'])
    compile(code,'<remote-readiness>','exec');exec(code,{})
    output=capsys.readouterr().out;data=parse_readiness(output,'a'*32)
    assert data['runtime_source_sha256']['src/campaigniq/__init__.py']==digest(b'# runtime')
    assert queries[0]=='BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY' and queries[-1]=='ROLLBACK'
    assert all(q.startswith(('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY','SELECT ','ROLLBACK')) for q in queries)
    assert 'secret-fixture-dsn' not in output


def test_workspace_fingerprint_is_order_independent_but_retains_document_bytes():
    b=dict(workspace='workspace-dennis',database='campaigniq_production_db',artifacts={'a':'one'},
        drafts=[dict(id='b',revision=1),dict(id='a',revision=2)],documents=[dict(id='b',content_base64='abc'),dict(id='a',content_base64='def')])
    before=workspace_manifest(b);b['drafts'].reverse();b['documents'].reverse()
    assert workspace_manifest(b)==before
    b['documents'][0]['content_base64']='changed'
    assert workspace_manifest(b)!=before


def test_plan_write_scope_archives_two_corrections_and_preserves_other_2026_records():
    artifacts={PROTOCOL_FILE:'protocol',**{k:'old' for k in TARGETS},'2026-01-lot-book.json':'preserve'}
    bundle=dict(workspace='workspace-dennis',database='campaigniq_production_db',artifacts=artifacts,drafts=[],documents=[])
    package=dict(capture_sha256='capture',protected_2026_artifact_sha256={k:digest(v.encode()) for k,v in artifacts.items() if k.startswith('2026-')},contract_mapping_candidates={})
    plan,payloads=make_plan(bundle,package,{'2025-01-finalized.json':'candidate'},{k:'new' for k in TARGETS},{}, {},
        dict(development_commit='a'*40,backup_sha256='b'*64),{'src/campaigniq/__init__.py':'c'*64},'a'*32)
    updates={op['key'] for op in plan['operations'] if op['action']=='compare_and_swap'}
    assert updates==TARGETS|{PROTOCOL_FILE}
    assert plan['protected_2026_artifact_sha256']=={'2026-01-lot-book.json':digest(b'preserve')}
    assert not plan['publication_authorized'] and not plan['production_write_implemented']
    assert '2026-01-lot-book.json' not in payloads


@pytest.mark.parametrize('change',['payload','archive','marker'])
def test_saved_plan_cannot_be_checked_after_input_tampering(tmp_path,change):
    material={'source':'original'};payload=b'payload'
    plan=dict(publication_authorized=False,production_write_implemented=False,payload_sha256={'safe.json':digest(payload)},archive_material_sha256=digest(canonical(material)))
    (tmp_path/'payloads').mkdir();(tmp_path/'payloads/safe.json').write_bytes(payload)
    save(tmp_path/'publication-plan.json',plan);save(tmp_path/'archive-material.json',material)
    save(tmp_path/'plan-complete.json',dict(plan_sha256=digest(canonical(plan)),publication_authorized=False))
    assert validate_saved_plan(tmp_path)==plan
    if change=='payload':(tmp_path/'payloads/safe.json').write_bytes(b'changed')
    if change=='archive':(tmp_path/'archive-material.json').write_text('{}')
    if change=='marker':(tmp_path/'plan-complete.json').write_text('{}')
    with pytest.raises(ValueError):validate_saved_plan(tmp_path)
