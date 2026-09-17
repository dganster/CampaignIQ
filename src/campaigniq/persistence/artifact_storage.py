"""Provider-neutral storage for durable CampaignIQ text artifacts."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import Protocol


class ArtifactStorage(Protocol):
    """Minimal durable-artifact contract required by CampaignIQ."""

    def exists(self, key: str) -> bool: ...

    def read_text(self, key: str) -> str: ...

    def write_text(self, key: str, content: str) -> None: ...

    def delete(self, key: str) -> None: ...

    def list_keys(self, *, prefix: str = "", suffix: str = "") -> tuple[str, ...]: ...


def _validate_key(key: str) -> PurePosixPath:
    candidate = PurePosixPath(key)
    if (
        not key
        or candidate.is_absolute()
        or any(part in {".", ".."} for part in key.split("/"))
        or "\\" in key
    ):
        raise ValueError(f"Invalid artifact key: {key!r}")
    return candidate


class LocalFilesystemArtifactStorage:
    """ArtifactStorage adapter backed by one local filesystem directory."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root.joinpath(*_validate_key(key).parts)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def read_text(self, key: str) -> str:
        return self._path(key).read_text(encoding="utf-8")

    def write_text(self, key: str, content: str) -> None:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temp_path = Path(stream.name)
            try:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            except BaseException:
                temp_path.unlink(missing_ok=True)
                raise
        try:
            temp_path.replace(target)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def list_keys(self, *, prefix: str = "", suffix: str = "") -> tuple[str, ...]:
        if not self.root.is_dir():
            return ()
        keys = (
            path.relative_to(self.root).as_posix()
            for path in self.root.rglob("*")
            if path.is_file()
        )
        return tuple(
            sorted(
                key
                for key in keys
                if key.startswith(prefix) and key.endswith(suffix)
            )
        )
