# Changelog

All notable changes to this project will be documented in this file.

This project adheres to [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this repository follows Semantic Versioning for release tags.

## [Unreleased]

## [1.2.0] - 2026-09-08

Session Search, Navigation, and Event-Driven Renderer Edition.

### Added

- Persistent session indexing with configurable scope, progress reporting,
  pagination, work-directory filtering, background warming, and physical index
  cleanup.
- Session search with source-aware matches, exact-phrase ranking, index-only
  disclosure, and noise filtering for captured UI/tool output.
- Direct navigation from search results into Codex Desktop conversations,
  including a draggable result preview, native find integration, round-aware
  output targeting, and repeated-match navigation.
- Contributor-facing CI, issue, pull-request, and ownership metadata.
- Background usage detail panel now shows the official English name of each
  Codex App background task next to its Chinese label, and the subtitle line
  now explains that the requests are initiated by the official agent tool and
  describes the official purpose of the selected background task.

### Changed

- Renderer startup and refresh paths defer expensive session and active-work
  scans, warm search state in the background, and favor event-driven updates.
- Work bubbles show the latest tool action immediately and preserve completed
  state more reliably across renderer/session handoffs.

### Fixed

- Session-index completion could freeze or remain blank after reaching 100%.
- Clearing the session index now removes its on-disk database, sidecars, and
  warm snapshot while preserving the disabled state across restarts.
- Search results no longer advertise metadata/tool-only matches as native
  in-thread exact hits, and exact body matches rank ahead of scattered tokens.
- Conversation navigation now handles virtualized content, repeated output,
  collapsed work rounds, and document-order round selection correctly.
- Renderer session handoff, new-session startup state, and active-work refresh
  no longer retain stale state during common transitions.

### Removed

- Removed the legacy standalone Qt and Tk HUD implementations and their
  dedicated visual-capture tool. Renderer remains the only main HUD surface;
  the optional PySide6 desktop work-bubble helper is unchanged.

## [1.1.0] - 2026-08-26

Renderer Delivery, Multi-Provider, and Work-Bubble Edition.

### Added

- Multi-provider support: add/delete/edit providers, per-provider model lists,
  model unit prices, default-provider editable configuration, and
  provider-aware usage sessions.
- Desktop session management: duplicate and migrate sessions with progress,
  open session working directories, and multi-threaded migrations.
- Windows system tray icon with exit menu.
- Codex App background-task switch and background usage audit with
  per-provider English names and role descriptions for background tasks.
- Bottom HUD backend connection breathing light, need-pick tiktoken panel,
  work-directory submenu in the CLI quick menu, and open work-directory
  actions in session management and usage overview.
- Safe cleanup and permanent session deletion with scan progress UI and a
  persisted deleted-usage ledger.
- Work-bubble improvements: motion animations, transparency/click handling,
  filtering of multi-agent sub-session bubbles, and completed-bubble
  restoration.
- Rest reminder schedule-aware reminders and clickable toast buttons.
- Renderer HUD startup/follow reliability: debug-launch support,
  session-switch latency reduction, and new-session reconciliation after
  composer send.

### Fixed

- 用量统计重复累加计算错误 (duplicate accumulation in usage statistics).
- Lock-screen white-screen freeze of Codex Desktop.
- Windows tray provider-id display and default-provider editability.
- Migration residue after session migration; deleted usage ledger for
  deleted providers; historical-session bubbles; bubble memory leaks; bubble
  spread/animations.
- Cross-day budget cache causing zero usage/amount; today/week usage refresh.
- Chinese input in session search box; settings dialog closing during scans;
  rest-reminder persistent test writing real user config.
- Session usage totalling aligned with live subagent totals.

### Changed

- Runtime surface is renderer-only; CDP failures return diagnostics instead
  of falling back to standalone HUDs. Tk/Qt HUD source removed from the
  default import graph.
- PySide6 desktop work-bubble helper ships inside the Windows installer as a
  default-included dependency, based on a slim Essentials Qt core
  (QtCore/QtGui/QtWidgets/Network) so the bundle stays compact (~25MB vs
  52MB full Addons).
- Build tooling excludes PySide6 Addons modules by default for the Windows
  installer (`--no-exclude-qt` restores the full bundle).
- HUD width self-adaptive; non-expanded HUD can be dragged directly.

### Removed

- Legacy standalone Qt/Tk HUD implementations and their dedicated
  visual-capture tool removed from the build path.

## [1.0.5] - 2026-06-28

Renderer Session Follow Hotfix.

### Fixed

- Moved renderer active-session tracking off the Windows native title/UIA hot
  path and onto a CDP binding emitted from the Codex renderer, so sidebar
  clicks and conversation switches can update the HUD without native polling.
- Debounced renderer filesystem wakeups and throttled active work rescans to
  reduce HUD CPU spikes while Codex is being clicked or sessions are switching.
- Resolved renderer thread ids through `state_5.sqlite` before falling back to
  recursive session-file scans, avoiding avoidable disk work during switches.

## [1.0.4] - 2026-06-28

macOS Smoke Workflow Patch.

### Fixed

- Restored the macOS Codex debug-launch argument regression test referenced by
  the `macOS Smoke` workflow.

## [1.0.3] - 2026-06-28

Renderer-Only Cross-Platform Patch.

### Changed

- Switched the runtime surface to renderer-only: legacy `auto`, `qt`, `tk`,
  `pyside6`, and `tkinter` display modes now normalize to `renderer`, and
  CDP failures return diagnostics instead of falling back to standalone HUDs.
- Stopped importing Tk, Qt HUD, PySide6, or the desktop work-overlay helper from
  the default CLI import graph.
- Added macOS CDP active-session probing and Codex debug launch support.
- Removed PySide6 and Tk/PySide hidden imports from the default package and
  Windows PyInstaller build path.
- Restored the PySide6 desktop work-bubble overlay as an optional helper driven
  from renderer sessions when `work_overlay_max_items` is enabled.
- Added a GitHub Actions `macOS Smoke` workflow to verify desktop-overlay
  installability, lazy CLI imports, source compilation, and task-related tests
  on `macos-latest`.

### Fixed

- Hardened Windows-only platform modules so cross-platform imports no longer
  require `ctypes.WINFUNCTYPE` at import time on macOS and other non-Windows
  environments.

## [1.0.2] - 2026-06-18

Completed Bubble Transition Patch.

### Fixed

- Reworked the work-overlay completion animation so cards shrink in place,
  move along the right-side vertical track, and restore back through the same
  path when a finished task becomes active again.
- Restored the information-rich completed bubble badge after the transition,
  including the title, runtime, token/cost/cache metrics, workdir label, and
  click-through affordances.
- Tightened startup filtering so historical completed tasks do not appear as
  fresh completed bubbles before the current runtime has actually seen them run.
- Moved work-overlay session-switch command draining off the UI refresh path so
  click-to-activate stays responsive while the HUD keeps repainting.
- Normalized the CDP websocket handshake authority/origin fields for IPv6 and
  loopback hosts so renderer probing and session switching stay compatible with
  more local debugger endpoints.

## [1.0.1] - 2026-06-11

Archived Sessions Summary Patch.

### Fixed

- Taught usage summary scans to include the sibling `archived_sessions`
  directory when the live `sessions` tree is summarized, so day/week totals
  stay correct after archived logs move out of the active folder.
- Extended active-session discovery and path resolution so archived session
  trees are considered alongside the live `sessions` tree, and unmatched
  tracker states can still fall back to the newest viable session.
- Kept the realtime watcher polling after event gaps, which helps the HUD
  recover when title events pause before the next backstop poll.
- Removed the redundant close button from the Tk expanded header so the top
  controls match the current layout and the close action stays in the window
  controls.

## [1.0.0] - 2026-06-09

Windows Installer Edition, the first supported installer-based release line.

### Added

- Added GitHub Release update helpers for checking the latest release, selecting
  the Windows setup asset, downloading it, and launching the installer.
- Added CLI update surfaces: `--version`, `--check-update`, and `--update`.
- Added a renderer HUD "版本更新" settings tab with current version display,
  update checking, and installer launch commands.
- Added a Tk settings "版本更新" tab so the fallback UI exposes the same version
  and update flow.
- Added an Inno Setup 6 installer script and `tools/build_installer.py` to
  produce `codex-usage-hud-vX.Y.Z-windows-x64-setup.exe`.
- Added README screenshots for the v1.0.0 HUD and update settings surfaces.
- Added `README_EN.md`; `README.md` now defaults to Chinese with an English
  companion file.

### Changed

- Promoted the package version to `1.0.0` and metadata to stable release status.
- Updated the PyInstaller build helper to collect bundled QR-code image assets
  into the single-file executable.
- Reworked the GitHub homepage README around the reference module structure:
  quick start, sponsors, support, features, pain points, auto update and
  installer, data locations, FAQ, development, and notes.
- Marked `v0.1.0`, `v0.2.0`, and `v0.3.0` as historical alpha / preview tags
  rather than recommended install entry points.

### Fixed

- Made the packaged Windows flow stop an existing HUD before replacing the
  installed executable, reducing update failures from locked files.

## [0.3.0] - 2026-06-05

Renderer Timeline Edition, focused on keeping the HUD in the Codex renderer
surface while preserving the Tk fallback path.

### Added

- Added a renderer-injected HUD driven through local Chrome DevTools Protocol,
  with top session/budget status and bottom request timeline panels rendered
  inside the Codex UI.
- Added CDP-based active conversation probing so the HUD can resolve the active
  Codex thread by session id or title before falling back to native title
  tracking.
- Added CLI switches to prefer the renderer HUD by default and force the legacy
  Tk HUD with `--tk-hud` / `--no-renderer-hud`.
- Added regression coverage for CDP target selection, renderer payloads, and
  renderer HUD client installation/update behavior.

### Changed

- Updated the live HUD command to prefer renderer injection when Codex exposes a
  local CDP target, with automatic fallback to Tk when renderer injection is not
  available.
- Improved top HUD anchoring so long Codex conversation titles, the three-dot
  conversation menu, and right-side header actions are treated as hard
  avoidance zones.
- Improved bottom request HUD anchoring so it follows the composer footer row
  and remains stable when attachments or image previews expand the composer.
- Refined request timeline formatting so cache hit rate follows input tokens,
  cached tokens are second-last, and total tokens are last.
- Kept Tk anchoring and geometry behavior aligned with the renderer follow
  model while preserving its fallback role.

### Fixed

- Preserved the application title-bar drag region while allowing HUD panels to
  be moved and resized inside the renderer.
- Removed duplicate reset controls and redundant HUD glyphs that cluttered the
  renderer panels.
- Fixed the bottom expanded timeline header so "round flow / newest first" no
  longer overlaps the request list.

## [0.2.0] - 2026-05-29

Smart Daemon Edition and repository polish for the current release line.

### Added

- Added Windows daemon mode for low-noise session tracking and auto-attach.
- Added optional startup persistence paths in `install.bat`.
- Added a single-file EXE build helper for local packaging.
- Added a fuller README homepage structure with install, usage, release, and maintenance sections.
- Added a top-level contributing guide so new contributors have a single entry point.

### Changed

- Synced the package version with the current `v0.2.0` tag.
- Updated project metadata links to the current repository.
- Aligned the release history entry points across README, changelog, and release note drafts.

## [0.1.0] - 2026-05-28

First public alpha release. The project has moved from a single-file prototype
into a modular, installable Python package with local-first data handling,
cross-platform discovery, a live SSE state machine, and a polished Tk HUD.

### Added

- Rebuilt the original single-file proof of concept into a standard `src/`
  package layout with separate `core`, `platforms`, and `ui` layers, plus CLI
  and module entry points.
- Added multi-platform Codex data discovery for Windows, macOS, and Linux, with
  fallback probing of the standard local Codex data directories.
- Added resilient JSONL parsing that tolerates partial trailing lines and
  reconstructs session state from local logs without crashing on incomplete
  writes.
- Added a read-only SQLite SSE state machine for live request tracking, using
  `mode=ro` access so local Codex databases are not locked by the HUD.
- Added accurate token accounting, including cached input tokens, reasoning
  tokens, and model-aware pricing with cached-token discount handling.
- Added rolling day/week usage summaries and budget threshold warnings directly
  from local session history.
- Added active-session mapping from conversation title, session index, and
  thread metadata so the HUD can follow the current conversation more reliably.
- Added a Tk HUD with dock/attach behavior, expanded drawer views, draggable
  and resizable windows, and long-text auto-scroll / numeric tween animations.
- Added CLI modes for one-shot snapshots and live HUD operation, plus compact
  output for terminal use.
- Added a zero-dependency runtime built entirely on the Python standard
  library.
- Added regression coverage that locks the parser, calculator, platform
  discovery, active-session resolver, and UI behavior in 39 unit tests.

### Changed

- Standardized the project into an installable package with `pyproject.toml`, a
  `codex-hud` console script, and `python -m codex_usage_hud` support.
- Separated platform-specific logic into dedicated adapters instead of mixing
  OS branching into the UI and parser layers.
- Refined session selection so pinned file, pinned session id, active-session
  follow, and activity-based fallback all resolve through one path.
- Improved status output so the CLI and HUD present session tokens, current task
  tokens, today/week totals, and budget state consistently.
- Persisted HUD geometry and window state so docked and free-floating layouts
  survive restarts.
- Tightened the overall engineering surface to favor reusable primitives,
  explicit boundaries, and easier future maintenance.

### Security

- Added [docs/PRIVACY.md](docs/PRIVACY.md) as a hard local-first privacy
  boundary.
- Added a GitHub issue template that warns contributors not to upload raw
  JSONL logs, raw SQLite databases, or unredacted prompts/responses.
- Kept local database access read-only and avoided telemetry, upload, or
  network paths by design.
