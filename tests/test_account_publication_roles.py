from unittest.mock import patch
import pytest
from campaigniq import account_schema_migrations as m
from campaigniq import account_publication_roles as p
from campaigniq import account_runtime_roles as r
from tests.test_account_runtime_roles import Connection,Result

class PublicationConnection(Connection):
    def __init__(self):super().__init__();self.ledger=[(1,),(2,)]
    def execute(self,query,args=None):
        if isinstance(query,str) and query.startswith('INSERT INTO campaigniq_accounts.schema_migrations'):
            self.calls.append((query,args));self.ledger.append((3,));return Result(None)
        return super().execute(query,args)


def test_publication_policy_grants_inserts_without_artifact_update_delete_or_original_writes():
    conn=PublicationConnection()
    with patch.object(m,'verify_schema'),patch.object(p,'role_catalog_digest',return_value='a'*64):
        plan=p.review_publication_grants(conn,'publisher')
        with pytest.raises(ValueError):p.apply_publication_grants(conn,'publisher',reviewed_sha256='0'*64)
        assert not any(q.startswith('GRANT') for q,_ in conn.calls)
        assert p.apply_publication_grants(conn,'publisher',reviewed_sha256=m.digest(plan))
        assert conn.grants['campaigniq_accounts.artifact_storage_dev']==('SELECT','INSERT')
        assert conn.grants['campaigniq_accounts.import_draft_documents']==('SELECT',)
        assert conn.grants['public.artifact_storage_dev']==('SELECT',)
        repeat=p.review_publication_grants(conn,'publisher')
        assert p.apply_publication_grants(conn,'publisher',reviewed_sha256=m.digest(repeat)) is False


def test_publication_review_requires_input_policy_and_rejects_unsafe_role():
    conn=PublicationConnection();conn.ledger=[(1,)]
    with patch.object(m,'verify_schema'),pytest.raises(ValueError):p.review_publication_grants(conn,'publisher')
    conn.ledger=[(1,),(2,)];conn.grants['campaigniq_accounts.artifact_storage_dev']=('UPDATE',)
    with patch.object(m,'verify_schema'),pytest.raises(PermissionError):p.review_publication_grants(conn,'publisher')


def test_version_three_reader_detects_changed_policy_or_catalog():
    from tests.test_account_schema_migrations import Connection as Schema
    ledger=[(1,m.SOURCE_SHA256,'b'*64),(2,r.SOURCE_SHA256,'c'*64),(3,p.SOURCE_SHA256,'a'*64)]
    with patch.object(m,'catalog_digest',return_value='d'*64),patch.object(r,'role_catalog_digest',return_value='a'*64):m.verify_schema(Schema(exists=True,ledger=ledger))
    with patch.object(m,'catalog_digest',return_value='d'*64),patch.object(r,'role_catalog_digest',return_value='e'*64),pytest.raises(ValueError):m.verify_schema(Schema(exists=True,ledger=ledger))
