"""Fail-closed local-only PostgreSQL workspace selection for manual backfill."""
from __future__ import annotations

import re
import shlex


def resolve_local_import_workspace(*, access_workspace: str | None, backend: str,
                                   dsn: str, requested: str, on_render: bool) -> str | None:
    """Select an explicit local development namespace, never impersonate on a server."""
    requested = requested.strip()
    if not requested:
        return access_workspace
    if on_render or access_workspace is not None:
        raise ValueError("Local workspace override is forbidden on hosted or authenticated sessions")
    if backend.strip().lower() != "postgres":
        raise ValueError("Local workspace override requires PostgreSQL")
    if not re.fullmatch(r"workspace-[A-Za-z0-9_-]{1,100}", requested):
        raise ValueError("Invalid local workspace identifier")
    try:
        fields = shlex.split(dsn)
        if not fields or any("=" not in field for field in fields):
            raise ValueError("Invalid connection string")
        info = dict(field.split("=", 1) for field in fields)
    except ValueError as exc:
        raise ValueError("Invalid PostgreSQL connection string") from exc
    if info.get("dbname") != "campaigniq_dev":
        raise ValueError("Local workspace override requires campaigniq_dev database")
    if info.get("host") not in (None, "", "localhost", "127.0.0.1", "/var/run/postgresql"):
        raise ValueError("Local workspace override requires a local PostgreSQL connection")
    return requested
