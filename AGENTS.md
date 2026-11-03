# Codex Project Instructions

## Product direction

Renderer mode is the canonical product direction. Keep future HUD work on the
in-renderer/CDP surface and keep it compatible with both Windows and macOS.

Qt/Tk HUD modes are deprecated legacy surfaces. Do not add new product behavior
to Qt/Tk, do not make Qt/Tk the default or recommended path, and do not solve
renderer performance issues by switching users back to Qt/Tk. Touch Qt/Tk only
for removal, migration, critical maintenance, or tests needed while legacy code
still exists.

Renderer performance work should move toward event-driven updates: no page,
session, config, or filesystem event should mean no recurring CPU work. Polling
is acceptable only as a temporary fallback with conservative backoff and should
be retired whenever a reliable listener exists.

## Codebase graph memory

For every project-related task, first run
`codebase-memory-mcp cli index_status '{"project":"E-Project-codex-usage-hud"}'`,
then use `search_graph` first (or `search_code`) with the task terms before
manual tracing. Skip this for non-project tasks. The index is navigation only:
verify paths, lines, and behavior in the current workspace. Do not automatically
reindex.

## Test scope

Unless the user explicitly requests full validation, do not run repository-wide,
exhaustive, long-running, integration, end-to-end, packaging, or cross-platform
test suites. Run only the smallest focused test set needed to validate behavior
affected by the current task.

## Push validation

Before pushing, keep the shared gate in `tools/run_validation.py` passing and
add focused tests for the changed behavior. Positional test paths supplement
the shared baseline. Enable the committed-snapshot pre-push gate with
`python tools/install_git_hooks.py` on each clone; do not bypass a failing hook.
Renderer changes require reviewing and explicitly updating both derived
contract files with `python tools/update_renderer_contract.py --update`.
Local focused checks do not establish that the remote platform matrix passed.

## Verbose build output

When running verbose build or packaging commands (for example `python tools/build_exe.py`, `python tools/build_installer.py`, PyInstaller, or Inno Setup), redirect full stdout/stderr to a log file and only surface a concise summary or log tail in the conversation. Do not stream full build logs into agent context unless explicitly requested.

Preferred pattern:

```powershell
python tools/build_exe.py *> build_exe.log
Get-Content build_exe.log -Tail 80
```

In Bash-compatible shells:

```bash
python tools/build_exe.py > build_exe.log 2>&1
tail -n 80 build_exe.log
```
