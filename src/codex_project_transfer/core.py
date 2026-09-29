"""Portable rollout snapshots. No credentials, database writes, or shell evaluation."""

from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sqlite3
import subprocess
import tempfile
import uuid

STORE = ".codex-chats"
TOKEN = "${CPT_PROJECT_ROOT}"
FORMAT = "codex-project-transfer/v1"
MAX_BYTES = 128 * 1024 * 1024


class TransferError(Exception):
    pass


def git(root, *args, input=None, env=None, check=True):
    p = subprocess.run(["git", "-C", str(root), *args], input=input,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       env=env, timeout=60)
    if check and p.returncode:
        raise TransferError(p.stderr.decode(errors="replace").strip() or "Git command failed")
    return p


def repository(path):
    return Path(git(path, "rev-parse", "--show-toplevel").stdout.decode().strip()).resolve()


def local_dir(root):
    # Shared across this repository's worktrees, never committed.
    value = git(root, "rev-parse", "--git-common-dir").stdout.decode().strip()
    p = Path(value)
    return (p if p.is_absolute() else root / p).resolve() / "cpt"


def home_path(value=None):
    return Path(value or os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser().resolve()


def safe_child(base, relative):
    relative = PurePosixPath(relative)
    if relative.is_absolute() or ".." in relative.parts or "\\" in str(relative):
        raise TransferError(f"Unsafe relative path: {relative}")
    target = base.joinpath(*relative.parts)
    current = target
    while current != base.parent:
        if current.is_symlink():
            raise TransferError(f"Refusing symlink: {current}")
        if current == base:
            break
        current = current.parent
    if not target.resolve().is_relative_to(base.resolve()):
        raise TransferError("Path escaped its data directory")
    return target


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".cpt-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def json_write(path, value):
    atomic_write(path, encode(value) + b"\n")


@contextlib.contextmanager
def locked(path):
    """An OS-released lock: process crashes cannot leave a stale lock behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as f:
        f.seek(0)
        if not f.read(1):
            f.write(b"0")
            f.flush()
        f.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise TransferError("Another CPT operation is running; retry shortly") from exc
        try:
            yield
        finally:
            f.seek(0)
            if os.name == "nt":
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def validate_id(value):
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError()
    except (ValueError, TypeError, AttributeError) as exc:
        raise TransferError("Unsupported session ID: expected a canonical UUID") from exc
    return value


def reject_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


def parse(data, partial=False):
    if len(data) > MAX_BYTES:
        raise TransferError("Session exceeds the 128 MiB per-file limit")
    if partial and not data.endswith(b"\n"):
        # Rollouts are live append logs. Never export a partially written final record.
        data = data[:data.rfind(b"\n") + 1]
    try:
        rows = [json.loads(line, parse_constant=reject_constant) for line in data.splitlines() if line.strip()]
    except (ValueError, UnicodeError) as exc:
        raise TransferError("Invalid or incomplete JSONL session") from exc
    if not rows or not all(isinstance(row, dict) for row in rows):
        raise TransferError("Empty or unsupported rollout")
    meta = rows[0].get("payload")
    if rows[0].get("type") != "session_meta" or not isinstance(meta, dict):
        raise TransferError("Rollout must begin with session_meta")
    sid = validate_id(meta.get("id"))
    if meta.get("session_id", sid) != sid:
        raise TransferError("Session metadata IDs disagree")
    if not isinstance(meta.get("cwd"), str):
        raise TransferError("Session metadata has no working directory")
    mode = meta.get("history_mode", "legacy")
    if mode not in ("legacy", "paginated"):
        raise TransferError(f"Unsupported history mode: {mode}")
    if mode == "paginated":
        ordinals = [r.get("ordinal") for r in rows]
        if any(type(o) is not int for o in ordinals) or any(a >= b for a, b in zip(ordinals, ordinals[1:])):
            raise TransferError("Paginated history requires increasing integer ordinals")
    return rows


def read_rollout(path, partial=False):
    if path.is_symlink():
        raise TransferError(f"Refusing symlink: {path}")
    with open(path, "rb") as f:
        return parse(f.read(MAX_BYTES + 1), partial)


def serialize(rows):
    return b"".join(encode(row) + b"\n" for row in rows)


def roots_for(root):
    roots = [root]
    listing = git(root, "worktree", "list", "--porcelain", "-z").stdout
    for field in listing.split(b"\0"):
        if field.startswith(b"worktree "):
            roots.append(Path(os.fsdecode(field[9:])).resolve())
    return sorted(set(roots), key=lambda p: len(str(p)), reverse=True)


def portable_path(value, roots):
    if not isinstance(value, str):
        return value
    if value == TOKEN or value.startswith(TOKEN + "/"):
        return value
    for root in roots:
        try:
            relative = Path(value).resolve().relative_to(root.resolve())
            return TOKEN + ("/" + relative.as_posix() if relative.parts else "")
        except ValueError:
            pass
    return value


def native_path(value, root):
    if not isinstance(value, str):
        return value
    if value == TOKEN:
        return str(root)
    if value.startswith(TOKEN + "/"):
        return str(safe_child(root, value[len(TOKEN) + 1:]))
    return value


def is_portable(value):
    return isinstance(value, str) and (value == TOKEN or value.startswith(TOKEN + "/"))


def map_paths(rows, mapper):
    """Map execution metadata only. Never rewrite messages or tool output."""
    rows = copy.deepcopy(rows)
    for row in rows:
        if row.get("type") not in ("session_meta", "turn_context"):
            continue
        payload = row.get("payload", {})
        if not isinstance(payload, dict):
            continue
        if "cwd" in payload:
            payload["cwd"] = mapper(payload["cwd"])
        for field in ("runtime_workspace_roots", "workspace_roots"):
            if isinstance(payload.get(field), list):
                payload[field] = [mapper(p) for p in payload[field]]
    return rows


def relation(existing, incoming):
    # Compare serialized JSON rather than Python equality (True != 1 in JSON).
    for index, (a, b) in enumerate(zip(existing, incoming)):
        if index == 0 and a.get("type") == b.get("type") == "session_meta":
            # The header provider is a receiving-machine routing choice, not
            # conversation content. Provider fields in messages/turns still count.
            a, b = copy.deepcopy(a), copy.deepcopy(b)
            a["payload"].pop("model_provider", None)
            b["payload"].pop("model_provider", None)
        if encode(a) != encode(b):
            return "diverged"
    if len(existing) == len(incoming):
        return "equal"
    return "ahead" if len(existing) > len(incoming) else "extends"


def store_dir(root):
    return safe_child(root, STORE)


def initialize(root):
    store = store_dir(root)
    manifest = safe_child(store, "manifest.json")
    if manifest.exists():
        if json.loads(manifest.read_text()).get("format") != FORMAT:
            raise TransferError("Unknown project session format")
    else:
        json_write(manifest, {"format": FORMAT})
    return store


def check_store(root):
    store = store_dir(root)
    manifest = safe_child(store, "manifest.json")
    if not manifest.exists() or json.loads(manifest.read_text()).get("format") != FORMAT:
        raise TransferError("Missing or unsupported session store; run cpt init")
    return store


def merge_snapshot(store, rows):
    sid = rows[0]["payload"]["id"]
    if not is_portable(rows[0]["payload"]["cwd"]):
        raise TransferError("Snapshot does not belong to a portable project")
    # Validate mapped paths even before importing, including Windows traversal.
    map_paths(rows, lambda v: native_path(v, store))
    target = safe_child(store, f"sessions/{sid}.jsonl")
    result = "added"
    if target.exists():
        previous = read_rollout(target)
        # Preserve the archive's original provider when exporting a locally
        # adapted continuation. Never rewrite historical turn/model fields.
        rows = copy.deepcopy(rows)
        original_provider = previous[0]["payload"].get("model_provider")
        if original_provider is None:
            rows[0]["payload"].pop("model_provider", None)
        else:
            rows[0]["payload"]["model_provider"] = original_provider
        result = relation(previous, rows)
        if result in ("equal", "ahead"):
            return result
        if result == "diverged":
            # Deterministic primary selection makes device exchanges converge.
            candidates = sorted((hashlib.sha256(serialize(r)).hexdigest(), serialize(r))
                                for r in (previous, rows))
            conflict = safe_child(store, f"conflicts/{sid}/{candidates[1][0]}.jsonl")
            changed = False
            if not conflict.exists():
                atomic_write(conflict, candidates[1][1])
                changed = True
            if target.read_bytes() != candidates[0][1]:
                atomic_write(target, candidates[0][1])
                changed = True
            return "diverged" if changed else "conflict_known"
    atomic_write(target, serialize(rows))
    return result


def session_files(home):
    for folder in ("sessions", "archived_sessions"):
        base = safe_child(home, folder)
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.jsonl")):
            yield safe_child(home, path.relative_to(home).as_posix())


def metadata_titles(home):
    """Best-effort read-only metadata; a changing SQLite schema is not imported."""
    result = {}
    for path in sorted(home.glob("state_*.sqlite")):
        if path.is_symlink():
            continue
        try:
            with contextlib.closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)) as c:
                for sid, title in c.execute("SELECT id, title FROM threads"):
                    if title:
                        result[sid] = title
        except sqlite3.Error:
            continue
    return result


def export_sessions(root, home):
    store = check_store(root)
    roots = roots_for(root)
    result = {"exported": [], "conflicts": [], "warnings": []}
    titles = metadata_titles(home)
    for path in session_files(home):
        try:
            # Most sessions belong to other repos. Avoid reading their contents.
            with open(path, "rb") as f:
                first = f.readline(MAX_BYTES + 1)
            first_rows = parse(first)
            cwd = first_rows[0]["payload"]["cwd"]
            if not is_portable(portable_path(cwd, roots)):
                continue
            rows = map_paths(read_rollout(path, partial=True), lambda v: portable_path(v, roots))
            sid = rows[0]["payload"]["id"]
            outcome = merge_snapshot(store, rows)
            if outcome == "diverged":
                result["conflicts"].append(sid)
            elif outcome in ("added", "extends"):
                result["exported"].append(sid)
            if sid in titles:
                title_path = safe_child(store, f"titles/{sid}.json")
                data = encode({"title": titles[sid]}) + b"\n"
                # Initial labels travel; ongoing renames remain client-local.
                if not title_path.exists():
                    atomic_write(title_path, data)
        except (OSError, TransferError, ValueError) as exc:
            result["warnings"].append(f"{path.name}: {exc}")
    return result


def store_sessions(store):
    for path in sorted(safe_child(store, "sessions").glob("*.jsonl")):
        rows = read_rollout(safe_child(store, path.relative_to(store).as_posix()))
        sid = rows[0]["payload"]["id"]
        if path.stem != sid:
            raise TransferError("Snapshot filename and session ID disagree")
        yield path, rows


def import_sessions(root, home, *, update_existing=False, exclude=(), dry_run=False, provider=None):
    if provider is not None and (not isinstance(provider, str) or not provider.strip() or "\n" in provider or "\r" in provider):
        raise TransferError("Target provider must be a nonempty provider ID")
    if dry_run:
        return _import_sessions(root, home, update_existing=update_existing, exclude=exclude, dry_run=True, provider=provider)
    with locked(safe_child(home, "cpt-import.lock")):
        return _import_sessions(root, home, update_existing=update_existing, exclude=exclude, provider=provider)


def _import_sessions(root, home, *, update_existing=False, exclude=(), dry_run=False, provider=None):
    store = check_store(root)
    roots = roots_for(root)
    known = {}
    for path in session_files(home):
        with open(path, "rb") as f:
            try:
                meta = parse(f.readline(MAX_BYTES + 1))[0]["payload"]
            except TransferError:
                continue
        known.setdefault(meta["id"], []).append(path)
    result = {"imported": [], "unchanged": [], "pending": [], "conflicts": [], "warnings": [],
              "provider": provider, "provider_adapted": []}
    plans = []
    for _, portable in store_sessions(store):
        sid = portable[0]["payload"]["id"]
        if sid in exclude:
            continue
        if not is_portable(portable[0]["payload"]["cwd"]):
            raise TransferError("Snapshot has an unmapped project directory")
        incoming = map_paths(portable, lambda v: native_path(v, root))
        if provider is not None:
            incoming[0]["payload"]["model_provider"] = provider
        matches = known.get(sid, [])
        if len(matches) > 1:
            result["warnings"].append(f"{sid}: multiple local rollouts; not modified")
            continue
        from datetime import datetime
        try:
            stamp = datetime.fromisoformat(portable[0]["payload"].get("timestamp", "").replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            raise TransferError("Session metadata has an invalid timestamp") from None
        target = matches[0] if matches else safe_child(home, f"sessions/{stamp:%Y/%m/%d}/rollout-{stamp:%Y-%m-%dT%H-%M-%S}-{sid}.jsonl")
        original = target.read_bytes() if matches else None
        if original is not None:
            local = parse(original)
            canonical = map_paths(local, lambda v: portable_path(v, roots))
            rel = relation(canonical, portable)
            if rel in ("equal", "ahead"):
                result["unchanged"].append(sid)
                continue
            if rel == "diverged":
                result["conflicts"].append(sid)
                continue
            if not update_existing:
                result["pending"].append(sid)
                continue
            # Preserve the existing native session's routing and bytes. Only
            # append new conversation records; provider adaptation is for new imports.
            data = original + (b"\n" if original and not original.endswith(b"\n") else b"")
            data += serialize(incoming[len(local):])
        else:
            data = serialize(incoming)
            if provider is not None and portable[0]["payload"].get("model_provider") != provider:
                result["provider_adapted"].append(sid)
        plans.append((sid, target, original, data))
        result["imported"].append(sid)
    if dry_run:
        return result
    for sid, target, original, data in plans:
        # Catch a session changed since planning. For updating existing rollouts,
        # the caller must close clients; this check isn't a cross-process lock.
        if (target.read_bytes() if target.exists() else None) != original:
            raise TransferError(f"{sid}: local session changed during import; retry after closing clients")
        if original is not None:
            digest = hashlib.sha256(original).hexdigest()
            backup = safe_child(home, f"cpt-backups/{sid}/{digest}.jsonl")
            if not backup.exists():
                atomic_write(backup, original)
        atomic_write(target, data)
    return result


def fork_conflict(root, sid, digest):
    validate_id(sid)
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise TransferError("Expected a full conflict SHA-256")
    store = check_store(root)
    rows = read_rollout(safe_child(store, f"conflicts/{sid}/{digest}.jsonl"))
    new_id = str(uuid.uuid5(uuid.UUID(sid), digest))
    for row in rows:
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        if row.get("type") == "session_meta":
            payload["id"] = new_id
            payload["forked_from_id"] = sid
        for key in ("session_id", "thread_id"):
            if payload.get(key) == sid:
                payload[key] = new_id
    merge_snapshot(store, rows)
    return new_id
