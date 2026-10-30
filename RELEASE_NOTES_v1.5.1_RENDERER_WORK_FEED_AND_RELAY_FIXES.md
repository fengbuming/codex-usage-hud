# codex-usage-hud v1.5.1

- Renderer HUD now exposes the current task state, latest assistant output,
  latest tool activity, and the three most recent tool records in the expanded
  activity panel.
- Provider relay startup is detached from HUD lifetime, preserves old session
  routes, rewrites automatic compaction model fallbacks, and restores source
  evidence subscriptions after helper replacement.
- Pinned sessions and their related trees are protected during provider history
  cleanup, including pins stored in Codex's Desktop sidebar state.
- Top HUD docking, width fitting, native title reveal, and output-speed display
  receive the fixes accumulated after v1.5.0.
- The Windows installer and Python package include `THIRD_PARTY_NOTICES.md`.

## Validation

- Focused renderer, parser, provider-relay, session-cleanup, and work-overlay
  tests pass.
- `python -m compileall -q src tools tests` passes.
- `python tools/pre_release_check.py` passes before tagging.

Windows x64 installer and its SHA-256 sidecar are attached to the GitHub
Release. macOS remains source/pip supported; CI smoke is a code-level gate and
does not replace real-device desktop interaction validation.
