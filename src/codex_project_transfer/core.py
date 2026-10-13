"""Portable rollout snapshots. No credentials, database writes, or shell evaluation."""

from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
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
        # Windows byte locks deny reads through competing handles. Lock first,
        # without inspecting file contents; locking beyond EOF is supported.
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


def thread_settings(row):
    payload = row.get("payload", {})
    if (row.get("type") == "event_msg" and isinstance(payload, dict)
            and payload.get("type") == "thread_settings_applied"):
        settings = payload.get("thread_settings")
        if isinstance(settings, dict):
            return settings
    return None


def runtime_paths(row, local_only=False):
    settings = thread_settings(row)
    if settings is not None:
        yield settings
    if row.get("type") != "world_state":
        return
    payload = row.get("payload", {})
    state = payload.get("state", {}) if isinstance(payload, dict) else {}
    config = state.get("environments", {}) if isinstance(state, dict) else {}
    environments = config.get("environments", {}) if isinstance(config, dict) else {}
    if isinstance(environments, dict):
        values = [environments.get("local")] if local_only else environments.values()
        for env in values:
            if isinstance(env, dict):
                yield env


def map_paths(rows, mapper):
    """Map structured execution paths, never message or tool-output text."""
    rows = copy.deepcopy(rows)
    for row in rows:
        containers = list(runtime_paths(row))
        if row.get("type") in ("session_meta", "turn_context"):
            containers.append(row.get("payload", {}))
        for payload in containers:
            if not isinstance(payload, dict):
                continue
            if "cwd" in payload:
                payload["cwd"] = mapper(payload["cwd"])
            for field in ("runtime_workspace_roots", "workspace_roots"):
                if isinstance(payload.get(field), list):
                    payload[field] = [mapper(p) for p in payload[field]]
    return rows


def bind_runtime_paths(rows, mapper):
    """Bind replayed local execution settings to the imported project scope."""
    result = copy.deepcopy(rows)
    destination = mapper(result[0]["payload"]["cwd"])
    for row in result:
        for payload in runtime_paths(row, local_only=True):
            if "cwd" in payload:
                value = payload["cwd"]
                payload["cwd"] = mapper(value) if is_portable(value) else destination
            for key in ("runtime_workspace_roots", "workspace_roots"):
                if isinstance(payload.get(key), list):
                    payload[key] = [mapper(v) if is_portable(v) else destination for v in payload[key]]
    return result


def provider_fields(row):
    if row.get("type") == "session_meta":
        yield row["payload"], "model_provider"
    settings = thread_settings(row)
    if settings is not None and "model_provider_id" in settings:
        yield settings, "model_provider_id"


def adapt_provider(rows, provider):
    result = copy.deepcopy(rows)
    if provider is not None:
        for row in result:
            for obj, key in provider_fields(row):
                obj[key] = provider
    return result


def runtime_bindings(rows):
    """Only replayed routing fields; conversation text is not a routing signal."""
    result = []
    for row in rows:
        values = []
        for obj, key in provider_fields(row):
            values.append((key, obj.get(key)))
        containers = list(runtime_paths(row, local_only=True))
        if row.get("type") == "session_meta":
            containers.append(row["payload"])
        for obj in containers:
            values.append({key: obj[key] for key in ("cwd", "runtime_workspace_roots", "workspace_roots") if key in obj})
        result.append(values)
    return result


def comparable(row):
    row = copy.deepcopy(row)
    for obj, key in provider_fields(row):
        obj.pop(key, None)
    if row.get("type") == "session_meta":
        base = row["payload"].get("history_base")
        if isinstance(base, dict):
            base.pop("end_byte_offset", None)  # recalculated from destination bytes
    return row


