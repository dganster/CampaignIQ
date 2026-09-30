"""Session access records, without credentials or arbitrary identity claims."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, MutableMapping
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from campaigniq.access import AccessContext
from campaigniq.persistence.artifact_storage import (
    ArtifactStorage,
    LocalFilesystemArtifactStorage,
)


def is_access_admin(access: AccessContext | None) -> bool:
    """Default to no administrators; use trusted OIDC subjects, never emails."""
    admins = {
        value.strip()
        for value in os.environ.get("CAMPAIGNIQ_ACCESS_ADMINS", "").split(",")
        if value.strip()
    }
    return access is not None and access.identity_id in admins


def access_history_storage(project_root: Path) -> ArtifactStorage:
    root = Path(os.environ.get("CAMPAIGNIQ_DATA_ROOT") or project_root / ".campaigniq")
    return LocalFilesystemArtifactStorage(root.expanduser() / "access_history")


def record_session_access(
    access: AccessContext | None,
    claims: Mapping,
    session: MutableMapping,
    storage: ArtifactStorage,
) -> str | None:
    """Record one authorized session; return a storage error without blocking use.

    A fresh browser session counts as a visit, including a session resumed with
    an existing authentication cookie. Widget reruns do not count as visits.
    """
    if access is None:
        return None
    session_key = "campaigniq_access_visit"
    session_identity = (access.identity_id, access.workspace_id)
    if session.get(session_key) == session_identity:
        return session.get("campaigniq_access_visit_error")
    event = {
        "event": "campaigniq_access",
        "visit_id": uuid4().hex,
        "visited_at_utc": datetime.now(timezone.utc).isoformat(),
        "identity_id": access.identity_id,
        "workspace_id": access.workspace_id,
    }
    email = claims.get("email")
    if claims.get("email_verified") is True and isinstance(email, str):
        event["email"] = email
    serialized = json.dumps(event, sort_keys=True)
    print(serialized, flush=True)
    error = None
    try:
        storage.write_text(
            f"visits/{event['visited_at_utc'][:10]}/{event['visit_id']}.json",
            serialized,
        )
    except Exception:
        error = "Access history could not be saved. The visit was written to the service logs."
        print("CampaignIQ access history storage failed", flush=True)
    session[session_key] = session_identity
    session["campaigniq_access_visit_error"] = error
    return error


def load_access_history(
    access: AccessContext | None,
    storage: ArtifactStorage,
) -> list[dict]:
    """Enforce administrator access before listing or reading any records."""
    if not is_access_admin(access):
        raise PermissionError("Access history requires administrator access.")
    rows = []
    for key in storage.list_keys(prefix="visits/", suffix=".json"):
        event = json.loads(storage.read_text(key))
        if event.get("event") == "campaigniq_access":
            rows.append({
                "Visited (UTC)": event["visited_at_utc"],
                "User": event.get("email") or event["identity_id"],
                "Identity": event["identity_id"],
                "Workspace": event["workspace_id"],
            })
    return sorted(rows, key=lambda row: row["Visited (UTC)"], reverse=True)
