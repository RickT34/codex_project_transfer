# Storage and transfer contract

## Storage

`manifest.json` declares `codex-project-transfer/v1`. Canonical JSONL files live under `sessions/<UUID>.jsonl`; conflicting variants use `conflicts/<UUID>/<SHA256>.jsonl`. Content hashing applies to canonical JSON serialization, not arbitrary original whitespace. Conversation messages and tool output are preserved. Execution bindings and ancestry byte offsets are transformed as described below.

The portable `${CPT_PROJECT_ROOT}` token replaces paths inside the selected repository or its registered worktrees. Imported sessions map onto the destination checkout; a subdirectory stays a subdirectory. Worktrees' source Git metadata stays in the rollout, but CPT does not restore the corresponding code branch or worktree. Check out the appropriate code before resuming. External paths and historical message text remain unchanged.

Structured paths in `session_meta`, `turn_context`, `thread_settings_applied`, and `world_state` environments are mapped. Replayed local runtime settings bind to the imported project; a portable subdirectory is retained, while a machine-specific local runtime cwd is rebound to the imported session cwd. Permission and tool-output text is not rewritten. New imports adapt both `session_meta.model_provider` and replayed `thread_settings.model_provider_id` to the destination provider. Provider/model credentials and runtime permissions must already be configured appropriately on the destination. This is a session transfer tool, not environment virtualization.

The repository roots come from Git, including `git worktree list`. Project sessions are selected by cwd; required history ancestors are also included, even when archived or outside those roots. Removed/unregistered worktrees and historical project locations require manual relocation or a future explicit alias map. No inference by directory name.

`legacy` and `paginated` are the accepted history modes. Paginated record ordinals are preserved and checked for monotonicity. Unknown modes are rejected. Complete newline-terminated records are snapshotted from live transcripts. Malformed records are reported, never silently dropped.

## Selecting native files

Codex may retain multiple physical rollouts with the same session ID. When duplicates exist, CPT reads the latest local `state_*.sqlite` registry in read-only mode and uses the matching `threads.rollout_path` as the authoritative active file. The path must match a scanned file with that metadata ID. File size, timestamp, and directory order do not override the registered selection. Other files remain untouched, and `selected_rollouts` reports the choice.

If no usable matching registry entry exists, export/reconciliation can still select the complete member of a true prefix chain. Genuine divergence remains an error with the candidate paths. Import leaves ambiguous duplicates unchanged. This is native file selection, not a database repair or merge of different conversations.

## Conflict semantics

- Equal conversation records: unchanged. Structured provider bindings in session metadata and replayed thread settings are ignored during comparison. The derived `history_base.end_byte_offset` is also ignored; parent ID and ordinal cutoff remain significant. Provider/model fields in historical turns or messages still participate in conflict detection.
- One history is a prefix of the other: retain the longer history.
- Divergence: retain both. The lexicographically smaller SHA256 determines the store's primary snapshot so exchanges converge; this is not a claim about which branch is semantically preferred.
- Import never overwrites a divergent local history. A stored conflict can be imported with a deterministic new UUID using `cpt fork ./ SESSION_ID SHA256` and then `cpt import ./`.
- No delete propagation. No last-write-wins replacement. Older snapshots cannot truncate newer local histories.

When an adapted continuation is exported, its archive retains the provider provenance of the matching archived prefix, so changing machines does not introduce a provider-only diff or false fork.

UI titles travel separately. Read `threads.name` from the numerically newest state database, use the append-only `session_index.jsonl` name when no database name is available, and use `threads.title` (the first-message preview) only as a last resort. All reads are read-only. Each changed exported title increments its stored revision; unchanged titles do not rewrite metadata. Git selects the larger `(revision, title)` pair: later observed revisions win and concurrent equal revisions converge deterministically by title. This is a label selection rule, not a wall-clock ordering or a merged rename history. The initial revision baseline is zero when unspecified.

Reconciliation applies `thread/name/set` to both new and unchanged native chats, verifies `thread/read.name`, and reports `titles_restored`. Export reports `titles_updated`. `--no-reconcile` defers title application as well as discovery.

