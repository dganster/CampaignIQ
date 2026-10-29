from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
import pytest
from campaigniq.account_preferences import LocalAccountPreferences,document
from campaigniq.account_administration import Permission
from campaigniq.account_legacy_access_rehearsal import RehearsalSession
from campaigniq.persistence.account_directory_codec import checksum
from campaigniq.ui.preferences import DEFAULT_PREFERENCES
from tests.test_account_legacy_access import harness,Result,A,B,MAPPING,ISSUER


@pytest.fixture
def preferences(harness):
    h=harness;h.preferences={};h.ready=True;h.fail=False
    original=h.conn.execute
    def execute(sql,params=()):
        if sql.startswith('SELECT to_regclass'):return Result([('campaigniq_preferences_dev.account_preferences' if h.ready else None,)])
        if sql.startswith('SELECT registry_id') or sql.startswith('SELECT pg_advisory'):return Result([])
        if sql.startswith('SELECT revision,document,document_sha256'):
            value=h.preferences.get(tuple(params));return Result([value] if value else [])
        if sql.startswith('INSERT INTO campaigniq_preferences_dev.account_preferences'):
            registry,user,account,rev,payload,digest=params
            h.preferences[(registry,user,account)]=(rev,payload.obj,digest)
            if h.fail:raise ValueError('Injected after-write failure')
            return SimpleNamespace(rowcount=1)
        if sql.startswith('CREATE SCHEMA') or sql.startswith('CREATE TABLE'):h.ready=True;return Result([])
        return original(sql,params)
    h.conn.execute=execute
    @contextmanager
    def connection(*,readonly=False):
        h.modes.append(readonly);before=deepcopy(h.preferences)
        try:yield h.conn
        except BaseException:h.preferences=before;raise
    h.store._connection=connection
    h.service=LocalAccountPreferences(h.store,trusted_issuer=ISSUER,authorization=MAPPING)
    return h


def test_separate_personal_storage_persists_across_service_instances(preferences):
    h=preferences;values={**DEFAULT_PREFERENCES,'fit_columns':True,'timezone':'America/Denver'}
    assert h.service.load(h.user,h.account).revision==0
    assert h.service.save(h.user,h.account,values,expected_revision=0)==1
    independent=LocalAccountPreferences(h.store,trusted_issuer=ISSUER,authorization=MAPPING)
    saved=independent.load(h.user,h.account)
    assert saved.revision==1 and dict(saved.values)==values
    assert any(h.modes) and h.modes[-1] is True
    assert not any('public.' in sql for sql,_ in h.conn.queries)


def test_current_identity_account_access_and_no_admin_financial_shortcut(preferences):
    h=preferences
    for user,account in ((h.user,h.other),(A,h.account),(RehearsalSession(B),h.account)):
        with pytest.raises(PermissionError):h.service.save(user,account,dict(DEFAULT_PREFERENCES),expected_revision=0)
    assert not h.preferences


def test_view_grantee_has_own_values_and_revocation_blocks_later_save(preferences):
    h=preferences;other_user=h.current[0].user(B).id
    h.current[0]=h.current[0].set_grant(A,other_user,h.account,Permission.VIEW,expected_revision=h.current[0].revision)
    h.service.save(h.user,h.account,{**DEFAULT_PREFERENCES,'fit_columns':True},expected_revision=0)
    user=RehearsalSession(B)
    assert not h.service.load(user,h.account).values
    h.service.save(user,h.account,dict(DEFAULT_PREFERENCES),expected_revision=0)
    assert dict(h.service.load(h.user,h.account).values)['fit_columns'] is True
    h.current[0]=h.current[0].set_grant(A,other_user,h.account,None,expected_revision=h.current[0].revision)
    before=deepcopy(h.preferences)
    with pytest.raises(PermissionError):h.service.save(user,h.account,dict(DEFAULT_PREFERENCES),expected_revision=1)
    assert h.preferences==before


def test_archived_display_history_allowed_disabled_user_blocked(preferences):
    h=preferences
    h.current[0]=h.current[0].configure_account(A,h.account,archived=True,expected_revision=h.current[0].revision)
    h.service.save(h.user,h.account,dict(DEFAULT_PREFERENCES),expected_revision=0)
    h.current[0]=h.current[0].configure_user(A,h.current[0].user(B).id,enabled=False,expected_revision=h.current[0].revision)
    with pytest.raises(PermissionError):h.service.load(RehearsalSession(B),h.other)


def test_stale_revision_and_injected_failure_preserve_all_rows(preferences):
    h=preferences;h.service.save(h.user,h.account,dict(DEFAULT_PREFERENCES),expected_revision=0)
    before=deepcopy(h.preferences)
    with pytest.raises(ValueError,match='reload'):h.service.save(h.user,h.account,dict(DEFAULT_PREFERENCES),expected_revision=0)
    h.fail=True
    with pytest.raises(ValueError,match='Injected'):h.service.save(h.user,h.account,dict(DEFAULT_PREFERENCES),expected_revision=1)
    assert h.preferences==before


@pytest.mark.parametrize('revision',[True,-1,'0',None])
def test_bad_revision_rejected_without_connection(preferences,revision):
    h=preferences;h.modes.clear()
    with pytest.raises(ValueError):h.service.save(h.user,h.account,dict(DEFAULT_PREFERENCES),expected_revision=revision)
    assert not h.modes


@pytest.mark.parametrize('values',[{}, {'fit_columns':'yes','timezone':'UTC','reporting_period':'YTD'},
    {**DEFAULT_PREFERENCES,'timezone':'Not/AZone'}, {**DEFAULT_PREFERENCES,'reporting_period':'invented'},
    {**DEFAULT_PREFERENCES,'account_id':'foreign'}])
def test_unsupported_fields_or_values_blocked_before_connection(preferences,values):
    h=preferences;h.modes.clear()
    with pytest.raises(ValueError):h.service.save(h.user,h.account,values,expected_revision=0)
    assert not h.modes


def test_corruption_cannot_be_overwritten(preferences):
    h=preferences;h.service.save(h.user,h.account,dict(DEFAULT_PREFERENCES),expected_revision=0)
    key=next(iter(h.preferences));rev,data,digest=h.preferences[key]
    h.preferences[key]=(rev,{**data,'fit_columns':True},digest)
    before=deepcopy(h.preferences)
    with pytest.raises(ValueError,match='integrity'):h.service.load(h.user,h.account)
    with pytest.raises(ValueError,match='integrity'):h.service.save(h.user,h.account,dict(DEFAULT_PREFERENCES),expected_revision=1)
    assert h.preferences==before


def test_initialization_explicit_admin_and_missing_schema_does_not_write(preferences):
    h=preferences;h.ready=False
    assert h.service.load(h.user,h.account).storage_ready is False
    with pytest.raises(ValueError):h.service.save(h.user,h.account,dict(DEFAULT_PREFERENCES),expected_revision=0)
    with pytest.raises(PermissionError):h.service.initialize(RehearsalSession(B))
    h.service.initialize(h.user)
    assert h.service.load(h.user,h.account).storage_ready is True and not h.preferences


def test_constructor_rejects_other_backend():
    with pytest.raises(ValueError):LocalAccountPreferences(object(),trusted_issuer=ISSUER,authorization=MAPPING)
