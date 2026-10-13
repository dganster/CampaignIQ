"""Opt-in PostgreSQL runtime wiring tests; no database connection required."""
import pytest
from campaigniq.runtime import build_local_runtime
from campaigniq.persistence.artifact_storage import LocalFilesystemArtifactStorage
from campaigniq.persistence.postgres_artifact_storage import PostgresArtifactStorage


def test_default_remains_filesystem(tmp_path, monkeypatch):
    monkeypatch.delenv("CAMPAIGNIQ_STORAGE_BACKEND", raising=False)
    monkeypatch.delenv("CAMPAIGNIQ_DEV_DATABASE_URL", raising=False)
    runtime = build_local_runtime(project_root=tmp_path)
    assert isinstance(runtime.artifact_storage, LocalFilesystemArtifactStorage)


def test_explicit_postgres_workspace_namespaces(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMPAIGNIQ_STORAGE_BACKEND", "postgres")
    monkeypatch.setenv("CAMPAIGNIQ_DEV_DATABASE_URL", "postgresql://example.invalid/dev")
    for workspace, expected in [(None, "shared"), ("workspace-andrew", "workspace-andrew"), ("workspace-dennis", "workspace-dennis")]:
        runtime = build_local_runtime(project_root=tmp_path, workspace_id=workspace)
        assert isinstance(runtime.artifact_storage, PostgresArtifactStorage)
        assert runtime.artifact_storage.namespace == expected
        assert runtime.artifact_storage.dsn == "postgresql://example.invalid/dev"
        assert runtime.authoritative_state_root.is_dir()
        assert runtime.historical_source_root.is_dir()


def test_postgres_requires_dsn(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMPAIGNIQ_STORAGE_BACKEND", "postgres")
    monkeypatch.delenv("CAMPAIGNIQ_DEV_DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="CAMPAIGNIQ_DEV_DATABASE_URL"):
        build_local_runtime(project_root=tmp_path)


def test_unknown_backend_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMPAIGNIQ_STORAGE_BACKEND", "unexpected")
    with pytest.raises(ValueError, match="Unsupported CAMPAIGNIQ_STORAGE_BACKEND"):
        build_local_runtime(project_root=tmp_path)


def test_postgres_still_rejects_unsafe_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMPAIGNIQ_STORAGE_BACKEND", "postgres")
    monkeypatch.setenv("CAMPAIGNIQ_DEV_DATABASE_URL", "postgresql://example.invalid/dev")
    with pytest.raises(ValueError, match="Invalid workspace ID"):
        build_local_runtime(project_root=tmp_path, workspace_id="../escape")
