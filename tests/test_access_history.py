import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from campaigniq.access import AccessContext
from campaigniq.persistence.artifact_storage import LocalFilesystemArtifactStorage
from campaigniq.ui.access_history import (
    access_history_storage,
    is_access_admin,
    load_access_history,
    record_session_access,
)


class AccessHistoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.storage = LocalFilesystemArtifactStorage(self.directory.name)
        self.access = AccessContext("dennis-subject", "dennis-workspace")

    def test_records_once_per_session_and_survives_storage_reopen(self):
        session = {}
        claims = {"email": "dennis@example.com", "email_verified": True,
                  "access_token": "secret", "name": "not-recorded"}
        output = io.StringIO()
        with redirect_stdout(output):
            record_session_access(self.access, claims, session, self.storage)
            record_session_access(self.access, claims, session, self.storage)
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        event = json.loads(output.getvalue())
        self.assertEqual(event["email"], "dennis@example.com")
        self.assertNotIn("secret", output.getvalue())
        self.assertNotIn("not-recorded", output.getvalue())
        self.assertTrue(event["visited_at_utc"].endswith("+00:00"))
        with patch.dict("os.environ", {"CAMPAIGNIQ_ACCESS_ADMINS": "dennis-subject"}):
            rows = load_access_history(self.access,
                LocalFilesystemArtifactStorage(self.directory.name))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Identity"], "dennis-subject")

    def test_another_session_creates_a_separate_visit(self):
        with redirect_stdout(io.StringIO()):
            record_session_access(self.access, {}, {}, self.storage)
            record_session_access(self.access, {}, {}, self.storage)
        self.assertEqual(len(self.storage.list_keys()), 2)

    def test_unverified_email_and_unauthenticated_access_are_not_recorded(self):
        with redirect_stdout(io.StringIO()):
            record_session_access(None, {"sub": "anyone"}, {}, self.storage)
        self.assertEqual(self.storage.list_keys(), ())
        with redirect_stdout(io.StringIO()):
            record_session_access(self.access, {"email": "fake@example.com"}, {}, self.storage)
        text = self.storage.read_text(self.storage.list_keys()[0])
        self.assertNotIn("fake@example.com", text)

    def test_only_explicit_administrators_can_read_all_workspaces(self):
        with patch.dict("os.environ", {"CAMPAIGNIQ_ACCESS_ADMINS": ""}):
            self.assertFalse(is_access_admin(self.access))
            with self.assertRaises(PermissionError):
                load_access_history(self.access, self.storage)
        with patch.dict("os.environ", {"CAMPAIGNIQ_ACCESS_ADMINS": "dennis-subject"}):
            self.assertFalse(is_access_admin(AccessContext("andrew", "dennis-workspace")))
            with self.assertRaises(PermissionError):
                load_access_history(AccessContext("andrew", "other-workspace"), self.storage)

    def test_storage_failure_keeps_one_service_log_entry_without_blocking_access(self):
        session = {}
        output = io.StringIO()
        with patch.object(self.storage, "write_text", side_effect=OSError), redirect_stdout(output):
            error = record_session_access(self.access, {}, session, self.storage)
            self.assertEqual(record_session_access(self.access, {}, session, self.storage), error)
        self.assertIsNotNone(error)
        self.assertEqual(output.getvalue().count('"event": "campaigniq_access"'), 1)

    def test_history_uses_shared_configured_data_root(self):
        with patch.dict("os.environ", {"CAMPAIGNIQ_DATA_ROOT": self.directory.name}):
            storage = access_history_storage(Path("/unused"))
        self.assertEqual(storage.root, Path(self.directory.name) / "access_history")
