from unittest.mock import patch
import pytest
from campaigniq import account_schema_migrations as m,account_preference_roles as p,account_runtime_roles as r,account_publication_roles as w
from tests.test_account_runtime_roles import Connection,Result
from tests.test_account_schema_migrations import Connection as Schema

class PreferencesConnection(Connection):
    def __init__(self):super().__init__();self.ledger=[(1,),(2,),(3,)]
    def execute(self,query,args=None):
        if isinstance(query,str) and query.startswith('INSERT INTO campaigniq_accounts.schema_migrations'):
            self.calls.append((query,args));self.ledger.append((4,));return Result(None)
        return super().execute(query,args)


def test_exact_review_separate_grants_and_idempotent_repeat():
    conn=PreferencesConnection()
    with patch.object(m,'verify_schema'),patch.object(p,'role_catalog_digest',return_value='a'*64):
        plan=p.review_preference_grants(conn,'preferences')
        with pytest.raises(ValueError):p.apply_preference_grants(conn,'preferences',reviewed_sha256='0'*64)
        assert not any(q.startswith('GRANT') for q,_ in conn.calls)
        assert p.apply_preference_grants(conn,'preferences',reviewed_sha256=m.digest(plan))
        assert conn.grants['campaigniq_accounts.account_preferences']==('SELECT','INSERT','UPDATE')
        for name in ('artifact_storage_dev','import_drafts','import_draft_documents'):
            assert conn.grants['campaigniq_accounts.'+name]==('SELECT',)
        assert conn.grants['public.artifact_storage_dev']==('SELECT',)
        repeat=p.review_preference_grants(conn,'preferences')
        assert not p.apply_preference_grants(conn,'preferences',reviewed_sha256=m.digest(repeat))


def test_missing_migration_or_unsafe_financial_grant_rejected():
    conn=PreferencesConnection();conn.ledger=[(1,),(2,)]
    with patch.object(m,'verify_schema'),pytest.raises(ValueError):p.review_preference_grants(conn,'preferences')
    conn.ledger=[(1,),(2,),(3,)];conn.grants['campaigniq_accounts.artifact_storage_dev']=('INSERT',)
    with patch.object(m,'verify_schema'),pytest.raises(PermissionError):p.review_preference_grants(conn,'preferences')


def test_v4_verifies_all_sources_and_latest_catalog():
    ledger=[(1,m.SOURCE_SHA256,'a'*64),(2,r.SOURCE_SHA256,'b'*64),(3,w.SOURCE_SHA256,'c'*64),(4,p.SOURCE_SHA256,'d'*64)]
    with patch.object(m,'catalog_digest',return_value='z'*64),patch.object(r,'role_catalog_digest',return_value='d'*64):
        m.verify_schema(Schema(exists=True,ledger=ledger))
        for i in range(4):
            changed=list(ledger);changed[i]=(changed[i][0],'0'*64,changed[i][2])
            with pytest.raises(ValueError):m.verify_schema(Schema(exists=True,ledger=changed))
    with patch.object(m,'catalog_digest',return_value='z'*64),patch.object(r,'role_catalog_digest',return_value='e'*64),pytest.raises(ValueError):m.verify_schema(Schema(exists=True,ledger=ledger))
