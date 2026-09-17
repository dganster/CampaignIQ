from pathlib import Path

import pytest

from campaigniq.persistence.artifact_storage import (
    ArtifactStorage,
    LocalFilesystemArtifactStorage,
)


def _exercise_storage_contract(storage: ArtifactStorage) -> None:
    assert not storage.exists("2026/08/example.json")
    assert storage.list_keys() == ()

    storage.write_text("2026/08/example.json", '{"version": 1}\n')
    storage.write_text("2026/08/other.txt", "other\n")
    storage.write_text("2026/09/example.json", '{"version": 2}\n')

    assert storage.exists("2026/08/example.json")
    assert storage.read_text("2026/08/example.json") == '{"version": 1}\n'
    assert storage.list_keys(prefix="2026/08/", suffix=".json") == (
        "2026/08/example.json",
    )

    storage.write_text("2026/08/example.json", '{"version": 3}\n')
    assert storage.read_text("2026/08/example.json") == '{"version": 3}\n'

    storage.delete("2026/08/example.json")
    storage.delete("2026/08/example.json")
    assert not storage.exists("2026/08/example.json")


def test_local_filesystem_adapter_satisfies_artifact_storage_contract(tmp_path) -> None:
    _exercise_storage_contract(LocalFilesystemArtifactStorage(tmp_path))


def test_local_filesystem_adapter_persists_under_configured_root(tmp_path) -> None:
    storage = LocalFilesystemArtifactStorage(tmp_path)
    storage.write_text("nested/artifact.json", "payload\n")

    assert (tmp_path / "nested" / "artifact.json").read_text(encoding="utf-8") == "payload\n"


@pytest.mark.parametrize(
    "key",
    ("", "/absolute.json", "../escape.json", "nested/../escape.json", "./artifact.json", r"nested\\artifact.json"),
)
def test_local_filesystem_adapter_rejects_nonportable_or_escaping_keys(
    tmp_path,
    key: str,
) -> None:
    storage = LocalFilesystemArtifactStorage(tmp_path)

    with pytest.raises(ValueError, match="Invalid artifact key"):
        storage.write_text(key, "payload")


def test_local_filesystem_adapter_lists_keys_deterministically(tmp_path) -> None:
    storage = LocalFilesystemArtifactStorage(tmp_path)
    storage.write_text("z.json", "z")
    storage.write_text("a.json", "a")
    storage.write_text("nested/m.json", "m")

    assert storage.list_keys(suffix=".json") == (
        "a.json",
        "nested/m.json",
        "z.json",
    )
