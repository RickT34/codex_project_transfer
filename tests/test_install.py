"""Exercise the distributed package outside its build checkout and environment."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import venv

from codex_project_transfer import __version__
from test_transfer import fixture, put


class InstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.base = Path(temporary.name)
        source = cls.base / "source with spaces"
        original = Path(__file__).resolve().parents[1]
        shutil.copytree(original / "src", source / "src",
                        ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
        for name in ("pyproject.toml", "README.en.md", "LICENSE"):
            shutil.copyfile(original / name, source / name)
        cls.env = dict(os.environ)
        cls.env.pop("PYTHONPATH", None)
        cls.env.pop("PYTHONHOME", None)
        # Build both formats, including a wheel from the unpacked sdist.
        subprocess.run([sys.executable, "-m", "build", "--no-isolation", str(source)],
                       env=cls.env, check=True, capture_output=True, text=True, timeout=120)
        environment = cls.base / "installed environment"
        venv.EnvBuilder(with_pip=True).create(environment)
        commands = environment / ("Scripts" if os.name == "nt" else "bin")
        cls.python = commands / ("python.exe" if os.name == "nt" else "python")
        cls.env["PATH"] = str(commands) + os.pathsep + cls.env.get("PATH", "")
        subprocess.run([str(cls.python), "-I", "-m", "pip", "install", "--no-index",
                        "--no-deps", str(next((source / "dist").glob("*.whl")))],
                       env=cls.env, check=True, capture_output=True, text=True, timeout=60)
        shutil.rmtree(source)
        cls.command = commands / ("cpt.exe" if os.name == "nt" else "cpt")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=self.base)
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.repo = self.work / "project with spaces"
        self.repo.mkdir()
        self.home = self.work / "codex home"
        self.env = dict(self.env, CODEX_HOME=str(self.home))
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True,
                       capture_output=True, timeout=15)

    def run_cli(self, *args):
        return subprocess.run([str(self.command), *args], cwd=self.repo, env=self.env,
                              capture_output=True, text=True, timeout=30)

    def test_installed_entry_points_work_without_source_or_activation(self):
        self.assertEqual(Path(shutil.which("cpt", path=self.env["PATH"])), self.command)
        version = self.run_cli("--version")
        self.assertEqual(version.returncode, 0, version.stderr)
        self.assertEqual(version.stdout.strip(), __version__)
        module = subprocess.run([str(self.python), "-I", "-m", "codex_project_transfer", "--version"],
                                cwd=self.repo, env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(module.returncode, 0, module.stderr)
        self.assertEqual(module.stdout.strip(), __version__)
        help_result = self.run_cli("import", "--help")
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("--dry-run", help_result.stdout)

    def test_installed_command_transfers_only_when_requested(self):
        rows = fixture(self.repo)
        sid = rows[0]["payload"]["id"]
        source = put(self.home, rows)
        original = source.read_bytes()
        initialized = self.run_cli("init")
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        snapshot = self.repo / ".codex-chats/sessions" / (sid + ".jsonl")
        self.assertFalse(snapshot.exists(), "init must not export conversations")
        self.assertFalse((self.repo / ".codex/hooks.json").exists())
        self.assertFalse((self.repo / ".git/hooks/post-merge").exists())

        exported = self.run_cli("export", "./")
        self.assertEqual(exported.returncode, 0, exported.stderr)
        self.assertIn(sid, json.loads(exported.stdout)["exported"])
        self.assertIn("blue-orchid", snapshot.read_text())
        receiver = self.work / "receiving home"
        args = ("import", "./", "--home", str(receiver), "--provider", "openai", "--no-reconcile")
        preview = self.run_cli(*args, "--dry-run")
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertFalse(list(receiver.rglob("*.jsonl")), "preview must not write sessions")
        imported = self.run_cli(*args)
        self.assertEqual(imported.returncode, 0, imported.stderr)
        self.assertIn(sid, json.loads(imported.stdout)["imported"])
        restored = list((receiver / "sessions").rglob("*.jsonl"))
        self.assertEqual(len(restored), 1)
        restored_rows = [json.loads(line) for line in restored[0].read_text().splitlines()]
        self.assertEqual(restored_rows[0]["payload"]["id"], sid)
        self.assertEqual(restored_rows[1:], rows[1:])
        self.assertEqual(source.read_bytes(), original, "export must preserve native history")
