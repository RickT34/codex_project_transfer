from __future__ import annotations

import argparse
import csv
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from . import __version__
from .core import (STORE, TransferError, check_store, export_sessions,
                   fork_conflict, home_path, import_sessions, initialize,
                   json_write, local_dir, locked, repository, safe_child, store_sessions)
from .native import reconcile
from .transport import configure, exchange, settings


def running_clients():
    """Conservative process-name check. Unknown platforms fail closed."""
    if os.name == "nt":
        p = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=10)
        if p.returncode:
            raise TransferError("Unable to check running clients")
        names = [row[0].lower() for row in csv.reader(io.StringIO(p.stdout)) if row]
    else:
        p = subprocess.run(["ps", "-axo", "comm="], capture_output=True, text=True, timeout=10)
        if p.returncode:
            raise TransferError("Unable to check running clients")
        names = [Path(row.strip()).name.lower() for row in p.stdout.splitlines()]
    return sorted({name for name in names if name in {
        "codex", "codex.exe", "chatgpt", "chatgpt.exe", "code", "code.exe",
        "code - insiders", "code - insiders.exe", "codex-app-server", "codex-app-server.exe",
    }})


def titles_for(root, ids):
    result = {}
    store = check_store(root)
    for sid in ids:
        path = safe_child(store, f"titles/{sid}.json")
        if path.exists():
            value = json.loads(path.read_text())
            if isinstance(value.get("title"), str):
                result[sid] = value["title"]
    return result


def native_repair(root, home, ids):
    if not ids:
        return {"verified": []}
    try:
        return reconcile(home, ids, titles=titles_for(root, ids))
    except (TransferError, OSError, subprocess.SubprocessError) as exc:
        return {"verified": [], "warnings": [f"Files preserved; native discovery failed: {exc}"]}


def sync(root, home, *, use_git=False, update_existing=False, discover=True):
    result = {"export": export_sessions(root, home)}
    if use_git:
        try:
            result["git"] = exchange(root)
        except (TransferError, OSError, subprocess.SubprocessError) as exc:
            # Saving local history must succeed even when the network is down.
            result["git"] = {"error": str(exc), "retry": "next explicitly requested sync"}
    result["import"] = import_sessions(root, home, update_existing=update_existing)
    if discover:
        # Retry discoveries that failed offline or because of a missing binary.
        queue_path = local_dir(root) / ("pending-" + __import__("hashlib").sha256(str(home).encode()).hexdigest()[:16] + ".json")
        pending = set(json.loads(queue_path.read_text())) if queue_path.exists() else set()
        pending.update(result["import"]["imported"])
        result["native"] = native_repair(root, home, sorted(pending))
        pending.difference_update(result["native"].get("verified", []))
        json_write(queue_path, sorted(pending))
    json_write(local_dir(root) / "last-sync.json", {"time": time.time(), **result})
    return result


def project_status(root):
    state = local_dir(root) / "last-sync.json"
    saved = local_dir(root) / "last-save.json"
    return {"store": str(root / STORE), "transport": {**settings(root), "mode": "manual"},
            "sessions": [rows[0]["payload"]["id"] for _, rows in store_sessions(check_store(root))],
            "conflicts": [p.relative_to(root / STORE).as_posix() for p in sorted((root / STORE / "conflicts").rglob("*.jsonl"))],
            "last_sync": json.loads(state.read_text()) if state.exists() else None,
            "last_save": json.loads(saved.read_text()) if saved.exists() else None}


