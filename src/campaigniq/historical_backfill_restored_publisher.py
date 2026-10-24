"""Exercise an atomic historical publication on a restored LOCAL database only.

Always reverses the committed experiment. No production execution or approval.
"""
import argparse
import base64
import calendar
from datetime import date, datetime, timezone
from hashlib import sha256
import gzip
import json
import os
from pathlib import Path
import re
import subprocess
import uuid

from campaigniq.historical_backfill_bundle import decode, validate, DRAFT_FIELDS, DOC_FIELDS, LIMIT
from campaigniq.historical_backfill_candidate_payloads import validate_generation
from campaigniq.historical_backfill_publication_package import canonical, digest
from campaigniq.persistence.monthly_publication import (
    PROTOCOL_FILE, _serialize_publication_protocol, is_month_published_in_storage,
)

DATABASE_PATTERN = r'campaigniq_restore_[0-9]{8}_[0-9]{6}'
WORKSPACE = 'workspace-dennis'
TABLES = ('artifact_storage_dev', 'import_drafts', 'import_draft_documents')


def local_database(name):
    if os.environ.get('RENDER') or os.environ.get('RENDER_SERVICE_ID'):
        raise ValueError('Restored-database publication tests refuse Render.')
    if not re.fullmatch(DATABASE_PATTERN, name):
        raise ValueError('Only a separately named local restored database is permitted.')
    return name


def connect_local(name):
    import psycopg
    local_database(name)
    if any(os.environ.get(k) for k in ('PGHOSTADDR','PGSERVICE','PGSERVICEFILE','PGOPTIONS')):
        raise ValueError('Clear inherited PostgreSQL routing options before this local test.')
    connection = psycopg.connect(host='/var/run/postgresql', port=5432, dbname=name,
        user=__import__('getpass').getuser(), connect_timeout=10, autocommit=True,
        options='-c timezone=UTC -c lock_timeout=10000 -c statement_timeout=120000')
    identity = connection.execute('SELECT current_database(), inet_server_addr()::text').fetchone()
    if identity != (name, None):
        connection.close()
        raise ValueError('The server is not the requested local socket database.')
    return connection


def load_inputs(folder, capture):
    """Validate completion and source manifests before any database connection."""
    marker = decode((folder/'package-complete.json').read_bytes())
    data = (folder/'publication-package.json').read_bytes()
    if digest(data) != marker.get('package_sha256') or marker.get('publication_authorized') is not False:
        raise ValueError('Package completion checksum or authorization differs.')
    package = decode(data)
    if (package.get('format') != 'campaigniq.historical_publication_package'
        or package.get('version') != 1 or package.get('workspace') != WORKSPACE
        or package.get('production_ready') is not False or package.get('publication_authorized') is not False):
        raise ValueError('Expected an unapproved offline publication package.')
    artifacts = {key:(folder/'candidates'/key).read_text() for key in package['candidate_artifact_sha256']
        if re.fullmatch(r'2025-(0[1-9]|1[0-2])-[a-z-]+\.json',key)}
    if {key:digest(text.encode()) for key,text in artifacts.items()} != package['candidate_artifact_sha256']:
        raise ValueError('Candidate payload checksums differ.')
    validate_generation(artifacts)
    if len(artifacts) != package['candidate_artifact_count']:
        raise ValueError('Candidate artifact count differs.')
    verification = decode(capture.with_name('capture-verification.json').read_bytes())
    if digest(capture.read_bytes()) != verification['compressed_file_sha256']:
        raise ValueError('Compressed capture checksum differs.')
    with gzip.open(capture,'rb') as stream:
        raw = stream.read(LIMIT+1)
    if len(raw)>LIMIT or digest(raw) != verification['capture_sha256'] or digest(raw) != package['capture_sha256']:
        raise ValueError('Package and capture checksums differ.')
    bundle = validate(decode(raw))
    expected = {k:digest(v.encode()) for k,v in bundle['artifacts'].items()}
    if expected != package['existing_artifact_sha256'] or set(artifacts)&set(expected):
        raise ValueError('Existing artifacts differ or candidate keys overlap.')
    if (digest(canonical(bundle['documents'])) != package['retained_documents_sha256']
        or digest(canonical(bundle['drafts'])) != package['retained_drafts_sha256']):
        raise ValueError('Retained drafts or documents differ.')
    protected = {k:v for k,v in expected.items() if k.startswith('2026-')}
    if not protected or protected != package['protected_2026_artifact_sha256']:
        raise ValueError('Protected artifact manifest differs.')
    if PROTOCOL_FILE not in bundle['artifacts']:
        raise ValueError('Original publication protocol is required.')
    return package, bundle, artifacts