def relation(existing, incoming):
    # Compare serialized JSON rather than Python equality (True != 1 in JSON).
    for a, b in zip(existing, incoming):
        if encode(comparable(a)) != encode(comparable(b)):
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
        # Retain archived route provenance across an adapted round trip.
        rows = copy.deepcopy(rows)
        for old, new in zip(previous, rows):
            if encode(comparable(old)) != encode(comparable(new)):
                break
            for (old_obj, old_key), (new_obj, new_key) in zip(provider_fields(old), provider_fields(new)):
                if old_key in old_obj:
                    new_obj[new_key] = old_obj[old_key]
                else:
                    new_obj.pop(new_key, None)
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
    """Read UI names, not the first-message preview stored in threads.title."""
    result = {}
    index = home / "session_index.jsonl"
    if index.is_file() and not index.is_symlink():
        try:
            with index.open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue  # An append may still be in progress.
                    if (isinstance(row, dict) and isinstance(row.get("id"), str)
                            and isinstance(row.get("thread_name"), str) and row["thread_name"].strip()):
                        result[row.get("id")] = row["thread_name"]
        except OSError:
            pass
    databases = [p for p in home.glob("state_*.sqlite") if p.stem[6:].isdigit() and not p.is_symlink()]
    if databases:
        path = max(databases, key=lambda p: int(p.stem[6:]))
        try:
            with contextlib.closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as c:
                for sid, name, preview in c.execute("SELECT id, name, title FROM threads"):
                    if isinstance(name, str) and name.strip():
                        result[sid] = name
                    elif sid not in result and isinstance(preview, str) and preview.strip():
                        result[sid] = preview
        except sqlite3.Error:
            pass
    return result


def title_order(value):
    """A title's revision advances only when the exported name changes."""
    if not isinstance(value, dict):
        raise TransferError("Invalid title metadata")
    title, revision = value.get("title"), value.get("revision", 0)
    if not isinstance(title, str) or not title.strip() or type(revision) is not int or revision < 0:
        raise TransferError("Invalid title metadata")
    return revision, title


class NativeSessionIndex(dict):
    """Active logical sessions and immutable, physically named history sources."""
    def __init__(self):
        super().__init__()
        self.history_sources = {}
        self.materialized_sources = set()


def history_source_id(storage_id):
    return str(uuid.uuid5(uuid.UUID(validate_id(storage_id)), "codex-project-transfer:history-source"))


def history_source_owner(meta):
    marker = meta.get("cpt_history_source")
    if marker is None:
        return None
    if not isinstance(marker, dict):
        raise TransferError("Invalid CPT history source marker")
    owner = validate_id(marker.get("thread_id"))
    if meta["id"] != history_source_id(owner):
        raise TransferError("Materialized history source ID does not match its marker")
    return owner


def storage_file_id(path, meta):
    match = re.search(r"-([0-9a-f-]{36})(?:_([0-9a-f-]{36}))?\.jsonl$", path.name)
    if not path.name.startswith("rollout-") or not match or match[1] != meta["id"]:
        return None
    try:
        return validate_id(match[2] or match[1])
    except TransferError:
        return None


def reidentify_session(rows, sid):
    """Change structured owner IDs in an export copy, not historical text."""
    result = copy.deepcopy(rows)
    original = result[0]["payload"]["id"]
    result[0]["payload"]["id"] = sid
    for row in result:
        payload = row.get("payload")
        if isinstance(payload, dict):
            for key in ("session_id", "thread_id"):
                if payload.get(key) == original:
                    payload[key] = sid
    return result


