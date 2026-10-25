"""Isolated LOCAL workspace publication simulation and logical restore rehearsal.

Never connects to Render. This does not restore a physical/full production DB,
authorize aliases, or provide a production publication path.
"""
import argparse
from datetime import date,datetime,timezone
import gzip
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid

from campaigniq.historical_backfill_bundle import decode,encode,LIMIT
from campaigniq.historical_backfill_candidate_payloads import CandidateCollector,validate_generation
from campaigniq.historical_backfill_preflight import run_preflight
from campaigniq.persistence.monthly_publication import PROTOCOL_FILE,_serialize_publication_protocol,is_month_published_in_storage


def local_parameters(dsn):
    from psycopg.conninfo import conninfo_to_dict
    if os.environ.get('RENDER') or os.environ.get('RENDER_SERVICE_ID'):
        raise ValueError('Rehearsal refuses Render.')
    p=conninfo_to_dict(dsn)
    if set(p)-{'host','port','dbname','user','password','connect_timeout','sslmode','application_name'}:
        raise ValueError('Service files, host addresses and connection options are not permitted.')
    if p.get('dbname')!='campaigniq_dev' or p.get('host') not in ('/var/run/postgresql','localhost','127.0.0.1','::1'):
        raise ValueError('Only the explicitly local campaigniq_dev database is permitted.')
    if not p.get('port','5432').isdigit() or not 1<=int(p.get('port','5432'))<=65535:
        raise ValueError('One local port is required.')
    p.setdefault('port','5432')
    if p['host']=='localhost':p['host']='127.0.0.1'
    p['connect_timeout']='10'
    return p


def schema_name(value):
    if not re.fullmatch(r'ciq_rehearsal_[0-9a-f]{12}(?:_baseline|_staged)?',value):
        raise ValueError('Only a generated rehearsal schema is permitted.')
    return value


def command_environment(parameters):
    env={k:v for k,v in os.environ.items() if not k.startswith('PG')}
    for key,name in [('host','PGHOST'),('port','PGPORT'),('dbname','PGDATABASE'),('user','PGUSER'),('password','PGPASSWORD')]:
        if key in parameters:env[name]=parameters[key]
    env['PGCONNECT_TIMEOUT']='10'
    return env


def tools_for_server(major):
    result={}
    for name in ('pg_dump','pg_restore'):
        exact=Path('/usr/lib/postgresql')/str(major)/'bin'/name
        binary=str(exact) if exact.is_file() else shutil.which(name)
        if not binary:raise ValueError('PostgreSQL backup/restore tools are missing.')
        version=subprocess.run([binary,'--version'],check=True,capture_output=True,text=True)
        match=re.search(r'PostgreSQL\)?\s+(\d+)',version.stdout)
        if match is None or int(match[1])!=major:
            raise ValueError('Install backup/restore clients matching the LOCAL server major version '+str(major)+'.')
        result[name]=binary
    return result


class SchemaStorage:
    def __init__(self,connection,schema,namespace):
        self.connection=connection;self.schema=schema_name(schema);self.namespace=namespace

    def _query(self,text,values=()):
        from psycopg import sql
        return self.connection.execute(sql.SQL(text).format(sql.Identifier(self.schema)),values)

    def read_text(self,key):
        row=self._query('SELECT content FROM {}.artifact_storage_dev WHERE namespace=%s AND artifact_key=%s',(self.namespace,key)).fetchone()
        if row is None:raise FileNotFoundError(key)
        return row[0]

    def exists(self,key):
        return self._query('SELECT 1 FROM {}.artifact_storage_dev WHERE namespace=%s AND artifact_key=%s',(self.namespace,key)).fetchone() is not None

    def list_keys(self,*,prefix='',suffix=''):
        return tuple(row[0] for row in self._query('SELECT artifact_key FROM {}.artifact_storage_dev WHERE namespace=%s ORDER BY artifact_key',(self.namespace,)).fetchall() if row[0].startswith(prefix) and row[0].endswith(suffix))

    def all_artifacts(self):
        return {key:self.read_text(key) for key in self.list_keys()}

    def insert_candidate(self,key,content):
        if not re.fullmatch(r'2025-(?:0[1-9]|1[0-2])-[a-z-]+\.json',key):
            raise ValueError('Only new 2025 candidate artifacts may be inserted.')
        self._query('INSERT INTO {}.artifact_storage_dev(namespace,artifact_key,content) VALUES (%s,%s,%s)',(self.namespace,key,content))


