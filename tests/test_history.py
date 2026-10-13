import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import uuid
from unittest.mock import patch

from codex_project_transfer import core
from test_native import paginated_fixture
from test_transfer import put


def fork_rows(root, parent, cutoff=None):
    cutoff = cutoff or parent[-1]["ordinal"] + 1
    sid = str(uuid.uuid4())
    meta = copy.deepcopy(parent[0])
    meta["ordinal"] = cutoff
    meta["payload"].update({"id": sid, "cwd": str(root),
        "forked_from_id": str(uuid.uuid4()),  # provenance is not the storage dependency
        "history_base": {"thread_id": parent[0]["payload"]["id"],
                         "end_ordinal_exclusive": cutoff, "end_byte_offset": 123}})
    if "session_id" in meta["payload"]:
        meta["payload"]["session_id"] = sid
    return [meta, {"ordinal": cutoff + 1, "type": "event_msg", "payload": {
        "type": "user_message", "message": "Child-only message", "images": []}}]


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.a, self.b = self.base / "source", self.base / "long destination 名称"
        self.ha, self.hb = self.base / "home-a", self.base / "home-b"
        for root in (self.a, self.b):
            root.mkdir()
            core.git(root, "init", "-q")
            core.initialize(root)

    def tearDown(self):
        self.temp.cleanup()

    def carry(self):
        shutil.copytree(self.a / core.STORE, self.b / core.STORE, dirs_exist_ok=True)

    def test_export_includes_recursive_ancestry_but_not_parent_future_turns(self):
        parent = paginated_fixture(self.base / "different project")
        branch = fork_rows(self.base / "different project", parent)
        child = fork_rows(self.a, branch)
        put(self.ha, parent + [{"ordinal": parent[-1]["ordinal"] + 1, "type": "event_msg",
                              "payload": {"type": "user_message", "message": "PRIVATE AFTER FORK"}}])
        put(self.ha, branch)
        put(self.ha, child)
        exported = core.export_sessions(self.a, self.ha)
        self.assertEqual(set(exported["dependencies"]), {parent[0]["payload"]["id"], branch[0]["payload"]["id"]})
        self.assertEqual(len(exported["exported"]), 3)
        texts = "".join(p.read_text() for p in (self.a / core.STORE / "sessions").glob("*.jsonl"))
        self.assertNotIn("PRIVATE AFTER FORK", texts)
        self.carry()
        result = core.import_sessions(self.b, self.hb, provider="destination-provider")
        self.assertEqual(len(result["imported"]), 3)
        files = {core.read_rollout(p)[0]["payload"]["id"]: p for p in core.session_files(self.hb)}
        for rows in (branch, child):
            restored = core.read_rollout(files[rows[0]["payload"]["id"]])
            ref = restored[0]["payload"]["history_base"]
            expected = files[ref["thread_id"]].stat().st_size
            self.assertEqual(ref["end_byte_offset"], expected)

    def test_missing_parent_blocks_export_before_any_snapshot_is_written(self):
        parent = paginated_fixture(self.a)
        child = fork_rows(self.a, parent)
        put(self.ha, child)
        with self.assertRaisesRegex(core.TransferError, parent[0]["payload"]["id"]):
            core.export_sessions(self.a, self.ha)
        self.assertEqual(list((self.a / core.STORE / "sessions").glob("*.jsonl")), [])

    def test_missing_parent_blocks_import_even_when_same_id_exists_locally(self):
        parent = paginated_fixture(self.a)
        child = fork_rows(self.a, parent)
        child[0]["payload"]["cwd"] = core.TOKEN
        core.merge_snapshot(self.b / core.STORE, child)
        put(self.hb, child)
        before = {str(p): p.read_bytes() for p in core.session_files(self.hb)}
        with self.assertRaisesRegex(core.TransferError, "missing history dependency"):
            core.import_sessions(self.b, self.hb)
        self.assertEqual(before, {str(p): p.read_bytes() for p in core.session_files(self.hb)})

    def test_native_verification_rejects_missing_ancestry_before_starting_codex(self):
        from codex_project_transfer.native import reconcile
        parent = paginated_fixture(self.a)
        child = fork_rows(self.a, parent)
        put(self.hb, child)
        with patch("codex_project_transfer.native.NativeClient", side_effect=AssertionError("must not launch")):
            with self.assertRaisesRegex(core.TransferError, parent[0]["payload"]["id"]):
                reconcile(self.hb, [child[0]["payload"]["id"]])

    def test_incomplete_archive_cannot_be_uploaded(self):
        from codex_project_transfer.transport import configure, exchange
        remote = self.base / "remote.git"
        core.git(self.base, "init", "--bare", "-q", str(remote))
        core.git(self.a, "remote", "add", "origin", str(remote))
        configure(self.a, "origin", "codex-sessions")
        child = fork_rows(self.a, paginated_fixture(self.a))
        child[0]["payload"]["cwd"] = core.TOKEN
        core.merge_snapshot(self.a / core.STORE, child)
        with self.assertRaisesRegex(core.TransferError, "missing history dependency"):
            exchange(self.a)
        self.assertEqual(core.git(self.a, "ls-remote", "origin", "refs/heads/codex-sessions").stdout, b"")

    def test_truncated_parent_and_cycles_are_rejected_before_import(self):
        parent = paginated_fixture(self.a)
        child = fork_rows(self.a, parent, cutoff=999)
        for rows in (parent, child):
            rows[0]["payload"]["cwd"] = core.TOKEN
            core.merge_snapshot(self.b / core.STORE, rows)
        with self.assertRaisesRegex(core.TransferError, "truncated"):
            core.import_sessions(self.b, self.hb)
        self.assertEqual(list(core.session_files(self.hb)), [])
        parent[0]["payload"]["history_base"] = {"thread_id": child[0]["payload"]["id"],
            "end_ordinal_exclusive": 1001, "end_byte_offset": 0}
        core.atomic_write(self.b / core.STORE / "sessions" / (parent[0]["payload"]["id"] + ".jsonl"), core.serialize(parent))
        with self.assertRaisesRegex(core.TransferError, "Cyclic"):
            core.import_sessions(self.b, self.hb)

    def test_dependency_offset_uses_existing_parent_bytes_not_reserialization(self):
        parent = paginated_fixture(self.a)
        child = fork_rows(self.a, parent)
        put(self.ha, parent); put(self.ha, child)
        core.export_sessions(self.a, self.ha); self.carry()
        local = core.map_paths(parent, lambda value: str(self.b) if value == str(self.a) else value)
        path = put(self.hb, local)
        noncanonical = b"".join(json.dumps(row, ensure_ascii=True, separators=(", ", ": ")).encode() + b"\n" for row in local)
        path.write_bytes(noncanonical)
        core.import_sessions(self.b, self.hb, provider="openai")
        restored = next(core.read_rollout(p) for p in core.session_files(self.hb)
                        if core.read_rollout(p)[0]["payload"]["id"] == child[0]["payload"]["id"])
        self.assertEqual(restored[0]["payload"]["history_base"]["end_byte_offset"], len(noncanonical))
        self.assertEqual(path.read_bytes(), noncanonical)

    def test_replayed_settings_and_world_state_are_portable_without_rewriting_messages(self):
        rows = paginated_fixture(self.a)
        raw_text = f"Historical output: {self.a} provider=source"
        rows += [
            {"ordinal": 9, "type": "event_msg", "payload": {"type": "thread_settings_applied", "thread_settings": {
                "model_provider_id": "source", "cwd": str(self.a), "runtime_workspace_roots": [str(self.a)]}}},
            {"ordinal": 10, "type": "world_state", "payload": {"full": True, "state": {
                "environments": {"environments": {"local": {"cwd": str(self.a)}}}}}},
            {"ordinal": 11, "type": "response_item", "payload": {"type": "message", "role": "user",
                "content": [{"type": "input_text", "text": raw_text}]}},
        ]
        sid = rows[0]["payload"]["id"]
        put(self.ha, rows); core.export_sessions(self.a, self.ha); self.carry()
        archive = self.b / core.STORE / "sessions" / (sid + ".jsonl")
        before = archive.read_bytes()
        core.import_sessions(self.b, self.hb, provider="destination")
        restored = core.read_rollout(next(core.session_files(self.hb)))
        settings = restored[-3]["payload"]["thread_settings"]
        self.assertEqual(settings["model_provider_id"], "destination")
        self.assertEqual(settings["cwd"], str(self.b))
        self.assertEqual(restored[-2]["payload"]["state"]["environments"]["environments"]["local"]["cwd"], str(self.b))
        self.assertEqual(restored[-1], rows[-1])
        core.export_sessions(self.b, self.hb)
        self.assertEqual(archive.read_bytes(), before)
