from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
import pytest
from psycopg.pq import TransactionStatus
from campaigniq.persistence import deployment_account_data as data
from campaigniq.account_deployment_inputs import DeploymentMonthlyInputs
from campaigniq.persistence.account_data_session import ClosedAccountSession,AccountTransactionFailed

class Connection:
    def __init__(self,proof=('on','repeatable read')):
        self.info=SimpleNamespace(transaction_status=TransactionStatus.INTRANS);self.proof=proof
    def execute(self,sql,args=None):
        rows=[('campaigniq_accounts',n) for n in ('artifact_storage_dev','import_draft_documents','import_drafts')]
        return SimpleNamespace(fetchall=lambda:rows,fetchone=lambda:self.proof)

def opened(*,write=False,proof=('on','repeatable read'),number='current'):
    account=str(uuid4());access=SimpleNamespace(account_id=account,storage_scope='scope')
    authorizations=[]
    def authorize(identity,selected,**kw):
        authorizations.append((identity,selected,kw));return access
    directory=SimpleNamespace(accounts=[SimpleNamespace(id=account,legacy=False,broker='Schwab')],authorize=authorize,number=number)
    settings=SimpleNamespace(registry_id=str(uuid4()));conn=Connection(proof)
    with patch.object(data,'DirectoryTransaction') as tx:
        tx.return_value._actor.return_value=(directory,SimpleNamespace(identity='current-actor',directory_revision=7))
        session,revision=data._open_session(conn,settings,object(),account,write=write)
        tx.return_value._actor.assert_called_once()
    assert authorizations==[('current-actor',account,dict(write=write))] and revision==7
    return session,directory

def known(session):
    service=SimpleNamespace(gateway=SimpleNamespace(_directory=SimpleNamespace(settings=None)))
    return DeploymentMonthlyInputs._known(service,session)

def test_authorized_same_connection_directory_avoids_reload_and_is_fresh_next_session():
    first,directory=opened()
    assert first._known_directory_snapshot is directory
    with patch('campaigniq.account_deployment_inputs.DirectoryTransaction',side_effect=AssertionError('Duplicate directory read')),patch('campaigniq.broker_account_binding.bindings',side_effect=lambda d,a:d.number):
        assert known(first)==known(first)=='current'
        first._active=False
        second,new=opened(number='changed')
        assert known(second)=='changed' and new is not directory

@pytest.mark.parametrize('write,proof',[(True,('on','repeatable read')),(False,('off','repeatable read')),(False,('on','read committed'))])
def test_no_seed_outside_proven_readonly_repeatable_read(write,proof):
    session,_=opened(write=write,proof=proof)
    assert not hasattr(session,'_known_directory_snapshot')
    with patch('campaigniq.account_deployment_inputs.DirectoryTransaction') as tx,patch('campaigniq.broker_account_binding.bindings',return_value={}):
        known(session);known(session)
        assert tx.return_value._load.call_count==2

@pytest.mark.parametrize('failure,error',[('expired',ClosedAccountSession),('failed',AccountTransactionFailed),('idle',ValueError)])
def test_seed_does_not_bypass_expiry_or_failed_transaction(failure,error):
    session,_=opened()
    if failure=='expired':session._active=False
    elif failure=='failed':session._connection.info.transaction_status=TransactionStatus.INERROR
    else:session._connection.info.transaction_status=TransactionStatus.IDLE
    with pytest.raises(error):known(session)

def test_new_snapshot_stage_names_capture_only_allowlisted_values():
    from campaigniq import navigation_timing as timing
    from tests.test_navigation_timing import UI
    ui=UI()
    names=('snapshot_current_result_verification','snapshot_retained_income','snapshot_history_verification','snapshot_retained_result_verification')
    with timing.navigation_run(ui):
        timing.authorize_panel(ui,administrator=True)
        for name in names:
            with timing.stage(name):pass
    assert set(names)<=ui.reports[0]['stages'].keys()
