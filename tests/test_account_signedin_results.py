"""Read committed engine output, never a recomputed preview."""
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from decimal import Decimal
import json
from types import SimpleNamespace
import pytest

from campaigniq import account_signedin_results as module
from campaigniq.account_administration import Permission
from campaigniq.account_legacy_access_rehearsal import RehearsalSession
from campaigniq.account_signin import signed_in_accounts
from tests.test_account_signedin_publication import signed, local, month, bound_context, context, FIXTURES, publish


@pytest.fixture
def results(signed,monkeypatch):
    c=signed;publish(c);c.read_sessions=[];c.snapshots=[]
    gateway=c.signed.gateway
    @contextmanager
    def connection(*,readonly=False):
        assert readonly is True
        c.snapshots.append(readonly)
        yield SimpleNamespace(execute=lambda *a,**k:None)
    gateway._directory._connection=connection
    gateway._current=lambda conn,user:(c.directory,signed_in_accounts(user,c.directory,trusted_issuer=c.admin.issuer))
    gateway._ready=lambda conn:True
    class Session:
        def __init__(self,conn,access,writable,registry):
            assert writable is False
            self._connection=conn;self._active=True
            self.artifacts=c.artifacts[access.account_id];self.drafts=c.repositories[access.account_id]
            c.read_sessions.append(self)
        def _check(self):assert self._active
    monkeypatch.setattr(module,'AccountDataSession',Session)
    return c


def load(c):return c.signed.results(c.user,c.account,c.draft.id,c.revision)


def test_committed_110_shares_original_hashes_and_immutable_summary(results,monkeypatch):
    import campaigniq.monthly_import_execution as execution
    c=results;before=dict(c.artifacts[c.account].rows)
    def forbidden(*a,**k):raise AssertionError('Results view recomputed import')
    monkeypatch.setattr(execution,'execute_monthly_import',forbidden)
    result=load(c)
    assert result.positions==( ('SPY','110.0000'), ) or Decimal(result.positions[0][1])==110
    assert result.month=='2025-01' and result.revision==4 and result.original_documents==4
    assert result.brokerage_realized_usd=='0' and not result.realized
    assert not result.forex_applicable and not result.crypto_applicable
    assert c.artifacts[c.account].rows==before
    assert c.snapshots==[True] and all(not s._active for s in c.read_sessions)
    with pytest.raises(FrozenInstanceError):result.month='2026-01'


def test_stale_and_foreign_draft_rejected_before_results_snapshot(results):
    c=results
    with pytest.raises(ValueError,match='Draft changed'):c.signed.results(c.user,c.account,c.draft.id,0)
    with pytest.raises(FileNotFoundError):c.signed.results(c.user,c.account,'foreign',0)
    assert not c.snapshots


@pytest.mark.parametrize('revision',[True,'4',None])
def test_strict_revision(results,revision):
    c=results
    with pytest.raises(ValueError):c.signed.results(c.user,c.account,c.draft.id,revision)
    assert not c.snapshots


def test_server_session_and_current_access_rechecked(results):
    c=results
    with pytest.raises(PermissionError):c.signed.results(c.admin,c.account,c.draft.id,c.revision)
    with pytest.raises(PermissionError):c.signed.results(RehearsalSession(c.member),c.account,c.draft.id,c.revision)
    c.directory=c.directory.set_grant(c.admin,c.directory.user(c.member).id,c.account,Permission.VIEW,
        expected_revision=c.directory.revision)
    assert c.signed.results(RehearsalSession(c.member),c.account,c.draft.id,c.revision).month=='2025-01'
    c.directory=c.directory.set_grant(c.admin,c.directory.user(c.member).id,c.account,None,expected_revision=c.directory.revision)
    with pytest.raises(PermissionError):c.signed.results(RehearsalSession(c.member),c.account,c.draft.id,c.revision)


def test_readonly_artifact_write_is_blocked_and_handle_expires(results,monkeypatch):
    def write(service,session,*a):session.artifacts.write_text('blocked.json','{}')
    monkeypatch.setattr(module,'_results_session',write)
    with pytest.raises(PermissionError):load(results)
    assert all(not s._active for s in results.read_sessions)