def workspace_state(connection):
    def scalar(value):
        if isinstance(value,datetime):return value.astimezone(timezone.utc).isoformat()
        if isinstance(value,uuid.UUID):return str(value)
        return value
    artifacts = dict(connection.execute('SELECT artifact_key,content FROM public.artifact_storage_dev WHERE namespace=%s',(WORKSPACE,)).fetchall())
    drafts = [dict(zip(DRAFT_FIELDS.split(','),map(scalar,row))) for row in connection.execute(
        'SELECT '+DRAFT_FIELDS+' FROM public.import_drafts WHERE workspace=%s',(WORKSPACE,)).fetchall()]
    documents = []
    fields = DOC_FIELDS.split(',')
    rows = connection.execute('SELECT '+','.join('d.'+f for f in fields)+
        ' FROM public.import_draft_documents d JOIN public.import_drafts p ON p.id=d.draft_id WHERE p.workspace=%s',(WORKSPACE,)).fetchall()
    for row in rows:
        item = dict(zip(fields,map(scalar,row)))
        content = bytes(item.pop('content'))
        if digest(content) != item['sha256']:raise ValueError('A restored document checksum differs.')
        item['content_base64'] = base64.b64encode(content).decode()
        documents.append(item)
    return artifacts, sorted(drafts,key=lambda r:r['id']), sorted(documents,key=lambda r:r['id'])


def require_workspace(connection,bundle,artifacts=None):
    actual = workspace_state(connection)
    expected = (bundle['artifacts'] if artifacts is None else artifacts,
        sorted(bundle['drafts'],key=lambda r:r['id']), sorted(bundle['documents'],key=lambda r:r['id']))
    if actual != expected:raise ValueError('Database differs from the saved capture; no replacement is permitted.')


def table_manifest(connection):
    """Hash all user-table rows, including other workspaces and row timestamps."""
    from psycopg import sql
    tables = connection.execute("SELECT schemaname,tablename FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema') AND schemaname NOT LIKE 'pg_toast%' ORDER BY 1,2").fetchall()
    result = {}
    for schema,table in tables:
        rows = sorted(row[0] for row in connection.execute(sql.SQL('SELECT to_jsonb(t)::text FROM {}.{} t').format(sql.Identifier(schema),sql.Identifier(table))).fetchall())
        result[schema+'.'+table] = dict(rows=len(rows),sha256=digest(canonical(rows)))
    return result


def lock_tables(connection):
    connection.execute('LOCK TABLE public.artifact_storage_dev, public.import_drafts, public.import_draft_documents IN SHARE ROW EXCLUSIVE MODE')


class PublishedView:
    def __init__(self,artifacts):self.artifacts=artifacts
    def exists(self,key):return key in self.artifacts
    def read_text(self,key):return self.artifacts[key]


def stage(connection,artifacts,*,inject_failure=False):
    for number,key in enumerate(sorted(artifacts,key=lambda k:(k.endswith('-finalized.json'),k)),1):
        connection.execute('INSERT INTO public.artifact_storage_dev(namespace,artifact_key,content) VALUES (%s,%s,%s)',(WORKSPACE,key,artifacts[key]))
        if inject_failure and number==3:raise InjectedFailure('controlled transaction failure')
    result = connection.execute('UPDATE public.artifact_storage_dev SET content=%s,updated_at=now() WHERE namespace=%s AND artifact_key=%s',
        (_serialize_publication_protocol(first_period_end=date(2025,1,31)),WORKSPACE,PROTOCOL_FILE))
    if result.rowcount != 1:raise ValueError('Publication protocol row is missing or ambiguous.')


class InjectedFailure(Exception):pass


