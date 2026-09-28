#!/usr/bin/env python3
"""Resolve either a source/plugin skill or its installed discovery copy."""
import json
from pathlib import Path
import subprocess
import sys

skill = Path(__file__).resolve().parents[1]
registration = skill / "plugin-root.json"
root = Path(json.loads(registration.read_text(encoding="utf-8"))["root"]) if registration.exists() else skill.parents[1]
launcher = root / "scripts" / "cpt.py"
if not launcher.is_file():
    raise SystemExit("Plugin directory moved or disappeared; reinstall the skill from its new location")
raise SystemExit(subprocess.call([sys.executable, str(launcher), *sys.argv[1:]]))
