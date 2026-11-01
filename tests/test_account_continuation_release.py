"""Checksum-bound continuation activation; no database or deployment side effects."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from campaigniq import account_release_config as config,account_deployment_startup as startup
from campaigniq.account_schema_migrations import digest
from tests.test_account_release_startup import proposal,auth,connections


def reviewed(repo,*,continuation=True,publication=False):
    value=proposal(repo);value.update(version=3,continue_existing=continuation,publication_enabled=publication)
    value['legacy_authorization']={'dennis-subject':'workspace-dennis','andrew-subject':'workspace-andrew'}
    value['target']['transport']='verified-tls'
    return value

@pytest.mark.parametrize('continuation,publication',[(True,False),(True,True),(False,False)])
def test_explicit_reviewed_flags_reach_application_without_connection(tmp_path,continuation,publication):
    value=reviewed(tmp_path,continuation=continuation,publication=publication)
    parsed=config.parse_release(json.dumps(value),reviewed_sha256=digest(value))
    with patch('psycopg.connect',side_effect=AssertionError('Offline composition opened a connection')):
        application=startup.compose_release(parsed,connections(value),auth(value),repo=tmp_path)
    assert application.continue_existing is continuation
    assert application.inputs._inputs.gateway._continue_existing is continuation
    assert application.publication_enabled is publication
    assert application.inputs.publication_enabled is publication
    assert not any(secret in repr(parsed) for secret in ('dennis-subject','andrew-subject'))

@pytest.mark.parametrize('flag',[None,1,0,'true',[],{}])
def test_nonboolean_continuation_flag_rejected_even_with_new_checksum(tmp_path,flag):
    value=reviewed(tmp_path);value['continue_existing']=flag
    with pytest.raises(ValueError):config.parse_release(json.dumps(value),reviewed_sha256=digest(value))

@pytest.mark.parametrize('change',['missing','old-version','unknown-workspace','third-owner','missing-transport','unknown-field'])
def test_ambiguous_or_unreviewed_scope_rejected(tmp_path,change):
    value=reviewed(tmp_path)
    if change=='missing':value.pop('continue_existing')
    elif change=='old-version':value['version']=2
    elif change=='unknown-workspace':value['legacy_authorization']['dennis-subject']='workspace-other'
    elif change=='third-owner':value['legacy_authorization']['third']='workspace-third'
    elif change=='missing-transport':value['target'].pop('transport')
    elif change=='unknown-field':value['auto_publish']=True
    with pytest.raises(ValueError):config.parse_release(json.dumps(value),reviewed_sha256=digest(value))

@pytest.mark.parametrize('flag',['continue_existing','publication_enabled'])
def test_changing_reviewed_availability_invalidates_checksum(tmp_path,flag):
    value=reviewed(tmp_path);checksum=digest(value);value[flag]=not value[flag]
    with pytest.raises(ValueError,match='checksum'):config.parse_release(json.dumps(value),reviewed_sha256=checksum)

@pytest.mark.parametrize('version',[1,2])
def test_old_releases_stay_disabled_for_retained_continuation(tmp_path,version):
    value=proposal(tmp_path);value['version']=version
    if version==2:value['target']['transport']='render-private-tls';value['target']['host']='dpg-db8duq8m7kps73e1rcd0-a'
    parsed=config.parse_release(json.dumps(value),reviewed_sha256=digest(value))
    assert parsed.continue_existing is False


def test_release_builder_includes_explicit_transport_and_reviewed_flag(tmp_path):
    value=reviewed(tmp_path);parsed=config.parse_release(json.dumps(value),reviewed_sha256=digest(value))
    targets={role:parsed.target(role) for role,_ in config.ROLE_ENV}
    rebuilt=config.release_document(parsed.settings,targets,value['auth'],repo=tmp_path,continue_existing=True,publication_enabled=True)
    assert rebuilt['version']==3 and rebuilt['continue_existing'] is True and rebuilt['publication_enabled'] is True
    assert rebuilt['target']['transport']=='verified-tls'
    with pytest.raises(ValueError):config.release_document(parsed.settings,targets,value['auth'],repo=tmp_path,continue_existing=1)

@pytest.mark.parametrize('continuation,publication,allowed',[(False,False,True),(False,True,False),(True,False,True),(True,True,True)])
def test_exact_launcher_check_only_preserves_availability_guards(tmp_path,monkeypatch,continuation,publication,allowed):
    # In the developer package and user checkout the launcher is under scripts/.
    source=Path(__file__).resolve().parents[1]/'scripts/start_accounts.py'
    root=tmp_path/'candidate';(root/'scripts').mkdir(parents=True);(root/'.streamlit').mkdir()
    script=root/'scripts/start_accounts.py';script.write_bytes(source.read_bytes())
    (root/'.streamlit/secrets.toml').write_text('[auth]\nclient_id="synthetic"\n')
    runtime=tmp_path/'runtime.private.json'
    runtime.write_text(json.dumps(dict(format='campaigniq.private_account_runtime',version=1,release_sha256='a'*64,
        connections={name:'synthetic' for _,name in config.ROLE_ENV},credential_packet_sha256='b'*64)));runtime.chmod(0o600)
    spec=importlib.util.spec_from_file_location('test_private_continuation_launcher',script);launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)
    monkeypatch.setenv('CAMPAIGNIQ_ACCOUNT_RUNTIME_FILE',str(runtime));monkeypatch.setattr('sys.argv',[str(script),'--check-only'])
    configured=SimpleNamespace(publication_enabled=publication,continue_existing=continuation)
    with patch.object(startup,'read_release',return_value=configured),patch.object(startup,'compose_release',return_value=object()) as compose,patch.object(startup,'verify_startup') as verify,patch.object(launcher.os,'execv',side_effect=AssertionError('Check-only launched application')):
        if allowed:
            launcher.main();assert compose.call_count==verify.call_count==1
        else:
            with pytest.raises(ValueError):launcher.main()
            assert compose.call_count==verify.call_count==0
