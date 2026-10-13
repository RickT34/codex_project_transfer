from contextlib import closing
import json
import sqlite3
import unittest

from codex_project_transfer import core
from codex_project_transfer.cli import titles_for
from codex_project_transfer.transport import exchange
import test_transfer


def set_name(home, sid, name, preview="First user message", version=5):
    home.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(home / f"state_{version}.sqlite")) as connection, connection:
        connection.execute("CREATE TABLE IF NOT EXISTS threads (id TEXT PRIMARY KEY, name TEXT, title TEXT)")
        connection.execute("INSERT OR REPLACE INTO threads VALUES (?, ?, ?)", (sid, name, preview))


class TitleTests(unittest.TestCase):
    setUp = test_transfer.TransferTests.setUp
    tearDown = test_transfer.TransferTests.tearDown

    def test_export_uses_ui_name_and_updates_without_changing_conversation(self):
        set_name(self.ha, self.sid, "自动生成的标题")
        first = core.export_sessions(self.a, self.ha)
        snapshot = self.a / core.STORE / "sessions" / (self.sid + ".jsonl")
        before = snapshot.read_bytes()
        self.assertEqual(titles_for(self.a, [self.sid]), {self.sid: "自动生成的标题"})
        self.assertEqual(first["titles_updated"], [self.sid])
        set_name(self.ha, self.sid, "后续修改的标题")
        renamed = core.export_sessions(self.a, self.ha)
        self.assertEqual(titles_for(self.a, [self.sid]), {self.sid: "后续修改的标题"})
        self.assertEqual(renamed["titles_updated"], [self.sid])
        self.assertEqual(snapshot.read_bytes(), before)
        self.assertEqual(self.source.read_bytes(), core.serialize(self.rows))
        title = self.a / core.STORE / "titles" / (self.sid + ".json")
        title_before = title.read_bytes()
        self.assertEqual(core.export_sessions(self.a, self.ha)["titles_updated"], [])
        self.assertEqual(title.read_bytes(), title_before)

    def test_latest_database_selected_numerically_and_name_beats_index_and_preview(self):
        set_name(self.ha, self.sid, "Stale database name", version=9)
        set_name(self.ha, self.sid, "Current generated name", version=10)
        (self.ha / "session_index.jsonl").write_text(json.dumps({"id": self.sid, "thread_name": "Index name"}) + "\n")
        self.assertEqual(core.metadata_titles(self.ha)[self.sid], "Current generated name")

    def test_index_retains_latest_name_when_database_has_only_preview(self):
        set_name(self.ha, self.sid, None)
        (self.ha / "session_index.jsonl").write_text(
            json.dumps({"id": self.sid, "thread_name": "Old name"}) + "\n" +
            json.dumps({"id": self.sid, "thread_name": "Current name"}) + '\n{"unfinished":')
        self.assertEqual(core.metadata_titles(self.ha)[self.sid], "Current name")

    def test_preview_is_used_only_when_no_ui_name_exists(self):
        set_name(self.ha, self.sid, None, preview="First message preview")
        self.assertEqual(core.metadata_titles(self.ha)[self.sid], "First message preview")

    def test_invalid_title_metadata_is_rejected(self):
        for value in ([], {"title": ""}, {"title": "Name", "revision": True}, {"title": "Name", "revision": -1}):
            with self.subTest(value=value), self.assertRaises(core.TransferError):
                core.title_order(value)


class TitleTransportTests(unittest.TestCase):
    setUp = test_transfer.TransferTests.setUp
    tearDown = test_transfer.TransferTests.tearDown
    make_remote = test_transfer.GitTransportTests.make_remote

    def test_later_title_reaches_other_device_without_reverting_to_lexical_minimum(self):
        self.make_remote()
        set_name(self.ha, self.sid, "A first title")
        core.export_sessions(self.a, self.ha)
        exchange(self.a)
        exchange(self.b, publish=False)
        self.assertEqual(titles_for(self.b, [self.sid])[self.sid], "A first title")
        set_name(self.ha, self.sid, "Z updated title")
        core.export_sessions(self.a, self.ha)
        exchange(self.a)
        exchange(self.b, publish=False)
        self.assertEqual(titles_for(self.b, [self.sid])[self.sid], "Z updated title")
        self.assertFalse(exchange(self.b)["pushed"])
        self.assertFalse(exchange(self.a)["pushed"])

    def test_concurrent_title_changes_converge_without_repeated_commits(self):
        self.make_remote()
        set_name(self.ha, self.sid, "Starting name")
        core.export_sessions(self.a, self.ha)
        exchange(self.a)
        exchange(self.b, publish=False)
        core.import_sessions(self.b, self.hb)
        set_name(self.ha, self.sid, "A changed name")
        set_name(self.hb, self.sid, "B changed name")
        core.export_sessions(self.a, self.ha)
        core.export_sessions(self.b, self.hb)
        exchange(self.a)
        exchange(self.b)
        exchange(self.a)
        self.assertEqual(titles_for(self.a, [self.sid]), titles_for(self.b, [self.sid]))
        self.assertFalse(exchange(self.a)["pushed"])
        self.assertFalse(exchange(self.b)["pushed"])