def experiment(connection,bundle,artifacts,output):
    """Commit on the clone, verify through a fresh connection, then undo exactly."""
    local_database(connection.info.dbname)
    baseline = None
    with connection.transaction():
        lock_tables(connection)
        require_workspace(connection,bundle)
        baseline = table_manifest(connection)
        protocol = connection.execute('SELECT content,updated_at FROM public.artifact_storage_dev WHERE namespace=%s AND artifact_key=%s',(WORKSPACE,PROTOCOL_FILE)).fetchone()
        try:
            with connection.transaction():stage(connection,artifacts,inject_failure=True)
        except InjectedFailure:pass
        if table_manifest(connection) != baseline:raise ValueError('Injected failure did not roll back exactly.')
    receipt = dict(database=connection.info.dbname,namespace=WORKSPACE,
        candidate_artifact_sha256={k:digest(v.encode()) for k,v in artifacts.items()},
        original_protocol_content=protocol[0],original_protocol_updated_at=protocol[1].isoformat(),
        baseline_table_manifest=baseline,publication_authorized=False)
    save(output/'recovery-receipt.json',receipt)
    expected = dict(bundle['artifacts'],**artifacts)
    expected[PROTOCOL_FILE] = _serialize_publication_protocol(first_period_end=date(2025,1,31))
    committed = False
    try:
        with connection.transaction():
            lock_tables(connection)
            require_workspace(connection,bundle)
            if table_manifest(connection) != baseline:raise ValueError('Restored database changed before staging.')
            stage(connection,artifacts)
            require_workspace(connection,bundle,expected)
        committed = True
        with connect_local(connection.info.dbname) as observer:
            with observer.transaction():
                observer.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                require_workspace(observer,bundle,expected)
                view = PublishedView(workspace_state(observer)[0])
                for year,last in ((2025,12),(2026,9)):
                    for month in range(1,last+1):
                        if not is_month_published_in_storage(view,period_end=date(year,month,calendar.monthrange(year,month)[1])):
                            raise ValueError('A staged or protected month is not visible to the publication reader.')
    finally:
        if committed:
            with connection.transaction():
                lock_tables(connection)
                require_workspace(connection,bundle,expected)
                for key in artifacts:
                    deleted=connection.execute('DELETE FROM public.artifact_storage_dev WHERE namespace=%s AND artifact_key=%s AND content=%s',(WORKSPACE,key,artifacts[key]))
                    if deleted.rowcount != 1:raise ValueError('Candidate changed; automatic rollback refused.')
                restored=connection.execute('UPDATE public.artifact_storage_dev SET content=%s,updated_at=%s WHERE namespace=%s AND artifact_key=%s',(*protocol,WORKSPACE,PROTOCOL_FILE))
                if restored.rowcount != 1:raise ValueError('Protocol rollback failed.')
                require_workspace(connection,bundle)
                if table_manifest(connection) != baseline:raise ValueError('Committed rollback differs from original table rows.')
    with connect_local(connection.info.dbname) as observer:
        with observer.transaction():
            observer.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            require_workspace(observer,bundle)
            if table_manifest(observer) != baseline:raise ValueError('Rollback is not visible from a fresh connection.')
    return dict(format='campaigniq.restored_database_publication_test',version=1,
        local_restored_database=connection.info.dbname,transaction_failure_rollback_verified=True,
        committed_publication_reader_verified=True,exact_all_user_table_rollback_verified=True,
        candidate_artifact_count=len(artifacts),protected_2026_artifact_count=sum(k.startswith('2026-') for k in bundle['artifacts']),
        production_connection_made=False,publication_authorized=False,
        full_portfolio_continuity_verified=False,contract_mappings_approved=False)


def save(path,value):
    with path.open('x',encoding='utf-8') as stream:
        os.chmod(path,0o600)
        json.dump(value,stream,sort_keys=True,indent=2,default=str)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--package',type=Path,required=True)
    parser.add_argument('--capture',type=Path,required=True)
    parser.add_argument('--local-restored-database',required=True)
    parser.add_argument('--backup',type=Path,required=True)
    parser.add_argument('--backup-sha256',required=True)
    parser.add_argument('--development-only',action='store_true',required=True)
    args=parser.parse_args()
    local_database(args.local_restored_database)
    repo=args.repo.expanduser().resolve()
    if Path(__file__).resolve().parents[2] != repo:parser.error('Run the installed development module.')
    def git(*parts):return subprocess.check_output(['git','-C',str(repo),*parts],text=True).strip()
    if git('branch','--show-current')!='feature/historical-backfill-2025' or git('status','--porcelain'):
        parser.error('Use the clean, committed development branch.')
    if digest(args.backup.expanduser().read_bytes()) != args.backup_sha256:
        parser.error('Full production backup checksum differs.')
    package,bundle,artifacts=load_inputs(args.package.expanduser(),args.capture.expanduser())
    output=Path.home()/'Downloads'/('CampaignIQ-restored-publication-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8])
    output.mkdir(mode=0o700,exist_ok=False)
    print('Testing the local full restore; production connections are disabled.',flush=True)
    try:
        with connect_local(args.local_restored_database) as connection:
            result=experiment(connection,bundle,artifacts,output)
    except Exception as error:
        save(output/'failure.json',dict(error_type=type(error).__name__,message=str(error) if isinstance(error,ValueError) else 'Database test failed; retained recovery receipt and backup are required for diagnosis.',publication_authorized=False))
        parser.exit(1,'Local test failed. Diagnostics: '+str(output)+'\n')
    result.update(backup_sha256=args.backup_sha256,package_sha256=digest((args.package.expanduser()/'publication-package.json').read_bytes()),development_commit=git('rev-parse','HEAD'))
    save(output/'restored-publication-report.json',result)
    print('Injected transaction rollback: PASS\nCommitted publication reader for 2025 and 2026: PASS\nExact rollback across all user tables: PASS')
    print('Report:',output/'restored-publication-report.json')
    print('Local restored database returned to its original state. Nothing was published to production.')


if __name__=='__main__':main()
