# Storage and automation contract

## Storage

`manifest.json` declares `codex-project-transfer/v1`. Canonical JSONL files live under `sessions/<UUID>.jsonl`; conflicting variants use `conflicts/<UUID>/<SHA256>.jsonl`. Content hashing applies to canonical JSON serialization, not arbitrary original whitespace. All original event fields are preserved except the documented execution-directory mapping.

The portable `${CPT_PROJECT_ROOT}` token replaces paths inside the selected repository or its registered worktrees. Imported sessions map onto the destination checkout; a subdirectory stays a subdirectory. Worktrees' source Git metadata stays in the rollout, but CPT does not restore the corresponding code branch or worktree. Check out the appropriate code before resuming. External paths and historical message text remain unchanged.

Only `session_meta` and `turn_context` fields `cwd`, `runtime_workspace_roots`, and `workspace_roots` are remapped. Other path-bearing permission, environment, browser, and tool fields are not generalized. New imports adapt the session header provider to the receiving project’s effective configuration. Provider/model credentials and runtime permissions must already be configured appropriately on the destination. This is a session transfer tool, not environment virtualization.

The repository roots come from Git, including `git worktree list`; unrelated histories are excluded. Removed/unregistered worktrees and historical project locations require manual relocation or a future explicit alias map. No inference by directory name.

`legacy` and `paginated` are the accepted history modes. Paginated record ordinals are preserved and checked for monotonicity. Unknown modes are rejected. Complete newline-terminated records are snapshotted from live transcripts. Malformed records are reported, never silently dropped.

## Conflict semantics

- Equal conversation records: unchanged. Only the first `session_meta.model_provider` is ignored during comparison because it is a local routing choice. Provider/model fields in historical turns or messages still participate in conflict detection.
- One history is a prefix of the other: retain the longer history.
- Divergence: retain both. The lexicographically smaller SHA256 determines the store's primary snapshot so exchanges converge; this is not a claim about which branch is semantically preferred.
- Import never overwrites a divergent local history. A stored conflict can be imported with a deterministic new UUID using `cpt fork` and then `cpt import`.
- No delete propagation. No last-write-wins replacement. Older snapshots cannot truncate newer local histories.

When an adapted continuation is exported, its archive retains the existing source header provider, so changing machines does not introduce a provider-only diff or false fork.

Initial title labels travel separately. Subsequent local renames are not a distributed rename log. A deterministic title tie-break prevents repeated Git commits when devices have different initial labels.

## Import and native registration

New sessions are placed in the target `CODEX_HOME/sessions/YYYY/MM/DD`. Fast-forward updates preserve the local byte prefix and append new records; backups are content-addressed under `CODEX_HOME/cpt-backups/`.

Manual fast-forward updates and `cpt run` require clients to be closed. There is no polling or automatic import. This check is intentionally conservative, not a security boundary; custom renamed executables may evade it, and starting a client concurrently with import remains unsupported. Hooks never update existing rollouts.

Per-repository and per-Codex-home OS file locks serialize CPT operations. Before writing, import also checks that its target did not change since planning. These locks do not lock Codex itself. Snapshots and target files are replaced atomically; a multi-session import can be retried after a crash. Import validates all planned records before writing.

The native adapter runs a short-lived `codex app-server --stdio` against the selected home, checks the returned home, calls `thread/read`, optionally `thread/name/set`, and verifies `thread/list` filtered to the destination provider plus the provider returned by `thread/read`. Using an all-provider list would falsely report visibility when the client’s normal list hides the imported session. This is **discovery verification**, not a model-generation or GUI test. Discovery failure leaves files intact and enters a retry queue for later syncs. No direct SQLite or session-index writes.

On Codex 0.155.1, a paginated transcript's message projection can remain empty until `thread/resume` initializes its recorder. The native integration test therefore resumes first and then checks `thread/items/list`. Do not treat a successful metadata-only read as proof that message history was restored.

## Provider selection

