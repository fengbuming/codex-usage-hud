# codex-usage-hud

[中文](README.md) | English

> **Live token usage, cache hit rate, and real cost inside Codex App — fully local, no data uploaded.**
> Stop API relay "background spending" and blind-waiting on long tasks.

[![Release](https://img.shields.io/github/v/release/fengbuming/codex-usage-hud?label=release)](https://github.com/fengbuming/codex-usage-hud/releases)
[![License](https://img.shields.io/github/license/fengbuming/codex-usage-hud)](LICENSE)
[![Windows](https://img.shields.io/badge/Windows-supported-0078D4)](https://github.com/fengbuming/codex-usage-hud/releases)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB)](pyproject.toml)
[![GitHub Stars](https://img.shields.io/github/stars/fengbuming/codex-usage-hud?style=social)](https://github.com/fengbuming/codex-usage-hud/stargazers)

`codex-usage-hud` injects a usage panel directly into the Codex UI (renderer injection, not a separate floating window): session tokens, cache hit rate, live USD estimate, daily/weekly budgets, and waiting status all on one screen. All data is read from your local Codex JSONL / SQLite logs only. **No telemetry, no prompt/response upload, no cloud account required.**

### Why you need it
- 💸 **Cost transparency, stop hidden spending** — API relays have opaque billing, and background requests can quietly run for hours before you notice. The HUD keeps your session/daily/weekly cost visible right next to Codex so you catch anomalies immediately.
- ⏳ **No more blind waiting on long tasks** — see in real-time if a request is running, which tool is slowest, and how long you've been waiting. Know if it's still working or if you should intervene.
- 🔒 **Privacy first** — fully local, zero telemetry, open-source and auditable.

## Quick Start

Download the latest Windows installer from [GitHub Releases](https://github.com/fengbuming/codex-usage-hud/releases):

- Windows: `codex-usage-hud-v*-windows-x64-setup.exe`

After installation, Start Menu shortcuts are available:

- `Codex Usage HUD`: daemon entry; when Codex App is not running, it launches Codex directly with the fixed debug/CDP port and injects the renderer HUD. If the HUD observes a fresh ordinary Codex launch while waiting, it automatically switches that launch to HUD-owned CDP mode. Codex instances that predate the wait, process-audit failures, and port conflicts still require an explicit **Restart Codex** click and are never interrupted automatically.
- `Stop Codex Usage HUD`: stops the running HUD.
- `Check for Updates`: checks GitHub Releases for a newer installer.

CLI commands are also available:

```powershell
codex-hud --once
codex-hud --daemon
codex-hud --stop
codex-hud --check-update
codex-hud --update
```

Providers without a local proxy or custom Desktop model keep their real upstream URL in `config.toml`. Once Codex reads that configuration, requests work even when neither the HUD nor its relay has been started since boot. Saving, selecting a default, and cloning providers all follow this rule.

Providers requiring a proxy or model rewriting, and cached loopback endpoints from existing sessions, use the independent relay. The HUD starts it automatically; `--stop` leaves the relay and old routes running. New direct configurations need no relay, and existing direct providers can migrate back to their upstream URL even if the relay is unavailable.

## Sponsors

[Want to be listed below?](https://github.com/fengbuming/codex-usage-hud/issues)

| Sponsor | Description |
| --- | --- |

## Community and Support

For questions, suggestions, or discussions, head to [GitHub Issues](https://github.com/fengbuming/codex-usage-hud/issues). I actively monitor and respond there.

## Main Features

- Renderer-only HUD: when Codex exposes a local CDP target, the HUD renders inside Codex App. New Codex launches observed while the HUD is alive either attach to their declared CDP port or go through one automatic CDP takeover; existing or ambiguous instances retain the explicit restart action. The app never falls back to Qt/Tk standalone windows.
- PySide6 desktop work bubbles: source / pip installs can enable square running bubbles and completed circular bubbles with `codex-usage-hud[desktop-overlay]`; the main HUD remains renderer-only.
- Codex theme sync: Renderer mode follows the live Codex App theme; without an available CDP target, diagnostics point you to restart Codex App with the debug port enabled.
- Real-time tokens and cost: input, cached input, output, reasoning, total, cache hit rate, and live USD estimate.
- Day/week budgets: custom limits, reset time, weekly reset day, thresholds, and manual weekly adjustment.
- Work status visibility: current activity, longest wait, slowest tool, and request timeline.
- Local settings: stored in the current user's `hud_settings.json`.
- Auto update: both settings UI and CLI can check GitHub Releases and launch the Windows installer.
- Windows installer: v1.0.0 ships an Inno Setup installer with Start Menu and optional desktop shortcuts.

## Pain Points and Fixes

Relay token usage and billing can be opaque, and background spending is hard to notice. `codex-usage-hud` keeps session, daily, and weekly cost next to Codex, with cache hit rate and estimated cost visible in real time.

![Live HUD Demo](docs/images/demo-hud-animation.gif)

Long Codex tasks can feel like blind waiting. The HUD shows whether the current request is still running, when it last refreshed, which tool was slowest, and where time is being spent. Desktop bubbles show task completion status in real time.

![Completion Badge Demo](docs/images/demo-completion-badges.gif)

Budget windows differ between users and providers. You can customize daily/weekly limits, reset dates, reset times, thresholds, and manual weekly adjustments to mirror your own relay or team budget.

![Settings panel](docs/images/demo-settings-panel.png)

Older script-based startup and shutdown flows were awkward. v1.0.0 adds a Windows installer, Start Menu daemon shortcut, stop shortcut, and update shortcut so daily use no longer depends on remembering Python commands.

![Version and update settings](docs/images/codex-usage-hud-v1-update.png)

Raw usage data is split across JSONL files, SQLite databases, and session indexes. The HUD merges those local sources into one snapshot and keeps `codex-hud --once` for quick checks and automation.

## Auto Update and Installer

Starting with v1.0.0, `codex-usage-hud` is released through GitHub Release Windows installers:

- Installer name: `codex-usage-hud-vX.Y.Z-windows-x64-setup.exe`
- Build command: `python tools/build_installer.py`
- Installer technology: Inno Setup 6
- Default install location: `%LOCALAPPDATA%\Programs\codex-usage-hud`

The settings UI has a Version Update tab that can check the latest release and launch the installer. CLI equivalents:

```powershell
codex-hud --check-update
codex-hud --update
```

The installer runs `codex-hud --stop` before replacing files so the previous HUD process does not keep the executable locked.

Current release strategy:

- Official releases are still centered on the Windows installer, and the default packaged build does not bundle PySide6 desktop bubble dependencies.
- If desktop work bubbles are needed today, the recommended path is a source / pip environment with `codex-usage-hud[desktop-overlay]`.
- macOS does not currently ship an installer; it stays on the source / pip path plus `macOS Smoke` code-level validation. See [docs/DESKTOP_OVERLAY_RELEASE_STRATEGY.md](docs/DESKTOP_OVERLAY_RELEASE_STRATEGY.md).

## Data Locations

- Codex session logs: `~/.codex/sessions/`
- Codex SSE / state databases: `~/.codex/logs_2.sqlite`, `~/.codex/state_5.sqlite`
- HUD settings: `%LOCALAPPDATA%\codex-usage-hud\hud_settings.json`
- HUD daemon log: `%LOCALAPPDATA%\codex-usage-hud\daemon.log`
- Default install directory: `%LOCALAPPDATA%\Programs\codex-usage-hud`

## Codex Theme Sync

The HUD can now follow the active Codex App theme, including light/dark
variants and `Copy theme` share strings. For implementation details, theme
export steps, and current limitations, see
[docs/CODEX_APP_THEME_SYNC.md](docs/CODEX_APP_THEME_SYNC.md).

## FAQ

### Are the old v0.x tags still recommended?

No. `v0.1.0`, `v0.2.0`, and `v0.3.0` remain as historical alpha / preview tags. `v1.0.0` is the first supported Windows installer release.

### The HUD does not appear inside Codex

Start it from `Codex Usage HUD` or `codex-hud --daemon`. The HUD uses one fixed local CDP/debug port. If Codex App is not running, the HUD launches it directly with CDP enabled and connects once. If an existing Codex instance exposes the matching port, the HUD connects directly. If CDP is unavailable or the port does not match, the top-right startup panel offers a **Restart Codex** button; Codex is closed and relaunched only after that click. The daemon remains alive after Codex exits: a later launch with a declared CDP port is attached directly, while an ordinary launch is automatically switched through one HUD-owned CDP relaunch. `--no-startup-prompt` remains accepted for compatibility with older startup entries but no longer changes these paths.

### Desktop work bubbles do not appear

The `work_overlay_max_items` setting controls the number of PySide6 desktop-level session bubbles; set it to `0` to disable desktop bubbles. If `codex-usage-hud[desktop-overlay]` is not installed, the renderer HUD still works and records a single `work_overlay_unavailable` diagnostic.

### Does it upload prompts or logs?

No. The project reads local logs and databases only. It does not send telemetry, upload prompts/responses, or require a cloud account. Read [docs/PRIVACY.md](docs/PRIVACY.md) before sharing issue screenshots or logs.

## Development

```powershell
python -m pip install -e ".[desktop-overlay]"  # optional: PySide6 desktop work bubbles
python -m compileall -q src tools tests
python -m pytest
python -m pytest -m ui        # optional: real PySide6 desktop-helper regressions
python tools/build_exe.py
python tools/build_installer.py
```

macOS without a local device:

- The GitHub Actions `macOS Smoke` workflow runs on `macos-latest` and covers `codex-usage-hud[desktop-overlay]` installation, lazy imports, `compileall`, and task-related pytest coverage.
- This workflow is code-level smoke coverage only, not a substitute for real desktop interaction validation; the current round does not use a remote Mac, so macOS desktop bubbles are still pending real-device confirmation. See [docs/MACOS_VALIDATION.md](docs/MACOS_VALIDATION.md) for the parked manual checklist.

Project structure:

```text
src/codex_usage_hud/
  cli.py                 CLI, daemon, and update command entry
  daemon.py              Windows/macOS Codex Desktop process listener
  ui/renderer_hud.py     Codex renderer-injected HUD
  ui/work_overlay_qt.py  Optional PySide6 desktop work-bubble helper
  updater.py             GitHub Release update check and installer launch
tools/
  build_exe.py           PyInstaller single-file exe build
  build_installer.py     Inno Setup installer build
  installer/             Inno Setup script
tests/                   Parser, UI, daemon, packaging, and updater tests
```

## Notes

`codex-usage-hud` is an external local monitoring tool. It does not modify Codex App installation files. Codex App or log format changes may require parser or renderer-injection updates.
