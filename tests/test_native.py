"""Explicit opt-in integration against the installed Codex binary. No model turns."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
import uuid

from codex_project_transfer import core
from codex_project_transfer.native import NativeClient, reconcile, effective_provider
from test_transfer import fixture, put


def paginated_fixture(root):
    rows = fixture(root)
    sid = rows[0]["payload"]["id"]
    turn = str(uuid.uuid4())
    rows[0]["payload"]["history_mode"] = "paginated"
    rows.insert(1, {"timestamp": "2026-09-28T00:00:00Z", "type": "event_msg", "payload": {
        "type": "task_started", "turn_id": turn, "root_turn_id": turn,
        "model_context_window": 100000, "collaboration_mode_kind": "default"}})
    rows.extend([
        {"timestamp": "2026-09-28T00:00:05Z", "type": "event_msg", "payload": {
            "type": "item_completed", "thread_id": sid, "turn_id": turn,
            "item": {"type": "UserMessage", "id": "user-1", "content": [
                {"type": "text", "text": "Remember blue-orchid", "text_elements": []}]},
            "started_at_ms": 1790553600000, "completed_at_ms": 1790553600001}},
        {"timestamp": "2026-09-28T00:00:06Z", "type": "event_msg", "payload": {
            "type": "item_completed", "thread_id": sid, "turn_id": turn,
            "item": {"type": "AgentMessage", "id": "agent-1", "content": [
                {"type": "Text", "text": "Recorded."}], "phase": "final_answer"},
            "started_at_ms": 1790553600002, "completed_at_ms": 1790553600003}},
        {"timestamp": "2026-09-28T00:00:07Z", "type": "event_msg", "payload": {
            "type": "task_complete", "turn_id": turn, "last_agent_message": "Recorded."}},
    ])
    for index, row in enumerate(rows):
        row["ordinal"] = index
    return rows


@unittest.skipUnless(os.environ.get("CPT_TEST_NATIVE") == "1" and shutil.which("codex"),
                     "Set CPT_TEST_NATIVE=1 to test the installed Codex binary")
class NativeTests(unittest.TestCase):
    def test_storage_version_suffix_restores_as_a_complete_parent_on_receiver(self):
        with tempfile.TemporaryDirectory(prefix="cpt-native-version-") as directory:
            base = Path(directory).resolve()
            source, destination, outside = base / "source", base / "destination", base / "other-project"
            ha, hb = base / "home-a", base / "home-b"
            for root in (source, destination):
                root.mkdir(); core.git(root, "init", "-q"); core.initialize(root)
            outside.mkdir(); hb.mkdir()
            parent = paginated_fixture(outside)
            path = put(ha, parent)
            with NativeClient(ha, cwd=source) as client:
                child = client.call("thread/fork", {"threadId": parent[0]["payload"]["id"],
                                                     "cwd": str(source), "excludeTurns": True})["thread"]["id"]
            version = str(uuid.uuid4())
            path.rename(path.with_name(path.stem + "_" + version + ".jsonl"))
            child_path = next(p for p in core.session_files(ha) if core.read_rollout(p)[0]["payload"]["id"] == child)
            rows = core.read_rollout(child_path)
            rows[0]["payload"]["history_base"]["thread_id"] = version
            rows.append({"timestamp": "2026-10-13T00:00:00Z", "ordinal": rows[-1]["ordinal"] + 1,
                         "type": "event_msg", "payload": {"type": "user_message", "message": "Continue the branch", "images": []}})
            core.atomic_write(child_path, core.serialize(rows))
            exported = core.export_sessions(source, ha)
            sources = [core.read_rollout(source / core.STORE / "sessions" / (sid + ".jsonl"))[0]["payload"]["cpt_history_source"]["thread_id"]
                       for sid in exported["dependencies"]]
            self.assertIn(version, sources)
            shutil.copytree(source / core.STORE, destination / core.STORE, dirs_exist_ok=True)
            core.import_sessions(destination, hb, provider="openai")
            self.assertIn(child, reconcile(hb, [child], cwd=destination)["verified"])
            with NativeClient(hb, cwd=destination) as client:
                client.call("thread/resume", {"threadId": child, "excludeTurns": True,
                                               "approvalPolicy": "never", "sandbox": "read-only"})
                history = client.call("thread/items/list", {"threadId": child, "limit": 100})
                self.assertIn("blue-orchid", json.dumps(history))

    def test_real_nested_fork_restores_inherited_messages_and_destination_settings(self):
        with tempfile.TemporaryDirectory(prefix="cpt-native-forks-") as directory:
            base = Path(directory).resolve()
            source = base / "project-a"
            destination = base / "much longer project-b 名称"
            outside = base / "ancestor-project"
            ha, hb = base / "home-a", base / "home-b"
            for root in (source, destination):
                root.mkdir(); core.git(root, "init", "-q"); core.initialize(root)
            outside.mkdir(); hb.mkdir()
            parent = paginated_fixture(outside)
            parent_path = put(ha, parent)
            with NativeClient(ha, cwd=source) as client:
                branch = client.call("thread/fork", {"threadId": parent[0]["payload"]["id"],
                                                      "cwd": str(outside), "excludeTurns": True})["thread"]["id"]
                leaf = client.call("thread/fork", {"threadId": branch, "cwd": str(source),
                                                    "excludeTurns": True})["thread"]["id"]
            leaf_path = next(p for p in core.session_files(ha) if core.read_rollout(p)[0]["payload"]["id"] == leaf)
            leaf_rows = core.read_rollout(leaf_path)
            leaf_rows.append({"timestamp": "2026-10-13T00:00:00Z", "ordinal": leaf_rows[-1]["ordinal"] + 1,
                              "type": "event_msg", "payload": {"type": "user_message", "message": "Continue the branch", "images": []}})
            core.atomic_write(leaf_path, core.serialize(leaf_rows))
            # A required ancestor is exported only through the referenced cutoff.
            later = {"timestamp": "2026-10-13T00:00:00Z", "ordinal": parent[-1]["ordinal"] + 1,
                     "type": "event_msg", "payload": {"type": "user_message", "message": "AFTER FORK", "images": []}}
            core.atomic_write(parent_path, core.serialize(parent + [later]))
            exported = core.export_sessions(source, ha)
            sources = {core.read_rollout(source / core.STORE / "sessions" / (sid + ".jsonl"))[0]["payload"]["cpt_history_source"]["thread_id"]
                       for sid in exported["dependencies"]}
            self.assertEqual(sources, {parent[0]["payload"]["id"], branch})
            shutil.copytree(source / core.STORE, destination / core.STORE, dirs_exist_ok=True)
            (hb / "config.toml").write_text('model_provider="receiver"\n'
                '[model_providers.receiver]\nname="Synthetic receiver"\n'
                'base_url="http://127.0.0.1:1/v1"\nwire_api="responses"\n')
            imported = core.import_sessions(destination, hb, provider="receiver")
            self.assertEqual(len(imported["imported"]), 3)
            verification = reconcile(hb, [leaf], cwd=destination)
            self.assertIn(leaf, verification["verified"], verification)
            with NativeClient(hb, cwd=destination) as client:
                metadata = client.call("thread/read", {"threadId": leaf})["thread"]
                self.assertEqual(metadata["modelProvider"], "receiver")
                self.assertEqual(Path(metadata["cwd"]).resolve(), destination)
                resumed = client.call("thread/resume", {"threadId": leaf, "excludeTurns": True,
                                                         "approvalPolicy": "never", "sandbox": "read-only"})
                self.assertEqual(resumed["modelProvider"], "receiver")
                self.assertEqual(Path(resumed["cwd"]).resolve(), destination)
                history = client.call("thread/items/list", {"threadId": leaf, "limit": 100})
                self.assertIn("blue-orchid", json.dumps(history))
                self.assertNotIn("AFTER FORK", json.dumps(history))
            self.assertEqual(core.export_sessions(destination, hb)["conflicts"], [])

    def transfer_and_resume(self, builder, paginated=False, cross_provider=False):
        with tempfile.TemporaryDirectory(prefix="cpt-native-test-") as directory:
            base = Path(directory)
            a, b = base / "source-project", base / "destination-project"
            ha, hb = base / "source-home", base / "destination-home"
            for root in (a, b):
                root.mkdir()
                core.git(root, "init", "-q")
                core.initialize(root)
            rows = builder(a)
            if cross_provider:
                rows[0]["payload"]["model_provider"] = "source-only"
            sid = rows[0]["payload"]["id"]
            put(ha, rows)
            with NativeClient(ha, cwd=a) as client:
                client.call("thread/read", {"threadId": sid, "includeTurns": False})
                client.call("thread/name/set", {"threadId": sid, "name": "Synthetic portable session"})
            core.export_sessions(a, ha)
            shutil.copytree(a / core.STORE, b / core.STORE, dirs_exist_ok=True)
            archived = (b / core.STORE / "sessions" / (sid + ".jsonl")).read_bytes()
            provider = None
            if cross_provider:
                hb.mkdir()
                (hb / "config.toml").write_text('model_provider = "destination"\nmodel = "destination-model"\n'
                    '[model_providers.destination]\nname = "Synthetic destination"\n'
                    'base_url = "http://127.0.0.1:1/v1"\nwire_api = "responses"\n')
                provider = effective_provider(hb, b)
                self.assertEqual(provider, "destination")
            imported = core.import_sessions(b, hb, provider=provider)
            if cross_provider:
                self.assertEqual(imported["provider_adapted"], [sid])
            from codex_project_transfer.cli import native_repair
            repaired = native_repair(b, hb, [sid])
            self.assertEqual(repaired["verified"], [sid])
            self.assertEqual(repaired["titles_restored"], [sid])
            self.assertFalse(repaired["ui_refresh"]["automatic"])
            self.assertEqual((b / core.STORE / "sessions" / (sid + ".jsonl")).read_bytes(), archived)
            with NativeClient(hb) as client:
                metadata = client.call("thread/read", {"threadId": sid, "includeTurns": False})
                self.assertEqual(metadata["thread"]["name"], "Synthetic portable session")
                self.assertEqual(Path(metadata["thread"]["cwd"]).resolve(), b.resolve())
                resumed = client.call("thread/resume", {"threadId": sid, "cwd": str(b),
                    "approvalPolicy": "never", "sandbox": "read-only", "excludeTurns": True})
                self.assertEqual(resumed["thread"]["id"], sid)
                if cross_provider:
                    self.assertEqual(resumed["modelProvider"], "destination")
                    self.assertEqual(resumed["model"], "destination-model")
                    listing = client.call("thread/list", {"limit": 100, "useStateDbOnly": True})
                    self.assertIn(sid, [t["id"] for t in listing["data"]])
                if paginated:
                    history = client.call("thread/items/list", {"threadId": sid, "limit": 100})
                else:
                    history = client.call("thread/read", {"threadId": sid, "includeTurns": True})
                self.assertIn("blue-orchid", json.dumps(history))
                self.assertIn("Recorded.", json.dumps(history))

    def test_legacy_history_native_read_and_resume(self):
        self.transfer_and_resume(fixture)

    def test_paginated_history_native_items_and_resume(self):
        self.transfer_and_resume(paginated_fixture, paginated=True)

    def test_cross_provider_legacy_import_and_resume(self):
        self.transfer_and_resume(fixture, cross_provider=True)

    def test_cross_provider_paginated_import_and_resume(self):
        self.transfer_and_resume(paginated_fixture, paginated=True, cross_provider=True)

    def test_provider_resolution_respects_native_project_trust(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "repo"
            home = Path(directory).resolve() / "home"
            root.mkdir(); home.mkdir()
            core.git(root, "init", "-q")
            (root / ".codex").mkdir()
            (root / ".codex/config.toml").write_text('model_provider="project-provider"\n')
            (home / "config.toml").write_text('model_provider="openai"\n'
                '[model_providers.project-provider]\nname="Project provider"\n'
                'base_url="http://127.0.0.1:1/v1"\nwire_api="responses"\n')
            self.assertEqual(effective_provider(home, root), "openai")
