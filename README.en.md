# Codex Project Transfer (cpt)

**Switch computers. Pick up the conversation.**

`cpt` is a command-line program for exporting, importing, and transferring native Codex conversations by Git project. Run `cpt ...` manually in your terminal: export into `.codex-chats/`, copy it to another device and import, or upload and download through a Git remote.

Each command runs once on demand. No hooks, background polling, or automatic saving. Codex keeps its normal chat history.

[中文](README.md)

## Install

Requires Python 3.10+ and Git. Default provider detection, native registration, and resuming chats also require a configured Codex CLI.

### Recommended: uv

Install uv following its [official instructions](https://docs.astral.sh/uv/getting-started/installation/). Download this project, then run these commands in the directory containing `pyproject.toml` (macOS, Linux, and Windows):

```sh
uv tool install .
uv tool update-shell
```

Open a new terminal and verify:

```sh
cpt --version
cpt --help
```

uv installs `cpt` in an isolated environment; activation is unnecessary. A regular installation copies the program, so the downloaded source can be moved or removed afterward. Installation may fetch build dependencies; the program has no third-party runtime dependencies. See [uv tool installation](https://docs.astral.sh/uv/guides/tools/#installing-tools) for PATH handling.

### Other installation methods

If you already have pipx, run in this project's directory:

```sh
pipx install .
pipx ensurepath
```

Open a new terminal to use `cpt`. Choose either uv or pipx.

Alternatively, use a Python virtual environment. macOS / Linux:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/cpt --help
```

Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\cpt.exe --help
```

Keep the directory containing `.venv` when using this method. Use the command's absolute path for other projects, or activate the environment to run `cpt` directly.

### Update and uninstall

After obtaining updated source, reinstall from this project's directory: `uv tool install --reinstall .`, `pipx install --force .`, or the virtual environment's `python -m pip install --upgrade .`.

Uninstall with the matching `uv tool uninstall codex-project-transfer`, `pipx uninstall codex-project-transfer`, or the environment's `python -m pip uninstall codex-project-transfer`. Uninstalling leaves project `.codex-chats/` archives and native Codex conversations intact.

## Quick start

After installation, enter the **Git project whose conversations you want to transfer**:

```sh
cpt init ./
cpt export ./
```

Copy the entire `.codex-chats/` directory to the corresponding Git project root on the receiving device, then run:

```sh
cpt import ./ --dry-run
cpt import ./
```


## Usage

### 1. Export Codex sessions into the project

```sh
# Initialize this project once; no saving or uploading occurs
cpt init ./

# Save project sessions and required ancestry under .codex-chats/
cpt export ./

# Inspect local status without uploading or downloading
cpt status ./
```

Use `check` instead of `export` to save and report local status in one operation.

Export stops by default if any chat lacks required ancestry. To save the remaining complete chats explicitly:

```sh
cpt export ./ --skip-incomplete
```

`skipped` lists each omitted chat ID and reason. Skipping returns exit code `2`, meaning partial completion. This option also applies to the export step of `check`, `upload`, and `sync`. It does not delete native chats or existing archive snapshots, and cannot repair an already incomplete archive.

For a first transfer to another device, place the **entire `.codex-chats/` directory** under that device's corresponding Git project root. Copy the directory or carry it in Git commits you manage yourself. Do not copy only one JSONL file: forked chats may depend on other files. If both devices already have snapshots, the upload/download workflow below preserves conflicting versions.

### 2. Import project snapshots into Codex

Install the tool on the receiving device and prepare its project and Codex provider configuration:

```sh
# Preview the plan without writing session records
cpt import ./ --dry-run

# Import, register, and verify the sessions
cpt import ./
```

These commands read the project's existing `.codex-chats/`; they do not download from a remote. Import restores saved generated titles and manual names. Export again to capture later title changes without recreating the archive. Titles on existing chats are restored too, without requiring `--update-existing`.

**If the open ChatGPT desktop / Codex desktop app does not show the imported chats or titles, refresh its list manually; restart the client if they remain absent.** CPT reports this in `native.ui_refresh` and never quits, restarts, or controls the client in the background. `native.verified` confirms native discovery, not that the current window has refreshed its cache.

Select the imported chat in Codex, or resume an ID returned by the tool:

```sh
codex resume SESSION_ID
```

The destination project's effective provider is used by default. To select one explicitly, such as `crs`:

```sh
cpt import ./ --provider crs
```

Options such as `--provider` and `--home` can follow the subcommand. Use `--home` for a different Codex data directory.

### 3. Transfer through a Git remote

On each device, remember an existing authorized remote once:

```sh
cpt init ./ --remote origin
```

Choose the operation you need:

```sh
# Sending device: save local project sessions and upload
cpt upload ./

# Receiving device: download and import without uploading local sessions
cpt download ./

# Current device: synchronize both directions once
cpt sync ./
```

Transfers use the remote's dedicated `codex-sessions` branch without automatically committing project code. Every command is a single operation.

## Availability

Native chat history is transferred; code, external attachments, credentials, and running processes are not. CLI restoration is verified on macOS. Desktop/VS Code GUI continuation and Windows/Linux real-device use still need validation. Ordinary ChatGPT web history is outside scope.

Install `cpt` on each device and configure its Codex provider, model, and credentials separately.
