from copy import deepcopy
import json
from unittest.mock import patch
import pytest
from campaigniq.account_release_config import parse_release
from campaigniq.account_schema_migrations import digest
from campaigniq.account_deployment_startup import compose_release
from tests.test_account_release_startup import proposal,connections,auth


def release(tmp_path):
    value=proposal(tmp_path);value.update(version=4,continue_existing=False,multi_asset_enabled=True)
    value['target']['transport']='verified-tls'
    return value


def test_reviewed_v4_routes_capability_to_application_without_connection_or_publication(tmp_path):
    value=release(tmp_path);config=parse_release(json.dumps(value),reviewed_sha256=digest(value))
    with patch('psycopg.connect',side_effect=AssertionError('No connection')):
        app=compose_release(config,connections(value),auth(value),repo=tmp_path)
    assert app.inputs.multi_asset_enabled is True
    assert app.inputs.publication_enabled is False
    assert app.inputs._publisher.multi_asset_enabled is True


@pytest.mark.parametrize('version',[1,2,3])
def test_prior_release_versions_default_to_disabled(tmp_path,version):
    value=proposal(tmp_path);value['version']=version
    if version>=2:value['target']['transport']='verified-tls'
    if version>=3:value['continue_existing']=False
    assert parse_release(json.dumps(value),reviewed_sha256=digest(value)).multi_asset_enabled is False


@pytest.mark.parametrize('flag',[1,'true',None])
def test_nonboolean_capability_rejected(tmp_path,flag):
    value=release(tmp_path);value['multi_asset_enabled']=flag
    with pytest.raises(ValueError):parse_release(json.dumps(value),reviewed_sha256=digest(value))


def test_enabling_capability_requires_new_review_checksum(tmp_path):
    value=release(tmp_path);value['multi_asset_enabled']=False;old=digest(value)
    value['multi_asset_enabled']=True
    with pytest.raises(ValueError,match='checksum'):parse_release(json.dumps(value),reviewed_sha256=old)
