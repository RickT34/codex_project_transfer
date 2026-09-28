---
name: codex-project-transfer
description: Check and save a project's native Codex chats locally, or upload, download, and synchronize them, only when the user explicitly asks; diagnose session-transfer problems. Use for project-scoped Codex session transfer and sync, not ordinary code commits or ChatGPT web history.
---

# Codex Project Transfer

Operate the bundled tool for the user. Run commands yourself and report the result; do not turn routine setup or diagnosis into a CLI tutorial.

## Runtime and installation

All installed files must live inside the skill's own directory: `SKILL.md`, UI metadata, launcher and installer scripts, Python source, supporting docs, license, and `.venv`. Do not install a thin wrapper pointing to an external clone, use an external runtime directory, or create symlinks to the source repository.

For a source checkout or native plugin bundle, if this skill directory does not yet contain `src/codex_project_transfer`, locate the repository two directories above it and run that repository's `scripts/install.py` with an available Python 3.10+ (`python3` on macOS/Linux, `py -3` on Windows). The installer copies the complete bundle into the selected `CODEX_HOME/skills/codex-project-transfer`, creates `.venv` there, and returns the installed skill path. Use that returned directory for every subsequent command. `--skills-dir` supports an explicitly chosen destination. Existing unrelated skills are preserved.

The installation is independent of the download/clone directory, which can be removed afterward. Do not copy the source `.venv`; create a fresh one on the receiving device. Tell the user to open a new chat for discovery. Installing this skill does not install a marketplace or transfer project sessions.

For an installed skill, the stable entry point is its own `scripts/cpt.py`. It uses `.venv` and source within that same skill directory, without global pip installation, pipx, environment activation, or PATH changes. Its bundled `scripts/install.py` can install another complete copy if explicitly requested.

In the commands below, `RUN` means the available Python plus the absolute path to this skill's `scripts/cpt.py`, passed as separate, correctly quoted arguments. Never invoke an arbitrary `cpt` from PATH. Global options precede the subcommand:

```text
RUN -C PROJECT [--home CODEX_HOME] COMMAND ...
```

## Set up a project

1. Resolve the project with `git rev-parse --show-toplevel`; inspect local CPT settings and remotes without contacting the network. Run `RUN --version` to check the local runtime.
2. Run `RUN -C PROJECT init` to prepare the local store without saving sessions. Add `--remote REMOTE` only when the user has selected an upload/download destination. This remembers the destination, but does not fetch or upload. If none is selected, finish local setup and ask for a destination only when a transfer is requested.
3. Run `status` and report setup status. Do not run `sync`, `upload`, `download`, native reconciliation, or a full export merely to finish installation.
4. No hooks or trust review are needed. Report setup completion and wait for a user-requested check, save, or transfer.

There are no hooks or automatic saves. Codex retains its own normal history; this plugin snapshots it into the project only on an explicit check/save/upload/sync request. No startup or shutdown export, Git-event import, background polling, or automatic retry is installed. A configured remote is a destination, not authorization to initiate transfers on later turns. Do not schedule or launch a watcher.

## User-requested operations

| User intent | Action |
|---|---|
| Check and save project sessions | `check` (export plus local status; no network/import) |
| Save this project's chats locally | `export` |
| Upload this project's chats | `upload`, then inspect the returned JSON |
| Download / restore from the other device | `download`, then inspect the returned JSON |
| Synchronize both directions now | `sync`, then inspect the returned JSON |
| Import files already in this repository | `import` (local only) |
| Preview a local import | `import --dry-run` |
| Check transfer health | `status` only; do not contact the remote |
| Imported files are not visible | `reconcile`, inspect `verified` and warnings |

Perform remote operations only in response to the user's upload, download, or sync request. Reuse an already authorized destination; if missing, ask before transferring. Keep the directions distinct: `download` never uploads or exports local sessions. `upload` reads and merges the remote first to avoid overwriting other devices' histories, but does not import anything into the local Codex home. `sync` is one explicit round trip, not an ongoing service.

On failure, preserve snapshots, report the error, and stop. Do not silently start polling or schedule future retries. A successful exit code alone is not proof of complete transfer: inspect errors, warnings, pending updates, conflicts, and native verification in the JSON result.

## Existing sessions and conflicts

Imports do not overwrite already-open chats. `pending` means newer history is saved but not applied. Tell the user to close Codex/ChatGPT/VS Code first; after that, `import --update-existing` can apply the already-downloaded fast-forwards locally. An agent inside the active client cannot truthfully promise to close itself and finish the operation. If a user needs to finish outside the client, provide the exact local-venv command for `run -- resume --all`, with real paths, as the necessary handoff. This command only imports already-saved local history and launches Codex. It does not save on startup/exit or fetch/upload.

For a divergent continuation, inspect `status` and preserve both histories. Use `fork SESSION_ID CONFLICT_SHA256` followed by `import` when the user wants both branches available independently. Never splice JSONL records, force-push the session branch, overwrite SQLite, or use a code reset to fix chat sync.

## Evidence boundaries

Native CLI/app-server legacy and paginated restoration have been tested on macOS with Codex 0.155.1. Desktop/VS Code GUI continuation and Windows/Linux runtime validation remain separate checks. `reconcile` verifies discovery, not a new model answer. Paginated item projection may populate only after native resume. Code, external attachments, credentials, and running processes are not transferred.

Read `docs/design.md` inside the installed skill directory only when debugging storage, transport, or compatibility. Keep technical implementation details out of routine user instructions.
