"""Install the complete skill into a clean, isolated plugin checkout."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from codex_project_transfer.core import git
from test_transfer import fixture, put


class SkillInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.plugin = self.base / "plugin with spaces"
        original = Path(__file__).resolve().parents[1]
        for folder in ("scripts", "src", "skills"):
            shutil.copytree(original / folder, self.plugin / folder,
                            ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
        self.skills = self.base / "discovered skills"
        self.repo = self.base / "target project"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        self.env = dict(os.environ, PYTHONPATH="/intentionally-unavailable",
                        CODEX_HOME=str(self.base / "codex-home"))

    def tearDown(self):
        self.temp.cleanup()

    def install(self):
        return subprocess.run([sys.executable, str(self.plugin / "scripts/install.py"),
                               "--skills-dir", str(self.skills)],
                              env=self.env, text=True, capture_output=True, timeout=30)

    def test_clean_install_uses_local_venv_and_installed_skill_launcher(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        installed = json.loads(result.stdout)
        python = Path(installed["python"])
        self.assertTrue(python.is_relative_to(self.plugin / ".venv"))
        prefix = subprocess.check_output([str(python), "-I", "-c", "import sys;print(sys.prefix)"], text=True)
        self.assertEqual(Path(prefix.strip()).resolve(), self.plugin / ".venv")
        launcher = Path(installed["skill"]) / "scripts/cpt.py"
        run = subprocess.run([sys.executable, str(launcher), "--version"],
                             env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout.strip(), "0.4.0")
        # Editable registration is local and the fresh venv needs no pip packages.
        probe = subprocess.check_output([str(python), "-I", "-c",
            "import importlib.util;print(importlib.util.find_spec('pip'))"], text=True)
        self.assertEqual(probe.strip(), "None")

    def test_installed_skill_saves_only_on_manual_check_without_hooks(self):
        installed = self.install()
        self.assertEqual(installed.returncode, 0, installed.stderr)
        launcher = self.skills / "codex-project-transfer/scripts/cpt.py"
        command = [sys.executable, str(launcher), "-C", str(self.repo)]
        subprocess.run(command + ["init"], env=self.env, check=True, capture_output=True, timeout=30)
        self.assertFalse((self.repo / ".codex/hooks.json").exists())
        rows = fixture(self.repo)
        sid = rows[0]["payload"]["id"]
        put(Path(self.env["CODEX_HOME"]), rows)
        snapshot = self.repo / ".codex-chats/sessions" / (sid + ".jsonl")
        self.assertFalse(snapshot.exists())
        result = subprocess.run(command + ["check"], env=self.env, text=True,
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(sid, json.loads(result.stdout)["export"]["exported"])
        self.assertIn("blue-orchid", snapshot.read_text())
        self.assertFalse((self.repo / ".git/hooks/post-merge").exists())

    def test_reinstallation_preserves_unrelated_skill(self):
        target = self.skills / "codex-project-transfer"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("Other owner's skill")
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((target / "SKILL.md").read_text(), "Other owner's skill")

    def test_reinstall_same_plugin_is_idempotent(self):
        first = self.install()
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self.install()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(json.loads(first.stdout), json.loads(second.stdout))