def parser():
    p = argparse.ArgumentParser(description="Carry native Codex sessions with a Git project")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("-C", "--project", default=".", help="Project directory (default: current directory)")
    p.add_argument("--home", help="Codex data directory (default: CODEX_HOME or ~/.codex)")
    sub = p.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Create a Git-friendly session store")
    init.add_argument("--remote", help="Remember the named remote for manually requested transfers")
    init.add_argument("--branch", default="codex-sessions", help="Dedicated remote session branch")
    sub.add_parser("export", help="Save all local sessions belonging to this repository")
    sub.add_parser("check", help="Manually save project sessions and report local status")
    sub.add_parser("upload", help="Explicitly save and upload project sessions (no native import)")
    download = sub.add_parser("download", help="Explicitly fetch and import sessions, without uploading")
    download.add_argument("--update-existing", action="store_true")
    download.add_argument("--no-reconcile", action="store_true")
    imp = sub.add_parser("import", help="Import repository sessions into Codex")
    imp.add_argument("--dry-run", action="store_true")
    imp.add_argument("--update-existing", action="store_true", help="Fast-forward existing sessions; requires closed clients")
    imp.add_argument("--no-reconcile", action="store_true")
    syn = sub.add_parser("sync", help="Explicit one-shot upload and download")
    syn.add_argument("--local", action="store_true", help="Skip remote Git exchange")
    syn.add_argument("--update-existing", action="store_true")
    syn.add_argument("--no-reconcile", action="store_true")
    run = sub.add_parser("run", help="Apply already-saved local history and start Codex; no automatic saving")
    run.add_argument("codex_args", nargs=argparse.REMAINDER)
    sub.add_parser("status", help="Show local settings, conflicts, and last sync result")
    sub.add_parser("reconcile", help="Ask Codex to discover imported sessions")
    fork = sub.add_parser("fork", help="Preserve a conflicting snapshot as a new native session")
    fork.add_argument("session_id")
    fork.add_argument("sha256")
    return p


def check_closed():
    clients = running_clients()
    if clients:
        raise TransferError("Close Codex/ChatGPT/VS Code before updating existing sessions: " + ", ".join(clients))


def perform(args):
    home = home_path(args.home)
    root = repository(args.project)
    if args.command == "run":
        check_closed()
        with locked(local_dir(root) / "sync.lock"):
            restored = import_sessions(root, home, update_existing=True)
            restored["native"] = native_repair(root, home, restored["imported"])
        print(json.dumps(restored, ensure_ascii=False), file=sys.stderr)
        command = args.codex_args
        if command[:1] == ["--"]:
            command = command[1:]
        code = subprocess.call(["codex", *command], cwd=root, env=dict(os.environ, CODEX_HOME=str(home)))
        return {"codex_exit_code": code}
    with locked(local_dir(root) / "sync.lock"):
        if args.command == "init":
            initialize(root)
            result = {"store": str(root / STORE)}
            if args.remote:
                configure(root, args.remote, args.branch)
                result["git"] = settings(root)
            return result
        check_store(root)
        if args.command in ("export", "check"):
            result = export_sessions(root, home)
            json_write(local_dir(root) / "last-save.json", {"time": time.time(), **result})
            if args.command == "check":
                return {"export": result, "status": project_status(root)}
            return result
        if args.command in ("upload", "download"):
            update = getattr(args, "update_existing", False)
            if update:
                check_closed()
            result = {}
            if args.command == "upload":
                result["export"] = export_sessions(root, home)
            try:
                # Upload reads the remote first only to preserve other devices' records.
                result["git"] = exchange(root, publish=args.command == "upload")
            except (TransferError, OSError, subprocess.SubprocessError) as exc:
                result["git"] = {"error": str(exc), "retry": "next explicitly requested transfer"}
            if args.command == "download" and not result["git"].get("error"):
                result["import"] = import_sessions(root, home, update_existing=update)
                if not args.no_reconcile:
                    result["native"] = native_repair(root, home, result["import"]["imported"])
            json_write(local_dir(root) / "last-sync.json", {"time": time.time(), **result})
            return result
        if args.command == "import":
            if args.update_existing and not args.dry_run:
                check_closed()
            result = import_sessions(root, home, update_existing=args.update_existing, dry_run=args.dry_run)
            if not args.dry_run and not args.no_reconcile:
                result["native"] = native_repair(root, home, result["imported"])
            return result
        if args.command == "sync":
            update = getattr(args, "update_existing", False)
            if update:
                check_closed()
            result = sync(root, home,
                          use_git=not args.local,
                          update_existing=update, discover=not getattr(args, "no_reconcile", False))
            return result
        if args.command == "reconcile":
            ids = [rows[0]["payload"]["id"] for _, rows in store_sessions(check_store(root))]
            return native_repair(root, home, ids)
        if args.command == "fork":
            return {"fork_id": fork_conflict(root, args.session_id, args.sha256)}
        if args.command == "status":
            return project_status(root)


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = perform(args)
        if result is not None:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        if isinstance(result, dict) and result.get("git", {}).get("error"):
            return 1
        return result.get("codex_exit_code", 0) if isinstance(result, dict) else 0
    except KeyboardInterrupt:
        return 130
    except (TransferError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"cpt: {exc}", file=sys.stderr)
        return 1
