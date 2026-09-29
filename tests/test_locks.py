"""Exercise actual OS locks in separate processes on every CI platform."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from codex_project_transfer.core import locked

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("cpt_bootstrap_runtime", ROOT / "scripts/runtime.py")
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)

CHILD = """
import os, sys
from pathlib import Path
root, directory, kind, action = sys.argv[1:]
sys.path.insert(0, str(Path(root) / 'src'))
sys.path.insert(0, str(Path(root) / 'scripts'))
from codex_project_transfer.core import locked, TransferError
from runtime import bootstrap_lock
context = locked(Path(directory) / 'operation.lock') if kind == 'operation' else bootstrap_lock(Path(directory))
try:
    with context:
        if action == 'exit-without-cleanup':
            os._exit(0)
        print('acquired')
except (TransferError, RuntimeError):
    print('busy')
    sys.exit(23)
"""


class LockTests(unittest.TestCase):
    def child(self, directory, kind, action="acquire"):
        return subprocess.run([sys.executable, "-I", "-c", CHILD, str(ROOT),
                               directory, kind, action], text=True, capture_output=True, timeout=15)

    def context(self, directory, kind):
        root = Path(directory)
        return locked(root / "operation.lock") if kind == "operation" else runtime.bootstrap_lock(root)

    def test_competing_process_gets_busy_then_acquires_after_release(self):
        for kind in ("operation", "bootstrap"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                with self.context(directory, kind):
                    blocked = self.child(directory, kind)
                    self.assertEqual(blocked.returncode, 23, blocked.stderr)
                    self.assertEqual(blocked.stdout.strip(), "busy")
                released = self.child(directory, kind)
                self.assertEqual(released.returncode, 0, released.stderr)
                self.assertEqual(released.stdout.strip(), "acquired")

    def test_process_exit_releases_lock_without_context_cleanup(self):
        for kind in ("operation", "bootstrap"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                exited = self.child(directory, kind, "exit-without-cleanup")
                self.assertEqual(exited.returncode, 0, exited.stderr)
                acquired = self.child(directory, kind)
                self.assertEqual(acquired.returncode, 0, acquired.stderr)
                self.assertEqual(acquired.stdout.strip(), "acquired")
