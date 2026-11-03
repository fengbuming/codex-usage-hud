# Development Guide / 开发指南

`codex-usage-hud` is intentionally small, local-first, and standard-library
only. This guide shows how to get a contributor-ready checkout on Windows and
how to validate changes before opening a pull request.

`codex-usage-hud` 的设计目标是轻量、本地优先、仅依赖标准库。本文面向
未来贡献者，说明如何在 Windows 上快速进入可开发状态，以及如何在提
交 PR 前完成基础验证。

## 1. Clone the repository / 克隆仓库

```powershell
git clone https://github.com/fengbuming/codex-usage-hud.git
cd codex-usage-hud
```

If you are working from a fork, clone your fork first and keep the upstream
remote available for synchronization.

如果你是从 fork 开始贡献，请优先克隆自己的 fork，并保留 upstream 远端
用于后续同步。

## 2. Enter development mode / 进入开发态环境

On Windows, run `install.bat` from the project root:

```powershell
.\install.bat
```

What the script does:

- creates a local `.venv` if one does not already exist
- installs the project in editable mode
- tries to make `codex-hud` available in your user PATH
- offers optional persistence registration for startup usage

脚本会执行以下操作：

- 如果本地还没有 `.venv`，就创建一个
- 以 editable 模式安装当前项目
- 尝试把 `codex-hud` 加入你的用户级 PATH
- 提供可选的开机持久化注册方式

For day-to-day development, choose `N` when prompted for persistence. That keeps
your environment clean while still giving you a live editable install.

日常开发时，建议在持久化提示里选择 `N`。这样可以保持环境简洁，同时
保留 editable 安装带来的实时生效能力。

### Persistence options / 持久化选项

- `Y` = Startup folder path with a hidden VBS launcher
- `R` = `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`
- `N` = skip persistence and stay in a development-only setup

## 3. Run the test suite / 运行测试套件

The canonical fast closeout command is staged: it stops at the first useful
failure instead of making you wait for the complete suite:

```powershell
python tools/run_validation.py
```

Install the push gate once per clone using the Python environment that has
pytest and the project dependencies installed:

```powershell
python tools/install_git_hooks.py
```

`git push` exports each outgoing tip into a temporary directory and runs the
same shared validation there. Only committed files are checked; dirty or
untracked files cannot hide a missing fix. Failed checks block the push and
the temporary directory is removed. This local gate does not replace the
Linux/Windows/macOS CI matrix. It never updates Renderer contracts for you:
review the bundle change, run `python tools/update_renderer_contract.py --update`,
and commit both derived files before pushing.

Add feature-specific tests without dropping the shared baseline:

```powershell
python tools/run_validation.py tests/test_renderer_application_menu.py
```

To check an existing commit without pushing, run
`python tools/check_push.py --revision HEAD`. Deleting a remote ref runs no tests.
The installer preserves custom hook setups and reports a conflict instead of
replacing them. Re-run it after changing your Python environment.

CI first runs this shared gate, then starts the platform matrix. Real Qt
helper tests carry `qt_ui` and run in a separate interpreter with the offscreen
plugin, keeping their native lifecycle and shutdown diagnostics separate from
portable tests. They are still required; the split does not discard failures.

Use the direct pytest command when you specifically need the complete suite:

```powershell
python -m pytest -q --durations=10
```

### When to run the full suite / 什么时候跑全量

The staged command is enough for ordinary isolated changes. Run
`python tools/run_validation.py --full` when a change crosses module or
runtime-owner boundaries, changes shared contracts or settings persistence,
touches Renderer/CDP, daemon startup/restart, session tracking, refresh
scheduling, filesystem/SQLite watching, platform integration, test/build
configuration, or fixes a regression/flaky failure whose impact is not
strictly local. It is also the required gate before a release or when a PR or
merge check explicitly requests full regression coverage.

`--full` uses the project's default pytest options, so it is the full default
suite but still excludes tests marked `ui`. For real widget lifecycle changes,
run the UI markers separately:

```powershell
python -m pytest -o addopts="" -m "ui or qt_ui" -q
```

If every collected marker must be included in one run, use:

```powershell
python -m pytest -o addopts="" -q --durations=10
```

Real PySide6 widget lifecycle regressions are marked `ui` and skipped by default.
Run them explicitly when touching desktop-helper widget behavior:

```powershell
python -m pytest -m ui
```

默认分阶段命令会跳过真实 PySide6 桌面 helper 生命周期回归；修改桌面气泡行为时再显式运行 `python -m pytest -m ui`。

## 4. Static syntax check / 静态语法检查

Use Python's built-in compiler pass for a fast syntax-only validation:

```powershell
python -m compileall src tests
```

This does not run tests, but it is a fast and reliable way to catch syntax
errors before CI or a reviewer does.

这不会执行测试，但可以在进入 CI 之前快速发现语法错误。

## 5. Suggested contributor workflow / 推荐贡献流程

```text
clone -> install.bat -> edit -> run_validation -> review -> PR
```

Renderer contract maintenance and the `--full` validation path are documented
in section 3 above; keep the contract update explicit after reviewing a bundle
change.

For anything that touches daemon behavior, startup semantics, or platform
integration, validate on the target OS before opening the PR.

涉及守护进程行为、开机启动语义或平台集成的改动，请尽量先在目标操作系统
上完成验证，再提交 PR。

## 6. Contribution notes / 贡献说明

- Keep the project standard-library only unless there is a very strong reason
  to discuss an exception first.
- Prefer small, reviewable diffs.
- Preserve the local-first and privacy-first boundaries.
- Update or add tests whenever behavior changes.

- 除非有非常充分的理由，否则请继续保持“仅标准库”的技术路线。
- 尽量提交小而清晰的 diff，便于审阅。
- 请始终维护 local-first 和 privacy-first 的边界。
- 任何行为变化都应同步补充或更新测试。

## 7. Future work / 未来方向

PRs exploring Linux/macOS daemon mode adaptation are welcome, especially if the
implementation can stay lightweight, dependency-free, and aligned with the
project's local-first philosophy.

欢迎提交关于 Linux/macOS 守护模式适配的 PR，尤其是那些能够保持轻量、
无额外依赖，并与本项目 local-first 理念一致的实现。
