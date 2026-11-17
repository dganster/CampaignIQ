from types import SimpleNamespace
from uuid import uuid4
import pytest
from psycopg.pq import TransactionStatus
from campaigniq.persistence.deployment_account_data import DeploymentDataSession
from campaigniq.persistence.account_data_session import ClosedAccountSession,AccountTransactionFailed


class Connection:
    def __init__(self,proof=('on','repeatable read')):
        self.info=SimpleNamespace(transaction_status=TransactionStatus.INTRANS)
        self.proof=proof;self.queries=[];self.bad_size=False;self.duplicate=False
        self.a,self.b=uuid4(),uuid4()
        self.rows={('scope-a',self.a):{'one':('é.pdf','application/pdf',b'one'),'two':('two.csv','text/csv',b'two')},
                   ('scope-b',self.b):{'foreign':('b.pdf','application/pdf',b'foreign')}}
    def execute(self,query,args=None):
        self.queries.append(query)
        if 'current_setting' in query:return SimpleNamespace(fetchone=lambda:self.proof)
        rows=self.rows.get((args[1],args[0]),{})
        if 'count(*)' in query:
            return SimpleNamespace(fetchone=lambda:(len(rows),sum(len(n.encode())+len(m.encode())+len(r) for n,m,r in rows.values())+int(self.bad_size)))
        if 'SELECT d.role,d.filename' in query:
            values=[(role,*row) for role,row in sorted(rows.items())]
            return SimpleNamespace(fetchall=lambda:values+values[:1] if self.duplicate else values)
        if 'SELECT d.filename,d.media_type,d.content' in query:return SimpleNamespace(fetchone=lambda:rows.get(args[2]))
        raise AssertionError(query)


def opened(proof=('on','repeatable read'),writable=False):
    c=Connection(proof);s=DeploymentDataSession(c,SimpleNamespace(account_id=str(uuid4())),writable,str(uuid4()))
    s._physical_scope='scope-a';return s,c


def test_exact_batch_and_optional_absence_use_only_two_queries_after_proof():
    s,c=opened();assert s.drafts.prime_documents(c.a)
    for _ in range(3):
        assert s.drafts.document(c.a,'one')==('é.pdf','application/pdf',b'one')
        assert s.drafts.document(c.a,'two')[2]==b'two'
        with pytest.raises(FileNotFoundError):s.drafts.document(c.a,'optional')
        assert s.drafts.prime_documents(c.a)
    assert len(c.queries)==3


@pytest.mark.parametrize('cause,error',[('closed',ClosedAccountSession),('failed',AccountTransactionFailed),('idle',ValueError)])
def test_cached_success_absence_and_prime_recheck_failed_or_expired_session(cause,error):
    s,c=opened();s.drafts.prime_documents(c.a)
    if cause=='closed':s._active=False
    elif cause=='failed':c.info.transaction_status=TransactionStatus.INERROR
    else:c.info.transaction_status=TransactionStatus.IDLE
    for fn in (lambda:s.drafts.document(c.a,'one'),lambda:s.drafts.document(c.a,'absent'),lambda:s.drafts.prime_documents(c.a)):
        with pytest.raises(error):fn()


@pytest.mark.parametrize('proof,writable',[(('off','repeatable read'),False),(('on','read committed'),False),(('on','repeatable read'),True)])
def test_other_modes_keep_existing_individual_reads(proof,writable):
    s,c=opened(proof,writable);assert not s.drafts.prime_documents(c.a)
    assert s.drafts.document(c.a,'one')[2]==b'one'
    c.rows['scope-a',c.a]['one']=('changed','text/plain',b'changed')
    assert s.drafts.document(c.a,'one')[2]==b'changed'
    assert not any('count(*)' in q for q in c.queries)


@pytest.mark.parametrize('limit',['MAX_BYTES','MAX_ENTRIES'])
def test_oversized_batches_fall_back_without_retaining_partial_inventory(limit):
    s,c=opened();setattr(s.drafts,limit,1)
    assert not s.drafts.prime_documents(c.a)
    assert s.drafts._primed_scope is None and not s.drafts._documents
    assert s.drafts.document(c.a,'one')[2]==b'one'
    assert not any('SELECT d.role,d.filename' in q for q in c.queries)


def test_foreign_draft_scope_and_fresh_session_cannot_reuse_old_batch():
    s,c=opened();s.drafts.prime_documents(c.a)
    with pytest.raises(FileNotFoundError):s.drafts.document(c.b,'foreign')
    s.drafts.prime_documents(c.b)
    with pytest.raises(FileNotFoundError):s.drafts.document(c.b,'foreign')
    s._physical_scope='scope-b';assert s.drafts.prime_documents(c.b)
    assert s.drafts.document(c.b,'foreign')[2]==b'foreign'
    with pytest.raises(FileNotFoundError):s.drafts.document(c.a,'one')
    s._active=False
    c.rows['scope-a',c.a]['one']=('new','text/plain',b'new')
    fresh=DeploymentDataSession(c,SimpleNamespace(account_id=str(uuid4())),False,str(uuid4()));fresh._physical_scope='scope-a'
    assert fresh.drafts.prime_documents(c.a) and fresh.drafts.document(c.a,'one')[2]==b'new'


@pytest.mark.parametrize('fault',['bad_size','duplicate'])
def test_inventory_conflict_is_withheld_without_adopting_any_bytes(fault):
    s,c=opened();setattr(c,fault,True)
    with pytest.raises(ValueError):s.drafts.prime_documents(c.a)
    assert s.drafts._primed_scope is None and not s.drafts._documents