def install_guard(connection,schema,namespace):
    from psycopg import sql
    schema_name(schema)
    connection.execute(sql.SQL('''CREATE FUNCTION {}.protect_artifacts() RETURNS trigger LANGUAGE plpgsql AS $body$
    BEGIN
      IF TG_OP='INSERT' THEN
        IF NEW.namespace=TG_ARGV[0] AND NEW.artifact_key ~ '^2025-(0[1-9]|1[0-2])-[a-z-]+[.]json$' THEN RETURN NEW; END IF;
      END IF;
      IF TG_OP='UPDATE' THEN
        IF OLD.artifact_key='.campaigniq-monthly-publication-v1.json'
           AND NEW.artifact_key=OLD.artifact_key AND NEW.namespace=OLD.namespace AND NEW.namespace=TG_ARGV[0] THEN
          IF NEW.content::jsonb->>'first_protected_period_end'='2025-01-31'
             AND NEW.content::jsonb->>'format'='campaigniq.monthly_publication'
             AND NEW.content::jsonb->>'version'='1' THEN RETURN NEW; END IF;
        END IF;
      END IF;
      RAISE EXCEPTION 'Rehearsal protected artifact mutation refused';
    END; $body$;''').format(sql.Identifier(schema)))
    connection.execute(sql.SQL('CREATE TRIGGER protected_artifacts BEFORE INSERT OR UPDATE OR DELETE ON {}.artifact_storage_dev FOR EACH ROW EXECUTE FUNCTION {}.protect_artifacts({})').format(sql.Identifier(schema),sql.Identifier(schema),sql.Literal(namespace)))


def _connect(parameters):
    import psycopg
    parameters=local_parameters(psycopg.conninfo.make_conninfo(**parameters))
    if any(os.environ.get(key) for key in ('PGHOSTADDR','PGSERVICE','PGSERVICEFILE','PGOPTIONS')):
        raise ValueError('Inherited PostgreSQL routing options must be cleared for local rehearsal.')
    connection=psycopg.connect(**parameters,autocommit=True,prepare_threshold=None)
    db,addr=connection.execute('SELECT current_database(),inet_server_addr()::text').fetchone()
    if db!='campaigniq_dev' or addr not in (None,'127.0.0.1','::1'):
        connection.close();raise ValueError('Server identity is not the local development database.')
    return connection


def _save(path,value):
    with path.open('x',encoding='utf-8') as f:
        os.chmod(path,0o600);json.dump(value,f,indent=2,sort_keys=True,default=str);f.write('\n')