## Paginated history dependencies

`session_meta.history_base.thread_id` identifies the actual storage dependency; `forked_from_id` is provenance and may name a different thread. Export recursively collects the required storage ancestry. Ancestors outside the selected project are included only through the maximum referenced ordinal cutoff, excluding their later unrelated turns. Cycles, missing parents, and truncated prefixes fail validation before snapshot writing.

Logical sessions use the registered active rollout; physical ancestry uses the exact storage filename ID (`U` in `rollout-…-U.jsonl`, or `V` in `rollout-…-U_V.jsonl`). An active version may itself depend on the original file with the same logical ID. These must remain separate to avoid false cycles and lost prefixes.

Export materializes each physical ancestor under UUIDv5(storage ID, `codex-project-transfer:history-source`), records its original storage ID in `cpt_history_source.thread_id`, and rewrites structured owner IDs and history references in the exported copy. Historical message text and native source files are untouched. Internal ancestry uses source `exec` and is excluded from automatic project-chat selection. Marker validation and stable IDs prevent repeated remapping on re-export. Existing native references are normalized only for import comparison, without rewriting native headers.

By default, export reports all incomplete project roots and stops before writing. Explicit `--skip-incomplete` omits each affected root and its unneeded dependencies, returns `skipped` diagnostics and exit 2, and preserves existing snapshots. Existing invalid archive entries still fail validation.

Every archive must contain its complete ancestry closure. Import validates this even for IDs that already exist locally. It processes parents before children, recalculating each byte boundary from the actual bytes that will exist on the receiver. Existing parents may have different JSON whitespace, longer histories, or platform path lengths; their required prefixes must match. Conflicting parent variants through a fork boundary are rejected instead of silently choosing a different ancestry.

Offsets are recalculated after portable serialization on export and again after destination adaptation on import. Git transfer validates the remote graph and rebases the local store; an incomplete archive cannot be published. Record ordinals are preserved.

## Import and native registration

New sessions are placed in the target `CODEX_HOME/sessions/YYYY/MM/DD`. Fast-forward updates preserve the local byte prefix and append new records; backups are content-addressed under `CODEX_HOME/cpt-backups/`.

Manual fast-forward updates and `cpt run` require clients to be closed. There is no polling or automatic import. This check is intentionally conservative, not a security boundary; custom renamed executables may evade it, and starting a client concurrently with import remains unsupported.

Per-repository and per-Codex-home OS file locks serialize CPT operations. Before writing, import also checks that its target did not change since planning. These locks do not lock Codex itself. Snapshots and target files are replaced atomically; a multi-session import can be retried after a crash. Import validates all planned records before writing.

The native adapter runs a short-lived `codex app-server --stdio` against the selected home, checks the returned home, calls `thread/read`, optionally `thread/name/set`, and verifies `thread/list` filtered to the destination provider plus the provider returned by `thread/read`. Using an all-provider list would falsely report visibility when the client’s normal list hides the imported session. This is **discovery verification**, not a model-generation or GUI test. Discovery failure leaves files intact and enters a retry queue for later manually requested syncs. Both newly imported and unchanged IDs are checked. The CLI returns a nonzero status for warnings, unresolved conflicts, pending updates, or native verification failure instead of treating a file copy as complete success. No direct SQLite or session-index writes.

The short-lived app-server does not refresh another process's desktop UI cache. The result includes an informational `ui_refresh` hint; this hint alone does not make a successful command fail. The user refreshes or restarts an open desktop client when needed. No automatic restart or background UI control is installed. The official app-server API documents name notifications on its transport, but those are not a desktop-wide refresh guarantee: https://developers.openai.com/codex/app-server/ .

On Codex 0.155.1, a paginated transcript's message projection can remain empty until `thread/resume` initializes its recorder. Reconciliation initializes required ancestors in ancestry order using native `thread/resume`, with lifecycle hooks disabled and without starting a model turn. Codex builds its own history projections and may append normal runtime-setting records to those native copies. No direct SQLite writes are used. Dependency records need not appear as standalone interactive chats; their readable routing metadata is verified. The native integration test restores a real nested fork and checks inherited messages via `thread/items/list`. Do not treat a successful metadata-only read as proof that message history was restored.

