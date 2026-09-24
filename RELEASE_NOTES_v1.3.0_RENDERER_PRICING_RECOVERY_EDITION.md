# codex-usage-hud v1.3.0

Renderer Recovery, Provider Pricing, and Configuration Edition.

## Highlights

- Official OpenAI pricing snapshots can be checked in the background, reviewed
  in the Renderer settings, and applied only after confirmation.
- Local models missing from the current official snapshot remain available for
  local billing and are marked separately from official changes.
- Renderer warmup after unlock and degraded-mode recovery now continue during
  idle periods and re-send the pending HUD state after the page recovers.
- Provider settings support names, normalized base URLs, provider cloning,
  default-provider switching without a HUD restart, and provider-specific
  continuation.
- Session search now handles title and session-ID queries with source-aware
  phrase matching and improved result navigation.
- Fixed white-screen recovery, form-control visibility, HUD search-box overlap,
  rest-reminder duplication, session transfer selection, and active-work bubble
  transitions.

## Security and maintenance

- Provider configuration writes use private temporary files; cloned bearer-token
  configurations are restricted to owner-only permissions on POSIX systems.
- Removed tracked cross-project internal WorkBuddy notes from the repository.

The Windows installer and its `.sha256` sidecar are attached to the GitHub
Release.
