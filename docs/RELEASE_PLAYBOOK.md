# Release Playbook

This repository keeps release instructions local to the worktree so future
patch releases can follow the same path without rediscovery.

## Patch release flow

1. Confirm the change scope is a small user-facing fix and not a breaking
   feature branch.
2. Bump `__version__` in both `src/codex_usage_hud/__init__.py` and
   `codex_usage_hud/__init__.py`.
3. Update `CHANGELOG.md` with a dated release section at the top.
4. Create a matching `RELEASE_NOTES_vX.Y.Z_*.md` body for GitHub Releases.
   `python tools/build_installer.py` also creates a matching installer
   `.sha256` sidecar. Upload both files to the GitHub Release.
5. Update version-sensitive tests or docs that read the package version
   directly.
6. Run the validation set:
   - `python -m pytest`
   - `python -m pytest -m "ui or qt_ui"`
   - `python -m compileall -q src tools tests`
   - `python tools/pre_release_check.py`
7. If the release changes renderer injection, desktop overlay behavior, or
   platform import boundaries, confirm the latest GitHub Actions `macOS Smoke`
   run is green and review
   `docs/DESKTOP_OVERLAY_RELEASE_STRATEGY.md`.
8. Commit the release work with a Lore-style message, then create the
   annotated tag `vX.Y.Z` with a matching tag message.

## Local conventions

- Keep release notes short and specific to the user-visible fix.
- Keep historical release notes untouched once tagged.
- Do not publish an installer without its `.sha256` sidecar. The client also
  reads GitHub's release-asset digest, while the sidecar gives users and
  external mirrors an independently visible checksum.
- Keep the release checklist in this file rather than a global skill so it only
  applies to this repository.
- Treat macOS `macos-latest` smoke as a code-level gate, not as a substitute
  for real-device desktop interaction validation.
- Use `git tag -a vX.Y.Z -m "codex-usage-hud vX.Y.Z"` unless the release says
  otherwise.
