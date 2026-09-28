#!/usr/bin/env python3
"""Prepare the local venv and register the bundled skill for agent discovery."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from runtime import ROOT, ensure_runtime


def install(skills_dir):
    python = ensure_runtime()
    source = ROOT / "skills" / "codex-project-transfer"
    target = skills_dir.expanduser().resolve() / source.name
    if target.is_symlink():
        raise RuntimeError("An existing skill symlink was preserved; choose another --skills-dir")
    registration = target / "plugin-root.json"
    if registration.is_symlink():
        raise RuntimeError("An existing skill registration symlink was preserved")
    if target.exists():
        if not registration.is_file() or json.loads(registration.read_text(encoding="utf-8")).get("root") != str(ROOT):
            raise RuntimeError("A different skill installation already exists; it was not overwritten")
    target.mkdir(parents=True, exist_ok=True)
    # Copy just the skill resources; its launcher resolves the persistent plugin root.
    for relative in ("SKILL.md", "agents/openai.yaml", "scripts/cpt.py"):
        destination = target / relative
        if destination.is_symlink() or any(p.is_symlink() for p in destination.parents if p.is_relative_to(target)):
            raise RuntimeError("Refusing a symlink in the skill installation")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, destination)
    registration.write_text(json.dumps({"root": str(ROOT)}, indent=2) + "\n", encoding="utf-8")
    return {"skill": str(target), "python": str(python), "plugin_root": str(ROOT),
            "next": "Open a new Codex chat to discover the skill"}


def main():
    parser = argparse.ArgumentParser(description="Install the skill with a plugin-local virtual environment")
    parser.add_argument("--skills-dir", type=Path,
                        default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "skills")
    args = parser.parse_args()
    try:
        print(json.dumps(install(args.skills_dir), indent=2))
        return 0
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"CPT installation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