def native_session_index(home):
    paths = NativeSessionIndex()
    warnings = []
    for path in session_files(home):
        try:
            with path.open("rb") as stream:
                meta = parse(stream.readline(MAX_BYTES + 1))[0]["payload"]
            owner = history_source_owner(meta)
            paths.setdefault(meta["id"], []).append((path, meta))
            if owner is not None:
                paths.materialized_sources.add(meta["id"])
            storage_id = storage_file_id(path, meta)
            if storage_id:
                paths.history_sources.setdefault(storage_id, []).append((path, meta))
        except (OSError, TransferError) as exc:
            warnings.append(f"{path.name}: {exc}")
    selections = []
    if any(len(entries) > 1 for entries in paths.values()):
        registered = registered_rollouts(home)
        for sid, entries in paths.items():
            active = registered.get(sid)
            if len(entries) < 2 or active is None:
                continue
            matched = [entry for entry in entries if entry[0].resolve() == active]
            if len(matched) == 1:
                paths[sid] = matched
                selections.append({"session_id": sid, "selected": str(matched[0][0]),
                                   "other_files": [str(path) for path, _ in entries if path != matched[0][0]]})
    return paths, warnings, selections


def registered_rollouts(home):
    """Read Codex's active file registry without updating its database."""
    databases = [p for p in home.glob("state_*.sqlite") if p.stem[6:].isdigit() and not p.is_symlink()]
    if not databases:
        return {}
    database = max(databases, key=lambda p: int(p.stem[6:]))
    try:
        with contextlib.closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as c:
            return {sid: Path(path).resolve() for sid, path in c.execute("SELECT id, rollout_path FROM threads")
                    if isinstance(path, str) and Path(path).is_absolute()}
    except sqlite3.Error:
        # With no usable registry, the content-based check must still reject
        # genuine divergence instead of guessing which file Codex uses.
        return {}


def load_native_session(index, sid):
    entries = index.get(sid, [])
    if not entries:
        raise TransferError(f"Missing native session {sid}")
    chosen = read_rollout(entries[0][0], partial=True)
    for path, _ in entries[1:]:
        candidate = read_rollout(path, partial=True)
        rel = relation(chosen, candidate)
        if rel == "diverged":
            files = ", ".join(str(path) for path, _ in entries)
            raise TransferError(f"{sid}: multiple divergent local rollouts and no matching active Codex index entry: {files}")
        if rel == "extends":
            chosen = candidate
    return chosen


def load_history_source(index, storage_id):
    entries = index.history_sources.get(storage_id, [])
    if not entries:
        raise TransferError(f"Missing native history source {storage_id}; restore its Codex history file before exporting or resuming")
    chosen = None
    for path, _ in entries:
        rows = read_rollout(path, partial=True)
        if storage_file_id(path, rows[0]["payload"]) != storage_id:
            raise TransferError(f"History source changed identity: {path}")
        if chosen is None:
            chosen = rows
        else:
            rel = relation(chosen, rows)
            if rel == "diverged":
                raise TransferError(f"Ambiguous physical history source {storage_id}")
            if rel == "extends":
                chosen = rows
    return chosen


def validate_store_history(store):
    from .history import ordered, prefix, reference
    # Parent variants must agree through each child's fork boundary.
    bundle = {rows[0]["payload"]["id"]: rows for _, rows in store_sessions(store)}
    order = ordered(bundle)
    for rows in bundle.values():
        ref = reference(rows)
        if ref:
            parent, cutoff = ref
            for conflict in safe_child(store, f"conflicts/{parent}").glob("*.jsonl"):
                variant = read_rollout(safe_child(store, conflict.relative_to(store).as_posix()))
                if relation(prefix(bundle[parent], cutoff), prefix(variant, cutoff)) != "equal":
                    raise TransferError(f"Ambiguous history dependency {parent}: conflicting parent prefixes")
    return bundle, order


def rebase_store(store):
    from .history import rebase
    bundle, _ = validate_store_history(store)
    rebased = rebase(bundle)
    for sid, rows in rebased.items():
        path = safe_child(store, f"sessions/{sid}.jsonl")
        data = serialize(rows)
        if path.read_bytes() != data:
            atomic_write(path, data)


