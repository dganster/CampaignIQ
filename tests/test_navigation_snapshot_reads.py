import json
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
import pytest
from psycopg.pq import TransactionStatus
from campaigniq.persistence.deployment_account_data import DeploymentDataSession
from campaigniq.persistence.account_data_session import ClosedAccountSession,AccountTransactionFailed
from campaigniq.account_continuation import ContinuationArtifacts
from campaigniq.account_table_policy_checks import validate_tables,PRIVILEGES
PROTOCOL='.campaigniq-monthly-publication-v1.json';KEY='2026-10-value.json'
class Connection:
    def __init__(self,proof=('on','repeatable read')):
        self.info=SimpleNamespace(transaction_status=TransactionStatus.INTRANS);self.proof=proof;self.calls=0
    def execute(self,*a):self.calls+=1;return SimpleNamespace(fetchone=lambda:self.proof)
class Reader:
    namespace='scope'
    def __init__(self):
        self.values={PROTOCOL:json.dumps(dict(format='campaigniq.monthly_publication',version=1,first_protected_period_end='2026-10-31')),KEY:'original'}
        self.list_calls=0;self.read_calls=0
    def list_keys(self):self.list_calls+=1;return tuple(self.values)
    def read_text(self,k):self.read_calls+=1;return self.values[k]
def session(conn=None,writable=False):
    return DeploymentDataSession(conn or Connection(),SimpleNamespace(account_id=str(uuid4())),writable,str(uuid4()))
def test_readonly_repeatable_snapshot_reuses_keys_marker_and_text():
    conn=Connection();s=session(conn);r=Reader();a=ContinuationArtifacts(s,(),r)
    for _ in range(5):assert a.exists(KEY) and a.read_text(KEY)=='original'
    assert (r.list_calls,r.read_calls,conn.calls)==(1,2,1)
@pytest.mark.parametrize('proof,writable',[(('off','repeatable read'),False),(('on','read committed'),False),(('on','repeatable read'),True)])
def test_other_transaction_modes_keep_live_validation(proof,writable):
    s=session(Connection(proof),writable);r=Reader();a=ContinuationArtifacts(s,(),r)
    assert a.read_text(KEY)=='original';r.values[KEY]='changed';assert a.read_text(KEY)=='changed'
    r.values[PROTOCOL]='invalid'
    with pytest.raises(ValueError):a.read_text(KEY)
    assert r.list_calls>1
@pytest.mark.parametrize('failure',['closed','sql-error','idle'])
def test_cache_hit_does_not_bypass_expired_or_failed_transaction(failure):
    s=session();a=ContinuationArtifacts(s,(),Reader());a.read_text(KEY)
    if failure=='closed':s._active=False;error=ClosedAccountSession
    elif failure=='sql-error':s._connection.info.transaction_status=TransactionStatus.INERROR;error=AccountTransactionFailed
    else:s._connection.info.transaction_status=TransactionStatus.IDLE;error=ValueError
    with pytest.raises(error):a.read_text(KEY)
def test_fresh_session_revalidates_changed_source_and_boundary():
    r=Reader();first=session();assert ContinuationArtifacts(first,(),r).read_text(KEY)=='original';first._active=False
    r.values[KEY]='changed';second=session();assert ContinuationArtifacts(second,(),r).read_text(KEY)=='changed'
    second._active=False;r.values[PROTOCOL]='invalid'
    with pytest.raises(ValueError):ContinuationArtifacts(session(),(),r)
def test_directory_reuse_is_limited_to_active_readonly_snapshot():
    from campaigniq.account_deployment_inputs import DeploymentMonthlyInputs
    service=SimpleNamespace(gateway=SimpleNamespace(_directory=SimpleNamespace(settings=None)));s=session()
    with patch('campaigniq.account_deployment_inputs.DirectoryTransaction') as tx,patch('campaigniq.broker_account_binding.bindings',return_value={'bound':'same'}):
        tx.return_value._load.return_value=object()
        assert DeploymentMonthlyInputs._known(service,s)=={'bound':'same'}
        assert DeploymentMonthlyInputs._known(service,s)=={'bound':'same'}
        assert tx.return_value._load.call_count==1
        s._active=False
        with pytest.raises(ClosedAccountSession):DeploymentMonthlyInputs._known(service,s)
        DeploymentMonthlyInputs._known(service,session());assert tx.return_value._load.call_count==2
def test_writable_directory_reads_are_not_reused():
    from campaigniq.account_deployment_inputs import DeploymentMonthlyInputs
    service=SimpleNamespace(gateway=SimpleNamespace(_directory=SimpleNamespace(settings=None)));s=session(writable=True)
    with patch('campaigniq.account_deployment_inputs.DirectoryTransaction') as tx,patch('campaigniq.broker_account_binding.bindings',return_value={}):
        DeploymentMonthlyInputs._known(service,s);DeploymentMonthlyInputs._known(service,s)
        assert tx.return_value._load.call_count==2
@pytest.mark.parametrize('change',['missing','extra','wrong-order','wrong-type','forbidden','missing-allowed','owner'])
def test_batched_policy_fails_closed_for_every_result_or_permission_mismatch(change):
    policy=(('public','sample',{'SELECT'}),);rows=[(1,p,p=='SELECT',False) for p in PRIVILEGES]
    if change=='missing':rows.pop()
    elif change=='extra':rows.append(rows[-1])
    elif change=='wrong-order':rows.reverse()
    elif change=='wrong-type':rows[0]=(True,'SELECT',True,False)
    elif change=='forbidden':rows[1]=(1,'INSERT',True,False)
    elif change=='missing-allowed':rows[0]=(1,'SELECT',False,False)
    elif change=='owner':rows[0]=(1,'SELECT',True,True)
    conn=SimpleNamespace(execute=lambda *a:SimpleNamespace(fetchall=lambda:rows))
    with pytest.raises(PermissionError):validate_tables(conn,policy,check_ownership=True)