## Provider selection

CLI imports resolve `model_provider` using native `config/read` for the destination repository. Codex determines the effective config layers and trust policy; the tool does not parse arbitrary project TOML or read credentials itself. When that API leaves the provider unset, Codex's built-in `openai` default applies. An explicit global `--provider ID` selects a different destination and also allows file import without a configuration probe. Unavailable automatic resolution is an error, not a silent source-provider fallback.

Newly created native rollouts receive the destination provider in their header and replayed thread settings. Existing native rollouts keep their routing and byte prefix; imported suffixes contain the original conversation records. Archive snapshots and historical model fields remain unchanged. There are no provider-index rewrites, schema migration branches, or compatibility adapters for previously registered sessions.

The library import API accepts an optional `provider` chosen by the caller. The CLI supplies the resolved choice for import, download, sync, and the local run wrapper. `reconcile` verifies the selected provider's visibility. Tests use distinct synthetic source/destination providers and verify native resume with the receiver's configured model, without requesting model generation.

## Explicit Git transport

Commands use `cpt COMMAND [PROJECT]`, with the current directory as the default project. `--home` and `--provider` work on either side of the subcommand. The run wrapper uses `cpt run PROJECT -- CODEX_ARGS` (put CPT options before PROJECT); conflict forks use `cpt fork [PROJECT] SESSION_ID SHA256`.

The remote name and branch live in local Git config (`cpt.remote`, `cpt.branch`). `init --remote` only remembers a destination. Only an explicit `upload`, `download`, or non-local `sync` command contacts it. Setup, status, and `run` never invoke Git transport. No remote from a downloaded manifest is executed.

Exchange reads only the dedicated remote branch, accepts only the declared store file shapes, merges native logs semantically, creates a tree using a temporary index, and creates a commit parented to the fetched remote head. It pushes without force. A rejected concurrent push leaves the local store intact; the next user-requested exchange fetches and reconciles again. There are no scheduled retries.

The current branch, working code, and ordinary index are untouched. Authored session commits use `Codex Project Transfer <cpt@localhost>`. Authentication uses the user's existing Git transport configuration; CPT stores no credentials. Offline exchange is reported while local snapshots remain saved. `upload` exports local records and performs the preserving fetch/merge/push without importing into Codex. `download` uses the same validated fetch/merge path with publication disabled, then imports; it never exports local sessions, creates a transfer commit, or pushes. `sync` explicitly performs both directions once.

## Fully manual operation

No hooks or background workers are installed. `check` explicitly exports project sessions and returns local status. `export`, `upload`, and `sync` also snapshot when invoked; `status`, setup, `download`, and normal Codex activity do not. `run` imports already-saved local history and launches Codex, without an export on startup or exit.

## CLI packaging and installation

The `pyproject.toml` console entry point installs `cpt` through standard Python packaging. `python -m codex_project_transfer` is also available in the installed environment. The program has no third-party runtime dependencies and requires Python 3.10+.

Use `uv tool install .` or `pipx install .` for an isolated, persistent command without environment activation. Normal wheel installations contain the Python package and do not depend on the original source checkout. Installation and PATH registration belong to the chosen package manager; CPT has no custom bootstrap, installer, or background service. Build dependencies may require network access at installation time.

For development and tests, create a virtual environment, then run:

```sh
python -m pip install -e ".[test]"
python -m unittest discover -s tests -v
```

The test extra supplies the distribution build tools. Installation tests build a source distribution and wheel, install the wheel into a fresh environment without network access, remove the build checkout, and exercise the installed command. CI uses regular package installation on Linux, macOS, and Windows with Python 3.10 and 3.13.

## Sources

- [Official CLI reference](https://learn.chatgpt.com/codex/cli/reference)
- [cct native discovery investigation](https://github.com/ahmojo/codex-claude-transfer/blob/Main/docs/research/codex-post-import-discovery.md)

No source code was copied from the referenced project.