def rehearse_database(bundle,candidates,parameters,output):
    """Create unique schemas, restore our own dump, simulate, then restore again."""
    validate_generation(candidates)
    from psycopg import sql
    from psycopg.types.json import Jsonb
    from psycopg.errors import RaiseException
    schema=schema_name('ciq_rehearsal_'+uuid.uuid4().hex[:12]);env=command_environment(parameters)
    baseline=bundle['artifacts'];namespace=bundle['workspace'];backup=output/'local-workspace-backup.dump'
    report=dict(scope='isolated_local_workspace_copy',schema=schema,baseline_schema=schema+'_baseline',staged_schema=schema+'_staged',
        local_workspace_restore_verified=False,transaction_rollback_verified=False,committed_stage_verified=False,
        protected_record_rejection_verified=False,backup_restore_rollback_verified=False,
        full_production_database_restore_verified=False,production_connection_made=False,publication_authorized=False)
    _save(output/'isolated-target.json',report)
    # Baseline loading is confined to this freshly created schema. No public DDL.
    with _connect(parameters) as connection:
        major=int(connection.execute('SHOW server_version_num').fetchone()[0])//10000
        tools=tools_for_server(major);report['local_server_major']=major
        with connection.transaction():
            connection.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
            connection.execute(sql.SQL('CREATE TABLE {}.artifact_storage_dev(namespace text NOT NULL,artifact_key text NOT NULL,content text NOT NULL,PRIMARY KEY(namespace,artifact_key))').format(sql.Identifier(schema)))
            connection.execute(sql.SQL('CREATE TABLE {}.workspace_capture(id integer PRIMARY KEY,body jsonb NOT NULL)').format(sql.Identifier(schema)))
            connection.execute(sql.SQL('INSERT INTO {}.workspace_capture VALUES (1,%s)').format(sql.Identifier(schema)),(Jsonb(bundle),))
            for key,content in baseline.items():
                connection.execute(sql.SQL('INSERT INTO {}.artifact_storage_dev VALUES (%s,%s,%s)').format(sql.Identifier(schema)),(namespace,key,content))
    def command(args):
        result=subprocess.run(args,env=env,capture_output=True,timeout=180)
        if result.returncode:
            # Keep tool diagnostics local; never echo DSNs, SQL rows or passwords.
            log=output/('database-tool-error-'+uuid.uuid4().hex[:8]+'.log')
            with log.open('xb') as f:os.chmod(log,0o600);f.write(result.stderr)
            raise ValueError('Local database tool failed; private diagnostic log saved in the output folder.')
    command([tools['pg_dump'],'--format=custom','--no-owner','--no-privileges','--schema='+schema,'--file='+str(backup)])
    os.chmod(backup,0o600);digest=sha256(backup.read_bytes()).hexdigest();report['backup_sha256']=digest
    def restore(rename_to):
        if sha256(backup.read_bytes()).hexdigest()!=digest:raise ValueError('Backup checksum changed before restore.')
        with _connect(parameters) as c:
            c.execute(sql.SQL('ALTER SCHEMA {} RENAME TO {}').format(sql.Identifier(schema),sql.Identifier(schema_name(rename_to))))
        command([tools['pg_restore'],'--exit-on-error','--no-owner','--no-privileges','--dbname=campaigniq_dev',str(backup)])
    def baseline_matches(c):
        storage=SchemaStorage(c,schema,namespace)
        captured=c.execute(sql.SQL('SELECT body FROM {}.workspace_capture WHERE id=1').format(sql.Identifier(schema))).fetchone()[0]
        return storage.all_artifacts()==baseline and encode(captured)==encode(bundle)
    restore(schema+'_baseline')
    with _connect(parameters) as c:
        if not baseline_matches(c):raise ValueError('Restored workspace differs from captured originals.')
        report['local_workspace_restore_verified']=True
        install_guard(c,schema,namespace);storage=SchemaStorage(c,schema,namespace)
        try:
            with c.transaction():
                for key in sorted(candidates)[:3]:storage.insert_candidate(key,candidates[key])
                raise RuntimeError('controlled_rehearsal_abort')
        except RuntimeError as error:
            if str(error)!='controlled_rehearsal_abort':raise
        if not baseline_matches(c):raise ValueError('Injected transaction did not roll back cleanly.')
        report['transaction_rollback_verified']=True
        protected=next(k for k in baseline if k.startswith('2026-'))
        try:
            with c.transaction():
                storage._query('UPDATE {}.artifact_storage_dev SET content=%s WHERE namespace=%s AND artifact_key=%s',('rejected test mutation',namespace,protected))
        except RaiseException:
            report['protected_record_rejection_verified']=True
        if not report['protected_record_rejection_verified'] or not baseline_matches(c):
            raise ValueError('Protected-record rejection failed.')
        with c.transaction():
            # Markers are last even within this single atomic transaction.
            for key in sorted(candidates,key=lambda k:(k.endswith('-finalized.json'),k)):
                storage.insert_candidate(key,candidates[key])
            if PROTOCOL_FILE in baseline:
                storage._query('UPDATE {}.artifact_storage_dev SET content=%s WHERE namespace=%s AND artifact_key=%s',(_serialize_publication_protocol(first_period_end=date(2025,1,31)),namespace,PROTOCOL_FILE))
            else:
                raise ValueError('Captured publication protocol is required for simulation.')
    # A separate connection verifies committed visibility through the actual reader.
    with _connect(parameters) as c:
        storage=SchemaStorage(c,schema,namespace);actual=storage.all_artifacts()
        expected=dict(baseline,**candidates);expected[PROTOCOL_FILE]=_serialize_publication_protocol(first_period_end=date(2025,1,31))
        if actual!=expected:raise ValueError('Committed candidate generation differs.')
        validate_generation({key:storage.read_text(key) for key in candidates})
        for year,last in [(2025,12),(2026,9)]:
            import calendar
            for month in range(1,last+1):
                end=date(year,month,calendar.monthrange(year,month)[1])
                if not is_month_published_in_storage(storage,period_end=end):raise ValueError('Publication reader cannot see a staged period.')
        captured=c.execute(sql.SQL('SELECT body FROM {}.workspace_capture WHERE id=1').format(sql.Identifier(schema))).fetchone()[0]
        if encode(captured)!=encode(bundle):raise ValueError('Committed stage changed retained drafts or source documents.')
        report['committed_stage_verified']=True
        report['protected_2026_artifact_count']=sum(k.startswith('2026-') for k in baseline)
    restore(schema+'_staged')
    with _connect(parameters) as c:
        if not baseline_matches(c):raise ValueError('Backup restore did not undo the committed simulation.')
    report['backup_restore_rollback_verified']=True
    report['candidate_artifact_count']=len(candidates)
    return report


