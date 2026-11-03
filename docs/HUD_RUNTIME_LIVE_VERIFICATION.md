# HUD Runtime Live Verification

Last updated: 2026-08-03

This checklist is for the remaining items that are not fully proven by:

- automated tests
- local latency baselines
- in-app browser smoke-host pages

For a mixed automated/manual run, start with:

```powershell
python tools/run_live_acceptance.py --prepare-mode debug
```

That command reruns the automated phase gate, writes a timestamped report under
`artifacts/live_acceptance/`, stops any existing HUD, and starts the HUD in
DEBUG mode for the live checks below.

For the normal-mode diagnostics pass, use:

```powershell
python tools/run_live_acceptance.py --prepare-mode normal --skip-automated-checks
```

To persist manual observations into the generated report, provide a JSON file:

```powershell
python tools/run_live_acceptance.py --prepare-mode none --skip-automated-checks --manual-observations path\to\manual_observations.json
```

Example shape:

```json
{
  "active_session_latency": {
    "status": "fail",
    "observed_ms": 2500,
    "note": "Visible HUD switch took about 2-3 seconds."
  },
  "idle_cpu": {
    "status": "not-run",
    "note": "No Task Manager capture was recorded."
  }
}
```

For a `pass` on a P8 real-App interaction item (for example
`windows_renderer_startup` or `windows_active_session`), include
`"evidence_scope": "real-codex-app"`. Daemon-only samples and Chromium or
smoke-host captures remain non-eligible and are recorded as `FAIL` when marked
`pass`; use `unknown`/`not-run` while the real App check is unavailable.
For idle CPU, also declare a renderer measurement scope; an observation with
`"measurement_scope": "hud-daemon-process-only"` cannot pass even when its
evidence scope says `real-codex-app`.

Keep the artifact eligibility metadata in the observation when it is available.
The acceptance runner rejects `p8_eligible=false` and evidence marked
`INVALIDATED`, `INELIGIBLE`, or equivalent even when the wrapper declares
`evidence_scope="real-codex-app"`. A low-CPU sample with watched input changes
must therefore remain a failed or pending check, never a P8 idle pass.

If you want the tool to collect a simple HUD-process idle CPU sample for the
launched daemon, add:

```powershell
python tools/run_live_acceptance.py --prepare-mode debug --idle-cpu-sample-seconds 60
```

Use this runbook when you need final evidence from a real Codex App window.

## Scope

These are the remaining live-runtime items called out in
[HUD_RUNTIME_COMPLETION_AUDIT.md](/E:/Project/codex-usage-hud/docs/HUD_RUNTIME_COMPLETION_AUDIT.md):

1. Real Codex App end-to-end latency
2. Real Codex App idle CPU / no background work
3. Real Codex App page-lifecycle confirmation beyond localhost smoke-host pages
4. Native Windows theme and drag/resize interaction

## Latest Settled Idle Attempt

The real `app://-/index.html` page was reattached through the public Renderer
bootstrap path and sampled read-only for 60 seconds on 2026-08-03
(`output/playwright/real-codex-app-idle-settled-20260803.json`). The HUD root
stayed ready, its payload signature stayed stable, and the scoped HUD-root
MutationObserver saw zero mutations. The complete Codex App process averaged
about `0.0065%` normalized CPU, but that process measurement cannot isolate HUD
JavaScript CPU.

The sample is **invalidated**, not an idle pass: four watched Codex session
JSONL files changed during the interval, including the current agent rollout.
The artifact records `status=INVALIDATED`, `noWatchedInputChanges=false`, and
`p8_eligible=false`. Do not promote this low-CPU observation to the P8 idle
requirement; repeat only when no watched session, settings, or background input
changes for the full interval.

## Active-Session Role-List Fix Evidence

The real App sidebar inspection confirmed that the selected thread rows are
descendants of `[role='list']` containers. Before the fix, the renderer could
fall back to a row-local parent, producing a title-only selection key with an
empty raw renderer session ID. `activeSessionContainer()` now prefers the
nearest row `[role='list']` ancestor and the document-level `[role='list']`
fallback before broad sidebar selectors.

The read-only real-App artifact
`output/playwright/real-codex-app-active-session-role-list-fix-20260803.json`
records the full result. After bootstrap, the selected 7-row list produced a
canonical ID immediately. Two existing-thread switches converged raw/canonical
state in `138.9-167.7ms`; `selectionSeq` advanced at the same point, while
authoritative `appliedSeq` confirmation took `996.5-1263.2ms`. The original
thread was restored. No Codex request, storage write, or product binding write
was made, and the temporary probe was removed.

## Preparation

1. Stop any existing HUD instance:

```powershell
codex-hud --stop
```

2. Enable DEBUG mode for the verification run:

```powershell
$env:CODEX_USAGE_HUD_DEBUG = "1"
```

3. Start the renderer HUD:

```powershell
codex-hud --daemon
```

4. Confirm the following files are available for evidence collection:

- HUD config:
  `%LOCALAPPDATA%\codex-usage-hud\hud_settings.json`
