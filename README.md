# Roblox FastFlag Manager

![Version](https://img.shields.io/badge/version-3.3.5-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey.svg)

A desktop application for managing Roblox FastFlags (FFlags). Provides a
unified interface to add, edit, group, import, and apply FastFlags to a
running or to-be-launched Roblox client, with both file-based
(`ClientAppSettings.json`) and live-memory application paths.

<div align="center">
  <img src="https://i.ibb.co/JRQF7LzY/Menu-Picture-Normal-just-the-starting.png" alt="Main interface" width="800">
</div>

---

## Table of contents

- [Features](#features)
- [Supported bootstrappers](#supported-bootstrappers)
- [Installation](#installation)
  - [Standalone installer](#standalone-installer)
  - [Build from source](#build-from-source)
- [Usage](#usage)
- [Configuration and data location](#configuration-and-data-location)
- [Project layout](#project-layout)
- [Updates](#updates)
- [Community](#community)
- [License](#license)

---

## Features

- **Multi-bootstrapper support** — works with vanilla Roblox, Bloxstrap,
  Voidstrap, Fishstrap, and similar bootstrappers.
- **Searchable flag catalog** — indexed list of known FastFlag names with
  prefix-aware type inference (`FFlag`, `DFInt`, `FFloat`, etc.).
- **Presets** — save, edit, reorder, import, and export named flag groups
  via JSON or compressed Base64 strings.
- **Hotkey bindings** — bind individual flags to keys for in-game
  toggling, including value cycling and revert-on-key.
- **Hybrid application** — writes flags to `ClientAppSettings.json` and,
  when Roblox is running, also patches values directly in process memory.
- **Update channels** — manual or automatic updates pulled from GitHub
  Releases, with a built-in changelog viewer.
- **System tray** — minimize-to-tray with single-instance enforcement.
- **Standalone installer** — Windows executable for users without a
  Python toolchain.

## Supported bootstrappers

| Bootstrapper     | Supported |
| :--------------- | :-------: |
| Bloxstrap        | Yes       |
| Voidstrap        | Yes       |
| Fishstrap        | Yes       |
| Vanilla / others | Yes       |

## Installation

### Standalone installer

1. Open the [Releases](../../releases) page.
2. Download the latest `FFM_Installer.exe`.
3. Run it and follow the setup wizard.
4. Launch the application from the Start menu.

### Build from source

**Prerequisites:**

- [Python 3.10+](https://www.python.org/downloads/)
- [Git](https://git-scm.com/downloads)

**Setup:**

```bash
git clone https://github.com/4anti/Roblox-Fastflag-Manager.git
cd Roblox-Fastflag-Manager
pip install -r requirements.txt
python main.pyw
```

The first launch will request administrator privileges (required for
process-memory access) and will create its data directory under
`%USERPROFILE%\.FFlagManager`.

## Usage

1. **Search** — use the search bar to filter the known-flag catalog or
   type a flag name directly.
2. **Add / edit** — set a value; the type is inferred from the flag's
   prefix and validated before save.
3. **Presets** — group flags into presets, reorder them, and share them
   as JSON files or Base64 strings.
4. **Hotkeys** — assign keys to toggle, cycle, or revert specific flags.
5. **Apply** — click *Apply Flags* to write `ClientAppSettings.json` and,
   if Roblox is running, patch values in memory.

## Configuration and data location

All user data is stored under `%USERPROFILE%\.FFlagManager\`:

| File                     | Purpose                                      |
| ------------------------ | -------------------------------------------- |
| `settings.json`          | App settings (theme, window state, options)  |
| `user_flags.json`        | The active flag list                         |
| `presets.json`           | Saved presets                                |
| `fflags_history.json`    | Snapshot history for undo/restore            |
| `known_flags.json`       | Discovered flag-address registry             |
| `logs/fflag_manager.log` | Rolling application log                      |

The repository itself does not ship any user data; these files are
created on first run.

## Project layout

```
.
├── main.pyw              # Entry point (single-instance, admin elevation, bootstrapper)
├── version.json          # Local version string used by the updater
├── requirements.txt      # Runtime Python dependencies
├── build_exe.py          # PyInstaller build script
├── FFM.spec              # PyInstaller spec
├── installer.iss         # Inno Setup installer script
├── ffm_v3_logo.ico       # Application icon
├── src/
│   ├── core/             # Flag, preset, offset, syscall, and Roblox process logic
│   ├── gui/              # PyWebView window + JS API + HTML/JS UI
│   └── utils/            # Config, logging, helpers, updater
└── .github/workflows/    # CI: build EXE + Inno Setup installer on tag push
```

## Community

[Discord server](https://discord.gg/ECekjAkQu7) — support, preset
sharing, and discussion.

## License

MIT — see [`LICENSE`](LICENSE).
