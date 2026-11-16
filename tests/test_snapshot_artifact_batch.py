from types import SimpleNamespace
from uuid import uuid4
import pytest
from psycopg.pq import TransactionStatus
from campaigniq.persistence.deployment_account_data import DeploymentDataSession
from campaigniq.persistence.account_data_session import ClosedAccountSession,AccountTransactionFailed

class Connection:
    def __init__(self,proof=('on','repeatable read')):
        self.info=SimpleNamespace(transaction_status=TransactionStatus.INTRANS);self.proof=proof;self.queries=[]
        self.rows={'scope-a':{'first':'one','second':'two','unicode':'é'},'scope-b':{'other':'account-b'}}
        self.inconsistent=False
    def execute(self,sql,args=None):
        self.queries.append(sql)
        if 'current_setting' in sql:return SimpleNamespace(fetchone=lambda:self.proof)
        values=self.rows[args[0]]
        if 'count(*)' in sql:return SimpleNamespace(fetchone=lambda:(len(values),sum(len(k.encode())+len(v.encode()) for k,v in values.items())+int(self.inconsistent)))
        if 'SELECT artifact_key,content' in sql:return SimpleNamespace(fetchall=lambda:sorted(values.items()))
        if 'SELECT artifact_key FROM' in sql:return SimpleNamespace(fetchall=lambda:[(k,) for k in sorted(values)])
        if 'SELECT content' in sql:return SimpleNamespace(fetchone=lambda:(values[args[1]],) if args[1] in values else None)
        if 'SELECT 1 FROM' in sql:return SimpleNamespace(fetchone=lambda:(1,) if args[1] in values else None)
        raise AssertionError(sql)

def opened(conn=None,write=False):
    c=conn or Connection();s=DeploymentDataSession(c,SimpleNamespace(account_id=str(uuid4())),write,str(uuid4()));s._physical_scope='scope-a';return s,c

def test_all_exact_bytes_loaded_once_with_filter_and_missing_semantics():
    s,c=opened();a=s.artifacts
    for _ in range(3):
        assert a.read_text('first')=='one' and a.read_text('unicode')=='é'
        assert a.list_keys(prefix='sec',suffix='ond')==('second',)
        assert a.exists('second') and not a.exists('absent')
        with pytest.raises(FileNotFoundError):a.read_text('absent')
    assert len(c.queries)==3 # proof, bounded inventory, exact namespace batch

@pytest.mark.parametrize('failure,error',[('expired',ClosedAccountSession),('failed',AccountTransactionFailed),('idle',ValueError)])
def test_every_cached_entry_point_rechecks_transaction(failure,error):
    s,c=opened();s.artifacts.read_text('first')
    if failure=='expired':s._active=False
    elif failure=='failed':c.info.transaction_status=TransactionStatus.INERROR
    else:c.info.transaction_status=TransactionStatus.IDLE
    for fn in (lambda:s.artifacts.read_text('first'),lambda:s.artifacts.exists('first'),lambda:s.artifacts.list_keys()):
        with pytest.raises(error):fn()

@pytest.mark.parametrize('proof,write',[(('off','repeatable read'),False),(('on','read committed'),False),(('on','repeatable read'),True)])
def test_writable_and_other_modes_keep_individual_reads(proof,write):
    s,c=opened(Connection(proof),write)
    assert s.artifacts.read_text('first')=='one';c.rows['scope-a']['first']='changed'
    assert s.artifacts.read_text('first')=='changed'
    assert not any('count(*)' in sql for sql in c.queries)

@pytest.mark.parametrize('limit',['MAX_BYTES','MAX_ENTRIES'])
def test_oversized_inventory_falls_back_without_retaining_bytes(limit):
    s,c=opened();setattr(s.artifacts,limit,1)
    assert s.artifacts.read_text('first')=='one';c.rows['scope-a']['first']='changed'
    assert s.artifacts.read_text('first')=='changed' and s.artifacts._snapshot_rows is None
    assert sum('count(*)' in sql for sql in c.queries)==1

def test_fresh_session_and_other_namespace_never_reuse_old_bytes():
    s,c=opened();assert s.artifacts.read_text('first')=='one';s._active=False
    c.rows['scope-a']['first']='changed';new,_=opened(c)
    assert new.artifacts.read_text('first')=='changed'
    new._physical_scope='scope-b'
    assert new.artifacts.list_keys()==('other',)
    with pytest.raises(FileNotFoundError):new.artifacts.read_text('first')

def test_inventory_conflict_fails_without_adopting_rows():
    s,c=opened();c.inconsistent=True
    with pytest.raises(ValueError,match='inventory differs'):s.artifacts.read_text('first')
    assert s.artifacts._snapshot_rows is None

def test_readonly_writes_and_invalid_keys_are_still_rejected():
    s,c=opened();s.artifacts.read_text('first')
    with pytest.raises(PermissionError):s.artifacts.write_text('first','changed')
    with pytest.raises(PermissionError):s.artifacts.delete('first')
    for key in ('',None):
        with pytest.raises((ValueError,TypeError,AttributeError)):s.artifacts.read_text(key)
    assert c.rows['scope-a']['first']=='one'
