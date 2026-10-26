"""Account-scoped preflight, stale edits and UI routing regression tests."""
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from campaigniq.account_import_preview import AccountImportPreviewService, UPLOAD_ROLES
from campaigniq.account_administration import Permission
from campaigniq.account_data_rehearsal import _seed
from campaigniq.import_draft_bridge import RestoredUpload
from campaigniq.persistence.account_data_session import LocalAccountDataGateway
from campaigniq.persistence.import_drafts import Draft, DraftConflict


class Repository:
    def __init__(self, scope):
        self.workspace = scope
        self.rows = {}
        self.docs = {}
        self.originals = []

    def create_or_resume(self, year, month):
        for d in self.rows.values():
            if (d.year, d.month) == (year, month): return d
        d = Draft(uuid4(), self.workspace, year, month, 0)
        self.rows[d.id] = d
        return d

    def get(self, key):
        if key not in self.rows: raise FileNotFoundError('draft not found')
        return self.rows[key]

    def list_drafts(self): return tuple(self.rows.values())

    def document(self, key, role):
        self.get(key)
        if (key, role) not in self.docs: raise FileNotFoundError('document not found')
        return self.docs[key, role]

    def upload(self, key, role, name, media, data, *, replace=False):
        d = self.get(key)
        old = self.docs.get((key, role))
        if old and not replace: raise DraftConflict('confirm replacement')
        if old: self.originals.append(old)
        self.docs[key, role] = name, media, data
        self.rows[key] = __import__('dataclasses').replace(d, revision=d.revision + 1)
        return d.revision + 1


class Artifacts:
    def __init__(self): self.reads = 0
    def exists(self, key): self.reads += 1; return False
    def list_keys(self, **kwargs): self.reads += 1; return ()
    def write_text(self, *a): raise AssertionError('Preflight attempted a write')
    def delete(self, *a): raise AssertionError('Preflight attempted a delete')


@pytest.fixture
def context(monkeypatch):
    registry = str(uuid4())
    directory, admin, member = _seed(registry)
    owner_id = directory.user(admin).id
    account = next(a.id for a in directory.accounts if a.owner_id == owner_id)
    other = next(a.id for a in directory.accounts if a.id != account)
    state = SimpleNamespace(directory=directory, admin=admin, member=member,
        account=account, other=other, registry=registry, repositories={}, artifacts={}, locks=[], calls=[])
    @contextmanager
    def transaction(gateway, identity, account_id, *, write=False):
        authorization = state.directory.authorize(identity, account_id, write=write)
        state.calls.append((identity, account_id, write))
        repo = state.repositories.setdefault(account_id, Repository(account_id))
        artifacts = state.artifacts.setdefault(account_id, Artifacts())
        class Connection:
            def execute(self, query, params):
                state.locks.append((query, params))
                return self
            def fetchone(self): return None
        @contextmanager
        def operation(): yield Connection()
        yield SimpleNamespace(drafts=repo, artifacts=artifacts, account=authorization, _operation=operation, _connection=Connection())
    monkeypatch.setattr(LocalAccountDataGateway, 'transaction', transaction)
    gateway = object.__new__(LocalAccountDataGateway)
    state.service = AccountImportPreviewService(gateway)
    return state


def test_reject_forged_gateway():
    with pytest.raises(ValueError): AccountImportPreviewService(object())


def test_same_month_is_separate_by_selected_account(context):
    c = context
    a = c.service.create(c.admin, c.account, 2025, 1)
    b = c.service.create(c.member, c.other, 2025, 1)
    assert a.id != b.id
    assert c.service.drafts(c.admin, c.account) == (a,)
    with pytest.raises(FileNotFoundError): c.service.preview(c.admin, c.account, b.id, 0)


def test_real_preflight_empty_draft_is_not_ready_and_never_publishes(context):
    c = context
    d = c.service.create(c.admin, c.account, 2025, 1)
    result = c.service.preview(c.admin, c.account, d.id, 0)
    assert result.account_id == c.account and result.draft_id == str(d.id)
    assert not result.inputs_ready and not result.publication_authorized and not result.inventory_reconciled
    assert any(v.role == 'opening_state' and not v.valid for v in result.validations)
    assert c.repositories[c.account].get(d.id).revision == 0
    assert 'FOR SHARE' in c.locks[-1][0]
    assert c.locks[-1][1] == (d.id, c.account)


