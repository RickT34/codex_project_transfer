"""Exercise actual OS locks in separate processes on every CI platform."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from codex_project_transfer.core import locked

ROOT = Path(__file__).resolve().parents[1]
CHILD = """
import os, sys
from pathlib import Path
root, directory, action = sys.argv[1:]
sys.path.insert(0, str(Path(root) / 'src'))
from codex_project_transfer.core import locked, TransferError
context = locked(Path(directory) / 'operation.lock')
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
    def child(self, directory, action="acquire"):
        return subprocess.run([sys.executable, "-I", "-c", CHILD, str(ROOT),
                               directory, action], text=True, capture_output=True, timeout=15)

    def test_competing_process_gets_busy_then_acquires_after_release(self):
        with tempfile.TemporaryDirectory() as directory:
            with locked(Path(directory) / "operation.lock"):
                blocked = self.child(directory)
                self.assertEqual(blocked.returncode, 23, blocked.stderr)
                self.assertEqual(blocked.stdout.strip(), "busy")
            released = self.child(directory)
            self.assertEqual(released.returncode, 0, released.stderr)
            self.assertEqual(released.stdout.strip(), "acquired")

    def test_process_exit_releases_lock_without_context_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            exited = self.child(directory, "exit-without-cleanup")
            self.assertEqual(exited.returncode, 0, exited.stderr)
            acquired = self.child(directory)
            self.assertEqual(acquired.returncode, 0, acquired.stderr)
            self.assertEqual(acquired.stdout.strip(), "acquired")
