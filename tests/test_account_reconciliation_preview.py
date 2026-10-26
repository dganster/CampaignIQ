"""Complete synthetic month through the real account-bound reconciliation path."""
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
import pytest

from campaigniq.account_reconciliation_preview import ReadOnlyArtifacts,reconcile_saved
from campaigniq.broker_account_binding import bind
from campaigniq.import_draft_bridge import RestoredUpload
from tests.test_broker_account_binding import bound_context
from tests.test_account_import_preview import context

FIXTURES=Path(__file__).parent/'fixtures'
ROLES={'thinkorswim_trade_history':'account-month-history.csv','schwab_brokerage_statement':'account-month-closing.txt',
       'schwab_opening_position_snapshot':'account-month-opening.txt','schwab_realized_gain_loss':'account-month-realized.txt'}


@pytest.fixture
def month(bound_context):
    c=bound_context
    c.directory=bind(c.directory,c.admin,c.account,'securities','12345678',expected_revision=0)
    c.draft=c.bound.create(c.admin,c.account,2025,1)
    revision=0
    for role,name in ROLES.items():
        data=(FIXTURES/name).read_bytes()
        revision=c.bound.save(c.admin,c.account,c.draft.id,revision,role,
            RestoredUpload(name,'text/csv' if name.endswith('.csv') else 'text/plain',data))
    c.revision=revision
    return c


def run(c,**kwargs):
    return reconcile_saved(c.bound,c.admin,c.account,c.draft.id,c.revision,
        forex_applicable=False,crypto_applicable=False,**kwargs)


def test_complete_month_uses_existing_pipeline_and_independent_statement(month):
    c=month
    before=asdict(c.repositories[c.account].get(c.draft.id)),dict(c.repositories[c.account].docs)
    result=run(c)
    assert result.inventory_reconciled and not result.publication_authorized
    assert result.account_id==c.account and result.revision==4
    assert result.mismatches==() and result.source_findings==()
    assert len(result.closing_quantities)==1 and Decimal(result.closing_quantities[0][1])==110
    assert before==(asdict(c.repositories[c.account].get(c.draft.id)),dict(c.repositories[c.account].docs))


def test_one_share_closing_difference_is_reported_without_override(month):
    c=month
    c.revision=c.bound.save(c.admin,c.account,c.draft.id,c.revision,'schwab_brokerage_statement',
        RestoredUpload('mismatch.txt','text/plain',(FIXTURES/'account-month-closing-mismatch.txt').read_bytes()),replace=True)
    result=run(c)
    assert not result.inventory_reconciled and not result.publication_authorized
    assert len(result.mismatches)==1
    assert Decimal(result.mismatches[0].computed)==110
    assert Decimal(result.mismatches[0].statement)==111
    assert Decimal(result.mismatches[0].difference)==-1


def test_reconciliation_cannot_use_stale_revision_or_foreign_draft(month):
    c=month
    with pytest.raises(ValueError,match='Draft changed'):
        reconcile_saved(c.bound,c.admin,c.account,c.draft.id,0,forex_applicable=False,crypto_applicable=False)
    foreign=c.bound.create(c.member,c.other,2025,1)
    with pytest.raises(FileNotFoundError):
        reconcile_saved(c.bound,c.admin,c.account,foreign.id,0,forex_applicable=False,crypto_applicable=False)


def test_missing_identity_or_input_never_reaches_pipeline(month,monkeypatch):
    import campaigniq.monthly_import_execution as execution
    def forbidden(*args,**kwargs):raise AssertionError('Pipeline called for unready evidence')
    monkeypatch.setattr(execution,'execute_monthly_import',forbidden)
    c=month
    c.directory=bind(c.directory,c.admin,c.account,'securities','87654321',expected_revision=1,confirm_replace=True)
    with pytest.raises(ValueError,match='identity is unverified'):run(c)
    c.directory=bind(c.directory,c.admin,c.account,'securities','12345678',expected_revision=2,confirm_replace=True)
    c.repositories[c.account].docs.pop((c.draft.id,'schwab_realized_gain_loss'))
    with pytest.raises(ValueError,match='inputs are incomplete'):run(c)


def test_preview_passes_only_readonly_storage_and_no_publication_options(month,monkeypatch):
    import campaigniq.monthly_import_execution as execution
    actual=execution.execute_monthly_import
    roots=[]
    def checked(*args,**kwargs):
        assert kwargs['preview_only'] is True and isinstance(kwargs['artifact_storage'],ReadOnlyArtifacts)
        assert 'historical_source_root' not in kwargs and 'reconciliation_decision' not in kwargs
        roots.append(kwargs['authoritative_state_root'].parent)
        return actual(*args,**kwargs)
    monkeypatch.setattr(execution,'execute_monthly_import',checked)
    assert run(month).inventory_reconciled
    assert roots and not roots[0].exists()


@pytest.mark.parametrize('method',['write_text','delete'])
def test_readonly_adapter_rejects_any_mutation(method):
    storage=ReadOnlyArtifacts(object())
    with pytest.raises(PermissionError):getattr(storage,method)('x.json','{}')


def test_view_grant_reconciles_and_revocation_blocks_later_access(month):
    from campaigniq.account_administration import Permission
    c=month
    c.directory=c.directory.set_grant(c.admin,c.directory.user(c.member).id,c.account,Permission.VIEW,expected_revision=1)
    assert reconcile_saved(c.bound,c.member,c.account,c.draft.id,c.revision,
        forex_applicable=False,crypto_applicable=False).inventory_reconciled
    c.directory=c.directory.set_grant(c.admin,c.directory.user(c.member).id,c.account,None,expected_revision=2)
    with pytest.raises(PermissionError):
        reconcile_saved(c.bound,c.member,c.account,c.draft.id,c.revision,forex_applicable=False,crypto_applicable=False)


def test_browser_displays_closing_quantities_and_never_publication(month,monkeypatch):
    from tests.test_account_import_preview import _app
    app=_app(month,monkeypatch)
    next(c for c in app.checkbox if c.label=='Forex applicable').uncheck()
    next(c for c in app.checkbox if c.label=='Crypto applicable').uncheck()
    next(b for b in app.button if b.label=='Preview closing inventory').click().run(timeout=15)
    assert not app.exception and len(app.dataframe)==1
    assert any('Closing inventory reconciles' in s.value for s in app.success)
    assert not any('publish' in b.label.lower() for b in app.button)
