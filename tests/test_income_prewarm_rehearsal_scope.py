from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock,patch
import pytest
from campaigniq.account_income_rehearsal import _retained_income_parity


def test_income_parity_uses_current_authorized_choices_and_fresh_readonly_session():
    user=object();events=[]
    @contextmanager
    def transaction(actor,account):
        assert actor is user and account=='owned'
        events.append('enter')
        session=SimpleNamespace(account=SimpleNamespace(storage_scope='scope'),
            artifacts=SimpleNamespace(list_keys=lambda:()),_check=lambda:events.append('check'))
        try:yield object(),session,1
        finally:events.append('close')
    gateway=SimpleNamespace(overview=Mock(return_value=(SimpleNamespace(id='owned',legacy=True),SimpleNamespace(id='new',legacy=False))),legacy_transaction=transaction)
    service=SimpleNamespace(gateway=gateway,_known=lambda session:{})
    with patch('campaigniq.account_income_rehearsal.legacy_statement_rows',return_value=[]),patch('campaigniq.account_income_evidence.legacy_statements',return_value=('exact-evidence',)) as read:
        _retained_income_parity(service,user)
    gateway.overview.assert_called_once_with(user)
    assert read.call_count==3 and events==['enter','check','close']


def test_revoked_access_is_not_skipped_or_treated_as_success():
    gateway=SimpleNamespace(overview=lambda user:(SimpleNamespace(id='revoked',legacy=True),),legacy_transaction=Mock(side_effect=PermissionError('revoked')))
    with pytest.raises(PermissionError):_retained_income_parity(SimpleNamespace(gateway=gateway),object())


def test_missing_authorized_retained_accounts_cannot_pass_vacuously():
    service=SimpleNamespace(gateway=SimpleNamespace(overview=lambda user:()))
    with pytest.raises(AssertionError,match='Accessible retained'):_retained_income_parity(service,object())