def collect_export_history(index, selected, skip_incomplete=False):
    from .history import prefix, reference
    cache, bundle, skipped = {}, {}, []

    def portable_comparison(rows):
        root = Path(rows[0]["payload"]["cwd"]).resolve()
        return bind_runtime_paths(map_paths(rows, lambda v: portable_path(v, [root])), lambda v: v)

    def merge(target, sid, rows):
        if sid not in target:
            target[sid] = rows
            return
        rel = relation(portable_comparison(target[sid]), portable_comparison(rows))
        if rel == "diverged":
            raise TransferError(f"Inconsistent history source snapshots for {sid}")
        if rel == "extends":
            target[sid] = rows

    def visit(sid, cutoff, required, visiting, storage):
        key = (storage, sid)
        if key in visiting:
            raise TransferError(f"Cyclic history dependency at {sid}")
        if len(visiting) >= 128:
            raise TransferError("History dependency depth exceeds 128")
        if key not in cache:
            cache[key] = load_history_source(index, sid) if storage else load_native_session(index, sid)
        source = cache[key]
        rows = copy.deepcopy(source if cutoff is None else prefix(source, cutoff))
        if storage:
            owner = history_source_owner(rows[0]["payload"]) or sid
            output_id = history_source_id(owner)
            rows = reidentify_session(rows, output_id)
            rows[0]["payload"]["cpt_history_source"] = {"thread_id": owner}
            # These are ancestry records, not additional interactive project chats.
            rows[0]["payload"]["source"] = "exec"
        else:
            output_id = sid
        ref = reference(rows)
        if ref:
            parent = visit(ref[0], ref[1], required, visiting | {key}, True)
            rows[0]["payload"]["history_base"]["thread_id"] = parent
        merge(required, output_id, rows)
        return output_id

    for sid in sorted(selected):
        required = {}
        try:
            visit(sid, None, required, set(), False)
            for dependency, rows in required.items():
                if dependency in bundle:
                    # Check consistency before committing this root's closure.
                    probe = {dependency: bundle[dependency]}
                    merge(probe, dependency, rows)
        except (TransferError, OSError) as exc:
            skipped.append({"session_id": sid, "reason": str(exc)})
            continue
        for dependency, rows in required.items():
            merge(bundle, dependency, rows)
    if skipped and not skip_incomplete:
        details = "; ".join(f"{item['session_id']}: {item['reason']}" for item in skipped)
        raise TransferError(f"Cannot export {len(skipped)} incomplete session(s): {details}. "
                            "Use --skip-incomplete to explicitly export only complete sessions")
    return bundle, skipped


