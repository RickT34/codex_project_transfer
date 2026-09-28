#!/usr/bin/env python3
"""Install a complete skill, including its source and venv, into one directory."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from runtime import ROOT, ensure_runtime

NAME = "codex-project-transfer"
INSTALL_FORMAT = "codex-project-transfer-skill/v1"


def install(skills_dir):
    source = ROOT if (ROOT / "SKILL.md").is_file() else ROOT / "skills" / NAME
    target = skills_dir.expanduser().resolve() / NAME
    if target.is_symlink():
        raise RuntimeError("An existing skill symlink was preserved; choose another --skills-dir")
    marker = target / ".cpt-install.json"
    if marker.is_symlink():
        raise RuntimeError("Refusing a symlinked installation marker")
    if target.exists():
        if not marker.is_file() or json.loads(marker.read_text(encoding="utf-8")).get("format") != INSTALL_FORMAT:
            raise RuntimeError("A different skill installation already exists; it was not overwritten")
    files = {
        "SKILL.md": source / "SKILL.md",
        "agents/openai.yaml": source / "agents/openai.yaml",
        "LICENSE": ROOT / "LICENSE",
    }
    for name in ("cpt.py", "runtime.py", "install.py"):
        files[f"scripts/{name}"] = ROOT / "scripts" / name
    for path in (ROOT / "src" / "codex_project_transfer").rglob("*.py"):
        if "__pycache__" not in path.parts:
            files[path.relative_to(ROOT).as_posix()] = path
    for name in ("design.md", "validation.md"):
        files[f"docs/{name}"] = ROOT / "docs" / name
    # Preflight all paths before modifying an existing installation.
    for relative, path in files.items():
        if not path.is_file() or not path.resolve().is_relative_to(ROOT):
            raise RuntimeError(f"Required bundled file is missing or external: {relative}")
        destination = target / relative
        if destination.is_symlink() or any(p.is_symlink() for p in destination.parents if p.is_relative_to(target)):
            raise RuntimeError("Refusing a symlink in the skill installation")
    if ROOT != target:
        for relative, path in files.items():
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
    marker.write_text(json.dumps({"format": INSTALL_FORMAT}) + "\n", encoding="utf-8")
    python = ensure_runtime(target)
    return {"skill": str(target), "python": str(python),
            "next": "Open a new Codex chat to discover the skill"}


def main():
    parser = argparse.ArgumentParser(description="Install all skill files and its venv inside the skill directory")
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
