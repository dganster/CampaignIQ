"""Read-only development backup verification. Never authorizes publication.

Snapshots contain all text artifacts, including drafts and publication markers.
A future publisher must use database transaction/locking controls as well;
two matching reads alone cannot exclude concurrent writers.
"""
from hashlib import sha256
import json

FORMAT = "campaigniq.historical_backfill_snapshot"


def capture_snapshot(storage):
    if not isinstance(storage.namespace, str) or not storage.namespace:
        raise ValueError("explicit workspace namespace required")
    def read_all():
        keys = sorted(storage.list_keys())
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate artifact keys")
        return {key: storage.read_text(key) for key in keys}
    artifacts = read_all()
    if artifacts != read_all():
        raise ValueError("artifacts changed during snapshot")
    hashes = {key: sha256(value.encode("utf-8")).hexdigest()
              for key, value in artifacts.items()}
    return {"format": FORMAT, "version": 1, "workspace": storage.namespace,
            "artifacts": artifacts, "sha256": hashes}


def validate_snapshot(snapshot):
    if (not isinstance(snapshot, dict)
            or set(snapshot) != {"format", "version", "workspace", "artifacts", "sha256"}
            or snapshot["format"] != FORMAT or type(snapshot["version"]) is not int
            or snapshot["version"] != 1
            or not isinstance(snapshot["workspace"], str) or not snapshot["workspace"]
            or not isinstance(snapshot["artifacts"], dict)
            or not isinstance(snapshot["sha256"], dict)):
        raise ValueError("invalid snapshot envelope")
    artifacts = snapshot["artifacts"]
    if any(not isinstance(k, str) or not k or not isinstance(v, str)
           for k, v in artifacts.items()):
        raise ValueError("invalid snapshot artifacts")
    expected = {k: sha256(v.encode("utf-8")).hexdigest() for k, v in artifacts.items()}
    if expected != snapshot["sha256"]:
        raise ValueError("snapshot checksum mismatch")
    return snapshot


def verify_snapshot(storage, snapshot):
    """Verify backup or restored copy; no writes, restore, or publish operations."""
    validate_snapshot(snapshot)
    current = capture_snapshot(storage)
    if current["workspace"] != snapshot["workspace"]:
        raise ValueError("workspace mismatch")
    before, after = snapshot["sha256"], current["sha256"]
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(k for k in before.keys() & after.keys() if before[k] != after[k])
    return {"verified": not (added or removed or changed),
            "added": added, "removed": removed, "changed": changed,
            "publication_authorized": False}


def serialize_snapshot(snapshot):
    validate_snapshot(snapshot)
    return json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
