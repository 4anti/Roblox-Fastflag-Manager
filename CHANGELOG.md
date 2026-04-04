# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.3.3] - 2026-04-05

### Added

- **Manual Update Mode**: New installs now default to manual updates. Users can manually trigger an update from the Settings tab via a professional animated update overlay.
- **In-App Changelog**: When an update is available, the changelog fetched directly from GitHub is beautifully rendered inside the Settings tab.
- **Auto Update Toggle**: The ability to opt back into silent background updates at launch has been added to Settings.

### Changed

- Refactored `updater.py` to extract GitHub release notes silently.
- Updated main launch sequence to respect the user's update setting before attempting background patching.

## [3.3.2] - 2026-04-04

### Fixed

- Fixed an issue causing application crashes on startup for certain users (#8).

## [3.3.1] - 2026-04-04

### Changed

- Adjusted Git ignore properties to improve development workflow for builders and contributors.

## [3.3.0] - 2026-03-28

### Added

- **Surgical Discovery**: Re-engineered Roblox bootstrap targeting. Automatically targets Bloxstrap, Voidstrap, Fishstrap, or vanilla launcher processes directly to retrieve correct directories dynamically.
- **In-App Notifications**: Modernized UI to include silent popup statuses and toast notifications instead of intrusive prompt boxes.
- **Scatter Sync**: Implemented background synchronization for presets across multiple config layers.

### Changed

- Enhanced premium glass UI theme constraints.
- Optimized codebase memory usage and initial render time by migrating to PyWebView.