def test_uploaded_documents_feed_existing_validator_and_temporary_files_removed(context, monkeypatch):
    c = context
    d = c.service.create(c.admin, c.account, 2025, 1)
    role = 'thinkorswim_trade_history'
    c.service.save(c.admin, c.account, d.id, 0, role, RestoredUpload('history.csv', 'text/csv', b'bad synthetic history'))
    import campaigniq.account_import_preview as module
    actual = module.prepare_monthly_import
    paths = []
    def observed(*args, **kw):
        path = kw['supplied_inputs'][__import__('campaigniq.import_contract', fromlist=['MonthlyInputRole']).MonthlyInputRole.THINKORSWIM_TRADE_HISTORY]
        assert path.read_bytes() == b'bad synthetic history'
        paths.append(path)
        assert kw['artifact_storage'] is c.artifacts[c.account]
        return actual(*args, **kw)
    monkeypatch.setattr(module, 'prepare_monthly_import', observed)
    result = c.service.preview(c.admin, c.account, d.id, 1)
    assert not result.inputs_ready
    assert paths and not paths[0].exists()
    assert 'FOR UPDATE' in c.locks[-2][0]


@pytest.mark.parametrize('operation', ['save', 'preview'])
def test_stale_revision_rejected_before_extracting_or_overwriting(context, operation):
    c = context
    d = c.service.create(c.admin, c.account, 2025, 1)
    c.service.save(c.admin, c.account, d.id, 0, UPLOAD_ROLES[0], RestoredUpload('test.txt', 'text/plain', b'one'))
    with pytest.raises(ValueError, match='Draft changed'):
        if operation == 'preview': c.service.preview(c.admin, c.account, d.id, 0)
        else: c.service.save(c.admin, c.account, d.id, 0, UPLOAD_ROLES[0], RestoredUpload('test.txt', 'text/plain', b'two'), replace=True)
    assert c.repositories[c.account].get(d.id).revision == 1


def test_view_grant_can_preview_not_create_or_upload_and_revocation_blocks_next_read(context):
    c = context
    d = c.service.create(c.admin, c.account, 2025, 1)
    c.directory = c.directory.set_grant(c.admin, expected_revision=c.directory.revision,
        user_id=c.directory.user(c.member).id, account_id=c.account, permission=Permission.VIEW)
    assert not c.service.preview(c.member, c.account, d.id, 0).inputs_ready
    with pytest.raises(PermissionError): c.service.create(c.member, c.account, 2025, 2)
    with pytest.raises(PermissionError):
        c.service.save(c.member, c.account, d.id, 0, UPLOAD_ROLES[0], RestoredUpload('x.txt','text/plain',b'x'))
    c.directory = c.directory.set_grant(c.admin, expected_revision=c.directory.revision,
        user_id=c.directory.user(c.member).id, account_id=c.account, permission=None)
    with pytest.raises(PermissionError): c.service.preview(c.member, c.account, d.id, 0)


@pytest.mark.parametrize('state', ['disabled', 'archived'])
def test_current_policy_rechecked_for_existing_draft(context, state):
    c = context
    d = c.service.create(c.admin, c.account, 2025, 1)
    if state == 'disabled':
        # Disable the member, preserving another enabled administrator.
        d = c.service.create(c.member, c.other, 2025, 1)
        c.directory = c.directory.configure_user(c.admin, expected_revision=c.directory.revision,
            user_id=c.directory.user(c.member).id, enabled=False)
        with pytest.raises(PermissionError): c.service.preview(c.member, c.other, d.id, 0)
    else:
        c.directory = c.directory.configure_account(c.admin, expected_revision=c.directory.revision, account_id=c.account, archived=True)
        assert not c.service.preview(c.admin, c.account, d.id, 0).inputs_ready
        with pytest.raises(PermissionError): c.service.create(c.admin, c.account, 2025, 2)


