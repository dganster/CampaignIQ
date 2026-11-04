from contextlib import contextmanager
from copy import deepcopy
import pytest
from campaigniq.account_deployment_publication import DeploymentAccountPublisher
from campaigniq.account_deployment_application import DeploymentMonthlyApplication
from tests.test_account_crypto_publication import ready
from tests.test_account_crypto_statement_sources import crypto,rev
from tests.test_gain_loss_association import saving,bound_context,context


def application(c,*,enabled=True,publication=True):
    publisher=object.__new__(DeploymentAccountPublisher)
    publisher.inputs=c.associated;publisher.multi_asset_enabled=enabled
    @contextmanager
    def transaction(user,account):
        storage=c.artifacts[account];before=deepcopy(storage.rows)
        try:
            with c.associated.gateway.transaction(user,account,write=True) as session:
                session._check=lambda **k:None
                yield session,c.directory.user(user).id
        except BaseException:storage.rows=before;raise
    publisher._transaction=transaction
    return DeploymentMonthlyApplication(c.associated,publisher,publication_enabled=publication)


@pytest.mark.parametrize('enabled,publication',[(False,True),(True,False)])
def test_server_capabilities_must_both_be_enabled(ready,enabled,publication):
    c=ready;app=application(c,enabled=enabled,publication=publication)
    with pytest.raises(PermissionError):app.publish(c.admin,c.account,c.selected.id,rev(c),confirm_publication=True,forex_applicable=False,crypto_applicable=True)
    assert not c.artifacts[c.account].rows


@pytest.mark.parametrize('case',['confirmation','stale','foreign','omit_crypto'])
def test_public_route_requires_current_scope_and_explicit_confirmation(ready,case):
    c=ready;app=application(c)
    with pytest.raises((ValueError,PermissionError)):
        app.publish(c.member if case=='foreign' else c.admin,c.account,c.selected.id,0 if case=='stale' else rev(c),
            confirm_publication=case!='confirmation',forex_applicable=False,crypto_applicable=case!='omit_crypto')
    assert not c.artifacts[c.account].rows


def test_enabled_application_publishes_once_using_verified_crypto_writer(ready):
    c=ready;app=application(c)
    result=app.publish(c.admin,c.account,c.selected.id,rev(c),confirm_publication=True,forex_applicable=False,crypto_applicable=True)
    assert result.revision==rev(c) and c.artifacts[c.account].exists('2026-10-finalized.json')
    before=deepcopy(c.artifacts[c.account].rows)
    with pytest.raises(ValueError):app.publish(c.admin,c.account,c.selected.id,rev(c),confirm_publication=True,forex_applicable=False,crypto_applicable=True)
    assert c.artifacts[c.account].rows==before