def export_sessions(root, home, *, skip_incomplete=False):
    from .history import rebase
    store = check_store(root)
    roots = roots_for(root)
    index, warnings, selections = native_session_index(home)
    selected = {sid for sid, entries in index.items()
                if sid not in index.materialized_sources
                and any(is_portable(portable_path(meta["cwd"], roots)) for _, meta in entries)}
    bundle, skipped = collect_export_history(index, selected, skip_incomplete)
    if skipped:
        warnings.append(f"Skipped {len(skipped)} incomplete session(s); see skipped for IDs and reasons")
    # Validate the entire dependency closure before writing any snapshot.
    from .history import ordered
    ordered(bundle)
    portable = {}
    for sid, rows in bundle.items():
        ancestry_roots = roots if sid in selected else [*roots, Path(rows[0]["payload"]["cwd"]).resolve()]
        mapped = map_paths(rows, lambda v: portable_path(v, ancestry_roots))
        portable[sid] = bind_runtime_paths(mapped, lambda v: v)
    portable = rebase(portable)
    existing = {rows[0]["payload"]["id"]: rows for _, rows in store_sessions(store)}
    ordered({**existing, **portable})
    result = {"exported": [], "dependencies": sorted(set(bundle) - selected), "conflicts": [], "warnings": warnings,
              "selected_rollouts": [item for item in selections if item["session_id"] in bundle], "skipped": skipped}
    for sid in ordered(portable):
        outcome = merge_snapshot(store, portable[sid])
        if outcome == "diverged":
            result["conflicts"].append(sid)
        elif outcome in ("added", "extends"):
            result["exported"].append(sid)
    rebase_store(store)
    titles = metadata_titles(home)
    result["titles_updated"] = []
    for sid in portable:
        path = safe_child(store, f"titles/{sid}.json")
        if sid in titles:
            previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"title": titles[sid]}
            revision, old_title = title_order(previous)
            if not path.exists() or old_title != titles[sid]:
                json_write(path, {"title": titles[sid], "revision": revision + 1})
                result["titles_updated"].append(sid)
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
    from .history import reference, prefix, byte_offset
    store = check_store(root)
    bundle, order = validate_store_history(store)
    source_ids = {owner: sid for sid, rows in bundle.items()
                  if (owner := history_source_owner(rows[0]["payload"])) is not None}
    roots = roots_for(root)
    index, _, selections = native_session_index(home)
    known = {sid: [path for path, _ in entries] for sid, entries in index.items()}
    result = {"imported": [], "unchanged": [], "pending": [], "conflicts": [], "warnings": [],
              "provider": provider, "provider_adapted": [],
              "selected_rollouts": [item for item in selections if item["session_id"] in bundle]}
    plans = []
    actual_bytes = {}
    for sid in order:
        portable = bundle[sid]
        if sid in exclude:
            if len(known.get(sid, [])) == 1:
                actual_bytes[sid] = known[sid][0].read_bytes()
            continue
        if not is_portable(portable[0]["payload"]["cwd"]):
            raise TransferError("Snapshot has an unmapped project directory")
        incoming = bind_runtime_paths(portable, lambda v: native_path(v, root))
        incoming = map_paths(incoming, lambda v: native_path(v, root))
        incoming = adapt_provider(incoming, provider)
        ref = reference(portable)
        if ref:
            parent, cutoff = ref
            if parent not in actual_bytes:
                raise TransferError(f"{sid}: history dependency {parent} cannot be imported")
            actual = map_paths(parse(actual_bytes[parent]), lambda v: portable_path(v, roots))
            actual = bind_runtime_paths(actual, lambda v: v)
            expected = bind_runtime_paths(bundle[parent], lambda v: v)
            if relation(prefix(actual, cutoff), prefix(expected, cutoff)) != "equal":
                raise TransferError(f"{sid}: local parent {parent} differs from the required history prefix")
            incoming[0]["payload"]["history_base"]["end_byte_offset"] = byte_offset(actual_bytes[parent], cutoff)
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
            actual_bytes[sid] = original
            local = parse(original)
            canonical = map_paths(local, lambda v: portable_path(v, roots))
            canonical = bind_runtime_paths(canonical, lambda v: v)
            base = canonical[0]["payload"].get("history_base")
            if isinstance(base, dict) and base.get("thread_id") in source_ids:
                base["thread_id"] = source_ids[base["thread_id"]]
            rel = relation(canonical, bind_runtime_paths(portable, lambda v: v))
            if rel in ("equal", "ahead"):
                result["unchanged"].append(sid)
                if sid not in index.materialized_sources and provider is not None and runtime_bindings(local[:len(incoming)]) != runtime_bindings(incoming):
                    result["warnings"].append(f"{sid}: existing native session has different provider or execution paths; not modified")
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
            incoming = adapt_provider(incoming, local[0]["payload"].get("model_provider"))
            data += serialize(incoming[len(local):])
        else:
            data = serialize(incoming)
            if provider is not None and portable[0]["payload"].get("model_provider") != provider:
                result["provider_adapted"].append(sid)
        actual_bytes[sid] = data
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
            if payload.pop("cpt_history_source", None) is not None:
                payload["source"] = "cli"  # an explicitly forked snapshot is now a user chat
        for key in ("session_id", "thread_id"):
            if payload.get(key) == sid:
                payload[key] = new_id
    merge_snapshot(store, rows)
    return new_id
