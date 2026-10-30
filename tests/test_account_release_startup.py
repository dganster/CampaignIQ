from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import os
import pytest
from campaigniq import account_release_config as config,account_deployment_startup as startup
from campaigniq.account_schema_migrations import digest
from campaigniq.account_deployment_application import DeploymentApplication
from tests.test_account_deployment_application import stores


def proposal(repo):
    root=repo/'src/campaigniq';root.mkdir(parents=True)
    (root/'__init__.py').write_text('# synthetic source\n')
    return dict(format='campaigniq.account_release',version=1,schema_version=4,
        registry_id='00000000-0000-0000-0000-000000000001',trusted_issuer='https://accounts.example.test',
        legacy_authorization={'verified-subject':'workspace-owner'},target=dict(host='db.example.invalid',port=5432,database='accounts'),
        roles=dict(inputs='account_input',publication='account_publish',preferences='account_prefs'),source_files=config.source_inventory(repo),
        auth=dict(redirect_uri='https://app.example.invalid/oauth2callback',client_id='reviewed-client',server_metadata_url='https://accounts.example.test/.well-known/openid-configuration'),publication_enabled=False)


def parsed(repo):
    value=proposal(repo);return config.parse_release(json.dumps(value),reviewed_sha256=digest(value)),value


def auth(value):return dict(value['auth'],client_secret='synthetic-secret',cookie_secret='s'*64)


def connections(value):
    return {name:'host=db.example.invalid port=5432 dbname=accounts user='+value['roles'][role]+' sslmode=verify-full password=synthetic-secret' for role,name in config.ROLE_ENV}


def test_reviewed_config_binds_complete_mapping_three_roles_auth_and_exact_code_without_secret_repr(tmp_path):
    settings,value=parsed(tmp_path)
    with patch('psycopg.connect',side_effect=AssertionError('No offline connection')):
        application=startup.compose_release(settings,connections(value),auth(value),repo=tmp_path)
    assert not application.inputs.publication_enabled
    assert application.store.target.user=='account_input'
    assert application.preferences.store.target.user=='account_prefs'
    assert 'verified-subject' not in repr(settings)
    config.verify_source(settings,tmp_path)
    (tmp_path/'src/campaigniq/extra.py').write_text('# extra')
    with pytest.raises(ValueError,match='Running code'):config.verify_source(settings,tmp_path)

@pytest.mark.parametrize('change',['version','schema','publication','mapping','same-role','routing','callback','metadata','client','extra-auth','bad-path','bad-hash'])
def test_invalid_configuration_is_rejected_even_with_matching_review_checksum(tmp_path,change):
    value=proposal(tmp_path)
    if change=='version':value['version']=True
    elif change=='schema':value['schema_version']=3
    elif change=='publication':value['publication_enabled']='true'
    elif change=='mapping':value['legacy_authorization']['another']='workspace-owner'
    elif change=='same-role':value['roles']['preferences']=value['roles']['inputs']
    elif change=='routing':value['target']['host']='localhost'
    elif change=='callback':value['auth']['redirect_uri']='http://app.example.invalid/oauth2callback'
    elif change=='metadata':value['auth']['server_metadata_url']='https://other.invalid/.well-known/openid-configuration'
    elif change=='client':value['auth']['client_id']=''
    elif change=='extra-auth':value['auth']['client_secret']='MUST NOT STORE'
    elif change=='bad-path':value['source_files']={'src/campaigniq/../escape.py':'a'*64}
    elif change=='bad-hash':value['source_files']['src/campaigniq/__init__.py']='not-a-hash'
    with pytest.raises(ValueError):config.parse_release(json.dumps(value),reviewed_sha256=digest(value))


def test_duplicate_keys_review_changes_and_nonfinite_json_are_not_accepted(tmp_path):
    value=proposal(tmp_path)
    with pytest.raises(ValueError,match='checksum'):config.parse_release(json.dumps(value),reviewed_sha256='0'*64)
    with pytest.raises(ValueError,match='Duplicate'):config.parse_release('{"version":1,"version":1}',reviewed_sha256='0'*64)
    with pytest.raises(ValueError):config.strict_json('{"value":NaN}')


