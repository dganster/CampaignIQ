from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import patch
import pytest
from psycopg.pq import TransactionStatus
from campaigniq.persistence.deployment_account_data import DeploymentDataSession
from campaigniq.persistence.account_data_session import ClosedAccountSession,AccountTransactionFailed

class Connection:
    def __init__(self,proof=('on','repeatable read')):
        self.info=SimpleNamespace(transaction_status=TransactionStatus.INTRANS);self.proof=proof
        self.reads=0;self.raw=b'original';self.missing=False
    def execute(self,sql,args=None):
        if "current_setting('transaction_read_only')" in sql:row=self.proof
        elif 'SELECT d.filename,d.media_type,d.content' in sql:
            self.reads+=1;row=None if self.missing else ('original.pdf','application/pdf',self.raw+args[1].encode())
        else:raise AssertionError('Unexpected SQL')
        return SimpleNamespace(fetchone=lambda:row)
def session(conn,writable=False):
    s=DeploymentDataSession(conn,SimpleNamespace(account_id=str(uuid4())),writable,str(uuid4()));s._physical_scope='scope-a';return s

def test_same_original_read_once_within_verified_snapshot():
    c=Connection();s=session(c);draft=uuid4()
    for _ in range(5):assert s.drafts.document(draft,'statement')[2]==b'originalscope-a'
    assert c.reads==1

@pytest.mark.parametrize('proof,writable',[(('off','repeatable read'),False),(('on','read committed'),False),(('on','repeatable read'),True)])
def test_other_modes_do_not_reuse_documents(proof,writable):
    c=Connection(proof);s=session(c,writable);draft=uuid4();s.drafts.document(draft,'statement');c.raw=b'changed'
    assert s.drafts.document(draft,'statement')[2]==b'changedscope-a' and c.reads==2

@pytest.mark.parametrize('failure',['closed','failed','idle'])
def test_expired_or_failed_session_cannot_return_cached_original(failure):
    c=Connection();s=session(c);draft=uuid4();s.drafts.document(draft,'statement')
    if failure=='closed':s._active=False;error=ClosedAccountSession
    elif failure=='failed':c.info.transaction_status=TransactionStatus.INERROR;error=AccountTransactionFailed
    else:c.info.transaction_status=TransactionStatus.IDLE;error=ValueError
    with pytest.raises(error):s.drafts.document(draft,'statement')
    assert c.reads==1

def test_new_session_and_other_scope_read_current_originals():
    c=Connection();first=session(c);draft=uuid4();first.drafts.document(draft,'statement');first._active=False;c.raw=b'changed'
    second=session(c);assert second.drafts.document(draft,'statement')[2]==b'changedscope-a'
    second._physical_scope='scope-b';assert second.drafts.document(draft,'statement')[2]==b'changedscope-b'
    assert c.reads==3

def test_missing_original_is_not_cached():
    c=Connection();c.missing=True;s=session(c);draft=uuid4()
    for _ in range(2):
        with pytest.raises(FileNotFoundError):s.drafts.document(draft,'statement')
    assert c.reads==2 and not s.drafts._documents

def test_document_cache_is_bounded_and_does_not_retain_oversized_originals(monkeypatch):
    c=Connection();s=session(c);draft=uuid4();monkeypatch.setattr(s.drafts,'MAX_ENTRIES',1)
    for role in ('first','second','first'):s.drafts.document(draft,role)
    assert c.reads==3 and len(s.drafts._documents)==1
    s=session(c);monkeypatch.setattr(s.drafts,'MAX_BYTES',1)
    s.drafts.document(draft,'first');s.drafts.document(draft,'first')
    assert c.reads==5 and not s.drafts._documents and s.drafts._document_bytes==0

class CatalogConnection:
    def __init__(self,proof=('on','repeatable read')):self.proof=proof
    def execute(self,sql,args=None):
        from campaigniq import account_schema_migrations as m
        from campaigniq import account_runtime_roles as roles
        if 'current_setting' in sql:return SimpleNamespace(fetchone=lambda:self.proof)
        if 'SELECT c.relname FROM pg_class' in sql:rows=[(x,) for x in m.TABLES]
        elif 'SELECT version,source_sha256' in sql:rows=[(1,m.SOURCE_SHA256,'a'*64),(2,roles.SOURCE_SHA256,'b'*64)]
        else:rows=[]
        return SimpleNamespace(fetchall=lambda:rows)

def test_catalog_reuses_identical_base_only_with_verified_readonly_snapshot():
    from campaigniq import account_schema_migrations as m
    from campaigniq import account_runtime_roles as roles
    for reuse in (False,True):
        with patch.object(m,'catalog_digest',return_value='a'*64) as base,patch.object(roles,'role_catalog_digest',return_value='b'*64) as complete:
            m.verify_schema(CatalogConnection(),reuse_catalog=reuse)
            base.assert_called_once()
            if reuse:complete.assert_called_once_with(complete.call_args.args[0],base_catalog='a'*64)
            else:assert complete.call_args.kwargs=={}

@pytest.mark.parametrize('proof',[('off','repeatable read'),('on','read committed')])
def test_catalog_reuse_rejects_other_transaction_modes(proof):
    from campaigniq import account_schema_migrations as m
    with pytest.raises(ValueError,match='Read-only repeatable'):m.verify_schema(CatalogConnection(proof),reuse_catalog=True)

def test_role_signature_is_identical_with_same_supplied_base_and_still_reads_acls():
    from campaigniq import account_runtime_roles as roles
    from campaigniq import account_schema_migrations as m
    class C:
        changed=False
        def execute(self,sql,args=None):return SimpleNamespace(fetchall=lambda:[('column acl',self.changed)] if 'pg_attribute' in sql else [('schema acl',False)])
    conn=C()
    with patch.object(m,'catalog_digest',return_value='a'*64) as base:
        expected=roles.role_catalog_digest(conn);base.assert_called_once()
        base.reset_mock();assert roles.role_catalog_digest(conn,base_catalog='a'*64)==expected;base.assert_not_called()
        conn.changed=True;assert roles.role_catalog_digest(conn,base_catalog='a'*64)!=expected
