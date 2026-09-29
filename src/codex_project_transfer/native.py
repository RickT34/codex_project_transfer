"""Use Codex's own read-repair API rather than editing its internal databases."""

from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import tempfile

from .core import TransferError, validate_id
from . import __version__


class NativeClient:
    def __init__(self, home, executable="codex", timeout=20, cwd=None):
        self.home = home
        self.executable = executable
        self.timeout = timeout
        self.messages = queue.Queue()
        self.counter = 0
        self.cwd = cwd

    def __enter__(self):
        env = dict(os.environ, CODEX_HOME=str(self.home))
        self.logs = tempfile.TemporaryFile()
        try:
            self.process = subprocess.Popen([self.executable, "app-server", "--stdio"],
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
    result = {"verified": [], "warnings": []}
    with NativeClient(home, executable, cwd=cwd) as client:
        if provider is None:
            config = client.call("config/read", {"cwd": str(cwd) if cwd else None, "includeLayers": False})
            provider = config["config"].get("model_provider") or "openai"
        result["provider"] = provider
        result["codex_version"] = client.version
        for sid in ids:
            thread = client.call("thread/read", {"threadId": sid, "includeTurns": False})
            if thread.get("thread", {}).get("id") != sid:
                raise TransferError("Codex returned a different thread")
            if thread["thread"].get("modelProvider") != provider:
                result["warnings"].append(f"{sid}: recorded provider differs from {provider}; this existing native session was not adapted")
            if titles and sid in titles:
                client.call("thread/name/set", {"threadId": sid, "name": titles[sid]})
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
        for sid in sorted(set(ids) & found):
            actual = client.call("thread/read", {"threadId": sid, "includeTurns": False})
            if actual.get("thread", {}).get("modelProvider") == provider:
                result["verified"].append(sid)
        if set(ids) - set(result["verified"]):
            result["warnings"].append("Some threads were readable but absent from the destination provider's interactive list")
    return result
