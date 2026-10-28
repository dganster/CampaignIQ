"""Selected published reports share a single read-only snapshot."""
from dataclasses import replace
from decimal import Decimal
from uuid import UUID,uuid4
import pytest

from campaigniq import account_signedin_comparison as module
from campaigniq.account_administration import Permission
from campaigniq.account_legacy_access_rehearsal import RehearsalSession
from campaigniq.account_signedin_comparison import PublishedSelection
from campaigniq.account_selection_rehearsal import synthetic_sources
from campaigniq.broker_account_binding import bind
from tests.test_account_signedin_results import results, signed, local, month, bound_context, context, FIXTURES


@pytest.fixture
def comparison(results,monkeypatch):
    c=results
    c.directory=replace(c.directory,accounts=tuple(replace(a,legacy=False,broker='schwab') if a.id==c.account else a for a in c.directory.accounts))
    c.directory=c.directory.add_account(c.admin,owner_id=c.directory.user(c.admin).id,name='Second new account',
        broker='schwab',expected_revision=c.directory.revision)
    c.second=c.directory.accounts[-1].id
    c.directory=bind(c.directory,c.admin,c.second,'securities','87654321',expected_revision=c.directory.revision)
    other=c.bound.create(c.admin,c.second,2025,1);revision=0
    for role,upload in synthetic_sources('87654321',double=True).items():
        revision=c.bound.save(c.admin,c.second,other.id,revision,role,upload)
    from tests.test_account_local_publication import MemoryStorage
    c.artifacts[c.second]=MemoryStorage(c.second)
    c.signed.publish(c.user,c.second,other.id,revision,authorize_local_publication=True,
        forex_applicable=False,crypto_applicable=False)
    c.selections=(PublishedSelection(c.account,c.draft.id,c.revision),PublishedSelection(c.second,other.id,revision))
    from campaigniq import account_signedin_results as results_module
    monkeypatch.setattr(module,'AccountDataSession',results_module.AccountDataSession)
    return c


def compare(c,**kw):return c.signed.compare(c.user,2025,1,c.selections,**kw)


def test_two_accounts_keep_results_separate_with_optional_330_total(comparison):
    c=comparison
    before={key:dict(storage.rows) for key,storage in c.artifacts.items() if hasattr(storage,'rows')}
    result=compare(c)
    assert result.combined_positions==() and not result.includes_combined_positions
    assert [Decimal(row.positions[0][1]) for row in result.accounts]==[110,220]
    assert c.snapshots==[True]
    result=compare(c,include_positions=True)
    assert result.includes_combined_positions and Decimal(result.combined_positions[0][1])==330
    assert before=={key:dict(storage.rows) for key,storage in c.artifacts.items() if hasattr(storage,'rows')}
    assert all(not session._active for session in c.read_sessions)


@pytest.mark.parametrize('selection,flag',[((),False),([],False),((object(),),False),(None,False)])
def test_bad_selection_rejected_before_read_snapshot(comparison,selection,flag):
    c=comparison
    with pytest.raises(ValueError):c.signed.compare(c.user,2025,1,selection,include_positions=flag)
    assert not c.snapshots


@pytest.mark.parametrize('revision',[True,-1,'4'])
def test_selection_revision_is_strict(revision):
    with pytest.raises(ValueError):PublishedSelection(str(uuid4()),uuid4(),revision)


def test_duplicate_account_and_nonboolean_totals_are_rejected(comparison):
    c=comparison
    with pytest.raises(ValueError):c.signed.compare(c.user,2025,1,(c.selections[0],)*2)
    with pytest.raises(ValueError):compare(c,include_positions=1)
    assert not c.snapshots


def test_stale_wrong_month_and_foreign_draft_do_not_load_any_result(comparison,monkeypatch):
    c=comparison
    calls=[]
    monkeypatch.setattr(module,'_results_session',lambda *a:calls.append(a))
    for bad in (replace(c.selections[1],revision=0),replace(c.selections[1],draft_id=c.draft.id)):
        with pytest.raises((ValueError,FileNotFoundError)):
            c.signed.compare(c.user,2025,1,(c.selections[0],bad),include_positions=True)
    with pytest.raises(ValueError):c.signed.compare(c.user,2025,2,c.selections)
    assert calls==[] and all(not s._active for s in c.read_sessions)


def test_missing_one_artifact_withholds_entire_report_and_totals(comparison):
    c=comparison;c.artifacts[c.second].rows.pop('2025-01-realized-attributions.json')
    with pytest.raises((ValueError,KeyError,FileNotFoundError)):compare(c,include_positions=True)
    assert all(not s._active for s in c.read_sessions)


def test_entire_selection_access_rechecked_and_archived_history_readable(comparison):
    c=comparison;member=RehearsalSession(c.member)
    c.directory=c.directory.set_grant(c.admin,c.directory.user(c.member).id,c.account,Permission.VIEW,
        expected_revision=c.directory.revision)
    with pytest.raises(PermissionError):c.signed.compare(member,2025,1,c.selections)
    c.directory=c.directory.set_grant(c.admin,c.directory.user(c.member).id,c.second,Permission.VIEW,
        expected_revision=c.directory.revision)
    assert len(c.signed.compare(member,2025,1,c.selections).accounts)==2
    c.directory=c.directory.configure_account(c.admin,c.second,archived=True,expected_revision=c.directory.revision)
    assert len(compare(c).accounts)==2
    c.directory=c.directory.set_grant(c.admin,c.directory.user(c.member).id,c.second,None,expected_revision=c.directory.revision)
    with pytest.raises(PermissionError):c.signed.compare(member,2025,1,c.selections)


def test_injected_write_blocked_for_every_selected_account(comparison,monkeypatch):
    def write(service,session,*a):session.artifacts.write_text('blocked.json','{}')
    monkeypatch.setattr(module,'_results_session',write)
    with pytest.raises(PermissionError):compare(comparison)
    assert all(not s._active for s in comparison.read_sessions)


def test_legacy_account_cannot_be_selected(comparison):
    c=comparison;c.directory=replace(c.directory,accounts=tuple(replace(a,legacy=True) if a.id==c.second else a for a in c.directory.accounts))
    with pytest.raises(ValueError,match='Existing workspace data'):compare(c)