def test_private_release_file_rejects_permissions_symlinks_and_unreviewed_changes(tmp_path):
    _,value=parsed(tmp_path);path=tmp_path/'release.json';path.write_text(json.dumps(value));path.chmod(0o600)
    result=startup.read_release(path,reviewed_sha256=digest(value));assert result.sha256==digest(value)
    path.chmod(0o644)
    with pytest.raises(ValueError,match='private'):startup.read_release(path,reviewed_sha256=digest(value))
    path.chmod(0o600);link=tmp_path/'link.json';link.symlink_to(path)
    with pytest.raises(ValueError):startup.read_release(link,reviewed_sha256=digest(value))
    value['publication_enabled']=True;path.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='checksum'):startup.read_release(path,reviewed_sha256=result.sha256)

@pytest.mark.parametrize('change',['client','cookie','metadata','redirect','missing-secret','named-provider','wrong-role','missing-role','code'])
def test_startup_rejects_auth_credentials_or_source_mismatch_before_any_database_connection(tmp_path,change):
    settings,value=parsed(tmp_path);provider=auth(value);dsns=connections(value)
    if change=='client':provider['client_id']='different'
    elif change=='cookie':provider['cookie_secret']='short'
    elif change=='metadata':provider['server_metadata_url']='https://wrong.invalid'
    elif change=='redirect':provider['redirect_uri']='https://wrong.invalid/oauth2callback'
    elif change=='missing-secret':provider.pop('client_secret')
    elif change=='named-provider':provider['google']={'client_id':'other'}
    elif change=='wrong-role':dsns[config.ROLE_ENV[0][1]]=dsns[config.ROLE_ENV[1][1]]
    elif change=='missing-role':dsns.pop(config.ROLE_ENV[2][1])
    elif change=='code':(tmp_path/'src/campaigniq/__init__.py').write_text('# changed')
    with patch('psycopg.connect',side_effect=AssertionError('No invalid-startup connection')),pytest.raises(ValueError):
        startup.compose_release(settings,dsns,provider,repo=tmp_path)


def test_startup_preflight_is_readonly_all_roles_and_directory_changes_fail_closed():
    app=DeploymentApplication(*stores());seen=[];current=[object()]
    class Connection:
        def execute(self,query):
            assert query.startswith('SELECT version')
            return SimpleNamespace(fetchall=lambda:[(1,),(2,),(3,),(4,)])
    @contextmanager
    def connection(*,readonly):seen.append(readonly);yield Connection()
    class Tx:
        def __init__(self,*args):pass
        def _load(self):return current[0]
    services=(app.store,app.inputs._publisher.writer,app.preferences.store)
    from contextlib import ExitStack
    with ExitStack() as stack:
        for service in services:stack.enter_context(patch.object(service,'_connection',new=connection))
        stack.enter_context(patch.object(startup,'DirectoryTransaction',Tx))
        for name in ('_verify_complete','validate_publication_role','validate_preference_role'):stack.enter_context(patch.object(startup,name))
        startup.verify_startup(app)
        assert seen==[True,True,True]
        with patch.object(Tx,'_load',side_effect=[object(),object(),object()]),pytest.raises(ValueError,match='changed'):startup.verify_startup(app)


def test_failed_startup_does_not_render_or_disclose_exception_secrets(tmp_path,monkeypatch):
    ui=SimpleNamespace(error=Mock(),secrets={})
    monkeypatch.setenv('CAMPAIGNIQ_ACCOUNT_RELEASE_FILE','/private/config')
    with patch.object(startup,'read_release',side_effect=ValueError('SECRET-CREDENTIAL')),patch.object(startup,'render_application') as render:
        startup.run(ui,repo=tmp_path)
        assert not render.called
    assert 'SECRET' not in str(ui.error.call_args)


def test_proposal_builder_uses_only_explicit_trusted_inputs_and_defaults_publication_off(tmp_path):
    settings,value=parsed(tmp_path)
    targets={role:settings.target(role) for role,_ in config.ROLE_ENV}
    built=config.release_document(settings.settings,targets,value['auth'],repo=tmp_path)
    assert built==value and not built['publication_enabled']
    assert 'client_secret' not in built['auth']