CLI imports resolve `model_provider` using native `config/read` for the destination repository. Codex determines the effective config layers and trust policy; the tool does not parse arbitrary project TOML or read credentials itself. When that API leaves the provider unset, Codex's built-in `openai` default applies. An explicit global `--provider ID` selects a different destination and also allows file import without a configuration probe. Unavailable automatic resolution is an error, not a silent source-provider fallback.

Only newly created native rollouts receive the destination provider in their `session_meta` header. Existing native rollouts keep their routing and byte prefix; imported suffixes contain the original conversation records. Archive snapshots and historical model fields remain unchanged. There are no provider-index rewrites, schema migration branches, or compatibility adapters for previously registered sessions.

The library import API accepts an optional `provider` chosen by the caller. The CLI supplies the resolved choice for import, download, sync, and the local run wrapper. `reconcile` verifies the selected provider's visibility. Tests use distinct synthetic source/destination providers and verify native resume with the receiver's configured model, without requesting model generation.

## Explicit Git transport

The remote name and branch live in local Git config (`cpt.remote`, `cpt.branch`). `init --remote` only remembers a destination. Only an explicit `upload`, `download`, or non-local `sync` command contacts it. Setup, status, and `run` never invoke Git transport. No remote from a downloaded manifest is executed.

Exchange reads only the dedicated remote branch, accepts only the declared store file shapes, merges native logs semantically, creates a tree using a temporary index, and creates a commit parented to the fetched remote head. It pushes without force. A rejected concurrent push leaves the local store intact; the next user-requested exchange fetches and reconciles again. There are no scheduled retries.

The current branch, working code, and ordinary index are untouched. Authored session commits use `Codex Project Transfer <cpt@localhost>`. Authentication uses the user's existing Git transport configuration; CPT stores no credentials. Offline exchange is reported while local snapshots remain saved. `upload` exports local records and performs the preserving fetch/merge/push without importing into Codex. `download` uses the same validated fetch/merge path with publication disabled, then imports; it never exports local sessions, creates a transfer commit, or pushes. `sync` explicitly performs both directions once.

## Fully manual operation

No hooks or background workers are installed. `check` explicitly exports project sessions and returns local status. `export`, `upload`, and `sync` also snapshot when invoked; `status`, setup, `download`, and normal Codex activity do not. `run` imports already-saved local history and launches Codex, without an export on startup or exit.

## Skill packaging and local runtime

The root plugin manifest exposes the skill instructions. Installation assembles a complete directory under the selected `CODEX_HOME/skills/codex-project-transfer`:

```text
codex-project-transfer/
├── SKILL.md
├── agents/openai.yaml
├── scripts/{cpt.py,runtime.py,install.py}
├── src/codex_project_transfer/
├── docs/{design.md,validation.md}
├── LICENSE
├── .cpt-install.json
└── .venv/
```

All executable code and supporting files are copied into this directory. No source-repository pointer is retained, and the source download can be removed after installation. The marker identifies the installation format without storing an external path. Reinstalling an owned skill is supported; unrelated skills are not overwritten. The installed installer can create another complete installation without the original repository.

The runtime uses Python's standard `venv` module without pip or network downloads. A local `.pth` points exclusively to the installed skill's own `src/`, and commands run with that venv's Python in isolated mode (`-I`). Virtual environments are created per device rather than copied.

The source-tree skill launcher directs the agent to install first. Its installed counterpart is the complete runtime launcher, so normal operations never reach outside the skill directory for project-transfer code. Native plugin bundles follow the same installation contract.

Agent/developer entry points from the source repository:

```sh
python3 scripts/install.py
# Use scripts/cpt.py in the returned installed skill directory afterward.
```

Development from the source repository can still use `scripts/cpt.py` and a repository-local venv. That development environment is not part of an installed skill.

## Sources

- [Official CLI reference](https://learn.chatgpt.com/codex/cli/reference)
- [cct native discovery investigation](https://github.com/ahmojo/codex-claude-transfer/blob/Main/docs/research/codex-post-import-discovery.md)

No source code was copied from the referenced project.
