import copy
import json

import pytest

from campaigniq.historical_backfill_snapshot import (
    capture_snapshot, serialize_snapshot, validate_snapshot, verify_snapshot,
)


class Storage:
    namespace = "workspace-dennis"

    def __init__(self):
        self.items = {"drafts/2025-01.json": "draft", "2026-09-finalized.json": "{}"}

    def list_keys(self):
        return tuple(self.items)

    def read_text(self, key):
        return self.items[key]

    def write_text(self, *args):
        raise AssertionError("must remain read-only")


def test_backup_roundtrip_and_independent_restore_copy():
    source = Storage()
    backup = json.loads(serialize_snapshot(capture_snapshot(source)))
    restored = Storage()
    restored.items = dict(backup["artifacts"])
    assert verify_snapshot(restored, backup)["verified"]
    assert not verify_snapshot(restored, backup)["publication_authorized"]


@pytest.mark.parametrize("mutation,field", [
    (lambda d: d.update({"new": "x"}), "added"),
    (lambda d: d.pop("drafts/2025-01.json"), "removed"),
    (lambda d: d.update({"2026-09-finalized.json": "altered"}), "changed"),
])
def test_detects_every_kind_of_restore_difference(mutation, field):
    storage = Storage()
    backup = capture_snapshot(storage)
    mutation(storage.items)
    report = verify_snapshot(storage, backup)
    assert not report["verified"]
    assert report[field]


def test_corrupt_backup_and_wrong_workspace_rejected():
    storage = Storage()
    backup = capture_snapshot(storage)
    bad = copy.deepcopy(backup)
    bad["artifacts"]["drafts/2025-01.json"] = "tampered"
    with pytest.raises(ValueError, match="checksum"):
        validate_snapshot(bad)
    storage.namespace = "workspace-andrew"
    with pytest.raises(ValueError, match="workspace"):
        verify_snapshot(storage, backup)


def test_mutation_during_capture_rejected():
    storage = Storage()
    original = storage.list_keys
    calls = 0
    def keys():
        nonlocal calls
        calls += 1
        if calls == 2:
            storage.items["concurrent"] = "write"
        return original()
    storage.list_keys = keys
    with pytest.raises(ValueError, match="changed"):
        capture_snapshot(storage)


@pytest.mark.parametrize("bad", [None, {}, {"version": True}])
def test_invalid_envelope_rejected(bad):
    with pytest.raises(ValueError):
        validate_snapshot(bad)
