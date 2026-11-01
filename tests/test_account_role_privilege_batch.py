from unittest.mock import patch
import pytest
from campaigniq import account_runtime_roles as runtime
from campaigniq.account_publication_roles import POLICY as PUBLICATION
from campaigniq.account_preference_roles import POLICY as PREFERENCES
from campaigniq.account_role_privilege_batch import read_privileges
from tests.role_state_reference import _role_state as reference
from tests.test_account_runtime_roles import Connection,Result

@pytest.mark.parametrize('policy',(runtime.POLICY,PUBLICATION,PREFERENCES))
def test_batched_state_and_digest_equal_frozen_scalar_validator(policy):
    old=Connection();new=Connection()
    for conn in (old,new):conn.grants={s+'.'+t:allowed for s,t,allowed in policy}
    expected=reference(old,'runtime',policy)
    assert runtime._role_state(new,'runtime',policy)==expected
    assert runtime.migration.digest(runtime._role_state(new,'runtime',policy))==runtime.migration.digest(expected)
    assert len(old.calls)>200
    assert sum('campaigniq_batched_privileges' in q for q,_ in new.calls)==2
    assert len(new.calls)<30

@pytest.mark.parametrize('policy',(runtime.POLICY,PUBLICATION,PREFERENCES))
@pytest.mark.parametrize('privilege',runtime.PRIVILEGES)
@pytest.mark.parametrize('index',range(len(runtime.POLICY)))
def test_each_table_privilege_matches_scalar_acceptance(policy,privilege,index):
    old=Connection();new=Connection();schema,table,_=policy[index]
    for conn in (old,new):conn.grants[schema+'.'+table]=(privilege,)
    try:expected=reference(old,'runtime',policy)
    except PermissionError:
        with pytest.raises(PermissionError):runtime._role_state(new,'runtime',policy)
    else:assert runtime._role_state(new,'runtime',policy)==expected

@pytest.mark.parametrize('attribute',('member','elevated','owner','create','column','option'))
def test_existing_guard_failures_match_reference(attribute):
    for fn in (reference,runtime._role_state):
        conn=Connection();setattr(conn,attribute,True)
        with pytest.raises(PermissionError):fn(conn,'runtime')

@pytest.mark.parametrize('broken',('missing','duplicate','null','wrong_order','malformed'))
def test_incomplete_or_ambiguous_batch_results_fail_closed(broken):
    conn=Connection();rows=conn.execute('/* campaigniq_batched_privileges */',(None,None,None,None,['public.x'],list(runtime.PRIVILEGES))).fetchall()
    if broken=='missing':rows.pop()
    elif broken=='duplicate':rows[1]=rows[0]
    elif broken=='null':rows[0]=(*rows[0][:2],None,*rows[0][3:])
    elif broken=='wrong_order':rows.reverse()
    else:rows[0]=(1,'SELECT')
    with patch.object(conn,'execute',return_value=Result(rows)),pytest.raises(ValueError):
        read_privileges(conn,'runtime',(('public','x',()),),runtime.PRIVILEGES)

def test_privilege_change_is_rechecked_in_same_connection():
    conn=Connection();runtime._role_state(conn,'runtime')
    conn.grants['public.artifact_storage_dev']=('UPDATE',)
    with pytest.raises(PermissionError):runtime._role_state(conn,'runtime')

@pytest.mark.parametrize('field',(3,5))
@pytest.mark.parametrize('index',range(len(runtime.POLICY)*len(runtime.PRIVILEGES)))
def test_any_table_or_column_grant_option_is_rejected(field,index):
    conn=Connection();policy=runtime.POLICY
    rows=conn.execute('/* campaigniq_batched_privileges */',(None,None,None,None,[s+'.'+t for s,t,_ in policy],list(runtime.PRIVILEGES))).fetchall()
    row=list(rows[index]);row[field]=True;rows[index]=tuple(row)
    with patch.object(conn,'execute',return_value=Result(rows)),pytest.raises(PermissionError):
        read_privileges(conn,'runtime',policy,runtime.PRIVILEGES)
