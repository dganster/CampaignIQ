from pathlib import Path

from campaigniq.persistence.artifact_storage import LocalFilesystemArtifactStorage
from campaigniq.runtime import build_local_runtime


def test_build_local_runtime_uses_campaigniq_runtime_directories(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("CAMPAIGNIQ_DATA_ROOT", raising=False)

    runtime = build_local_runtime(project_root=tmp_path)

    expected_state = tmp_path / ".campaigniq" / "authoritative_state"
    expected_history = tmp_path / ".campaigniq" / "thinkorswim_history"

    assert runtime.authoritative_state_root == expected_state
    assert runtime.historical_source_root == expected_history
    assert expected_state.is_dir()
    assert expected_history.is_dir()
    assert isinstance(runtime.artifact_storage, LocalFilesystemArtifactStorage)
    assert runtime.artifact_storage.root == expected_state


def test_local_runtime_artifact_storage_is_operational(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("CAMPAIGNIQ_DATA_ROOT", raising=False)

    runtime = build_local_runtime(project_root=tmp_path)

    runtime.artifact_storage.write_text("probe/runtime.txt", "cloud-boundary")

    assert runtime.artifact_storage.exists("probe/runtime.txt")
    assert runtime.artifact_storage.read_text("probe/runtime.txt") == "cloud-boundary"
    assert runtime.artifact_storage.list_keys(prefix="probe/") == ("probe/runtime.txt",)


def test_build_local_runtime_uses_configured_data_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    configured_root = tmp_path / "mounted-cloud-storage"
    monkeypatch.setenv("CAMPAIGNIQ_DATA_ROOT", str(configured_root))

    runtime = build_local_runtime(project_root=tmp_path / "ignored-project-root")

    expected_state = configured_root / "authoritative_state"
    expected_history = configured_root / "thinkorswim_history"

    assert runtime.authoritative_state_root == expected_state
    assert runtime.historical_source_root == expected_history
    assert expected_state.is_dir()
    assert expected_history.is_dir()
    assert isinstance(runtime.artifact_storage, LocalFilesystemArtifactStorage)
    assert runtime.artifact_storage.root == expected_state


def test_configured_data_root_artifact_storage_is_operational(
    tmp_path: Path,
    monkeypatch,
) -> None:
    configured_root = tmp_path / "mounted-cloud-storage"
    monkeypatch.setenv("CAMPAIGNIQ_DATA_ROOT", str(configured_root))

    runtime = build_local_runtime(project_root=tmp_path / "ignored-project-root")
    runtime.artifact_storage.write_text("probe/cloud.txt", "durable-cloud-state")

    assert runtime.artifact_storage.exists("probe/cloud.txt")
    assert (
        runtime.artifact_storage.read_text("probe/cloud.txt")
        == "durable-cloud-state"
    )
