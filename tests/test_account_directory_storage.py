"""Serialization and connection-boundary regression tests without a database."""
from copy import deepcopy
import os
import pwd
from uuid import uuid4

import pytest

from campaigniq.account_administration import Identity, LegacyOwner, Permission, propose_legacy_directory
from campaigniq.persistence.account_directory_codec import checksum, decode, encode, loads
from campaigniq.persistence.postgres_account_directory import LocalPostgresAccountDirectory, local_conninfo


ADMIN = Identity('https://accounts.google.com', 'admin-subject')
MEMBER = Identity('https://accounts.google.com', 'member-subject')


@pytest.fixture
def initial():
    return propose_legacy_directory((
        LegacyOwner(ADMIN, 'workspace-admin', 'Admin', 'Existing', 'Schwab'),
        LegacyOwner(MEMBER, 'workspace-member', 'Member', 'Existing', 'Schwab'),
    ), ADMIN)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in ('RENDER', 'RENDER_SERVICE_ID', 'PGHOSTADDR', 'PGSERVICE', 'PGSERVICEFILE', 'PGOPTIONS'):
        monkeypatch.delenv(name, raising=False)


def test_directory_round_trip_keeps_permissions_audit_and_legacy_bindings(initial):
    d = initial.set_grant(ADMIN, initial.users[1].id, initial.accounts[0].id, Permission.EDIT, expected_revision=0)
    restored = decode(encode(d))
    assert restored == d
    assert restored.authorize(MEMBER, d.accounts[0].id, write=True).storage_scope == 'workspace-admin'
    assert restored.events[0].details == d.events[0].details


@pytest.mark.parametrize('field', ['users', 'accounts', 'grants', 'events'])
def test_collections_must_be_arrays_on_wire(initial, field):
    value = encode(initial)
    value[field] = {}
    with pytest.raises(ValueError):
        decode(value)


@pytest.mark.parametrize('value', [True, 2, '1', None])
def test_unknown_or_coerced_version_is_rejected(initial, value):
    data = encode(initial)
    data['version'] = value
    with pytest.raises(ValueError):
        decode(data)


@pytest.mark.parametrize('change', ['extra', 'missing', 'bad_admin', 'bad_owner', 'duplicate_user', 'duplicate_scope', 'no_admin'])
def test_corrupted_snapshot_cannot_grant_access(initial, change):
    data = encode(initial)
    if change == 'extra':
        data['all_accounts_allowed'] = True
    elif change == 'missing':
        del data['grants']
    elif change == 'bad_admin':
        data['users'][0]['admin'] = 'true'
    elif change == 'bad_owner':
        data['accounts'][0]['owner_id'] = str(uuid4())
    elif change == 'duplicate_user':
        data['users'].append(deepcopy(data['users'][0]))
    elif change == 'duplicate_scope':
        other = deepcopy(data['accounts'][0]); other['id'] = str(uuid4()); data['accounts'].append(other)
    else:
        data['users'][0]['enabled'] = False
    with pytest.raises(ValueError):
        decode(data)


def test_duplicate_json_fields_and_nonfinite_numbers_rejected():
    with pytest.raises(ValueError, match='Duplicate'):
        loads('{"version":1,"version":1}')
    with pytest.raises(ValueError, match='Non-finite'):
        loads('{"version":NaN}')


def test_invalid_event_sequence_rejected(initial):
    d = initial.configure_account(ADMIN, initial.accounts[0].id, name='Updated', expected_revision=0)
    data = encode(d)
    data['events'][0]['sequence'] = 2
    with pytest.raises(ValueError):
        decode(data)


def test_checksum_is_order_independent_but_detects_access_changes(initial):
    original = encode(initial)
    assert checksum(original) == checksum(dict(reversed(list(original.items()))))
    other = deepcopy(original); other['users'][1]['admin'] = True
    assert checksum(original) != checksum(other)


def test_default_local_settings_are_explicit_and_do_not_connect(monkeypatch):
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    monkeypatch.setattr(psycopg, 'connect', lambda *a, **k: pytest.fail('Constructor attempted a connection'))
    store = LocalPostgresAccountDirectory('dbname=campaigniq_dev', str(uuid4()), development_only=True)
    data = conninfo_to_dict(store._conninfo)
    assert data['host'] == '/var/run/postgresql' and data['port'] == '5432'
    assert data['user'] == pwd.getpwuid(os.getuid()).pw_name


@pytest.mark.parametrize('dsn', [
    'dbname=campaigniq_production_db',
    'dbname=campaigniq_dev host=127.0.0.1',
    'dbname=campaigniq_dev host=localhost',
    'dbname=campaigniq_dev host=dpg-example.oregon-postgres.render.com',
    'dbname=campaigniq_dev host=/tmp',
    'dbname=campaigniq_dev port=5433',
    'dbname=campaigniq_dev user=postgres',
    'dbname=campaigniq_dev service=unsafe',
    'dbname=campaigniq_dev options=-cread_only=off',
    'dbname=campaigniq_dev hostaddr=127.0.0.1',
    'dbname=campaigniq_dev passfile=/tmp/passwords',
    'not a valid connection string',
    '',
])
def test_remote_production_or_implicit_routing_is_blocked(dsn):
    with pytest.raises(ValueError):
        local_conninfo(dsn, development_only=True)


@pytest.mark.parametrize('name', ['RENDER', 'RENDER_SERVICE_ID', 'PGHOSTADDR', 'PGSERVICE', 'PGSERVICEFILE', 'PGOPTIONS'])
def test_cloud_and_environment_routing_overrides_fail_closed(monkeypatch, name):
    monkeypatch.setenv(name, 'unsafe')
    with pytest.raises(ValueError):
        local_conninfo('dbname=campaigniq_dev', development_only=True)


@pytest.mark.parametrize('flag', [False, 1, 'true', None])
def test_development_opt_in_cannot_be_coerced(flag):
    with pytest.raises(ValueError):
        local_conninfo('dbname=campaigniq_dev', development_only=flag)


def test_command_dispatch_rejects_actor_injection_and_arbitrary_methods(monkeypatch):
    store = LocalPostgresAccountDirectory('dbname=campaigniq_dev', str(uuid4()), development_only=True)
    monkeypatch.setattr(store, '_connection', lambda **k: pytest.fail('Invalid command reached SQL'))
    with pytest.raises(ValueError):
        store.execute(ADMIN, 0, '__getattribute__', name='users')
    with pytest.raises(ValueError):
        store.execute(ADMIN, 0, 'configure_user', user_id=str(uuid4()), actor=MEMBER)
    with pytest.raises(ValueError):
        store.execute(ADMIN, 0, 'set_grant', user_id=str(uuid4()), account_id=str(uuid4()), permission='edit')
