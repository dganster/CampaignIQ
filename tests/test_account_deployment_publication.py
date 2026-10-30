from contextlib import contextmanager
from copy import deepcopy
from datetime import date
import json
from unittest.mock import patch
import pytest
from campaigniq.account_deployment_publication import DeploymentAccountPublisher,AppendOnlyArtifacts
from campaigniq.account_deployment_inputs import DeploymentMonthlyInputs
from campaigniq.account_backend_config import AccountDatabaseTarget
from campaigniq.persistence.deployment_account_directory import PostgresAccountDirectory
from campaigniq.persistence.deployment_account_data import PostgresAccountDataGateway
from campaigniq.persistence.lot_book_store import load_lot_book_from_storage
from tests.test_account_local_publication import local,month,bound_context,context,FIXTURES
from tests.test_deployment_account_directory import settings


def make_stores():
    config=settings()
    def store(role):
        target=AccountDatabaseTarget('db.example.invalid','accounts',role)
        return PostgresAccountDirectory('host=db.example.invalid port=5432 dbname=accounts user='+role+' sslmode=verify-full',config,target)
    return store('account_runtime'),store('account_publisher')

@pytest.fixture
def deployed(local,monkeypatch):
    from dataclasses import replace
    c=local;reader,writer=make_stores()
    reader.settings=writer.settings=replace(writer.settings,registry_id=c.registry)
    gateway=PostgresAccountDataGateway(reader);service=DeploymentMonthlyInputs(gateway)
    service._lock=c.bound._lock;service._known=c.bound._known
    # Registry identity is pinned in real deployment settings. The engine receipt
    # uses this property; synthetic fixture registry is used only in these units.
    publisher=DeploymentAccountPublisher(service,writer)
    @contextmanager
    def transaction(user,account):
        c.directory.authorize(user,account,write=True)
        before=dict(c.artifacts[account].rows)
        try:
            with c.bound.gateway.transaction(user,account,write=True) as session:
                yield session,c.directory.user(user).id
        except BaseException:c.artifacts[account].rows=before;raise
    monkeypatch.setattr(publisher,'_transaction',transaction)
    c.publisher=publisher;return c


def publish(c,**kwargs):return c.publisher.publish(c.admin,c.account,c.draft.id,c.revision,confirm_publication=True,forex_applicable=False,crypto_applicable=False,**kwargs)


def test_actual_engine_uses_deployment_receipt_preserves_originals_and_publishes_110(deployed):
    c=deployed;docs=dict(c.repositories[c.account].docs);result=publish(c)
    assert result.revision==4 and len(result.artifact_keys)==10
    book=load_lot_book_from_storage(c.artifacts[c.account],'2025-01-lot-book.json')
    assert sum(lot.quantity for lots in book.lot_book._lots.values() for lot in lots)==110
    receipt=json.loads(c.artifacts[c.account].read_text('2025-01-account-publication-receipt.json'))
    assert receipt['format']=='campaigniq.deployment_account_publication' and len(receipt['original_documents'])==4
    assert c.repositories[c.account].docs==docs
    before=dict(c.artifacts[c.account].rows)
    with pytest.raises(ValueError):publish(c)
    assert c.artifacts[c.account].rows==before

@pytest.mark.parametrize('confirmed',[False,None,1,'yes'])
def test_confirmation_is_exact_before_any_transaction(deployed,confirmed,monkeypatch):
    c=deployed
    monkeypatch.setattr(c.publisher,'_transaction',lambda *a:(_ for _ in ()).throw(AssertionError('No connection allowed')))
    with pytest.raises(PermissionError):c.publisher.publish(c.admin,c.account,c.draft.id,c.revision,confirm_publication=confirmed)


def test_stale_revision_and_foreign_actor_cannot_publish(deployed):
    c=deployed
    with pytest.raises(ValueError):c.publisher.publish(c.admin,c.account,c.draft.id,0,confirm_publication=True,forex_applicable=False,crypto_applicable=False)
    with pytest.raises(PermissionError):c.publisher.publish(c.member,c.account,c.draft.id,c.revision,confirm_publication=True,forex_applicable=False,crypto_applicable=False)
    assert not c.artifacts[c.account].rows


def test_failure_after_marker_has_no_partial_artifacts(deployed,monkeypatch):
    c=deployed;storage=c.artifacts[c.account];original=storage.write_text
    def failed(key,text):
        original(key,text)
        if key.endswith('-finalized.json'):raise RuntimeError('injected')
    monkeypatch.setattr(storage,'write_text',failed)
    with pytest.raises(RuntimeError):publish(c)
    assert not storage.rows


def test_wrong_writer_backend_or_same_role_rejected():
    reader,writer=make_stores();service=DeploymentMonthlyInputs(PostgresAccountDataGateway(reader))
    with pytest.raises(ValueError):DeploymentAccountPublisher(service,reader)
    other=AccountDatabaseTarget('other.example.invalid','accounts','account_publisher')
    mismatch=PostgresAccountDirectory('host=other.example.invalid port=5432 dbname=accounts user=account_publisher sslmode=verify-full',writer.settings,other)
    with pytest.raises(ValueError):DeploymentAccountPublisher(service,mismatch)
    with pytest.raises(ValueError):DeploymentAccountPublisher(object(),writer)


def test_append_only_storage_never_updates_or_deletes():
    from types import SimpleNamespace
    calls=[]
    @contextmanager
    def operation():yield SimpleNamespace(execute=lambda q,p:calls.append((q,p)))
    session=SimpleNamespace(artifacts=SimpleNamespace(namespace='scope',exists=lambda k:k=='retained'),_check=lambda **k:None,_operation=operation)
    storage=AppendOnlyArtifacts(session);storage.write_text('new','content');storage.delete('absent')
    assert len(calls)==1 and calls[0][0].startswith('INSERT INTO campaigniq_accounts.')
    with pytest.raises(PermissionError):storage.delete('retained')
