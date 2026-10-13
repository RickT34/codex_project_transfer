"""Use Codex's own read-repair API rather than editing its internal databases."""

from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import tempfile

from .core import TransferError, validate_id, native_session_index, collect_export_history
from . import __version__


class NativeClient:
    def __init__(self, home, executable="codex", timeout=20, cwd=None, disable_hooks=False):
        self.home = home
        self.executable = executable
        self.timeout = timeout
        self.messages = queue.Queue()
        self.counter = 0
        self.cwd = cwd
        self.disable_hooks = disable_hooks

    def __enter__(self):
        env = dict(os.environ, CODEX_HOME=str(self.home))
        self.logs = tempfile.TemporaryFile()
        try:
            command = [self.executable, "app-server", "--stdio"]
            if self.disable_hooks:
                command += ["--disable", "hooks"]
            self.process = subprocess.Popen(command,
                                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=self.logs, env=env, cwd=self.cwd)
        except BaseException:
            self.logs.close()
            raise

        def reader():
            try:
                for line in self.process.stdout:
                    try:
                        self.messages.put(json.loads(line))
                    except (ValueError, UnicodeError):
                        continue
            finally:
                self.messages.put(None)

        self.reader = threading.Thread(target=reader, daemon=True)
        self.reader.start()
        try:
            result = self.call("initialize", {
                "clientInfo": {"name": "codex-project-transfer", "version": __version__},
                "capabilities": {"experimentalApi": True},
            })
            actual = result.get("codexHome")
            if not actual or Path(actual).resolve() != self.home.resolve():
                raise TransferError("Codex app-server did not confirm the requested CODEX_HOME")
            self.send({"method": "initialized"})
            self.version = result.get("userAgent", "unknown")
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def send(self, message):
        self.process.stdin.write(json.dumps(message).encode() + b"\n")
        self.process.stdin.flush()

    def call(self, method, params):
        self.counter += 1
        request_id = self.counter
        self.send({"id": request_id, "method": method, "params": params})
        import time
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                message = self.messages.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty as exc:
                raise TransferError(f"Codex {method} timed out") from exc
            if message is None:
                raise TransferError(f"Codex exited before replying to {method}")
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise TransferError(f"Codex {method}: {message['error']}")
            return message.get("result", {})

    def __exit__(self, *_):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.reader.join(timeout=2)
        self.process.stdout.close()
        self.logs.close()


def effective_provider(home, cwd, executable="codex"):
    """Let Codex resolve trusted project/user/system config layers; never read keys."""
    try:
        with NativeClient(home, executable, cwd=cwd) as client:
            result = client.call("config/read", {"cwd": str(cwd), "includeLayers": False})
            provider = result["config"].get("model_provider") or "openai"
            if not isinstance(provider, str) or not provider.strip():
                raise TransferError("Codex returned an invalid model_provider")
            return provider
    except (OSError, subprocess.SubprocessError, KeyError, TransferError) as exc:
        raise TransferError("Cannot resolve the destination provider with Codex config/read; "
                            "configure Codex or pass --provider PROVIDER_ID explicitly") from exc


def reconcile(home, ids, executable="codex", titles=None, *, cwd=None, provider=None):
    ids = sorted({validate_id(sid) for sid in ids})
    if not ids:
        return {"verified": []}
    # Metadata/list visibility alone does not prove a fork's history is present.
    from .history import ordered, reference
    index, _, _ = native_session_index(home)
    bundle, _ = collect_export_history(index, ids)
    order = ordered(bundle)
    dependencies = {reference(rows)[0] for rows in bundle.values() if reference(rows)}
    storage_versions = set(bundle) - set(index)
    result = {"verified": [], "warnings": [], "prepared_dependencies": [], "titles_restored": [],
              "ui_refresh": {"automatic": False,
                             "hint": "If imported chats or titles are not visible in the open desktop app, refresh its chat list or restart it manually. Native verification does not verify the UI cache."},
              "storage_dependencies": sorted(bundle[sid][0]["payload"]["cpt_history_source"]["thread_id"] for sid in storage_versions)}
    with NativeClient(home, executable, cwd=cwd, disable_hooks=True) as client:
        if provider is None:
            config = client.call("config/read", {"cwd": str(cwd) if cwd else None, "includeLayers": False})
            provider = config["config"].get("model_provider") or "openai"
        result["provider"] = provider
        result["codex_version"] = client.version
        # A fork's inherited items live in its ancestors' native projections.
        # Initialize those recorders in ancestry order without starting a model
        # turn. Hooks are disabled for this short-lived indexing process.
        for sid in order:
            if sid in dependencies and sid not in storage_versions:
                client.call("thread/resume", {"threadId": sid, "excludeTurns": True,
                                               "modelProvider": provider,
                                               "approvalPolicy": "never", "sandbox": "read-only"})
                result["prepared_dependencies"].append(sid)
        for sid in ids:
            if sid in storage_versions:
                continue  # A pinned physical version is not a standalone chat.
            thread = client.call("thread/read", {"threadId": sid, "includeTurns": False})
            if thread.get("thread", {}).get("id") != sid:
                raise TransferError("Codex returned a different thread")
            if thread["thread"].get("modelProvider") != provider:
                result["warnings"].append(f"{sid}: recorded provider differs from {provider}; this existing native session was not adapted")
            if titles and sid in titles:
                client.call("thread/name/set", {"threadId": sid, "name": titles[sid]})
                named = client.call("thread/read", {"threadId": sid, "includeTurns": False})
                if named.get("thread", {}).get("name") == titles[sid]:
                    result["titles_restored"].append(sid)
                else:
                    result["warnings"].append(f"{sid}: native title restoration could not be verified")
        found = set()
        cursor = None
        cursors = set()
        for _ in range(1000):
            page = client.call("thread/list", {"limit": 100, "cursor": cursor,
                                               "modelProviders": [provider], "useStateDbOnly": False})
            found.update(t["id"] for t in page.get("data", []))
            cursor = page.get("nextCursor")
            if not cursor:
                break
            if cursor in cursors:
                raise TransferError("Codex returned a repeated pagination cursor")
            cursors.add(cursor)
        # Verify the actual read route as well as the provider-filtered list.
        for sid in sorted(set(ids) & (found | dependencies | storage_versions)):
            if sid in storage_versions:
                result["verified"].append(sid)  # ancestry/prefix validation above
                continue
            actual = client.call("thread/read", {"threadId": sid, "includeTurns": False})
            metadata = actual.get("thread", {})
            correct_cwd = cwd is None or (isinstance(metadata.get("cwd"), str)
                and Path(metadata["cwd"]).resolve().is_relative_to(Path(cwd).resolve()))
            if not correct_cwd:
                result["warnings"].append(f"{sid}: native working directory is outside the destination project")
            if metadata.get("modelProvider") == provider and correct_cwd:
                result["verified"].append(sid)
        if set(ids) - set(result["verified"]):
            result["warnings"].append("Some threads were readable but absent from the destination provider's interactive list")
    return result
