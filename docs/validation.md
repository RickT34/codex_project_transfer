# Validation

Previous native validation environment: macOS arm64, Python 3.14.3, Codex CLI/app-server 0.155.1 and a focused native check with 0.159.0.

## Automated checks

```sh
python -m pip install -e ".[test]"
python -m unittest discover -s tests -v
```

Tests use temporary Git repositories, local bare remotes, isolated Codex homes, and synthetic chats. Real user sessions and installed user commands are not modified. Set `CPT_TEST_NATIVE=1` to include the opt-in tests against an installed Codex CLI; these are skipped by default.

Coverage includes:

- Recursive paginated ancestry export, prefix-only collection of out-of-project parents, cycle/truncation/missing-parent rejection, and offset rebasing against destination byte lengths and existing JSON formatting.
- Physical version-suffix ancestry, distinct original/active files sharing a logical ID, stable materialized dependencies, and source-file preservation on re-import.
- Strict aggregate incomplete-session errors before snapshot writes, and explicit partial export with skipped IDs, nonzero exit, preserved source files, and importable remaining chats.
- A real two-level fork generated through Codex, transferred across paths/providers, then resumed with inherited messages visible.
- Replayed thread settings and local world-state paths mapped without changing historical message text.

- Project filtering, subdirectory mapping, registered worktrees, and macOS path aliases.
- Preservation of historical message text while execution paths are remapped.
- Complete-line snapshots, rejection of unsupported history modes and malformed paginated ordinals.
- Duplicate native rollouts selected by the read-only Codex registry, including a shorter active file; inactive copies remain unchanged. Stale registry entries do not resolve genuine divergence.
- UI names read from the newest numeric database version with session-index fallback; generated/manual title changes exported without changing session content or churning unchanged snapshots.
- Title-only Git updates, concurrent-name convergence, and no repeated metadata-only commits after convergence.
- Native source naming, title export/import, and destination `thread/read.name` verification across both history modes/providers; manual UI-refresh guidance remains informational.
- Idempotent imports, fast-forward-only updates, original-file backups, duplicate-ID protection, and dry runs.
- Preserving divergent continuations, deterministic exchange convergence, and recovery after competing Git pushes.
- Preserving the code HEAD and staged index during transfer; rejecting unrelated remote branch contents.
- Download without publishing local-only sessions; upload without importing into the native Codex home.
- Manual check/save without network, imports, or native processes; initialization and status do not save or create hooks.
- Path traversal and symlink protection, OS file locks, offline snapshot retention, and native-discovery retry state.
- Source distribution and wheel builds, offline wheel installation into a fresh environment, and installed CLI/module entry points after the build checkout is removed.
- Direct `cpt export ./` and `cpt import ./` from the installed command, default-current-directory operation, paths with spaces, import preview without writes, restored message contents, and initialization without snapshots or hooks.
- Cross-provider import with a source provider unavailable on the receiver, destination-specific list visibility, native resume, and archive preservation for both native history modes.
- Provider-neutral round-trip comparison, no false forks after a continued chat is re-exported, and preservation of real message-level differences.
- Native configuration trust policy and explicit provider selection without an automatic probe.
- Native `legacy` history transfer followed by Codex read/resume and verification of original user/assistant text.
- Native `paginated` history transfer followed by `thread/resume` and `thread/items/list`, verifying original messages and the same thread ID.

No model turn is requested by these tests. Native resume initialization and restored transcript contents do not establish that a new generated answer uses that context correctly.

## CLI packaging validation (2026-10-13)

On Linux with Python 3.12.3, the default suite ran 70 tests: 63 passed and 7 opt-in native tests were skipped. A separate isolated uv check verified installation, reinstallation, running `cpt --version` after removing the source checkout, and uninstallation. The Windows/macOS CI matrix and native GUI behavior were not run in this check.

## Remaining validation

- Desktop App and VS Code GUI listing, project grouping, and clicking to continue an imported chat.
- A newly generated model answer after transfer.
- Real device-to-device transfer over SSH/HTTPS; automated Git tests use local bare remotes.
- Windows/Linux real-device behavior and the provided GitHub Actions matrix.

## Manual acceptance

1. Prepare a private test repository on two devices and install `cpt` on both.
2. Run `cpt init ./ --remote origin` in each project. Setup should not save sessions or contact the remote.
3. On A, create a disposable chat containing a unique token. Run `cpt check ./`; verify a local snapshot is created.
4. Run `cpt upload ./` and verify the dedicated remote session branch changed.
5. On B, run `cpt download ./`. Verify that B did not publish its local-only sessions. Close clients before applying an update to an existing chat.
6. Resume the imported chat and ask for the token. This step makes a model request and supplies the separate GUI/model acceptance check.
