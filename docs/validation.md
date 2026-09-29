# Validation

Environment: macOS arm64, Python 3.14.3, Codex CLI/app-server 0.155.1.

## Automated checks

```sh
CPT_TEST_NATIVE=1 .venv/bin/python -m unittest discover -s tests -v
```

Tests use temporary Git repositories, local bare remotes, isolated Codex homes, and synthetic chats. Real user sessions and the real skill installation directory are not modified.

Coverage includes:

- Project filtering, subdirectory mapping, registered worktrees, and macOS path aliases.
- Preservation of historical message text while execution paths are remapped.
- Complete-line snapshots, rejection of unsupported history modes and malformed paginated ordinals.
- Idempotent imports, fast-forward-only updates, original-file backups, duplicate-ID protection, and dry runs.
- Preserving divergent continuations, deterministic exchange convergence, and recovery after competing Git pushes.
- Preserving the code HEAD and staged index during transfer; rejecting unrelated remote branch contents.
- Download without publishing local-only sessions; upload without importing into the native Codex home.
- Manual check/save without network, imports, or native processes; initialization and status do not save or create hooks.
- Path traversal and symlink protection, OS file locks, offline snapshot retention, and native-discovery retry state.
- Fresh skill-local venv installation without pip, complete bundled source and docs, operation after the original repository is moved, reinstalling from the installed bundle alone, idempotent reinstall, and preserving unrelated skills.
- Cross-provider import with a source provider unavailable on the receiver, destination-specific list visibility, native resume, and archive preservation for both native history modes.
- Provider-neutral round-trip comparison, no false forks after a continued chat is re-exported, and preservation of real message-level differences.
- Native configuration trust policy and explicit provider selection without an automatic probe.
- Native `legacy` history transfer followed by Codex read/resume and verification of original user/assistant text.
- Native `paginated` history transfer followed by `thread/resume` and `thread/items/list`, verifying original messages and the same thread ID.

No model turn is requested by these tests. Native resume initialization and restored transcript contents do not establish that a new generated answer uses that context correctly.

The skill and plugin manifest are also checked with the skill-creator and plugin-creator validators.

## Remaining validation

- Desktop App and VS Code GUI listing, project grouping, and clicking to continue an imported chat.
- A newly generated model answer after transfer.
- Real device-to-device transfer over SSH/HTTPS; automated Git tests use local bare remotes.
- Windows/Linux real-device behavior and the provided GitHub Actions matrix.
- Discovery of the installed skill in a fresh Codex UI chat.

## Manual acceptance

1. Prepare a private test repository on two devices and install the skill on both.
2. Ask the agent to initialize the project and remember an authorized remote. Setup should not save sessions or contact the remote.
3. On A, create a disposable chat containing a unique token. Ask the agent to check and save the project sessions; verify a local snapshot is created.
4. Ask the agent to upload and verify the dedicated remote session branch changed.
5. Ask the agent on B to download. Verify that B did not publish its local-only sessions. Close clients before applying an update to an existing chat.
6. Resume the imported chat and ask for the token. This step makes a model request and supplies the separate GUI/model acceptance check.
