import sqlite3
import unittest
from contextlib import closing

from codex_project_transfer import core
import test_transfer as fixtures
from test_transfer import append, fixture, put
import uuid


def register(home, sid, path):
    with closing(sqlite3.connect(home / "state_5.sqlite")) as connection, connection:
        connection.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT)")
        connection.execute("INSERT INTO threads VALUES (?, ?)", (sid, str(path)))


class RolloutSelectionTests(unittest.TestCase):
    setUp = fixtures.TransferTests.setUp
    tearDown = fixtures.TransferTests.tearDown

    def alternative(self, home, root):
        rows = append(fixture(root, self.sid, "Different inactive conversation"), "extra inactive message")
        path = home / "sessions" / "superseded.jsonl"
        core.atomic_write(path, core.serialize(rows))
        return path

    def test_export_uses_registered_file_even_if_another_copy_is_longer(self):
        other = self.alternative(self.ha, self.a)
        before = {self.source: self.source.read_bytes(), other: other.read_bytes()}
        register(self.ha, self.sid, self.source)
        result = core.export_sessions(self.a, self.ha)
        self.assertEqual(result["exported"], [self.sid])
        self.assertEqual(result["warnings"], [])
        self.assertEqual(result["selected_rollouts"][0]["selected"], str(self.source))
        stored = core.read_rollout(self.a / core.STORE / "sessions" / (self.sid + ".jsonl"))
        self.assertEqual(stored[1:], self.rows[1:])
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)

    def test_import_updates_only_the_registered_native_copy(self):
        core.export_sessions(self.a, self.ha)
        import shutil
        shutil.copytree(self.a / core.STORE, self.b / core.STORE, dirs_exist_ok=True)
        core.import_sessions(self.b, self.hb)
        active = next(core.session_files(self.hb))
        other = self.alternative(self.hb, self.b)
        other_before = other.read_bytes()
        register(self.hb, self.sid, active)
        put(self.ha, append(self.rows, "New source message"))
        core.export_sessions(self.a, self.ha)
        shutil.copytree(self.a / core.STORE, self.b / core.STORE, dirs_exist_ok=True)
        result = core.import_sessions(self.b, self.hb, update_existing=True)
        self.assertEqual(result["imported"], [self.sid])
        self.assertEqual(result["selected_rollouts"][0]["selected"], str(active))
        self.assertIn("New source message", active.read_text())
        self.assertEqual(other.read_bytes(), other_before)
        with closing(sqlite3.connect(self.hb / "state_5.sqlite")) as connection:
            self.assertEqual(connection.execute("SELECT rollout_path FROM threads WHERE id=?", (self.sid,)).fetchone(), (str(active),))

    def test_stale_registry_does_not_resolve_divergent_copies(self):
        other = self.alternative(self.ha, self.a)
        register(self.ha, self.sid, self.ha / "missing.jsonl")
        with self.assertRaisesRegex(core.TransferError, "no matching active Codex index entry") as caught:
            core.export_sessions(self.a, self.ha)
        self.assertIn(str(self.source), str(caught.exception))
        self.assertIn(str(other), str(caught.exception))
        self.assertEqual(list((self.a / core.STORE / "sessions").glob("*.jsonl")), [])

    def test_without_registry_a_true_prefix_still_selects_complete_history(self):
        later = self.ha / "sessions" / "later.jsonl"
        core.atomic_write(later, core.serialize(append(self.rows, "Later message")))
        result = core.export_sessions(self.a, self.ha)
        self.assertEqual(result["exported"], [self.sid])
        self.assertEqual(result["selected_rollouts"], [])
        stored = self.a / core.STORE / "sessions" / (self.sid + ".jsonl")
        self.assertIn("Later message", stored.read_text())

    def test_storage_version_lookup_survives_a_different_active_selection(self):
        version = str(uuid.uuid4())
        frozen = self.ha / "sessions" / f"rollout-2026-09-28T00-00-00-{self.sid}_{version}.jsonl"
        before = core.serialize(self.rows)
        core.atomic_write(frozen, before)
        active = self.alternative(self.ha, self.a)
        register(self.ha, self.sid, active)
        index, _, _ = core.native_session_index(self.ha)
        selected = core.load_native_session(index, self.sid)
        snapshot = core.load_history_source(index, version)
        self.assertIn("Different inactive conversation", core.serialize(selected).decode())
        self.assertEqual(snapshot[0]["payload"]["id"], self.sid)
        self.assertEqual(snapshot[1:], self.rows[1:])
        self.assertEqual(frozen.read_bytes(), before)
        self.assertIn(version, index.history_sources)

    def test_unreferenced_storage_versions_are_not_exported_as_project_chats(self):
        version = str(uuid.uuid4())
        frozen = self.ha / "sessions" / f"rollout-2026-09-28T00-00-00-{self.sid}_{version}.jsonl"
        core.atomic_write(frozen, core.serialize(self.rows))
        register(self.ha, self.sid, self.source)
        result = core.export_sessions(self.a, self.ha)
        self.assertEqual(result["exported"], [self.sid])
        self.assertFalse((self.a / core.STORE / "sessions" / (version + ".jsonl")).exists())