@pytest.mark.parametrize('key',['finalized.json','realized-attributions.json','market-applicability.json','forex-settlement-attributions.json'])
def test_missing_generation_component_does_not_return_partial_results(results,key):
    results.artifacts[results.account].rows.pop('2025-01-'+key)
    with pytest.raises((ValueError,KeyError,FileNotFoundError)):load(results)


def test_original_document_corruption_blocks_results(results):
    c=results;repo=c.repositories[c.account];key=(c.draft.id,'thinkorswim_trade_history')
    name,media,data=repo.docs[key];repo.docs[key]=(name,media,data+b'corrupt')
    with pytest.raises(ValueError,match='Retained originals differ'):load(c)


def test_wrong_artifact_period_and_market_choice_conflict_block_results(results):
    c=results;storage=c.artifacts[c.account];key='2025-01-realized-attributions.json';old=storage.rows[key]
    value=json.loads(old);value['period_end']='2025-02-28';storage.rows[key]=json.dumps(value)
    with pytest.raises(ValueError,match='monetary artifact period'):load(c)
    storage.rows[key]=old
    key='2025-01-market-applicability.json';value=json.loads(storage.rows[key]);value['forex_applicable']=True
    storage.rows[key]=json.dumps(value)
    with pytest.raises(ValueError,match='market choices differ'):load(c)


def test_unknown_basis_is_displayed_as_unknown_without_imputation(results):
    c=results;storage=c.artifacts[c.account];key='2025-01-lot-book.json'
    value=json.loads(storage.rows[key]);value['lots'][0]['basis_total']=None
    storage.rows[key]=json.dumps(value)
    assert any(row[3]=='Unknown' for row in load(c).lots)


def test_broker_money_and_forex_subtotals_remain_separate(results):
    from datetime import date,datetime
    from dataclasses import replace
    from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
    from campaigniq.domain.lot_attribution import RealizedAttribution
    from campaigniq.domain.value_objects.instrument import Instrument
    from campaigniq.persistence.realized_attribution_store import serialize_realized_attributions
    from campaigniq.persistence.forex_settlement_attribution_store import serialize_forex_settlement_attributions
    from tests.test_forex_settlement_attribution_store import _attribution
    c=results;storage=c.artifacts[c.account]
    record=RealizedGainLossRecord(date(2025,1,15),Instrument('SPY'),Decimal('1'),Decimal('100.12'),
        Decimal('100.12'),Decimal('90.10'),Decimal('10.02'),'FIFO','Short')
    storage.rows['2025-01-realized-attributions.json']=serialize_realized_attributions(
        period_start=date(2025,1,1),period_end=date(2025,1,31),attributions=(RealizedAttribution(record,()),))
    fx=_attribution();fx=replace(fx,settlement=replace(fx.settlement,trade_at=datetime(2025,1,15),settlement_at=datetime(2025,1,17)))
    storage.rows['2025-01-forex-settlement-attributions.json']=serialize_forex_settlement_attributions(
        period_start=date(2025,1,1),period_end=date(2025,1,31),attributions=(fx,))
    for key in ('2025-01-account-publication-receipt.json','2025-01-market-applicability.json'):
        value=json.loads(storage.rows[key]);value['forex_applicable']=True;storage.rows[key]=json.dumps(value)
    result=load(c)
    assert result.brokerage_realized_usd=='10.02' and result.forex_settled_usd=='3.00'
    assert result.realized[0][-1]=='10.02' and result.forex_settlements[0][2]=='3.00'


def test_crypto_quantities_keep_provisional_status_and_warnings(results):
    c=results;storage=c.artifacts[c.account]
    for key in ('2025-01-account-publication-receipt.json','2025-01-market-applicability.json'):
        value=json.loads(storage.rows[key]);value['crypto_applicable']=True;storage.rows[key]=json.dumps(value)
    storage.rows['2025-01-crypto.json']=json.dumps(dict(format='campaigniq.crypto_month',version=1,
        period_end='2025-01-31',ending_quantities={'BTC/USD':'0.123456'},
        status='provisional export evidence',warnings=['Independent month-end evidence is missing.']))
    result=load(c)
    assert result.crypto_positions==( ('BTC/USD','0.123456'), )
    assert result.crypto_status=='provisional export evidence'
    assert result.crypto_warnings==('Independent month-end evidence is missing.',)
