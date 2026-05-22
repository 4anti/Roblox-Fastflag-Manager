# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [3.3.8] - 2026-05-22

### Changed

- Offset source priority: the GitHub mirror (`data/FFlags.hpp`) is now
  tried **before** `offsets.ntgetwritewatch.workers.dev` in the fetch
  chain (`offset_sources.py`). On Roblox builds where imtheo's dumper is
  offline, workers.dev serves a dump whose **numeric (FInt/FFloat)
  pointers are wrong** — they resolve into read-only `.rdata`, so those
  flags silently fell back to JSON-only instead of applying via live
  memory. Prioritizing our verified mirror fixes this. Revert when
  imtheo's dumper is back for the current build.
- `data/FFlags.hpp` updated to a Polaris-format dump for
  `version-4b6315bf1f0a4dbb` (13,227 offsets). Every pointer was verified
  against the live executable to resolve to writable `.data` with the
  correct default value (e.g. CameraMaxZoomDistance=400,
  VoiceChatVolumeThousandths=1000). A small `FFlagOffsets` struct block
  is included so the existing loader/validator accepts it with no code
  change; the bundled baseline is refreshed to match.
- Offset fetch chain now uses `offsets.imtheo.lol/FFlags.hpp` as the
  secondary imtheo source in place of `imtheo.lol/Offsets/FFlags.hpp`.
  Both serve byte-identical Format A content; the new host is the
  current canonical mirror. Applied to the in-app loader
  (`offset_sources.py`) and the `mirror-offsets.yml` GitHub Action
  (both the `.hpp` and `.json` chains).
- The logo's "NNK+ FastFlags Available!" count is now generated from
  `data/FFlags.hpp` by `update_version.py` at release time (was a
  hardcoded "13K+"), so it stays in sync with the actual offset count.

### Fixed

- Numeric flags (FInt/FFloat — camera zoom, simulation radius, sender
  rates, etc.) apply via **live memory** again instead of being marked
  "JSON-only". They were JSON-only because the workers.dev mirror pointed
  them at read-only `.rdata`; the corrected `data/FFlags.hpp` points them
  at the real writable storage. (Boolean flags were unaffected — their
  pointers were always correct.)
- `JSON-ONLY` log lines now include the region detail (flag type,
  address, page protection) instead of just the flag name, so an
  unwritable pointer can be diagnosed at a glance (`flag_manager.py`).
- AOB scanner robustness: `find_pattern` now walks committed, readable
  memory regions via `VirtualQueryEx` and tolerates partial reads
  (`STATUS_PARTIAL_COPY`) instead of skipping an entire 10 MB chunk
  whenever a single page in it is unreadable. The old all-or-nothing
  read silently skipped large spans of the (Hyperion-protected) Roblox
  image, which could make valid signatures unfindable. Adds a `[scan]`
  coverage log line (regions scanned / read failures) to distinguish a
  genuinely-absent pattern from a scan foiled by unreadable memory.
- FPS unlock (`TaskSchedulerTargetFps`) applies again. It now writes the
  flag's dumped offset via the normal live-memory path (a dynamic value
  Roblox re-reads at runtime) instead of a hardcoded byte-pattern hook whose
  signature went stale on current (Hyperion) builds. The stale hook made the
  flag wrongly show as "failed / Unavailable" even though the value is
  writable and takes effect. Note: the JSON FFlag method for FPS no longer
  works on current Roblox — FFM applies this one via memory.
- Mirror workflow no longer commits truncated/stub offset dumps. When the
  upstream dumper serves a near-empty file mid-Roblox-update (only the 3
  `FFlagList` struct offsets), the auto-refresh used to accept it — nuking
  `data/FFlags.hpp` and collapsing the README badge to "3". The fetch now
  requires >=500 offsets, the badge is derived from the committed `.hpp`
  (not the JSON, which some mirrors don't provide a count for), and
  `update_version.py` refuses to bundle a <500-offset baseline at release.

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