def test_replacement_preserves_original_and_requires_explicit_choice(context):
    c = context
    d = c.service.create(c.admin, c.account, 2025, 1)
    first = RestoredUpload('one.txt','text/plain',b'original')
    second = RestoredUpload('two.txt','text/plain',b'replacement')
    c.service.save(c.admin,c.account,d.id,0,UPLOAD_ROLES[0],first)
    with pytest.raises(DraftConflict): c.service.save(c.admin,c.account,d.id,1,UPLOAD_ROLES[0],second)
    c.service.save(c.admin,c.account,d.id,1,UPLOAD_ROLES[0],second,replace=True)
    assert c.repositories[c.account].originals == [('one.txt','text/plain',b'original')]


@pytest.mark.parametrize('revision', [True, '0', None])
def test_revision_type_is_explicit(context, revision):
    with pytest.raises(ValueError): context.service.preview(context.admin, context.account, uuid4(), revision)


@pytest.mark.parametrize('role', ['../../escape', 'opening_state', 'unknown'])
def test_non_upload_roles_rejected(context, role):
    with pytest.raises(ValueError):
        context.service.save(context.admin,context.account,uuid4(),0,role,RestoredUpload('x.txt','text/plain',b'x'))


@pytest.mark.parametrize('year,month', [(True,1),(2025,False),('2025',1)])
def test_create_rejects_noninteger_period(context,year,month):
    with pytest.raises(ValueError): context.service.create(context.admin,context.account,year,month)


def _app(context, monkeypatch):
    import sys
    from types import ModuleType
    from streamlit.testing.v1 import AppTest
    module = ModuleType('campaigniq_import_preview_test_context')
    from campaigniq.persistence.postgres_account_directory import LocalPostgresAccountDirectory
    module.store = object.__new__(LocalPostgresAccountDirectory)
    module.store._snapshot = lambda: context.directory
    module.store._load = lambda conn: context.directory
    module.store.authorize = lambda identity, account_id: context.directory.authorize(identity, account_id)
    module.config = {'registry_id': context.registry}
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return AppTest.from_string('''
from campaigniq.account_import_preview_ui import render
from campaigniq_import_preview_test_context import store, config
import streamlit as st
render(st, store, config)
''').run()


def test_browser_create_and_preview_missing_inputs(context, monkeypatch):
    app = _app(context, monkeypatch)
    assert not app.exception
    next(b for b in app.button if b.label == 'Create or resume draft').click().run()
    assert not app.exception
    next(b for b in app.button if b.label == 'Preview saved inputs').click().run()
    assert not app.exception and len(app.dataframe) == 1
    assert any('missing or invalid' in item.value for item in app.info)
    assert not any('Publish' in b.label for b in app.button)


def test_browser_switch_user_cannot_reuse_foreign_draft(context, monkeypatch):
    c = context
    c.service.create(c.admin,c.account,2025,1)
    app = _app(c, monkeypatch)
    next(s for s in app.selectbox if s.label == 'Demo user').select(c.directory.user(c.member).id).run()
    assert not app.exception
    accounts = next(s for s in app.selectbox if s.label == 'Brokerage account')
    assert accounts.value == c.other and len(accounts.options) == 1
    assert not any(b.label == 'Preview saved inputs' for b in app.button)


def test_browser_view_grant_hides_mutation_controls(context,monkeypatch):
    c = context
    c.service.create(c.admin,c.account,2025,1)
    c.directory = c.directory.set_grant(c.admin, user_id=c.directory.user(c.member).id,
        account_id=c.account,permission=Permission.VIEW,expected_revision=c.directory.revision)
    app = _app(c,monkeypatch)
    next(s for s in app.selectbox if s.label == 'Demo user').select(c.directory.user(c.member).id).run()
    next(s for s in app.selectbox if s.label == 'Brokerage account').select(c.account).run()
    assert not app.exception
    assert any(b.label == 'Preview saved inputs' for b in app.button)
    assert not any(b.label in {'Save document','Create or resume draft'} for b in app.button)
    next(b for b in app.button if b.label == 'Preview saved inputs').click().run()
    assert len(app.dataframe) == 1
    c.directory = c.directory.set_grant(c.admin,user_id=c.directory.user(c.member).id,
        account_id=c.account,permission=None,expected_revision=c.directory.revision)
    app.run()
    assert not app.exception and not app.dataframe


