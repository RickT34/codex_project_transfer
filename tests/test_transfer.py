import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import uuid
from unittest.mock import patch

from codex_project_transfer import core
from codex_project_transfer.cli import sync, main, parser, perform
from codex_project_transfer.transport import configure, exchange


def fixture(root, sid=None, text="Remember the synthetic token blue-orchid"):
    sid = sid or str(uuid.uuid4())
    return [
        {"timestamp": "2026-09-28T00:00:00Z", "type": "session_meta", "payload": {
            "id": sid, "timestamp": "2026-09-28T00:00:00Z", "cwd": str(root),
            "originator": "codex_cli_rs", "cli_version": "0.155.1", "source": "cli",
            "model_provider": "openai", "base_instructions": {"text": "Synthetic test session."},
            "history_mode": "legacy"}},
        {"timestamp": "2026-09-28T00:00:01Z", "type": "response_item", "payload": {
            "type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}},
        {"timestamp": "2026-09-28T00:00:02Z", "type": "event_msg", "payload": {
            "type": "user_message", "message": text, "images": []}},
        {"timestamp": "2026-09-28T00:00:03Z", "type": "response_item", "payload": {
            "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Recorded."}]}},
        {"timestamp": "2026-09-28T00:00:04Z", "type": "event_msg", "payload": {
            "type": "agent_message", "message": "Recorded."}},
    ]


def append(rows, text="next turn"):
    return rows + [{"type": "event_msg", "payload": {"type": "user_message", "message": text}}]


