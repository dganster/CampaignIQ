from unittest.mock import patch
import pytest
from campaigniq.account_role_privilege_batch import read_role_guards
from campaigniq import account_runtime_roles as runtime
from tests.test_account_runtime_roles import Connection,Result


def rows():
    return [(i,s,*([False]*11)) for i,s in enumerate(('public','campaigniq_accounts'),1)]


@pytest.mark.parametrize('index',range(2))
@pytest.mark.parametrize('field',range(2,13))
def test_each_role_membership_creation_grant_option_or_ownership_violation_is_rejected(index,field):
    values=rows();row=list(values[index]);row[field]=True;values[index]=tuple(row)
    conn=Connection()
    with patch.object(conn,'execute',return_value=Result(values)),pytest.raises(PermissionError):
        read_role_guards(conn,'runtime')


@pytest.mark.parametrize('broken',('absent','missing','duplicate','reversed','null','integer_bool','malformed','wrong_ordinal'))
def test_incomplete_or_ambiguous_catalog_results_fail_closed(broken):
    values=rows()
    if broken=='absent':values=[]
    elif broken=='missing':values.pop()
    elif broken=='duplicate':values[1]=values[0]
    elif broken=='reversed':values.reverse()
    elif broken=='malformed':values[0]=(1,'public')
    elif broken=='wrong_ordinal':values[0]=(True,*values[0][1:])
    else:values[0]=(*values[0][:2],None if broken=='null' else 0,*values[0][3:])
    conn=Connection()
    with patch.object(conn,'execute',return_value=Result(values)),pytest.raises((ValueError,PermissionError)):
        read_role_guards(conn,'runtime')


def test_one_fresh_guard_query_and_one_privilege_query_per_role_state():
    conn=Connection();runtime._role_state(conn,'runtime')
    assert len(conn.calls)==2
    assert 'campaigniq_batched_role_guards' in conn.calls[0][0]
    assert conn.calls[0][1]==(['public','campaigniq_accounts'],'runtime')
    conn.member=True
    with pytest.raises(PermissionError):runtime._role_state(conn,'runtime')
    assert len(conn.calls)==3
