# codex-usage-hud v1.3.2

Codex Desktop UI Compatibility and Recovery Patch.

- Eliminates repeated budget-badge DOM writes and synchronous reflows observed
  dominating the renderer CPU profile during active conversations.
- Restores custom model and reasoning-option augmentation in the redesigned
  Codex Desktop model picker.
- Restores session-search keyword filling and native highlighting with the new
  dynamically generated find-input IDs.
- Changes renderer-stall handling to pause HUD injection without automatically
  restarting Codex or repeatedly reinstalling into an unresponsive renderer,
  with a dismissible notice that clears after recovery.
- Identifies Codex task catch-up summaries and the current task-title generator
  in background usage instead of grouping them under unknown tasks.
- Keeps Codex clickable while the rest reminder is visible and removes the
  full-screen blur/animation that could add renderer compositing pressure.

The Windows installer and its `.sha256` sidecar are attached to the GitHub
Release.