def main():
    p=argparse.ArgumentParser();p.add_argument('--repo',type=Path,required=True);p.add_argument('--capture',type=Path,required=True)
    p.add_argument('--development-only',action='store_true',required=True);args=p.parse_args()
    repo=args.repo.expanduser().resolve()
    if subprocess.check_output(['git','-C',str(repo),'branch','--show-current'],text=True).strip()!='feature/historical-backfill-2025':
        p.error('Use the development checkout.')
    if subprocess.check_output(['git','-C',str(repo),'status','--porcelain'],text=True).strip():
        p.error('Commit the tested rehearsal source before running it.')
    if Path(__file__).resolve().parents[2] != repo:
        p.error('Run the installed module from the selected development checkout.')
    try:
        parameters=local_parameters(os.environ.get('CAMPAIGNIQ_DEV_DATABASE_URL','postgresql:///campaigniq_dev?host=/var/run/postgresql'))
    except Exception:
        p.error('Configure a local campaigniq_dev connection only; remote/service connections and Render execution are refused.')
    for key in list(os.environ):
        if key.startswith('PG'):del os.environ[key]
    path=args.capture.expanduser().resolve();verification=json.loads(path.with_name('capture-verification.json').read_text())
    packed=path.read_bytes()
    if sha256(packed).hexdigest()!=verification['compressed_file_sha256']:p.error('Capture checksum differs.')
    with gzip.open(path,'rb') as stream:raw=stream.read(LIMIT+1)
    if len(raw)>LIMIT or sha256(raw).hexdigest()!=verification['capture_sha256']:p.error('Capture content checksum differs.')
    bundle=decode(raw);output=Path.home()/'Downloads'/('CampaignIQ-publication-rehearsal-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8])
    output.mkdir(mode=0o700,exist_ok=False)
    print('Preparing full artifact candidates from the saved capture; no production connection...',flush=True)
    collector=CandidateCollector(bundle['artifacts'])
    report=run_preflight(bundle,campaign_continuity=True,candidate_collector=collector,
        progress=lambda row:print(row['month']+': '+str(len(row['blocking_findings']))+' candidate/preflight findings',flush=True))
    _save(output/'candidate-preflight.json',report)
    if not report['all_2025_drafts_passed'] or len(collector.periods)!=12 or report.get('boundary_plan',{}).get('status')!='prepared_for_review':
        p.exit(1,'Rehearsal blocked before database writes. Report: '+str(output/'candidate-preflight.json')+'\n')
    _save(output/'contract-mapping-candidates.json',collector.contract_mapping_candidates)
    print('Twelve complete candidate generations prepared. Starting isolated LOCAL database rehearsal...',flush=True)
    try:
        result=rehearse_database(bundle,collector.artifacts,parameters,output)
    except Exception as error:
        _save(output/'rehearsal-failure.json',dict(error_type=type(error).__name__,local_rehearsal_passed=False,publication_authorized=False))
        p.exit(1,'Local rehearsal failed; existing production was never connected. Diagnostics retained in '+str(output)+'\n'+(str(error)+'\n' if isinstance(error,ValueError) else 'Check the local PostgreSQL connection and permissions.\n'))
    result['contract_mappings_approved']=False;result['local_rehearsal_passed']=True
    result['capture_sha256']=verification['capture_sha256']
    result['development_commit']=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
    result['candidate_artifact_sha256']={key:sha256(text.encode()).hexdigest() for key,text in collector.artifacts.items()}
    _save(output/'rehearsal-report.json',result)
    print('Local workspace backup/restore: PASS\nInjected transaction rollback: PASS\nProtected 2026 mutation rejection: PASS\nCommitted staging visibility: PASS\nBackup restore rollback: PASS')
    print('Report:',output/'rehearsal-report.json')
    print('Schemas and backup retained. This is a local workspace rehearsal, not a full production database restore. Nothing was published to production.')


if __name__=='__main__':main()
