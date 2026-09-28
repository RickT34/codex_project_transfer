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
    def __init__(self, home, executable="codex", timeout=20):
        self.home = home
        self.executable = executable
        self.timeout = timeout
        self.messages = queue.Queue()
        self.counter = 0

    def __enter__(self):
        env = dict(os.environ, CODEX_HOME=str(self.home))
        self.logs = tempfile.TemporaryFile()
        try:
            self.process = subprocess.Popen([self.executable, "app-server", "--stdio"],
                                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=self.logs, env=env)
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


def reconcile(home, ids, executable="codex", titles=None):
    ids = sorted({validate_id(sid) for sid in ids})
    if not ids:
        return {"verified": []}
    result = {"verified": [], "warnings": []}
    with NativeClient(home, executable) as client:
        result["codex_version"] = client.version
        for sid in ids:
            thread = client.call("thread/read", {"threadId": sid, "includeTurns": False})
            if thread.get("thread", {}).get("id") != sid:
                raise TransferError("Codex returned a different thread")
            if titles and sid in titles:
                client.call("thread/name/set", {"threadId": sid, "name": titles[sid]})
        found = set()
        cursor = None
        cursors = set()
        for _ in range(1000):
            page = client.call("thread/list", {"limit": 100, "cursor": cursor,
                                               "modelProviders": [], "useStateDbOnly": True})
            found.update(t["id"] for t in page.get("data", []))
            cursor = page.get("nextCursor")
            if not cursor:
                break
            if cursor in cursors:
                raise TransferError("Codex returned a repeated pagination cursor")
            cursors.add(cursor)
        result["verified"] = sorted(set(ids) & found)
        if set(ids) - found:
            result["warnings"].append("Some threads were readable but absent from the default interactive list")
    return result
