"""Read-only, workspace-scoped database capture for offline backfill checking."""
from datetime import datetime, timezone
from hashlib import sha256
import base64
import gzip
import json
from uuid import UUID

WORKSPACE='workspace-dennis'
SERVICE='srv-daop896gekts73egdsfg'
SSH_TARGET=SERVICE+'@ssh.oregon.render.com'
FORMAT='campaigniq.backfill_workspace_capture'
LIMIT=1024*1024*1024
DRAFT_FIELDS='id,workspace,year,month,state,revision,created_at,updated_at'
DOC_FIELDS='id,draft_id,role,filename,media_type,sha256,content,revision,uploaded_at,superseded'


def encode(bundle):
    return json.dumps(bundle,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()


def decode(data):
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('Duplicate JSON field.')
            result[key]=value
        return result
    return json.loads(data,object_pairs_hook=unique)


def validate(bundle):
    if (not isinstance(bundle,dict) or set(bundle)!={'format','version','workspace','database','captured_at','artifacts','drafts','documents'}
        or bundle['format']!=FORMAT or type(bundle['version']) is not int or bundle['version']!=1
        or bundle['workspace']!=WORKSPACE or bundle['database'] not in ('campaigniq_dev','campaigniq_production_db')):
        raise ValueError('Invalid workspace capture envelope.')
    if datetime.fromisoformat(bundle['captured_at']).tzinfo is None:raise ValueError('Capture time lacks timezone.')
    artifacts=bundle['artifacts']
    if not isinstance(artifacts,dict) or any(not isinstance(k,str) or not k or not isinstance(v,str) for k,v in artifacts.items()):
        raise ValueError('Invalid artifact capture.')
    if not isinstance(bundle['drafts'],list) or not isinstance(bundle['documents'],list):raise ValueError('Invalid capture row lists.')
    drafts={};active=set()
    for d in bundle['drafts']:
        if set(d)!=set(DRAFT_FIELDS.split(',')) or d['workspace']!=WORKSPACE or d['state'] not in ('draft','abandoned'):
            raise ValueError('Invalid draft metadata.')
        UUID(d['id'])
        if d['id'] in drafts or any(type(d[k]) is not int for k in ('year','month','revision')) or not 1900<=d['year']<=2200 or not 1<=d['month']<=12 or d['revision']<0:
            raise ValueError('Duplicate or invalid draft.')
        for key in ('created_at','updated_at'):
            if datetime.fromisoformat(d[key]).tzinfo is None:raise ValueError('Draft timestamp lacks timezone.')
        if d['state']=='draft':
            month=(d['year'],d['month'])
            if month in active:raise ValueError('Duplicate active month.')
            active.add(month)
        drafts[d['id']]=d
    ids=set();roles=set()
    for d in bundle['documents']:
        if set(d)!=(set(DOC_FIELDS.split(','))-{'content'})|{'content_base64'}:raise ValueError('Invalid document fields.')
        UUID(d['id'])
        if d['id'] in ids or d['draft_id'] not in drafts or type(d['superseded']) is not bool:
            raise ValueError('Duplicate/orphan document.')
        if (type(d['revision']) is not int or not 1<=d['revision']<=drafts[d['draft_id']]['revision']
            or not isinstance(d['role'],str) or not d['role'] or not isinstance(d['filename'],str) or not d['filename']
            or not isinstance(d['media_type'],str)):
            raise ValueError('Invalid document metadata.')
        if datetime.fromisoformat(d['uploaded_at']).tzinfo is None:raise ValueError('Document timestamp lacks timezone.')
        content=base64.b64decode(d['content_base64'],validate=True)
        if not content or len(content)>20*1024*1024 or sha256(content).hexdigest()!=d['sha256']:
            raise ValueError('Document checksum mismatch or invalid size.')
        pair=(d['draft_id'],d['role'])
        if not d['superseded']:
            if pair in roles:raise ValueError('Duplicate current document role.')
            roles.add(pair)
        ids.add(d['id'])
    if len(encode(bundle))>LIMIT:raise ValueError('Workspace capture exceeds size limit.')
    return bundle


def capture_connection(connection,expected_database):
    if expected_database not in ('campaigniq_dev','campaigniq_production_db'):raise ValueError('Unexpected database.')
    connection.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
    try:
        identity=connection.execute("SELECT current_database(), current_setting('transaction_read_only'), current_setting('transaction_isolation')").fetchone()
        if identity!=(expected_database,'on','repeatable read'):raise ValueError('Read-only snapshot verification failed.')
        artifacts=connection.execute('SELECT artifact_key,content FROM public.artifact_storage_dev WHERE namespace=%s ORDER BY artifact_key',(WORKSPACE,)).fetchall()
        ds=connection.execute('SELECT '+DRAFT_FIELDS+' FROM public.import_drafts WHERE workspace=%s ORDER BY year,month,id',(WORKSPACE,)).fetchall()
        cols=','.join('d.'+k for k in DOC_FIELDS.split(','))
        docs=connection.execute('SELECT '+cols+' FROM public.import_draft_documents d JOIN public.import_drafts p ON p.id=d.draft_id WHERE p.workspace=%s ORDER BY d.draft_id,d.role,d.revision,d.id',(WORKSPACE,)).fetchall()
    finally:connection.execute('ROLLBACK')
    if len(artifacts)!=len(dict(artifacts)):raise ValueError('Duplicate artifact keys.')
    def scalar(v):
        return v.isoformat() if isinstance(v,datetime) else str(v) if isinstance(v,UUID) else v
    drafts=[{k:scalar(v) for k,v in zip(DRAFT_FIELDS.split(','),row)} for row in ds]
    documents=[]
    for row in docs:
        item={k:scalar(v) for k,v in zip(DOC_FIELDS.split(','),row) if k!='content'}
        item['content_base64']=base64.b64encode(bytes(row[6])).decode();documents.append(item)
    return validate(dict(format=FORMAT,version=1,workspace=WORKSPACE,database=expected_database,
        captured_at=datetime.now(timezone.utc).isoformat(),artifacts=dict(artifacts),drafts=drafts,documents=documents))


def remote_script(nonce):
    """Send only this read-only capture code, never credentials, to the known service."""
    if len(nonce)!=32 or any(c not in '0123456789abcdef' for c in nonce):raise ValueError('Invalid capture nonce.')
    from pathlib import Path
    source=Path(__file__).read_text()
    return source+'''\nimport os, psycopg
if os.environ.get('RENDER_SERVICE_ID')!=SERVICE:raise ValueError('Unexpected Render service.')
try:
    with psycopg.connect(os.environ['CAMPAIGNIQ_DEV_DATABASE_URL'],autocommit=True,connect_timeout=10,options='-c default_transaction_read_only=on') as c:
        data=encode(capture_connection(c,'campaigniq_production_db'))
except Exception:
    raise SystemExit('Read-only capture failed; verify service database permissions/configuration.')
print('CAMPAIGNIQ_READ_ONLY_EXPORT '+json.dumps({'nonce': '''+repr(nonce)+''', 'sha256':sha256(data).hexdigest(), 'gzip_base64':base64.b64encode(gzip.compress(data)).decode()}))
'''


def parse_remote_output(output,nonce):
    lines=[l for l in output.splitlines() if l.startswith('CAMPAIGNIQ_READ_ONLY_EXPORT ')]
    if len(lines)!=1:raise ValueError('Missing or ambiguous read-only capture response.')
    item=decode(lines[0].split(' ',1)[1])
    if set(item)!={'nonce','sha256','gzip_base64'} or item['nonce']!=nonce:raise ValueError('Capture response identity mismatch.')
    import io
    with gzip.GzipFile(fileobj=io.BytesIO(base64.b64decode(item['gzip_base64'],validate=True))) as f:data=f.read(LIMIT+1)
    if len(data)>LIMIT or sha256(data).hexdigest()!=item['sha256']:raise ValueError('Capture transfer checksum mismatch.')
    return validate(decode(data))
