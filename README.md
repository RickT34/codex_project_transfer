# Codex Project Transfer（cpt）

**换台电脑，接着聊你的项目。**

`cpt` 是一个命令行程序，用于按 Git 项目导出、导入和传输原生 Codex 对话。用户在终端手动执行 `cpt ...`：导出到项目的 `.codex-chats/`，复制到另一台设备后导入，也可以通过 Git 远端上传、下载。

每条命令按需执行一次，没有钩子、后台轮询或自动保存。Codex 仍正常保留自己的聊天历史。

[English](README.en.md)

## 安装

需要 Python 3.10+、Git；导入时的默认 provider 检测、原生登记和续聊还需要已配置好的 Codex CLI。

### 推荐：uv

先按 [uv 官方说明](https://docs.astral.sh/uv/getting-started/installation/) 安装 uv。下载本项目，在包含 `pyproject.toml` 的目录中运行（macOS、Linux、Windows 通用）：

```sh
uv tool install .
uv tool update-shell
```

重新打开终端后验证：

```sh
cpt --version
cpt --help
```

uv 会创建隔离环境并安装 `cpt` 命令，无需激活环境。普通安装会复制程序文件，安装后可以移动或删除下载的源码目录。安装时可能需要联网获取构建依赖；程序本身没有第三方运行依赖。PATH 处理参见 [uv 工具安装说明](https://docs.astral.sh/uv/guides/tools/#installing-tools)。

### 其他安装方式

已安装 pipx 的用户也可以在本项目目录执行：

```sh
pipx install .
pipx ensurepath
```

重新打开终端即可使用 `cpt`。uv 与 pipx 任选一种即可。

也可以只使用 Python 虚拟环境。macOS / Linux：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/cpt --help
```

Windows PowerShell：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\cpt.exe --help
```

这种方式需要保留 `.venv` 所在目录；后续可通过该命令的完整路径操作任意项目，或激活环境后使用 `cpt`。

### 更新与卸载

获取最新源码后，在本项目目录重新安装：uv 使用 `uv tool install --reinstall .`，pipx 使用 `pipx install --force .`；虚拟环境安装使用该环境的 `python -m pip install --upgrade .`。

卸载使用对应的 `uv tool uninstall codex-project-transfer`、`pipx uninstall codex-project-transfer` 或环境内的 `python -m pip uninstall codex-project-transfer`。卸载程序不会删除项目中的 `.codex-chats/` 或 Codex 中的对话。

## 快速开始

安装完成后，进入**需要传输对话的 Git 项目**，而不是本工具源码目录：

```sh
cpt init ./
cpt export ./
```

将整个 `.codex-chats/` 通过git等方式同步到接受端，然后在接收端运行：

```sh
cpt import ./ --dry-run
cpt import ./
```

省略路径默认使用当前目录，也可指定 `cpt export "/path/to/project"`。`cpt --help` 和 `cpt import --help` 可查看命令与选项。

## 使用指南

### 1. 从 Codex 导出到项目

```bash
# 首次使用该项目时初始化，不会上传或保存会话
cpt init ./

# 将项目会话及所需父历史保存到项目的 .codex-chats/
cpt export ./

# 查看本地状态，不上传或下载
cpt status ./
```

如果想一次完成“保存并检查结果”，可用 `check` 代替 `export`。

如果部分会话确实缺少父历史，默认导出会停止。要先保存其余完整会话，可显式运行：

```bash
cpt export ./ --skip-incomplete
```

结果中的 `skipped` 会列出跳过的会话 ID 和原因；发生跳过时退出码为 `2`，表示部分完成。该选项也适用于 `check`、`upload` 和 `sync` 的导出步骤，不会删除原生会话或仓库里已有的快照，也不能修复已有的不完整存档。

首次搬到另一台设备时，将**整个 `.codex-chats/` 文件夹**放到那台设备的对应 Git 项目根目录。可以通过文件复制或自己管理的 Git 提交携带；不要只拷贝单个 JSONL，分叉会话可能依赖其他文件。两端都已有会话快照时，可使用下方的上传/下载流程来保留冲突版本。

### 2. 从项目导入到 Codex

在接收设备安装好本工具，并准备好目标项目和 Codex 的 provider 配置，然后运行：

```bash
# 预览导入计划，不写入会话记录
cpt import ./ --dry-run

# 正式导入，并进行原生登记与校验
cpt import ./
```

这两条命令使用项目中已有的 `.codex-chats/`，不会从 Git 远端下载。导入会恢复存档中的自动生成标题和手动命名；再次导出会更新已变化的标题，无需重新创建会话存档。已有会话的标题也会恢复，不要求使用 `--update-existing`。

**如果已打开的 ChatGPT 桌面端 / Codex 桌面端没有显示新会话或标题，请手动刷新列表；仍未显示时重启客户端。** CPT 会在 `native.ui_refresh` 中提示，不会自动退出、重启或后台控制客户端。`native.verified` 只表示原生服务确认了会话，不代表当前窗口的缓存已刷新。

完成后，可以在 Codex 中选择会话，或使用返回的会话 ID：

```bash
codex resume SESSION_ID
```

默认读取接收端项目的有效 provider。如需显式指定，例如 `crs`：

```bash
cpt import ./ --provider crs
```

`--provider`、`--home` 可直接写在子命令后。`--home` 用于指定其他 Codex 数据目录。

### 3. 通过 Git 远端传输

如果希望工具负责传输，在两台设备上分别配置一次已存在的远端：

```bash
cpt init ./ --remote origin
```

然后按需选择操作：

```bash
# 发送设备：先保存本机项目会话，再上传
cpt upload ./

# 接收设备：下载并导入，不上传本机会话
cpt download ./

# 当前设备：执行一次双向同步
cpt sync ./
```

传输使用远端的独立 `codex-sessions` 分支，不会自动提交项目代码。这些命令都是单次执行。

## 使用范围

- 同步的是原生 Codex 会话；代码、外部附件、登录凭据和运行中的程序需要另行处理。
- 已验证 macOS 上 Codex CLI 的历史恢复。桌面 App、VS Code 的实际界面续聊，以及 Windows/Linux 实机使用仍待验证。
- 普通 ChatGPT 网页聊天不在支持范围内。
- 每台设备单独安装 `cpt`，并配置自己的 Codex provider、模型和登录信息。
