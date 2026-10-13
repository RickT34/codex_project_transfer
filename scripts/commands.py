"""Create a command inside the skill and an optional POSIX PATH symlink."""
import os
from pathlib import Path
import shlex
import tempfile

MARKER = "Managed by codex-project-transfer"


def install_command(skill, python, link_dir=None):
    directory = skill / "bin"
    if directory.is_symlink():
        raise RuntimeError("The skill bin directory must not be a symlink")
    directory.mkdir(exist_ok=True)
    if os.name == "nt":
        command = directory / "cpt.cmd"
        # Keep the batch file ASCII; cmd expands its own directory as a native
        # path even when the user's profile contains non-ASCII characters.
        content = (f"@echo off\r\n:: {MARKER}\r\nsetlocal DisableDelayedExpansion\r\n"
                   '"%~dp0..\\.venv\\Scripts\\python.exe" -X utf8 -I -m codex_project_transfer %*\r\nexit /b %errorlevel%\r\n')
    else:
        command = directory / "cpt"
        content = (f"#!/bin/sh\n# {MARKER}\n"
                   f'exec {shlex.quote(str(python))} -X utf8 -I -m codex_project_transfer "$@"\n')
    if command.is_symlink() or (command.exists() and MARKER not in command.read_text(encoding="utf-8")):
        raise RuntimeError(f"Existing command was preserved: {command}")
    fd, temporary = tempfile.mkstemp(prefix=".cpt-command-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content.encode("utf-8"))
        os.chmod(temporary, 0o755)
        os.replace(temporary, command)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

    invoked = command
    if link_dir is not None:
        if os.name == "nt":
            raise RuntimeError("On Windows, add the installed skill's bin directory to PATH instead of using --link-dir")
        link_dir = link_dir.expanduser().resolve()
        link_dir.mkdir(parents=True, exist_ok=True)
        link = link_dir / "cpt"
        if link != command:
            if link.is_symlink():
                if link.resolve() != command.resolve():
                    raise RuntimeError(f"A different cpt command already exists and was preserved: {link}")
            elif link.exists():
                raise RuntimeError(f"A different cpt command already exists and was preserved: {link}")
            else:
                link.symlink_to(command)
        invoked = link
    path_entries = {os.path.normcase(str(Path(entry).expanduser().resolve()))
                    for entry in os.environ.get("PATH", "").split(os.pathsep) if entry}
    return {"command": str(invoked), "command_directory": str(invoked.parent),
            "command_on_path": os.path.normcase(str(invoked.parent)) in path_entries}
