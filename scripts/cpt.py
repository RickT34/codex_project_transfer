#!/usr/bin/env python3
"""Stable agent entry point; all commands run inside the installed skill's local venv."""
import os
import subprocess
import sys

from runtime import ensure_runtime


def main():
    try:
        python = ensure_runtime()
        command = [str(python), "-I", "-m", "codex_project_transfer", *sys.argv[1:]]
        if os.name == "nt":
            return subprocess.call(command)
        os.execv(str(python), command)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"CPT runtime setup failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
