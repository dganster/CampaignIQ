from copy import deepcopy
from types import SimpleNamespace
import pytest
from campaigniq import account_deployment_preferences as p
from campaigniq.account_preferences import PreferenceIntegrityError
from campaigniq.ui.preferences import DEFAULT_PREFERENCES
from campaigniq.account_administration import Permission
from campaigniq.account_legacy_access_rehearsal import RehearsalSession
from tests.test_account_preferences import preferences
from tests.test_account_legacy_access import harness,A,B
from tests.test_account_deployment_publication import make_stores

@pytest.fixture
def deployment(preferences,monkeypatch):
    h=preferences;local=h.service;store,_=make_stores()
    store.settings=SimpleNamespace(registry_id=h.store.registry_id)
    original=h.conn.execute
    def execute(sql,params=()):return original(sql.replace('campaigniq_accounts.account_preferences','campaigniq_preferences_dev.account_preferences'),params)
    h.conn.execute=execute;store._connection=h.store._connection
    class Tx:
        def __init__(self,*args):pass
        def _actor(self,user):return local._actor(h.conn,user)
    monkeypatch.setattr(p,'DirectoryTransaction',Tx)
    monkeypatch.setattr(p,'validate_preference_role',lambda conn:None)
    monkeypatch.setattr(p,'require_transaction',lambda conn:None)
    h.service=p.DeploymentAccountPreferences(store);return h


def test_persistence_stale_and_post_write_rollback(deployment):
    h=deployment;values={**DEFAULT_PREFERENCES,'fit_columns':True}
    assert h.service.save(h.user,h.account,values,expected_revision=0)==1
    assert dict(p.DeploymentAccountPreferences(h.service.store).load(h.user,h.account).values)==values
    assert h.modes[-1] is True
    before=deepcopy(h.preferences)
    with pytest.raises(ValueError,match='reload'):h.service.save(h.user,h.account,values,expected_revision=0)
    h.fail=True
    with pytest.raises(ValueError,match='Injected'):h.service.save(h.user,h.account,values,expected_revision=1)
    assert h.preferences==before


def test_view_personal_isolation_and_current_revocation(deployment):
    h=deployment;other=h.current[0].user(B).id
    h.current[0]=h.current[0].set_grant(A,other,h.account,Permission.VIEW,expected_revision=h.current[0].revision)
    h.service.save(h.user,h.account,{**DEFAULT_PREFERENCES,'fit_columns':True},expected_revision=0)
    h.service.save(RehearsalSession(B),h.account,dict(DEFAULT_PREFERENCES),expected_revision=0)
    assert dict(h.service.load(h.user,h.account).values)['fit_columns'] is True
    h.current[0]=h.current[0].set_grant(A,other,h.account,None,expected_revision=h.current[0].revision)
    with pytest.raises(PermissionError):h.service.load(RehearsalSession(B),h.account)


def test_corrupt_preferences_cannot_be_replaced(deployment):
    h=deployment;h.service.save(h.user,h.account,dict(DEFAULT_PREFERENCES),expected_revision=0)
    key=next(iter(h.preferences));revision,values,_=h.preferences[key];h.preferences[key]=(revision,values,'0'*64)
    with pytest.raises(PreferenceIntegrityError):h.service.load(h.user,h.account)
    with pytest.raises(PreferenceIntegrityError):h.service.save(h.user,h.account,dict(DEFAULT_PREFERENCES),expected_revision=1)

@pytest.mark.parametrize('revision',[True,-1,'0',None])
def test_bad_revision_precedes_connection(deployment,revision):
    h=deployment;h.modes.clear()
    with pytest.raises(ValueError):h.service.save(h.user,h.account,dict(DEFAULT_PREFERENCES),expected_revision=revision)
    assert not h.modes


def test_backend_and_table_are_fixed():
    with pytest.raises(ValueError):p.DeploymentAccountPreferences(object())
    with pytest.raises(ValueError):p.LocalAccountPreferences._read(None,(),table='public.foreign')
