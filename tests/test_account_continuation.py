"""Continuation preserves retained bytes and fails closed on conflicting sources."""
from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from campaigniq.account_continuation import (ContinuationArtifacts,ContinuationDrafts,
    attach_continuation,expected_months,future_key,future_month)
from campaigniq.account_deployment_publication import AppendOnlyArtifacts
from campaigniq.persistence.deployment_account_data import new_schwab_account
from tests.test_account_local_publication import MemoryStorage

class Session:
    def __init__(self):self.active=True;self.writable=True;self.calls=[]
    def _check(self,*,write=False):
        if not self.active:raise RuntimeError('expired')
        if write and not self.writable:raise PermissionError('readonly')
    @contextmanager
    def _operation(self):
        self._check();yield SimpleNamespace(execute=lambda q,p:self.calls.append((q,p)))

def overlay(retained=None):
    session=Session();reader=MemoryStorage('workspace-dennis')
    session.artifacts=ContinuationArtifacts(session,retained or {'2026-09-lot-book.json':'retained'},reader)
    session.retained_rows=tuple(session.artifacts._retained.items())
    return session,reader

def test_exact_reviewed_history_ranges():
    assert len(expected_months('workspace-dennis'))==21
    assert len(expected_months('workspace-andrew'))==9
    for scope in ('shared','workspace-other','account-foreign'):
        with pytest.raises(ValueError):expected_months(scope)

@pytest.mark.parametrize('year,month',[(2025,1),(2026,9),(2026,0),(2026,13),(True,10),(2201,1)])
def test_past_and_invalid_months_rejected(year,month):
    with pytest.raises(ValueError):future_month(year,month)

@pytest.mark.parametrize('key',['2026-09-lot-book.json','preferences.json','2026-13-finalized.json','other'])
def test_artifact_keys_cannot_escape_reviewed_frontier(key):
    with pytest.raises(ValueError):future_key(key)

def test_overlay_preserves_sources_and_supports_dynamic_append_without_cached_rows():
    original={'2026-09-lot-book.json':'retained'};session,reader=overlay(original)
    original['2026-09-lot-book.json']='external mutation'
    reader.write_text('2026-10-lot-book.json','new')
    assert session.artifacts.read_text('2026-09-lot-book.json')=='retained'
    assert session.artifacts.read_text('2026-10-lot-book.json')=='new'
    assert session.artifacts.list_keys(suffix='-lot-book.json')==('2026-09-lot-book.json','2026-10-lot-book.json')
    for name in ('write_text','delete','initialize'):
        with pytest.raises(PermissionError):getattr(session.artifacts,name)(*(() if name=='initialize' else ('forbidden',)))
    session.active=False
    for action in (lambda:session.artifacts.read_text('2026-09-lot-book.json'),lambda:session.artifacts.list_keys(),lambda:session.artifacts.namespace):
        with pytest.raises(RuntimeError):action()

def test_conflict_withholds_entire_overlay_without_precedence_or_repair():
    session,reader=overlay();reader.write_text('2026-09-lot-book.json','conflict');before=dict(reader.rows)
    for action in (lambda:session.artifacts.read_text('2026-09-lot-book.json'),lambda:session.artifacts.read_text('absent'),lambda:session.artifacts.list_keys()):
        with pytest.raises(ValueError):action()
    assert reader.rows==before

def test_unknown_or_historical_private_artifacts_withhold_all_results():
    session,reader=overlay();reader.write_text('2025-01-lot-book.json','foreign historical')
    with pytest.raises(ValueError):session.artifacts.list_keys()

def test_append_wrapper_can_only_insert_new_future_keys_never_update_public():
    session,reader=overlay();writer=AppendOnlyArtifacts(session)
    writer.write_text('2026-10-lot-book.json','new')
    assert len(session.calls)==1 and session.calls[0][0].startswith('INSERT INTO campaigniq_accounts.')
    for key in ('2026-09-lot-book.json','preferences.json'):
        with pytest.raises(ValueError):writer.write_text(key,'changed')
    reader.write_text('2026-10-lot-book.json','committed')
    with pytest.raises(PermissionError):writer.write_text('2026-10-lot-book.json','changed')
    assert len(session.calls)==1

def test_drafts_cannot_create_or_resume_historical_ids_and_expire():
    session=Session();calls=[]
    repo=SimpleNamespace(workspace='workspace-dennis',create_or_resume=lambda y,m:calls.append((y,m)) or SimpleNamespace(year=y,month=m),
        get=lambda _:SimpleNamespace(year=2026,month=9),list_drafts=lambda:())
    drafts=ContinuationDrafts(session,repo)
    with pytest.raises(ValueError):drafts.create_or_resume(2026,9)
    with pytest.raises(ValueError):drafts.get('old-id')
    assert not calls
    drafts.create_or_resume(2026,10);assert calls==[(2026,10)]
    session.active=False
    with pytest.raises(RuntimeError):drafts.create_or_resume(2026,11)

def test_legacy_route_requires_explicit_opt_in_and_reviewed_workspace():
    account=SimpleNamespace(id='same-id',legacy=True,storage_scope='workspace-dennis',broker='schwab')
    directory=SimpleNamespace(accounts=(account,))
    with pytest.raises(ValueError):new_schwab_account(directory,'same-id')
    assert new_schwab_account(directory,'same-id',continue_existing=True) is account
    account.storage_scope='shared'
    with pytest.raises(ValueError):new_schwab_account(directory,'same-id',continue_existing=True)

