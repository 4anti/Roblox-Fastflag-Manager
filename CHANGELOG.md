# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
