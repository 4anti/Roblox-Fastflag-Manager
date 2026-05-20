# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.3.7] - 2026-05-20

### Added

- Six-source offset fallback chain so users behind antivirus SSL
  interception, corporate firewalls, or with imtheo.lol temporarily
  unreachable can still load offsets. Order:
  1. imtheo.lol via Python requests
  2. imtheo.lol via system `curl.exe` (Windows native SSL / schannel)
  3. GitHub mirror via Python requests
  4. GitHub mirror via `curl.exe`
  5. Disk cache (`~/.FFlagManager/offsets_cache.json`)
  6. Bundled baseline (shipped inside the .exe — guaranteed to work
     even on first run with no network)
- `data/FFlags.hpp` GitHub mirror, auto-refreshed every ~6 hours by a
  new `.github/workflows/mirror-offsets.yml` workflow.
- `src/data/FFlags_baseline.hpp` shipped with every installer build;
  refreshed at release time by `scripts/update_version.py`.
- Captive-portal / proxy-error rejection: a fetched body must parse to
  >=500 flags AND a valid `FFlagList.Pointer` before being accepted,
  preventing AV intercept HTML from poisoning the disk cache.
- Per-source startup telemetry line (`[OK] Offsets source: <id>, ...`)
  plus `offset_source` and `baseline_stale` fields on the loading
  status API for the UI to surface.

### Changed

- Cache file relocated from the install directory (`Program Files\...`)
  to `~/.FFlagManager/offsets_cache.json`. The old in-repo location was
  not writable by non-admin processes after Inno install, which
  silently disabled the cache fallback for many users. One-shot
  migration copies the old file forward on first run.
- Cache writes are now atomic (write-to-tmp + `os.replace`) so a crash
  mid-write cannot corrupt the cache.
- Cleaner error messages: long `HTTPSConnectionPool(...)` tracebacks
  are replaced with short per-source `[!] host via path: reason` lines.
- Redesigned GitHub and Discord buttons in Settings > About with SVG
  icons (Octocat and Discord mark) in a tall card-style layout.
- Developer avatar in About section now fetches the real GitHub profile
  picture, falling back to the static "4" if offline.

### Fixed

- White-on-white hover bug affecting all subtle buttons in light theme
  (text and SVG icons were invisible on hover).

## [3.3.6] - 2026-05-16

### Added

- "Clear allowed FFlags on exit / when Roblox closes" toggle in
  Settings (default ON for new installs). When enabled, FFM
  overwrites `ClientAppSettings.json` with `{}` across every
  detected Roblox version directory in three situations:
  - the app exits (UI exit button or tray Exit),
  - Auto Apply is turned OFF while Roblox is not running, and
  - the running Roblox process exits (one-shot transition
    detected by the background monitor).
  This ensures no leftover allowed FFlags take effect on the next
  Roblox launch when FFM isn't actively applying.
- `RobloxManager.clear_fflags_json()` helper that mirrors the
  existing scatter-sync write path used by `apply_fflags_json`.

### Removed

- "Emergency Revert" / "Execute Panic Revert" button and the
  underlying `panic_revert` API method. Restoring the original
  values of arbitrary FFlags requires a complete defaults table,
  which FFM does not have, so the button could not honour its
  promise. The new auto-clear toggle is the supported kill-switch.
- "Rescan FFlag Offsets" button (and its `rescan_offsets` API
  method). FFM has sourced offsets from Imtheo since 3.3.5, so the
  user-facing rescan no longer reflects how the app actually
  discovers flag locations. The Settings → Safety & Reset section
  is removed as a result. Internal scanning helpers used by the
  normal apply flow are unchanged.

## [3.3.5] - 2026-05-03

### Added

- Imtheo-based offset loader (`src/core/offset_loader.py`) with offline
  disk-cache fallback and Roblox build-version mismatch warnings.

### Changed

- FlagManager now sources known flags and types from Imtheo.
- Removed legacy local scanner and unused `src/native/` C++ helpers.
- Repo cleanup: rewrote `README.md` / `SECURITY.md`, expanded
  `.gitignore`, switched CI to GitHub's auto-generated release notes.

### Fixed

- Right-click context menu (was throwing `ReferenceError` on an
  undefined `f` variable in `showContextMenu`).
- `build_exe.py` no longer imports the deleted `generate_icon` module.

## [3.3.4] - 2026-04-05

### Fixed

- Update flow now correctly triggers the Windows UAC elevation prompt
  when applying an update from the background updater.
- The application is automatically relaunched after Inno Setup completes
  an update (silent installer flags adjusted).

### Changed

- Tightened error handling around `ShellExecuteW` calls in
  `src/utils/updater.py`.

## [3.3.3] - 2026-04-05

### Added

- Manual update mode (now the default for new installs). Updates can be
  triggered from the Settings tab.
- Changelog viewer in the Settings tab, fetched from the GitHub release
  body when an update is available.
- "Auto update" toggle in Settings to opt back in to silent background
  updates.

### Changed

- `src/utils/updater.py` now extracts GitHub release notes alongside the
  installer URL.
- The main launch sequence respects the user's update mode before any
  network call.

## [3.3.2] - 2026-04-04

### Fixed

- Startup crash affecting some users (#8).

## [3.3.1] - 2026-04-04

### Changed

- `.gitignore` adjustments for development workflow.

## [3.3.0] - 2026-03-28

### Added

- Multi-bootstrapper detection: Bloxstrap, Voidstrap, Fishstrap, and
  vanilla Roblox processes are now targeted directly so directories are
  resolved dynamically from the running launcher.
- In-app toast notifications replace blocking prompt dialogs for status
  messages.
- Background preset synchronisation across config layers.

### Changed

- UI migrated to PyWebView; reduced memory usage and initial render
  time.
