from copy import deepcopy
from datetime import date
from decimal import Decimal
import pytest

from campaigniq.persistence.crypto_month import build_crypto_month
from campaigniq.monthly_import_execution import _verified_prior_deliveries
from campaigniq.domain.lot_book import LotBook
from campaigniq.persistence.artifact_storage import LocalFilesystemArtifactStorage
from campaigniq.persistence.reconciliation_decision import ReconciliationDecision, PersistedReconciliationMismatch, serialize_reconciliation_decision, reconciliation_decision_key, BOUNDARY_TIMING_EXCEPTION, ACCEPT_TRANSACTION_DERIVED_STATE
from campaigniq.persistence.import_provenance import capture_monthly_input_provenance, serialize_monthly_import_provenance
from campaigniq.persistence.monthly_publication import ensure_publication_protocol_in_storage, publish_finalized_month_marker_to_storage
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.import_contract import MonthlyInputRole


def empty_report(account):
    return dict(account=account,fills=[],cash_ledger=[],holdings_snapshot={},warnings=[],
                opening_cash_usd=None,last_reported_cash_usd=None,net_funding_usd='0')


@pytest.mark.parametrize('placeholder', ['Crypto #',None,''])
def test_first_identified_account_accepts_unused_placeholder(placeholder):
    old = build_crypto_month(empty_report(placeholder))
    original = deepcopy(old)
    result = build_crypto_month(empty_report('Crypto #SYNTHETIC'),old)
    assert result['account'] == 'Crypto #SYNTHETIC'
    assert result['ending_lots'] == {}
    assert old == original


def test_two_real_accounts_still_require_separate_opening_evidence():
    old = build_crypto_month(empty_report('Crypto #FIRST'))
    with pytest.raises(ValueError,match='account changed'):
        build_crypto_month(empty_report('Crypto #SECOND'),old)


@pytest.mark.parametrize('field,value', [
    ('ending_lots',{'BTC/USD':[{'quantity':'1','cost_usd':'100'}]}),
    ('holdings_snapshot',{'BTC/USD':{'quantity':'1'}}),
    ('fills',[{'pair':'BTC/USD'}]),
    ('cash_ledger',[{'amount_usd':'5'}]),
    ('last_reported_cash_usd','5'),
])
def test_unidentified_history_with_value_or_activity_is_not_merged(field,value):
    old = build_crypto_month(empty_report('Crypto #'))
    old[field] = value
    with pytest.raises(ValueError,match='account changed'):
        build_crypto_month(empty_report('Crypto #SYNTHETIC'),old)


def retained_evidence(tmp_path):
    history = tmp_path/'history';history.mkdir()
    cash_header = 'Account Statement\n\nCash Balance\nDATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE\n'
    archive = history/'Account Trade History May 2026.csv'
    archive.write_text(cash_header+'5/30/26,04:05:52,EXP,1,SOLD -100.0 GS UPON GOLDMAN SACHS GROUP INC,-1.50,,72000,0\n\n')
    current = tmp_path/'June.csv';current.write_text(cash_header+'\n\n')
    storage = LocalFilesystemArtifactStorage(tmp_path/'state')
    end = date(2026,5,31)
    ensure_publication_protocol_in_storage(storage,first_period_end=end)
    publish_finalized_month_marker_to_storage(storage,period_end=end)
    decision = ReconciliationDecision(date(2026,5,1),end,BOUNDARY_TIMING_EXCEPTION,ACCEPT_TRANSACTION_DERIVED_STATE,'Settlement crossing month end',('Dated broker history',),(PersistedReconciliationMismatch(Instrument('GS'),Decimal('0'),Decimal('100')),),True)
    storage.write_text(reconciliation_decision_key(period_end=end),serialize_reconciliation_decision(decision))
    provenance = capture_monthly_input_provenance({MonthlyInputRole.THINKORSWIM_TRADE_HISTORY:archive})
    storage.write_text('2026-05-import-provenance.json',serialize_monthly_import_provenance(period_start=date(2026,5,1),period_end=end,inputs=provenance))
    return current,history,storage,archive


def test_published_archived_delivery_does_not_require_overlapping_new_export(tmp_path):
    current,history,storage,_ = retained_evidence(tmp_path)
    events = _verified_prior_deliveries(current,period_start=date(2026,6,1),opening_lot_book=LotBook(),storage=storage,historical_source_root=history)
    assert len(events) == 1
    assert events[0].changes[0].quantity == Decimal('-100')
    assert events[0].occurred_at.date() == date(2026,5,30)


def test_changed_archive_cannot_supply_delivery_evidence(tmp_path):
    current,history,storage,archive = retained_evidence(tmp_path)
    archive.write_text(archive.read_text().replace('-100.0','-99.0'))
    with pytest.raises(ValueError,match='differs from its published source'):
        _verified_prior_deliveries(current,period_start=date(2026,6,1),opening_lot_book=LotBook(),storage=storage,historical_source_root=history)


def test_overlapping_and_archived_delivery_is_not_duplicated(tmp_path):
    current,history,storage,archive = retained_evidence(tmp_path)
    current.write_bytes(archive.read_bytes())
    events = _verified_prior_deliveries(current,period_start=date(2026,6,1),opening_lot_book=LotBook(),storage=storage,historical_source_root=history)
    assert len(events) == 1
