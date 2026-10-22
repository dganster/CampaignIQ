import pytest
from campaigniq.local_import_workspace import resolve_local_import_workspace

BASE = dict(access_workspace=None, backend="postgres", dsn="dbname=campaigniq_dev user=dganster", requested="workspace-dennis", on_render=False)

def test_local_workspace_explicit_opt_in():
    assert resolve_local_import_workspace(**BASE) == "workspace-dennis"

def test_default_retains_existing_behavior():
    assert resolve_local_import_workspace(**{**BASE, "requested": ""}) is None

def test_authenticated_or_hosted_override_rejected():
    for changes in ({"access_workspace": "workspace-alice"}, {"on_render": True}):
        with pytest.raises(ValueError):
            resolve_local_import_workspace(**{**BASE, **changes})

def test_production_or_remote_database_rejected():
    for dsn in ("dbname=campaigniq_production_db user=dganster", "dbname=campaigniq_dev host=db.example.com"):
        with pytest.raises(ValueError):
            resolve_local_import_workspace(**{**BASE, "dsn": dsn})

def test_bad_workspace_rejected():
    with pytest.raises(ValueError):
        resolve_local_import_workspace(**{**BASE, "requested": "../workspace-dennis"})