def put(home, rows):
    sid = rows[0]["payload"]["id"]
    path = home / "sessions/2026/09/28" / f"rollout-2026-09-28T00-00-00-{sid}.jsonl"
    core.atomic_write(path, core.serialize(rows))
    return path


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.a = self.base / "project A"
        self.b = self.base / "project B"
        self.a.mkdir()
        self.b.mkdir()
        for root in [self.a, self.b]:
            core.git(root, "init", "-q")
            core.initialize(root)
        self.ha = self.base / "home-a"
        self.hb = self.base / "home-b"
        self.rows = fixture(self.a)
        self.sid = self.rows[0]["payload"]["id"]
        self.source = put(self.ha, self.rows)

    def tearDown(self):
        self.temp.cleanup()

    def carry(self):
        import shutil
        core.export_sessions(self.a, self.ha)
        shutil.copytree(self.a / core.STORE, self.b / core.STORE, dirs_exist_ok=True)

    def test_roundtrip_preserves_messages_and_remaps_metadata(self):
        self.rows[1]["payload"]["content"][0]["text"] = str(self.a) + " must remain literal"
        put(self.ha, self.rows)
        self.carry()
        result = core.import_sessions(self.b, self.hb)
        self.assertEqual(result["imported"], [self.sid])
        path = next(core.session_files(self.hb))
        imported = core.read_rollout(path)
        self.assertEqual(imported[0]["payload"]["cwd"], str(self.b))
        self.assertEqual(imported[1:], self.rows[1:])
        self.assertEqual(core.import_sessions(self.b, self.hb)["unchanged"], [self.sid])

    def test_only_this_project(self):
        other = put(self.ha, fixture(self.base / "unrelated"))
        result = core.export_sessions(self.a, self.ha)
        self.assertEqual(result["exported"], [self.sid])
        self.assertTrue(other.exists())

    def test_subdirectory_and_worktree(self):
        sub = self.a / "src"
        sub.mkdir()
        rows = fixture(sub)
        put(self.ha, rows)
        result = core.export_sessions(self.a, self.ha)
        self.assertEqual(len(result["exported"]), 2)
        portable = core.read_rollout(self.a / core.STORE / "sessions" / (rows[0]["payload"]["id"] + ".jsonl"))
        self.assertEqual(portable[0]["payload"]["cwd"], core.TOKEN + "/src")

    def test_registered_worktree_sessions_are_included(self):
        core.git(self.a, "-c", "user.name=Test", "-c", "user.email=test@localhost", "commit", "--allow-empty", "-qm", "initial")
        worktree = self.base / "linked worktree"
        core.git(self.a, "worktree", "add", "--detach", str(worktree))
        linked = fixture(worktree)
        put(self.ha, linked)
        result = core.export_sessions(self.a, self.ha)
        self.assertIn(linked[0]["payload"]["id"], result["exported"])


    def test_live_update_is_refused_by_cli(self):
        self.carry()
        with patch("codex_project_transfer.cli.running_clients", return_value=["codex"]):
            with patch("sys.stderr", new=io.StringIO()):
                code = main(["-C", str(self.b), "--home", str(self.hb), "import", "--update-existing"])
        self.assertEqual(code, 1)
        self.assertFalse(self.hb.exists())

    def test_unknown_history_format_and_missing_paginated_ordinals_rejected(self):
        rows = copy.deepcopy(self.rows)
        rows[0]["payload"]["history_mode"] = "future-format"
        with self.assertRaises(core.TransferError):
            core.parse(core.serialize(rows))
        rows[0]["payload"]["history_mode"] = "paginated"
        with self.assertRaises(core.TransferError):
            core.parse(core.serialize(rows))

    def test_duplicate_local_ids_are_not_overwritten(self):
        self.carry()
        core.import_sessions(self.b, self.hb)
        source = next(core.session_files(self.hb))
        duplicate = self.hb / "sessions" / ("duplicate-" + self.sid + ".jsonl")
        duplicate.write_bytes(source.read_bytes())
        result = core.import_sessions(self.b, self.hb, update_existing=True)
        self.assertTrue(result["warnings"])
        self.assertEqual(source.read_bytes(), duplicate.read_bytes())

    def test_partial_tail_is_not_exported(self):
        with self.source.open("ab") as f:
            f.write(b'{"type":"unfinished')
        self.carry()
        saved = core.read_rollout(self.a / core.STORE / "sessions" / (self.sid + ".jsonl"))
        self.assertEqual(len(saved), len(self.rows))

    def test_fast_forward_defers_then_backs_up(self):
        self.carry()
        core.import_sessions(self.b, self.hb)
        put(self.ha, append(self.rows))
        self.carry()
        self.assertEqual(core.import_sessions(self.b, self.hb)["pending"], [self.sid])
        result = core.import_sessions(self.b, self.hb, update_existing=True)
        self.assertEqual(result["imported"], [self.sid])
        backups = list((self.hb / "cpt-backups").rglob("*.jsonl"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(len(core.read_rollout(backups[0])), len(self.rows))

    def test_divergence_keeps_both_histories(self):
        self.carry()
        core.import_sessions(self.b, self.hb)
        local_path = next(core.session_files(self.hb))
        core.atomic_write(local_path, core.serialize(append(core.read_rollout(local_path), "device B")))
        put(self.ha, append(self.rows, "device A"))
        self.carry()
        before = local_path.read_bytes()
        self.assertEqual(core.import_sessions(self.b, self.hb, update_existing=True)["conflicts"], [self.sid])
        self.assertEqual(local_path.read_bytes(), before)
        core.export_sessions(self.b, self.hb)
        variants = list((self.b / core.STORE / "conflicts").rglob("*.jsonl"))
        self.assertEqual(len(variants), 1)
        forked = core.fork_conflict(self.b, self.sid, variants[0].stem)
        self.assertNotEqual(forked, self.sid)
        self.assertEqual(core.fork_conflict(self.b, self.sid, variants[0].stem), forked)

    def test_dry_run_does_not_create_home(self):
        self.carry()
        result = core.import_sessions(self.b, self.hb, dry_run=True)
        self.assertEqual(result["imported"], [self.sid])
        self.assertFalse(self.hb.exists())

    def test_exclude_open_session(self):
        self.carry()
        self.assertEqual(core.import_sessions(self.b, self.hb, exclude=[self.sid])["imported"], [])

    def test_session_id_traversal_rejected(self):
        self.rows[0]["payload"]["id"] = "../../escape"
        with self.assertRaises(core.TransferError):
            core.parse(core.serialize(self.rows))

    def test_path_traversal_rejected(self):
        self.carry()
        p = self.b / core.STORE / "sessions" / (self.sid + ".jsonl")
        rows = core.read_rollout(p)
        rows[0]["payload"]["cwd"] = core.TOKEN + "/../../escape"
        core.atomic_write(p, core.serialize(rows))
        with self.assertRaises(core.TransferError):
            core.import_sessions(self.b, self.hb)

    def test_symlink_escape_rejected(self):
        self.carry()
        self.hb.mkdir()
        outside = self.base / "outside"
        outside.mkdir()
        try:
            (self.hb / "sessions").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Symlinks not permitted")
        with self.assertRaises(core.TransferError):
            core.import_sessions(self.b, self.hb)
        self.assertEqual(list(outside.iterdir()), [])

    def test_boolean_number_not_equal(self):
        self.assertEqual(core.relation([{"x": True}], [{"x": 1}]), "diverged")

    def test_malformed_store_does_not_partially_import(self):
        self.carry()
        bad = self.b / core.STORE / "sessions" / "zz-invalid.jsonl"
        bad.write_text("garbage")
        with self.assertRaises(core.TransferError):
            core.import_sessions(self.b, self.hb)
        self.assertEqual(list(core.session_files(self.hb)), [])



    def test_os_lock_released(self):
        path = self.base / "lock"
        with core.locked(path):
            with self.assertRaises(core.TransferError):
                with core.locked(path):
                    pass
        with core.locked(path):
            pass

    def test_network_failure_still_saves(self):
        with patch("codex_project_transfer.cli.exchange", side_effect=core.TransferError("offline")):
            result = sync(self.a, self.ha, use_git=True, discover=False)
        self.assertEqual(result["export"]["exported"], [self.sid])
        self.assertIn("offline", result["git"]["error"])

    def test_pending_native_discovery_retried(self):
        self.carry()
        with patch("codex_project_transfer.cli.native_repair", return_value={"verified": [], "warnings": ["no binary"]}):
            sync(self.b, self.hb, use_git=False)
        with patch("codex_project_transfer.cli.native_repair", return_value={"verified": [self.sid]}) as repair:
            sync(self.b, self.hb, use_git=False)
            self.assertIn(self.sid, repair.call_args.args[2])

    def test_manual_check_saves_locally_without_network_or_import(self):
        core.git(self.a, "remote", "add", "origin", "https://example.invalid/never-contact.git")
        configure(self.a, "origin", "codex-sessions")
        with patch("codex_project_transfer.cli.exchange", side_effect=AssertionError("network")), \
             patch("codex_project_transfer.cli.import_sessions", side_effect=AssertionError("import")), \
             patch("codex_project_transfer.cli.native_repair", side_effect=AssertionError("native process")):
            result = perform(parser().parse_args(["-C", str(self.a), "--home", str(self.ha), "check"]))
        self.assertIn(self.sid, result["export"]["exported"])
        self.assertIn(self.sid, result["status"]["sessions"])
        self.assertTrue((self.a / core.STORE / "sessions" / (self.sid + ".jsonl")).exists())

    def test_init_and_status_never_save_or_create_hooks(self):
        with patch("codex_project_transfer.cli.export_sessions", side_effect=AssertionError("save")):
            perform(parser().parse_args(["-C", str(self.a), "init"]))
            perform(parser().parse_args(["-C", str(self.a), "status"]))
        self.assertFalse((self.a / ".codex/hooks.json").exists())
        self.assertEqual(list((self.a / core.STORE / "sessions").glob("*.jsonl")), [])


    def test_local_sync_does_not_use_remembered_remote(self):
        core.git(self.a, "remote", "add", "origin", "https://example.invalid/never-contact.git")
        configure(self.a, "origin", "codex-sessions")
        with patch("codex_project_transfer.cli.exchange", side_effect=AssertionError("network")):
            result = sync(self.a, self.ha, discover=False)
        self.assertNotIn("git", result)




class GitTransportTests(unittest.TestCase):
    setUp = TransferTests.setUp
    tearDown = TransferTests.tearDown
    def make_remote(self):
        remote = self.base / "remote.git"
        core.git(self.base, "init", "--bare", "-q", str(remote))
        for root in [self.a, self.b]:
            core.git(root, "remote", "add", "origin", str(remote))
            configure(root, "origin", "codex-sessions")
        return remote

    def test_remote_roundtrip_preserves_staging_and_code_branch(self):
        self.make_remote()
        (self.a / "code.txt").write_text("staged code")
        core.git(self.a, "add", "code.txt")
        index_before = (self.a / ".git/index").read_bytes()
        head_before = (self.a / ".git/HEAD").read_bytes()
        core.export_sessions(self.a, self.ha)
        self.assertTrue(exchange(self.a)["pushed"])
        self.assertEqual((self.a / ".git/index").read_bytes(), index_before)
        self.assertEqual((self.a / ".git/HEAD").read_bytes(), head_before)
        self.assertFalse(exchange(self.a)["pushed"])
        self.assertEqual(exchange(self.b)["received"], 1)
        self.assertEqual(core.import_sessions(self.b, self.hb)["imported"], [self.sid])
        put(self.hb, append(core.read_rollout(next(core.session_files(self.hb)))))
        # This deliberately creates two rollouts with one ID; export remains
        # monotonic, while import refuses ambiguous duplicates.
        core.export_sessions(self.b, self.hb)
        exchange(self.b)
        exchange(self.a)
        self.assertEqual(core.import_sessions(self.a, self.ha, update_existing=True)["imported"], [self.sid])

    def test_refuse_code_branch_transport(self):
        self.make_remote()
        branch = core.git(self.a, "symbolic-ref", "--short", "HEAD").stdout.decode().strip()
        with self.assertRaises(core.TransferError):
            configure(self.a, "origin", branch)

    def test_manual_download_never_publishes_local_sessions(self):
        remote = self.make_remote()
        core.export_sessions(self.a, self.ha)
        exchange(self.a)
        local_only = fixture(self.b, text="Private B session not requested for upload")
        put(self.hb, local_only)
        core.export_sessions(self.b, self.hb)
        before = core.git(remote, "rev-parse", "refs/heads/codex-sessions").stdout
        result = perform(parser().parse_args(["-C", str(self.b), "--home", str(self.hb), "download", "--no-reconcile"]))
        self.assertFalse(result["git"]["pushed"])
        self.assertNotIn("export", result)
        self.assertIn(self.sid, result["import"]["imported"])
        self.assertEqual(core.git(remote, "rev-parse", "refs/heads/codex-sessions").stdout, before)
        remote_files = core.git(remote, "ls-tree", "-r", "--name-only", "refs/heads/codex-sessions").stdout.decode()
        self.assertNotIn(local_only[0]["payload"]["id"], remote_files)

    def test_manual_upload_does_not_import_remote_chats(self):
        self.make_remote()
        remote_only = fixture(self.b, text="Remote-only session")
        put(self.hb, remote_only)
        core.export_sessions(self.b, self.hb)
        exchange(self.b)
        before = {str(p): p.read_bytes() for p in core.session_files(self.ha)}
        with patch("codex_project_transfer.cli.native_repair", side_effect=AssertionError("native import")):
            result = perform(parser().parse_args(["-C", str(self.a), "--home", str(self.ha), "upload"]))
        self.assertTrue(result["git"]["pushed"])
        self.assertNotIn("import", result)
        self.assertEqual({str(p): p.read_bytes() for p in core.session_files(self.ha)}, before)

    def test_refuse_unrelated_remote_tree(self):
        self.make_remote()
        (self.a / "code.txt").write_text("important")
        core.git(self.a, "add", "code.txt")
        core.git(self.a, "-c", "user.name=Test", "-c", "user.email=test@localhost", "commit", "-qm", "code")
        core.git(self.a, "push", "origin", "HEAD:refs/heads/codex-sessions")
        with self.assertRaises(core.TransferError):
            exchange(self.b)

    def test_two_device_divergence_converges_without_losing_branches(self):
        self.make_remote()
        core.export_sessions(self.a, self.ha)
        exchange(self.a)
        exchange(self.b)
        core.import_sessions(self.b, self.hb)
        put(self.ha, append(self.rows, "A changed"))
        path_b = next(core.session_files(self.hb))
        core.atomic_write(path_b, core.serialize(append(core.read_rollout(path_b), "B changed")))
        core.export_sessions(self.a, self.ha)
        core.export_sessions(self.b, self.hb)
        exchange(self.a)
        exchange(self.b)
        exchange(self.a)
        self.assertFalse(exchange(self.b)["pushed"])
        self.assertFalse(exchange(self.a)["pushed"])
        primary = (self.a / core.STORE / "sessions" / (self.sid + ".jsonl")).read_text()
        conflicts = "\n".join(p.read_text() for p in (self.a / core.STORE / "conflicts").rglob("*.jsonl"))
        self.assertIn("A changed", primary + conflicts)
        self.assertIn("B changed", primary + conflicts)

    def test_push_race_is_rejected_without_destroying_local_snapshot(self):
        self.make_remote()
        core.export_sessions(self.a, self.ha)
        exchange(self.a)
        exchange(self.b)
        core.import_sessions(self.b, self.hb)
        put(self.ha, append(self.rows, "A wins race"))
        core.export_sessions(self.a, self.ha)
        real_git = core.git

        def race(root, *args, **kwargs):
            if args[0] == "push" and root == self.b:
                exchange(self.a)
            return real_git(root, *args, **kwargs)

        path = next(core.session_files(self.hb))
        core.atomic_write(path, core.serialize(append(core.read_rollout(path), "B retained")))
        core.export_sessions(self.b, self.hb)
        with patch("codex_project_transfer.transport.git", side_effect=race):
            with self.assertRaises(core.TransferError):
                exchange(self.b)
        self.assertIn("B retained", (self.b / core.STORE / "sessions" / (self.sid + ".jsonl")).read_text())
        exchange(self.b)
        exchange(self.a)
        self.assertFalse(exchange(self.b)["pushed"])


if __name__ == "__main__":
    unittest.main()
