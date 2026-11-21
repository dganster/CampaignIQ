from copy import deepcopy
from dataclasses import replace
import json
import pytest
from campaigniq.account_administration import AuditEvent
from campaigniq.ui.account_signedin_administration_view import audit_detail_fields,activity_rows
from tests.test_administration_activity_display import recorded_directory
from tests.test_account_signedin_admin_view import screen


def test_saved_binding_fields_are_readable_and_exact_fingerprint_is_preserved():
    d=recorded_directory();original=deepcopy(d);e=d.events[0]
    fields=dict(audit_detail_fields(d,e))
    assert fields['Broker']=='Schwab' and fields['Account type']=='Equities and options'
    assert fields['Account ending']=='5678' and fields['Broker account fingerprint']==dict(e.details)['digest']
    assert d==original


def test_access_status_fields_and_unknown_values_are_not_reinterpreted():
    d=recorded_directory()
    assert dict(audit_detail_fields(d,d.events[2]))=={'User receiving access':'Current user name','Permission':'Edit'}
    assert dict(audit_detail_fields(d,d.events[4]))=={'Saved name':'Earlier user name','User status':'Enabled','User role':'Member'}
    e=AuditEvent(1,d.users[0].id,'future_action',d.users[0].id,(('future_value','exact_original'),('enabled','unknown'),('user_id','missing')))
    assert audit_detail_fields(d,e)==[('Future value','exact_original'),('User status','unknown'),('User receiving access','Unavailable user')]


@pytest.mark.parametrize('deployment',[False,True])
def test_wrapping_table_and_nested_raw_record_preserve_full_selected_event(deployment):
    at=screen(deployment=deployment);d=recorded_directory();before=deepcopy(d)
    long_name='Long brokerage account name '+('detail '*20).strip()
    d=d.configure_account(d.users[0].identity,d.accounts[0].id,name=long_name,expected_revision=d.revision)
    at.session_state['directory']=d;at.run(timeout=15)
    assert not at.exception
    table=next(t for t in at.table if 'Changed by' in t.value.columns)
    assert table.value.to_dict('records')==activity_rows(d)
    assert long_name in set(table.value['User or account'])
    assert not any('Changed by' in df.value.columns for df in at.dataframe)
    technical=next(e for e in at.expander if e.label=='Technical audit details')
    raw=next(e for e in technical.expander if e.label=='Raw audit record (troubleshooting)')
    assert not technical.proto.expanded and not raw.proto.expanded
    assert 'Broker: Schwab' in [t.value for t in technical.text]
    assert json.loads(raw.json[0].value)['details']==dict(d.events[0].details)
    next(s for s in at.selectbox if s.label=='Activity event').select(d.revision).run(timeout=15)
    technical=next(e for e in at.expander if e.label=='Technical audit details')
    raw=next(e for e in technical.expander if e.label=='Raw audit record (troubleshooting)')
    assert json.loads(raw.json[0].value)['details']['name']==long_name
    assert 'Saved name: '+long_name in [t.value for t in technical.text]
    assert at.session_state['directory']==d and not at.session_state['calls']