def test_browser_disabled_user_has_no_account_selector(context,monkeypatch):
    c = context
    c.directory = c.directory.configure_user(c.admin,user_id=c.directory.user(c.member).id,
        enabled=False,expected_revision=c.directory.revision)
    app = _app(c,monkeypatch)
    next(s for s in app.selectbox if s.label == 'Demo user').select(next(u.id for u in c.directory.users if u.identity == c.member)).run()
    assert not app.exception
    assert not any(s.label == 'Brokerage account' for s in app.selectbox)
    assert any('disabled' in e.value for e in app.error)


def test_extraction_failure_removes_temporary_files_and_keeps_saved_bytes(context,monkeypatch):
    c = context
    d = c.service.create(c.admin,c.account,2025,1)
    role = 'schwab_brokerage_statement'
    c.service.save(c.admin,c.account,d.id,0,role,RestoredUpload('broken.pdf','application/pdf',b'not pdf'))
    import campaigniq.monthly_upload_staging as staging
    paths=[]
    def fail(source,destination):
        paths.append(source)
        assert source.read_bytes() == b'not pdf'
        raise ValueError('invalid pdf')
    monkeypatch.setattr(staging,'extract_pdf_text',fail)
    with pytest.raises(ValueError): c.service.preview(c.admin,c.account,d.id,1)
    assert not paths[0].exists()
    assert c.repositories[c.account].document(d.id,role)[2] == b'not pdf'


@pytest.mark.parametrize('flag', ['forex_applicable','crypto_applicable'])
def test_market_choices_require_explicit_booleans(context,flag):
    with pytest.raises(ValueError):
        context.service.preview(context.admin,context.account,uuid4(),0,**{flag:'false'})


@pytest.mark.parametrize('render_variable',['RENDER','RENDER_SERVICE_ID'])
def test_launcher_blocks_render_before_database_or_config_creation(monkeypatch,render_variable):
    import sys
    import campaigniq.account_import_preview_ui as ui
    monkeypatch.setenv(render_variable,'true')
    monkeypatch.setattr(sys,'argv',['preview','--development-only'])
    def forbidden(): raise AssertionError('Config creation attempted on Render')
    monkeypatch.setattr(ui,'create_preview',forbidden)
    with pytest.raises(ValueError): ui.launch()


def test_preview_does_not_import_dashboard_or_build_default_runtime(context, monkeypatch):
    import sys
    import campaigniq.runtime as runtime
    def forbidden(*args, **kwargs):
        raise AssertionError('The regular dashboard runtime must never start')
    monkeypatch.setattr(runtime, 'build_local_runtime', forbidden)
    # Catch a dashboard import even when an earlier test already cached it.
    monkeypatch.setitem(sys.modules, 'campaigniq.ui.dashboard', None)
    c=context
    draft=c.service.create(c.admin,c.account,2025,1)
    assert not c.service.preview(c.admin,c.account,draft.id,0).inputs_ready


def test_pure_staging_keeps_existing_extraction_logic_without_dashboard_import():
    import ast
    import inspect
    import campaigniq.monthly_upload_staging as staging
    source=Path(__file__).resolve().parents[1]/'src/campaigniq/ui/dashboard.py'
    tree=ast.parse(source.read_text())
    original=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_save_uploaded_monthly_inputs')
    standalone=ast.parse(inspect.getsource(staging.stage_monthly_uploads)).body[0]
    standalone.name=original.name
    assert ast.dump(original,include_attributes=False)==ast.dump(standalone,include_attributes=False)


def test_browser_preview_with_dashboard_unavailable(context, monkeypatch):
    import sys
    import campaigniq.runtime as runtime
    def forbidden(*args, **kwargs):
        raise AssertionError('Unexpected runtime initialization')
    monkeypatch.setattr(runtime,'build_local_runtime',forbidden)
    monkeypatch.setitem(sys.modules,'campaigniq.ui.dashboard',None)
    c=context
    c.service.create(c.admin,c.account,2025,1)
    app=_app(c,monkeypatch)
    next(b for b in app.button if b.label=='Preview saved inputs').click().run()
    assert not app.exception and len(app.dataframe)==1
    assert not app.error
    assert not any(s.label=='Navigate' for s in app.radio)
    assert not any(title.value=='CampaignIQ' for title in app.title)
