# Storage and automation contract

## Storage

`manifest.json` declares `codex-project-transfer/v1`. Canonical JSONL files live under `sessions/<UUID>.jsonl`; conflicting variants use `conflicts/<UUID>/<SHA256>.jsonl`. Content hashing applies to canonical JSON serialization, not arbitrary original whitespace. All original event fields are preserved except the documented execution-directory mapping.

The portable `${CPT_PROJECT_ROOT}` token replaces paths inside the selected repository or its registered worktrees. Imported sessions map onto the destination checkout; a subdirectory stays a subdirectory. Worktrees' source Git metadata stays in the rollout, but CPT does not restore the corresponding code branch or worktree. Check out the appropriate code before resuming. External paths and historical message text remain unchanged.

Only `session_meta` and `turn_context` fields `cwd`, `runtime_workspace_roots`, and `workspace_roots` are remapped. Other path-bearing permission, environment, browser, and tool fields are not generalized. Provider/model configuration and runtime permissions must be configured appropriately on the destination. This is a session transfer tool, not environment virtualization.

The repository roots come from Git, including `git worktree list`; unrelated histories are excluded. Removed/unregistered worktrees and historical project locations require manual relocation or a future explicit alias map. No inference by directory name.

`legacy` and `paginated` are the accepted history modes. Paginated record ordinals are preserved and checked for monotonicity. Unknown modes are rejected. Complete newline-terminated records are snapshotted from live transcripts. Malformed records are reported, never silently dropped.

## Conflict semantics

- Equal JSON records: unchanged.
- One history is a prefix of the other: retain the longer history.
- Divergence: retain both. The lexicographically smaller SHA256 determines the store's primary snapshot so exchanges converge; this is not a claim about which branch is semantically preferred.
- Import never overwrites a divergent local history. A stored conflict can be imported with a deterministic new UUID using `cpt fork` and then `cpt import`.
- No delete propagation. No last-write-wins replacement. Older snapshots cannot truncate newer local histories.

Initial title labels travel separately. Subsequent local renames are not a distributed rename log. A deterministic title tie-break prevents repeated Git commits when devices have different initial labels.

## Import and native registration

New sessions are placed in the target `CODEX_HOME/sessions/YYYY/MM/DD`. Fast-forward updates preserve the local byte prefix and append new records; backups are content-addressed under `CODEX_HOME/cpt-backups/`.

Manual fast-forward updates and `cpt run` require clients to be closed. There is no polling or automatic import. This check is intentionally conservative, not a security boundary; custom renamed executables may evade it, and starting a client concurrently with import remains unsupported. Hooks never update existing rollouts.

Per-repository and per-Codex-home OS file locks serialize CPT operations. Before writing, import also checks that its target did not change since planning. These locks do not lock Codex itself. Snapshots and target files are replaced atomically; a multi-session import can be retried after a crash. Import validates all planned records before writing.

The native adapter runs a short-lived `codex app-server --stdio` against the selected home, checks the returned home, calls `thread/read`, optionally `thread/name/set`, and verifies `thread/list`. This is **discovery verification**, not a model-generation or GUI test. Discovery failure leaves files intact and enters a retry queue for later syncs. No direct SQLite or session-index writes.

On Codex 0.155.1, a paginated transcript's message projection can remain empty until `thread/resume` initializes its recorder. The native integration test therefore resumes first and then checks `thread/items/list`. Do not treat a successful metadata-only read as proof that message history was restored.

## Explicit Git transport

The remote name and branch live in local Git config (`cpt.remote`, `cpt.branch`). `init --remote` only remembers a destination. Only an explicit `upload`, `download`, or non-local `sync` command contacts it. Setup, status, and `run` never invoke Git transport. No remote from a downloaded manifest is executed.

Exchange reads only the dedicated remote branch, accepts only the declared store file shapes, merges native logs semantically, creates a tree using a temporary index, and creates a commit parented to the fetched remote head. It pushes without force. A rejected concurrent push leaves the local store intact; the next user-requested exchange fetches and reconciles again. There are no scheduled retries.

The current branch, working code, and ordinary index are untouched. Authored session commits use `Codex Project Transfer <cpt@localhost>`. Authentication uses the user's existing Git transport configuration; CPT stores no credentials. Offline exchange is reported while local snapshots remain saved. `upload` exports local records and performs the preserving fetch/merge/push without importing into Codex. `download` uses the same validated fetch/merge path with publication disabled, then imports; it never exports local sessions, creates a transfer commit, or pushes. `sync` explicitly performs both directions once.

## Fully manual operation

No hooks or background workers are installed. `check` explicitly exports project sessions and returns local status. `export`, `upload`, and `sync` also snapshot when invoked; `status`, setup, `download`, and normal Codex activity do not. `run` imports already-saved local history and launches Codex, without an export on startup or exit.

## Skill packaging and local runtime

The root `.codex-plugin/plugin.json` exposes `skills/codex-project-transfer`. The skill instructs the agent to perform setup and operations, inspect JSON results, and reserve user interaction for missing upload destinations, authentication, and closing active clients.

`scripts/runtime.py` creates `<plugin-root>/.venv` with the standard library's `venv` module. Since the runtime has no third-party dependencies, pip and package downloads are unnecessary. A local `.pth` registers the bundled source inside that environment. Commands execute with its Python in isolated mode (`-I`).

`scripts/cpt.py` is the bootstrap/command entry point. The skill's own `scripts/cpt.py` routes to it from either a native plugin copy or a standalone registered skill. `scripts/install.py` initializes the venv and copies the skill's discovery resources to the chosen `CODEX_HOME/skills`, recording the persistent plugin root in `plugin-root.json`. It does not register a marketplace, silently replace an unrelated skill, or upload conversations. Native plugin managers can discover the bundled skill directly.

Installation and command invocations (for agent/developer use):

```sh
python3 scripts/install.py
python3 scripts/cpt.py -C /path/to/project init --remote origin
# Only on the user's corresponding request:
python3 scripts/cpt.py -C /path/to/project upload
python3 scripts/cpt.py -C /path/to/project download
```

Use `py -3` on Windows when appropriate. The launcher does not require activation or a global `cpt` executable. A moved plugin must be reinstalled. Read-only plugin directories cannot host this local environment; place the plugin in a persistent writable location.

Development checks:

```sh
python3 scripts/cpt.py --version
.venv/bin/python -m unittest discover -s tests -v
CPT_TEST_NATIVE=1 .venv/bin/python -m unittest discover -s tests -p test_native.py -v
```

## Sources

- [Official CLI reference](https://learn.chatgpt.com/codex/cli/reference)
- [cct native discovery investigation](https://github.com/ahmojo/codex-claude-transfer/blob/Main/docs/research/codex-post-import-discovery.md)

No source code was copied from the referenced project.
