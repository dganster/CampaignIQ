"""Selected account reporting retains ownership, revisions and separate results."""
from contextlib import contextmanager,ExitStack
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4
import pytest

from campaigniq.account_administration import Permission
from campaigniq.account_selection_preview import SelectedDraft,compare_selected,selected_sessions
from campaigniq.broker_account_binding import bind
from campaigniq.import_draft_bridge import RestoredUpload
from campaigniq.persistence.account_data_session import ClosedAccountSession
from tests.test_account_reconciliation_preview import month,bound_context,context,ROLES,FIXTURES


@pytest.fixture
def pair(month,monkeypatch):
    c=month
    c.directory=bind(c.directory,c.admin,c.other,'securities','87654321',expected_revision=c.directory.revision)
    c.other_draft=c.bound.create(c.member,c.other,2025,1)
    revision=0
    for role,name in ROLES.items():
        text=(FIXTURES/name).read_text().replace('12345678','87654321').replace('1234-5678','8765-4321')
        text=text.replace('+10,','+20,').replace('100.0000 100.0000 10,000.00 10,000.00','200.0000 100.0000 20,000.00 20,000.00')
        text=text.replace('110.0000 100.0000 11,000.00 11,000.00','220.0000 100.0000 22,000.00 22,000.00')
        revision=c.bound.save(c.member,c.other,c.other_draft.id,revision,role,
            RestoredUpload(name,'text/csv' if name.endswith('.csv') else 'text/plain',text.encode()))
    c.directory=c.directory.set_grant(c.admin,c.directory.user(c.admin).id,c.other,Permission.VIEW,expected_revision=c.directory.revision)
    c.selection=(SelectedDraft(c.account,c.draft.id,c.revision,False,False),SelectedDraft(c.other,c.other_draft.id,revision,False,False))
    # In-memory repositories exercise the real financial pipeline. SQL snapshot
    # semantics are tested by the separate explicit PostgreSQL rehearsal.
    @contextmanager
    def sessions(gateway,identity,ids):
        c.directory.select(identity,ids)
        with ExitStack() as stack:
            yield tuple(stack.enter_context(gateway.transaction(identity,i)) for i in ids)
    import campaigniq.account_selection_preview as module
    monkeypatch.setattr(module,'selected_sessions',sessions)
    return c


def compare(c,selection=None,**kwargs):
    return compare_selected(c.bound,c.admin,2025,1,c.selection if selection is None else selection,**kwargs)


def test_two_accounts_remain_separate_and_quantity_total_is_opt_in(pair):
    c=pair;result=compare(c)
    assert result.all_inventory_reconciled and not result.publication_authorized
    assert result.quantity_totals==()
    assert [r.account_id for r in result.accounts]==[c.account,c.other]
    assert [Decimal(r.closing_quantities[0][1]) for r in result.accounts]==[110,220]
    totals=compare(c,include_totals=True)
    assert Decimal(totals.quantity_totals[0][1])==330
    assert len(totals.accounts)==2 and totals.accounts[0].draft_id!=totals.accounts[1].draft_id


def test_failed_account_is_preserved_and_totals_withheld(pair):
    c=pair
    repo=c.repositories[c.other]
    key=(c.other_draft.id,'schwab_brokerage_statement')
    name,media,data=repo.docs[key];repo.docs[key]=(name,media,data.replace(b'220.0000',b'221.0000'))
    result=compare(c,include_totals=True)
    assert not result.all_inventory_reconciled and result.quantity_totals==()
    assert result.accounts[0].inventory_reconciled and not result.accounts[1].inventory_reconciled
    assert Decimal(result.accounts[1].mismatches[0].difference)==-1


def test_unauthorized_selection_is_not_silently_trimmed(pair,monkeypatch):
    c=pair
    c.directory=c.directory.set_grant(c.admin,c.directory.user(c.admin).id,c.other,None,expected_revision=c.directory.revision)
    import campaigniq.account_selection_preview as module
    def forbidden(*args,**kwargs):raise AssertionError('Financial source read before whole selection authorized')
    monkeypatch.setattr(module,'_reconcile_session',forbidden)
    with pytest.raises(PermissionError):compare(c)


@pytest.mark.parametrize('kind',['stale','foreign','month'])
def test_wrong_month_stale_or_foreign_draft_cannot_produce_partial_report(pair,kind):
    c=pair;items=list(c.selection)
    if kind=='stale':items[1]=replace(items[1],revision=0)
    elif kind=='foreign':items[1]=replace(items[1],draft_id=c.draft.id)
    else:
        other=c.bound.create(c.member,c.other,2025,2);items[1]=replace(items[1],draft_id=other.id,revision=0)
    with pytest.raises((ValueError,FileNotFoundError)):compare(c,tuple(items))


@pytest.mark.parametrize('selection',[(),[],None])
def test_empty_or_mutable_selection_rejected(pair,selection):
    if selection is None:selection='not a selection'
    with pytest.raises(ValueError):compare(pair,selection)


def test_duplicate_accounts_rejected(pair):
    with pytest.raises(ValueError):compare(pair,(pair.selection[0],pair.selection[0]))


def test_actual_session_coordinator_uses_one_readonly_connection_and_expires_handles(month):
    c=month;connections=[]
    from tests.test_account_data_session import Connection
    conn=Connection()
    @contextmanager
    def readonly(*,readonly=False):
        assert readonly is True
        connections.append(conn)
        yield conn
    c.bound.gateway._directory._connection=readonly
    c.bound.gateway._directory.registry_id=c.registry
    with selected_sessions(c.bound.gateway,c.admin,(c.account,)) as sessions:
        handles=sessions
        assert all(s._connection is conn and s._writable is False for s in sessions)
        assert conn.queries[0][0]=='SET LOCAL search_path = campaigniq_accounts_dev, pg_catalog'
    assert connections==[conn]
    with pytest.raises(ClosedAccountSession):handles[0]._check()


def test_unauthorized_coordinator_never_opens_financial_snapshot(month):
    c=month
    @contextmanager
    def forbidden(**kwargs):
        raise AssertionError('Unauthorized financial snapshot opened')
        yield
    c.bound.gateway._directory._connection=forbidden
    with pytest.raises(PermissionError):
        with selected_sessions(c.bound.gateway,c.admin,(c.account,c.other)):pass


def test_selection_order_does_not_change_totals(pair):
    a=compare(pair,include_totals=True)
    b=compare(pair,tuple(reversed(pair.selection)),include_totals=True)
    assert a.quantity_totals==b.quantity_totals
    assert tuple(r.account_id for r in a.accounts)==tuple(reversed(tuple(r.account_id for r in b.accounts)))


def test_browser_comparison_renders_both_accounts_and_optional_total(pair,monkeypatch):
    from tests.test_account_import_preview import _app
    app=_app(pair,monkeypatch)
    select=next(s for s in app.multiselect if s.label=='Accounts to compare')
    select.set_value([pair.account,pair.other]).run()
    for checkbox in app.checkbox:
        if checkbox.label.startswith(('Forex applicable —','Crypto applicable —')):checkbox.uncheck()
    next(c for c in app.checkbox if c.label=='Show quantity totals').check()
    next(b for b in app.button if b.label=='Compare saved account months').click().run(timeout=15)
    assert not app.exception and len(app.dataframe)==3
    assert any('All selected accounts reconcile' in status.value for status in app.success)
    assert not any('publish' in b.label.lower() for b in app.button)
