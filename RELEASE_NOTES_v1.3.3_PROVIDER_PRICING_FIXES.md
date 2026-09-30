# codex-usage-hud v1.3.3

This patch release fixes provider deletion, provider migration, pricing refresh, and CI reliability.

## Fixed

- Deleted providers no longer reappear in the live HUD registry or provider switch menu.
- Stale pricing mirrors are supplemented from the bundled snapshot so model price lists remain complete.
- Applying a confirmed price update refreshes the existing session estimate without replacing the top HUD with a recalculation state.
- Slow provider migrations close the fallback copy/migrate dialog after the direct migration succeeds.
- Missing target provider credentials now produce an actionable message instead of an opaque `Missing environment variable` failure.
- Cross-platform CI provider notification and pricing synchronization tests are separated correctly.
