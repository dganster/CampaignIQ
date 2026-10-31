"""Private Render TLS is explicit reviewed policy, never a connection fallback."""
from copy import deepcopy
from dataclasses import replace
import json
from unittest.mock import patch
import pytest
from campaigniq.account_backend_config import AccountDatabaseTarget,AccountDirectorySettings
from campaigniq.account_release_config import release_document,parse_release
from campaigniq.account_schema_migrations import digest
from campaigniq.account_deployment_application import DeploymentApplication
from campaigniq.persistence.deployment_account_directory import PostgresAccountDirectory

HOST='dpg-db8duq8m7kps73e1rcd0-a'

def dsn(role='input_role',mode='require',host=HOST):
    return f'host={host} port=5432 dbname=accounts user={role} sslmode={mode}'

def config(repo):
    root=repo/'src/campaigniq';root.mkdir(parents=True);(root/'__init__.py').write_text('# reviewed\n')
    settings=AccountDirectorySettings('11111111-1111-4111-8111-111111111111','https://accounts.google.com',(('subject','workspace-owner'),))
    targets={key:AccountDatabaseTarget(HOST,'accounts',role,transport='render-private-tls') for key,role in (('inputs','input_role'),('publication','publish_role'),('preferences','prefs_role'))}
    auth={'redirect_uri':'https://app.example.invalid/oauth2callback','client_id':'public-client','server_metadata_url':'https://accounts.google.com/.well-known/openid-configuration'}
    return settings,targets,auth

def test_internal_tls_requires_explicit_reviewed_policy():
    default=AccountDatabaseTarget(HOST,'accounts','input_role')
    with pytest.raises(ValueError):default.conninfo(dsn())
    assert 'sslmode=verify-full' in default.conninfo(dsn(mode='verify-full'))
    private=replace(default,transport='render-private-tls')
    with patch('psycopg.connect',side_effect=AssertionError('No automatic connection')):
        assert 'sslmode=require' in private.conninfo(dsn())

@pytest.mark.parametrize('host',['db.example.invalid',HOST+'.oregon-postgres.render.com','localhost','127.0.0.1',HOST+',other','DPG-db8duq8m7kps73e1rcd0-a'])
def test_private_policy_cannot_select_external_or_arbitrary_hosts(host):
    with pytest.raises(ValueError):AccountDatabaseTarget(host,'accounts','input_role',transport='render-private-tls')

@pytest.mark.parametrize('mode',['disable','allow','prefer','verify-ca','verify-full'])
def test_private_policy_accepts_only_its_exact_tls_mode(mode):
    target=AccountDatabaseTarget(HOST,'accounts','input_role',transport='render-private-tls')
    with pytest.raises(ValueError):target.conninfo(dsn(mode=mode))

@pytest.mark.parametrize('change',['host','user','routing','mode'])
def test_private_policy_still_pins_all_connection_details(change):
    target=AccountDatabaseTarget(HOST,'accounts','input_role',transport='render-private-tls');value=dsn()
    if change=='host':value=dsn(host='dpg-other-a')
    if change=='user':value=dsn(role='other_role')
    if change=='routing':value+=' hostaddr=127.0.0.1'
    if change=='mode':value=dsn(mode='disable')
    with pytest.raises(ValueError):target.conninfo(value)

def test_private_release_uses_version_two_and_checksum_binds_transport(tmp_path):
    settings,targets,auth=config(tmp_path)
    value=release_document(settings,targets,auth,repo=tmp_path)
    assert value['version']==2 and value['target']['transport']=='render-private-tls'
    parsed=parse_release(json.dumps(value),reviewed_sha256=digest(value))
    assert parsed.target('inputs').transport=='render-private-tls'
    changed=deepcopy(value);changed['target']['transport']='verified-tls'
    with pytest.raises(ValueError):parse_release(json.dumps(changed),reviewed_sha256=parsed.sha256)
    changed=deepcopy(value);changed['version']=1
    with pytest.raises(ValueError):parse_release(json.dumps(changed),reviewed_sha256=digest(changed))

def test_release_roles_cannot_mix_transport_policies(tmp_path):
    settings,targets,auth=config(tmp_path);targets['preferences']=replace(targets['preferences'],transport='verified-tls')
    with pytest.raises(ValueError):release_document(settings,targets,auth,repo=tmp_path)

def test_application_cannot_mix_transport_policies(tmp_path):
    settings,targets,_=config(tmp_path)
    stores=[PostgresAccountDirectory(dsn(role=t.user),settings,t) for t in targets.values()]
    t=replace(targets['preferences'],transport='verified-tls')
    stores[2]=PostgresAccountDirectory(dsn(role=t.user,mode='verify-full'),settings,t)
    with pytest.raises(ValueError):DeploymentApplication(*stores)