- daemon log:
  `%LOCALAPPDATA%\codex-usage-hud\daemon.log`
  (2 MiB per file, five rotated backups). Keep `daemon.log` and all
  `daemon.log.*` backups before restarting a stale HUD. Refresh progress is
  sampled at most once every 30 seconds per component; session, task and status
  changes are recorded immediately. Records include PID, thread, phase,
  session/selection identifiers and state-file revision, without prompt or
  command text. A `begin`/phase without its following completion narrows down
  where a sampled refresh stopped. These logs diagnose recurrence; they do
  not constitute lock/unlock acceptance or a fix for stale bubbles.
- runtime diagnostics:
  `%LOCALAPPDATA%\codex-usage-hud\renderer_fallback.log`

5. If possible, start a screen recording before the checks below.

## Live Checks

### 1. DEBUG HUD Ready Row

Goal:
- verify the runtime errors panel appears in a real Codex App renderer and shows
  the ready row with no active errors

Steps:
1. Open Codex App with the HUD attached.
2. Expand the runtime errors panel.
3. Verify the panel shows:
   - `debug.ready`
   - `DEBUG HUD active`
4. Drag the panel to a visibly different position.
5. Refresh or navigate enough to force HUD reattachment / redraw.
6. Verify the panel remains expanded and the position persists.

Evidence to capture:
- screen recording or screenshots before/after drag

### 2. Active Session Switch Latency

Goal:
- verify visible active-session HUD state follows the currently selected Codex
  thread within the `<150ms` target

Suggested setup:
- two threads with clearly different titles or visible token totals

Steps:
1. Open two different Codex threads.
2. Start screen recording.
3. Click thread A, then thread B, multiple times.
4. Compare the click moment with the HUD state change frame-by-frame.

Pass condition:
- HUD state visibly tracks the selected thread within about 9 frames at 60fps
  (`~150ms`)

Current evidence status:
- row/canonical selection is now confirmed against the real `[role='list']`
  sidebar, but authoritative stable confirmation remains above the target;
  keep this check as `fail` until the remaining `appliedSeq`/visible-stability
  tail is resolved.

Evidence to capture:
- screen recording
- optional note of observed worst-case frame delta

### 3. Current Session Append Latency

Goal:
- verify current-session usage reacts to real request activity within the
  `<250ms` target

Steps:
1. Open a live Codex thread.
2. Start screen recording.
3. Submit a short prompt that triggers visible request activity.
4. Observe the first visible request / token usage change in the HUD.

Pass condition:
- visible current-session request/usage state reacts within about 15 frames at
  60fps (`~250ms`)

Evidence to capture:
- screen recording
- optional note of observed worst-case frame delta

### 4. Idle CPU / No Background Work

Goal:
- verify there is no sustained recurring CPU work when the HUD is attached but
  no relevant session/settings/layout/overlay events occur

Steps:
1. Leave Codex App on a stable idle thread.
2. Do not type, switch sessions, resize panels, or trigger overlay commands for
   at least 60 seconds.
3. Observe the HUD process in Task Manager.

Recommended evidence:
- Task Manager `Details` or `Processes` view
- optional Resource Monitor if needed

Pass condition:
- no sustained CPU usage from the HUD process
- no obvious periodic bursts that suggest recurring snapshot rebuilds or payload
  pushes during idle

### 5. Renderer CDP Update Failure Recovery

Goal:
- verify a real Renderer update failure is recorded and clears after the
  update function is restored

Steps:
1. In a local CDP evaluation, retain `window.__codexUsageHudUpdate` and
   temporarily replace it with a function that throws.
2. Trigger `window.__codexUsageHudReportActiveSession(...)` once and wait for
   the DEBUG error count to increase.
3. Inspect `renderer_fallback.log` for `source=cdp` and
   `code=cdp.update_failed`.
4. Restore the retained update function, trigger one more active-session
   report, and confirm a matching `runtime_error_resolved` record and zero
   visible errors.

Evidence must declare `evidence_scope: real-codex-app`; do not use a smoke-host
page for this check.

### 6. Normal-Mode Diagnostics

Goal:
- verify non-DEBUG mode still writes structured diagnostics in a real Codex App
  run

Steps:
1. Stop the HUD:

```powershell
codex-hud --stop
```

2. Disable DEBUG mode:

```powershell
Remove-Item Env:CODEX_USAGE_HUD_DEBUG -ErrorAction SilentlyContinue
```

3. Start the HUD again:

```powershell
codex-hud --daemon
```

4. Reproduce one known runtime error if practical.
5. Inspect `renderer_fallback.log`.

Pass condition:
- a new JSON line appears with:
  - `source`
  - `severity`
  - `code`
  - `message`
  - `context`
  - `firstSeenAt`
  - `lastSeenAt`

## Remaining Manual-Only Items

If the project wants a strict completion claim, these are the most important
manual-only evidence items:

- real Codex App active-session latency under `<150ms`
- real Codex App current-session update latency under `<250ms`
- real Codex App idle CPU / no recurring background work
- native Windows theme and drag/resize persistence

## Close-Out Guidance

After running this checklist:

1. Update [progress.md](/E:/Project/codex-usage-hud/progress.md) with:
   - commands run
   - which live checks passed
   - which checks were not run
   - any platform-specific constraints
2. Update [HUD_RUNTIME_COMPLETION_AUDIT.md](/E:/Project/codex-usage-hud/docs/HUD_RUNTIME_COMPLETION_AUDIT.md)
   to move any newly-proven items out of `Partially proven` / `Unverified locally`.
