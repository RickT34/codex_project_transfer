"""Optional Git transport on a dedicated branch; never stages the user's code."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile

from .core import (FORMAT, STORE, MAX_BYTES, TransferError, atomic_write, check_store,
                   git, is_portable, map_paths, merge_snapshot, native_path, parse, safe_child, validate_id)


def settings(root):
    values = {}
    for key, default in (("remote", ""), ("branch", "codex-sessions")):
        p = git(root, "config", "--local", "--get", f"cpt.{key}", check=False)
        values[key] = p.stdout.decode().strip() if p.returncode == 0 else default
    return values


def configure(root, remote, branch):
    if not remote or remote.startswith("-") or remote not in git(root, "remote").stdout.decode().splitlines():
        raise TransferError("Choose an existing named Git remote")
    git(root, "check-ref-format", "refs/heads/" + branch)
    current = git(root, "symbolic-ref", "--quiet", "HEAD", check=False).stdout.decode().strip()
    if current == "refs/heads/" + branch:
        raise TransferError("The session branch must differ from the code branch")
    git(root, "config", "--local", "cpt.remote", remote)
    git(root, "config", "--local", "cpt.branch", branch)


def allowed_path(path):
    if path == STORE + "/manifest.json":
        return True
    if re.fullmatch(r"\.codex-chats/(sessions/[0-9a-f-]{36}\.jsonl|titles/[0-9a-f-]{36}\.json|conflicts/[0-9a-f-]{36}/[0-9a-f]{64}\.jsonl)", path):
        return True
    return False


def exchange(root, remote=None, branch=None, *, publish=True):
    config = settings(root)
    remote = remote or config["remote"]
    branch = branch or config["branch"]
    if not remote or remote.startswith("-") or remote not in git(root, "remote").stdout.decode().splitlines():
        raise TransferError("Remote sync is not configured; use cpt init --remote origin")
    git(root, "check-ref-format", "refs/heads/" + branch)
    ref = "refs/heads/" + branch
    current = git(root, "symbolic-ref", "--quiet", "HEAD", check=False).stdout.decode().strip()
    if current == ref:
        raise TransferError("Refusing to publish over the checked-out code branch")
    # Report authentication failures to the invoking agent instead of hanging.
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    store = check_store(root)
    advertised = git(root, "ls-remote", "--exit-code", remote, ref, env=env, check=False)
    if advertised.returncode not in (0, 2):
        raise TransferError("Cannot reach Git remote: " + advertised.stderr.decode(errors="replace").strip())
    parent = None
    result = {"remote": remote, "branch": branch, "received": 0, "conflicts": [], "pushed": False}
    if advertised.returncode == 0:
        # Private ref avoids collisions with ordinary fetch/pull's FETCH_HEAD.
        git(root, "fetch", "--no-tags", "--no-write-fetch-head", remote,
            f"+{ref}:refs/cpt/incoming", env=env)
        parent = git(root, "rev-parse", "refs/cpt/incoming").stdout.decode().strip()
        entries = git(root, "ls-tree", "-r", "-z", parent).stdout.split(b"\0")
        remote_files = {}
        total = 0
        for entry in entries:
            if not entry:
                continue
            attributes, raw_path = entry.split(b"\t", 1)
            mode, kind, oid = attributes.split()
            path = raw_path.decode()
            if mode != b"100644" or kind != b"blob" or not allowed_path(path):
                raise TransferError("Session branch contains unsupported files; refusing to overwrite it")
            size = int(git(root, "cat-file", "-s", oid.decode()).stdout)
            total += size
            if size > MAX_BYTES or total > 512 * 1024 * 1024:
                raise TransferError("Remote session store exceeds transfer size limits")
            remote_files[path] = git(root, "cat-file", "blob", oid.decode()).stdout
        manifest = remote_files.get(STORE + "/manifest.json")
        if not manifest or json.loads(manifest).get("format") != FORMAT:
            raise TransferError("Remote branch is not a CPT session store")
        # Validate every remote artifact before changing local files.
        parsed = []
        for path, data in remote_files.items():
            if path.endswith(".jsonl"):
                rows = parse(data)
                sid = rows[0]["payload"]["id"]
                if not is_portable(rows[0]["payload"]["cwd"]):
                    raise TransferError("Remote snapshot has no portable project root")
                map_paths(rows, lambda v: native_path(v, root))
                if "/sessions/" in path and Path(path).stem != sid:
                    raise TransferError("Remote session filename does not match its ID")
                if "/conflicts/" in path:
                    import hashlib
                    from .core import serialize
                    if Path(path).parent.name != sid or Path(path).stem != hashlib.sha256(serialize(rows)).hexdigest():
                        raise TransferError("Remote conflict digest does not match its contents")
                parsed.append((path, rows))
            elif "/titles/" in path:
                validate_id(Path(path).stem)
                title = json.loads(data)
                if not isinstance(title.get("title"), str):
                    raise TransferError("Invalid title metadata")
        for path, rows in parsed:
            if "/conflicts/" in path:
                target = safe_child(root, path)
                if not target.exists():
                    atomic_write(target, remote_files[path])
            else:
                outcome = merge_snapshot(store, rows)
                if outcome == "diverged":
                    result["conflicts"].append(rows[0]["payload"]["id"])
                if outcome in ("added", "extends", "diverged"):
                    result["received"] += 1
        for path, data in remote_files.items():
            if "/titles/" in path:
                target = safe_child(root, path)
                # Stable tie-breaking avoids endless title-only Git commits.
                if not target.exists() or data < target.read_bytes():
                    atomic_write(target, data)
    if not publish:
        return result
    files = []
    for path in sorted(store.rglob("*")):
        if path.is_file() or path.is_symlink():
            relative = path.relative_to(root).as_posix()
            if not allowed_path(relative):
                raise TransferError(f"Unexpected file in session store: {relative}")
            files.append((relative, safe_child(root, relative).read_bytes()))
    # Build a tree in a throwaway index. The caller's index and HEAD are untouched.
    with tempfile.TemporaryDirectory(prefix="cpt-index-") as directory:
        index_env = dict(env, GIT_INDEX_FILE=str(Path(directory) / "index"))
        git(root, "read-tree", "--empty", env=index_env)
        for path, data in files:
            oid = git(root, "hash-object", "-w", "--stdin", input=data).stdout.decode().strip()
            git(root, "update-index", "--add", "--cacheinfo", f"100644,{oid},{path}", env=index_env)
        tree = git(root, "write-tree", env=index_env).stdout.decode().strip()
    if parent and git(root, "rev-parse", parent + "^{tree}").stdout.decode().strip() == tree:
        return result
    args = ["commit-tree", tree, "-m", "Sync Codex project sessions"]
    if parent:
        args += ["-p", parent]
    commit_env = dict(env, GIT_AUTHOR_NAME="Codex Project Transfer", GIT_AUTHOR_EMAIL="cpt@localhost",
                      GIT_COMMITTER_NAME="Codex Project Transfer", GIT_COMMITTER_EMAIL="cpt@localhost")
    commit = git(root, *args, env=commit_env).stdout.decode().strip()
    # No force push. If another device wins the race, preserve local data and retry next run.
    git(root, "push", remote, f"{commit}:{ref}", env=env)
    result["pushed"] = True
    return result