def test_attachment_routes_exact_namespace_rejects_new_nonempty_retained_draft():
    session,reader=overlay();session.artifacts=reader;session.drafts=SimpleNamespace()
    account=SimpleNamespace(id='same-id',legacy=True,storage_scope='workspace-dennis')
    queries=[]
    class Conn:
        def execute(self,q,p):
            queries.append((q,p))
            return SimpleNamespace(fetchall=lambda:[('2026-09-lot-book.json','canonical')] if 'artifact_storage' in q else [(2026,10,1,1)])
    @contextmanager
    def operation():yield Conn()
    session._operation=operation
    with patch('campaigniq.account_continuation.published_months',return_value=expected_months('workspace-dennis')),patch('campaigniq.account_continuation._results') as canonical:
        with pytest.raises(ValueError,match='saved sources'):attach_continuation(session,account)
        assert canonical.call_count==21
    assert all(p==('workspace-dennis',) for q,p in queries)
    assert all(q.startswith('SELECT ') for q,p in queries)
    assert session.artifacts is reader

# These fixtures use synthetic source documents only; no retained user data.
from tests.test_account_local_publication import local,month,bound_context,context,FIXTURES
from campaigniq.import_draft_bridge import RestoredUpload
from campaigniq.account_local_publication import publish_synthetic_month,_publish_in_session
from campaigniq.persistence.lot_book_store import load_lot_book_from_storage

def dated_sources(month):
    from tests.test_account_reconciliation_preview import ROLES
    result={}
    for role,name in ROLES.items():
        if month==10 and role=='schwab_opening_position_snapshot':continue
        text=(FIXTURES/name).read_text()
        replacements=({'1/1/25':'9/1/26','1/31/25':'9/30/26','01/15/25':'09/15/26',
            'January 1-31, 2025':'September 1-30, 2026','December 1-31, 2024':'August 1-31, 2026',
            '01/01/2025':'09/01/2026','01/31/2025':'09/30/2026'} if month==9 else
            {'1/1/25':'10/1/26','1/31/25':'10/31/26','01/15/25':'10/15/26',
            'January 1-31, 2025':'October 1-31, 2026','01/01/2025':'10/01/2026','01/31/2025':'10/31/2026',
            '110.0000':'120.0000','11,000.00':'12,000.00'})
        for old,new in replacements.items():text=text.replace(old,new)
        result[role]=RestoredUpload(name,'text/csv' if name.endswith('.csv') else 'text/plain',text.encode())
    return result

def synthetic_september(c):
    draft=c.bound.create(c.admin,c.account,2026,9);revision=0
    for role,upload in dated_sources(9).items():revision=c.bound.save(c.admin,c.account,draft.id,revision,role,upload)
    publish_synthetic_month(c.bound,c.admin,c.account,draft.id,revision,authorize_synthetic_publication=True,
        forex_applicable=False,crypto_applicable=False)
    return dict(c.artifacts[c.account].rows)

def test_real_engine_continues_same_account_from_september_without_rewriting_retained_bytes(local):
    c=local;retained=synthetic_september(c);private=MemoryStorage(c.account)
    draft=c.bound.create(c.admin,c.account,2026,10);revision=0
    for role,upload in dated_sources(10).items():revision=c.bound.save(c.admin,c.account,draft.id,revision,role,upload)
    docs=dict(c.repositories[c.account].docs)
    session=Session();session.drafts=ContinuationDrafts(session,c.repositories[c.account])
    session.artifacts=ContinuationArtifacts(session,retained,private);session.retained_rows=tuple(retained.items())
    class Connection:
        def execute(self,query,params):
            if query.startswith('INSERT INTO campaigniq_accounts.artifact_storage_dev'):
                namespace,key,content=params
                assert namespace==c.account and key not in private.rows
                private.write_text(key,content)
            return SimpleNamespace(fetchone=lambda:None)
    @contextmanager
    def operation():yield Connection()
    session._operation=operation;session._connection=Connection()
    session.account=c.directory.authorize(c.admin,c.account)
    session.artifacts=AppendOnlyArtifacts(session)
    result=_publish_in_session(c.bound,session,c.directory.user(c.admin).id,c.account,draft.id,revision,
        forex_applicable=False,crypto_applicable=False,receipt_format='campaigniq.deployment_account_publication')
    assert result.account_id==c.account
    state=load_lot_book_from_storage(session.artifacts,'2026-10-lot-book.json')
    assert sum(lot.quantity for lots in state.lot_book._lots.values() for lot in lots)==120
    assert all(session.artifacts.read_text(key)==value for key,value in retained.items())
    assert c.artifacts[c.account].rows==retained and c.repositories[c.account].docs==docs
    assert private.rows and all(key.startswith('2026-10-') for key in private.rows)
    before=dict(private.rows)
    with pytest.raises(ValueError):_publish_in_session(c.bound,session,c.directory.user(c.admin).id,c.account,draft.id,revision,
        forex_applicable=False,crypto_applicable=False,receipt_format='campaigniq.deployment_account_publication')
    assert private.rows==before

def test_private_protocol_cannot_move_retained_visibility_boundary():
    from campaigniq.account_continuation import validate_protocol
    import json
    canonical=dict(format='campaigniq.monthly_publication',version=1,first_protected_period_end='2026-10-31')
    validate_protocol(json.dumps(canonical))
    canonical['version']=True
    with pytest.raises(ValueError):validate_protocol(json.dumps(canonical))
    canonical['version']=1
    canonical['first_protected_period_end']='2025-01-31'
    with pytest.raises(ValueError):validate_protocol(json.dumps(canonical))
