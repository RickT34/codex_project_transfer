"""Offline, bundle-local virtual environment for the stdlib-only runtime."""
from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import venv

ROOT = Path(__file__).resolve().parents[1]


@contextlib.contextmanager
def bootstrap_lock(root):
    with (root / ".cpt-bootstrap.lock").open("a+b") as stream:
        # A Windows byte lock also blocks reads. Empty files can be locked past
        # EOF, so acquisition needs no initialization read or write.
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("Another skill runtime setup is running; retry shortly") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def ensure_runtime(root=ROOT):
    root = root.resolve()
    source = root / "src" / "codex_project_transfer"
    if not (source / "__init__.py").is_file():
        raise RuntimeError("Skill runtime source is missing; reinstall the complete skill bundle")
    environment = root / ".venv"
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if environment.is_symlink():
        raise RuntimeError("The skill .venv must be a local directory, not a symlink")
    with bootstrap_lock(root):
        if not python.exists():
            if environment.exists():
                raise RuntimeError("Existing .venv is incomplete; repair or rename it before reinstalling")
            # Runtime has no third-party dependencies: no pip, network, or global installs.
            venv.EnvBuilder(with_pip=False).create(environment)
        probe = subprocess.run([str(python), "-I", "-c",
            "import json,sys,sysconfig; print(json.dumps({'prefix':sys.prefix,'site':sysconfig.get_path('purelib'),'version':list(sys.version_info[:2])}))"],
            check=True, capture_output=True, text=True, timeout=20)
        info = json.loads(probe.stdout)
        if Path(info["prefix"]).resolve() != environment or info["version"] < [3, 10]:
            raise RuntimeError("The interpreter is not this skill's Python 3.10+ virtual environment")
        site = Path(info["site"]).resolve()
        if not site.is_relative_to(environment):
            raise RuntimeError("Virtual environment site-packages escaped the skill directory")
        site.mkdir(parents=True, exist_ok=True)
        pth = site / "codex-project-transfer-local.pth"
        # ASCII source avoids locale-dependent .pth decoding on older Windows Python.
        data = "import sys; sys.path.insert(0, " + ascii(str(root / "src")) + ")\n"
        if "\n" in str(root) or "\r" in str(root):
            raise RuntimeError("Skill path cannot contain newlines")
        if not pth.exists() or pth.read_text(encoding="utf-8") != data:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=site, delete=False) as stream:
                stream.write(data)
                temporary = stream.name
            os.replace(temporary, pth)
        loaded = subprocess.run([str(python), "-I", "-c",
            "import codex_project_transfer; print(codex_project_transfer.__file__)"],
            check=True, capture_output=True, text=True, timeout=20)
        if Path(loaded.stdout.strip()).resolve() != source / "__init__.py":
            raise RuntimeError("Virtual environment imported a different skill installation")
    return python
