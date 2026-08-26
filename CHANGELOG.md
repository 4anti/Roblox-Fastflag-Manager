# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [4.2.0] - 2026-08-26

### Added

- **Copy flag name:** right-click a flag in the Editor and the clipboard
  icon sits next to the orange name (wraps with long names). Copy does
  not close the menu; drag still works from the name.
- **Black & White UI theme:** flat black surfaces, or full white with
  Light Mode. No color accents. Last item in the UI Theme list. Light
  Mode stays available on this skin.

### Fixed

- **Settings and Presets scrollbars:** inset like the Editor so the
  window-edge resize strip no longer steals the thumb or wheel.
- **Pasted `DFlag…` names:** a leading `DFlag` that is not a real prefix
  (`FFlag` / `DFFlag` / `FInt` / `DFInt` / …) is stripped on import and when
  `user_flags.json` loads, so `DFlagEnablePerFrameSampling` matches the dump
  name `EnablePerFrameSampling`. Real `DFFlag` / `DFInt` names are unchanged.
- **Play / Automatic Launch start-only flags:** writes `ClientAppSettings.json`
  into the folder about to launch, flushes it, reads it back, and retries
  once. Roblox is not started if the keys are still missing. Values are
  stored as `"True"` / `"False"` strings. Unprefixed bool names (for example `DebugDisplayFPS`)
  are written as `FFlag...`. Idle JSON-clear waits out the Play write so it
  cannot wipe the file mid-launch.

### Changed

- **Legacy is first in the UI Theme dropdown.** Black & White is last.
- **App version is 4.2.0.**
- **Auto Apply leaves ClientAppSettings.json on disk** when Roblox or FFM
  closes, so the next Play/shortcut already has flags in the version
  folder. Turning Auto Apply off still clears. Auto Apply skips a second
  JSON write when the file is already staged. Join Roblox polls until
  memory is readable instead of sleeping 1s after it is. The monitor
  checks for a new Roblox PID about every 0.25s until one is attached.

## [4.1.0] - 2026-08-15

### Added

- **Uninstall user data:** after the usual Yes/No prompt, Setup shows a
  User data page on the same wizard as the rest of uninstall. Tick
  settings, presets, custom flags, flag history, or caches to delete
  them. Unticked items stay. Logs under `.FFlagManager\logs` are always
  removed. Silent uninstall skips the page and only deletes logs.
  Program Files is always removed. Roblox files are not.

### Fixed

- **Update Now:** exits as soon as Setup starts so the installer can replace
  FFM.exe. The app used to stay open for a second and hide the close-apps
  prompt. Timing matches Auto Update. Canceling UAC leaves this session
  running.
- **Advanced version card:** no longer flashes "Roblox version fixed" then
  snaps back to mismatch. Refresh used to always download the latest
  client and paint "fixed" even when you were already on it; the 1-second
  poll then compared the install to the offset dump (often still a build
  behind). The card now splits three cases: Roblox is behind (a download
  helps), Roblox is current and the dump is catching up (no download),
  and a real match. Live-memory apply still skips when the dump does not
  match the running client.
- **Flag-file saves:** no longer bump a folder timestamp, so an older install
  or third-party launcher tree is not picked as the current client. FFM
  follows the running Roblox process when one is open, otherwise the
  stock Roblox Versions folder. Updates go into stock Roblox unless that
  folder is missing.
- **Title-bar version chip:** matches the Advanced card. If Roblox is already
  current and the offset dump is catching up, the chip stays green
  instead of amber "mismatch".
- **Available-flags sidebar:** no longer stays empty until you type a search
  character. The first empty lookup was cached before the flag list
  finished loading.
- **Leftover Roblox builds:** Launch, Play, and Fix Roblox remove extra
  `version-*` folders under stock Roblox once the current production player
  is on disk and Roblox is closed. Stale folders (and leftover launcher
  exes at the Versions root) could send you back to an old client.

### Changed

- **Launch and Play stay on production.** FFM starts `RobloxPlayerBeta.exe`
  from the current production folder, writes the production channel so the
  player does not hop deploys, and rewrites any `channel:` token in a join
  URI to production.
- **Version checker copy:** the compare label is "Comparing Roblox vs offset
  dump". The result card lists Roblox, offset dump, and CDN latest GUIDs.
  Match / Mismatch / Offsets behind are unchanged.
- **App version is 4.1.0.**

## [4.0.3] - 2026-07-09

### Fixed

- **Update Now (Auto Update off):** downloaded the installer and never
  launched it. The installer ran through a detached cmd batch, which
  dropped the interactive session token Windows needs for UAC (Program
  Files installs require admin). Elevation failed with no prompt. The
  manual path now opens the installer with `ShellExecuteW("open", ...)`,
  the same call Auto Update already uses. Auto Update is unchanged.
- **"Update Available!" title-bar pill:** clickable again. It sat inside the
  window drag region (`-webkit-app-region: drag`), so the pointer looked
  clickable and the click never fired. Clicks now land via
  `.vc-actionable` (`pointer-events: auto`, `-webkit-app-region: no-drag`,
  and a drag-shield mousedown exception). The class is on for
  "Update Available!" and "Restart needed", off when idle, so the rest
  of the title bar still drags.

### Changed

- **"Update Available!" pill uses the theme `--warning` yellow instead of
  the accent color**, matching "Restart needed". The adjacent dot uses the
  same warning color and glow.
- **Update card moved from Settings → Application to Settings → Advanced,
  under the Roblox Version card.** The title-bar pill switches to Advanced
  and scrolls the card into view. The Auto Update toggle stays in
  Application → Application.

## [4.0.2] - 2026-07-09

### Fixed

- **Live memory writes skip on version mismatch.** When the running Roblox
  build no longer matches FFM's offset dump, Step 2 of Apply bails with
  an amber `[!] Live memory skipped` line (offsets target X, Roblox is
  on Y) instead of writing to the wrong addresses. JSON still applies.
  Live flags resume once offsets catch up (use Fix Roblox). This matches
  the "crash after applying" reports from users whose Roblox auto-updated
  ahead of the dump.
- **Bootstrapper-only installs get flags.** With no stock Roblox install,
  FFM merges into the newest bootstrapper version directory instead of
  failing the JSON step with no message.

## [4.0.1] - 2026-07-08

### Added

- **Automatic Launch:** opt in under Settings → Advanced to make FFM the
  Roblox Play handler. Single exe, two modes (Froststrap-style): a Play
  click applies flags and launches Roblox with no window and no admin
  prompt. Claims both `roblox-player:` and `roblox:` schemes. Off by
  default. Turning it off restores the previous handler.
- **DF-lock:** after live injection, the flag's metadata attribute byte
  is patched in the heap so Roblox cannot revert the value from config.
  A silent re-verify tick catches a Roblox self-unlock.
- **Turbo enforcement:** a tight read-before-write loop catches a
  reverted flag within one frame. Default for new installs.
- **FPS unlocker:** writes `FramerateCap = 9999` on
  `GlobalBasicSettings_13.xml` and marks the file read-only. Runs at
  startup with no prompt. FPS-cap FastFlags are ignored so they cannot
  fight it. Toggle in Advanced.
- **Fix Roblox:** one click when the installed build no longer matches
  FFM's offsets (live memory stops applying). Refreshes offsets first,
  then downloads and installs the matching Roblox build if still off.
  Disk-space check before download. Cancelable progress bar. Production
  builds only, and it only upgrades. It will not install an older build
  that cannot join.
- Cherry Blossom theme, themed loading screens, and persisted Matrix
  animation speed.
- Apply sound: a chime on manual apply and the first auto-apply. Toggle
  and volume live in Appearance.
- Right-click a preset to open the settings menu at the cursor. "Show in
  folder" reveals `presets.json` in Explorer.
- **Kill Switch:** pause every flag at once with a global hotkey and a
  one-click Restore banner, without restarting Roblox.
- **Revert to Original:** right-click a flag to put its original in-game
  value back without disabling it.
- Live `FString` / `DFString` flags (telemetry and analytics URLs, and
  similar) now apply in memory, not only at launch.
- **Editable presets:** open a preset, edit flag values inline, and stage
  deletions (with undo). Pending change and delete counters show what is
  unsaved. Save appears only when there is something to save.
- **Scheduled Apply:** optional 0-60s delay after Roblox opens before
  flags are injected, for flags or situations that need the game to load
  first. Lives in Settings.
- **Roblox version indicator:** the top bar shows the current Roblox
  build. Settings shows whether it matches the build FFM's offsets
  target: green when injection is ready, yellow (installed vs needed)
  when they differ.
- **Editor change log:** changing a flag's value in the editor prints
  `old -> new` in the Output console.
- After importing flags in the editor, save them as a named, color-tagged
  preset, or cancel.
- One trash button removes all unavailable flags (undoable).
- Maximize / Restore button in the title bar.
- Presets can be imported from `.txt` files, not only `.json`.

### Changed

- Settings uses pinned category pills (Advanced / Application / About)
  and labelled cards. Search sidebar is hidden on Presets and Settings
  so those pages get the full width.
- Preset switching only reverts flags the new preset does not use, writes
  the new ones in place, and leaves untouched flags alone. Fewer memory
  writes, no mid-switch flicker.
- Preset export "JSON - flags only" is a flat `{ "FlagName": "value" }`
  file, the format Roblox and Bloxstrap consume directly. The old
  "JSON - full" option is gone. Base64 remains for a full backup.
- Faster website joins: when the installed build is already latest, the
  Play handler skips both pre-launch network checks and launches
  immediately.
- Faster repeat Fix Roblox via a per-file hash cache. Unchanged files are
  not re-hashed on retry.
- Version pill (top-right) opens Settings → Advanced directly.
- Kill Switch is now the Flags on / Flags off pill in the header, matching
  the Auto Apply toggle. One click pauses every flag. A second click
  restores them. Rapid clicks are debounced.
- History limit defaults to 20, range Off-100. Unlimited is gone.
- Auto-apply is ON by default. Added flags apply to the running game
  right away.
- Switching presets reverts the previous preset first. Applying preset B
  no longer leaves preset A's leftover flags active in the running game.
- Adding a flag is instant. No freeze or "busy applying" failure while a
  previous apply is still running.
- The per-flag Un-apply bind is now Toggle: press once to revert, press
  again to re-apply.
- License changed from MIT to PolyForm Noncommercial 1.0.0. Personal and
  hobby use stay free. Commercial redistribution is not permitted. Full
  text in `LICENSE`.
- Installer version stays in sync automatically.

### Fixed

- Flag file-writes are scoped to the stock Roblox install. Third-party
  bootstrapper installs (Bloxstrap, Fishstrap, Froststrap, Voidstrap,
  Plexity) get memory injection instead, so their `ClientAppSettings.json`
  and mods stay untouched. If a third-party owns the Play handler, FFM
  takes it over only to correct a version mismatch, then hands it back.
- Play button doing nothing. A `roblox-player` scheme with a launch
  command but no `URL Protocol` marker made browsers ignore Play. FFM
  writes the marker back on startup without changing who owns the handler.
- Applying flags or switching presets no longer freezes Roblox. The
  background enforcer and apply could race on memory writes and the
  address cache. Writes are serialized. The enforcer stands down for the
  duration of an apply.
- `FString` / `DFString` values of 16 characters or more no longer crash
  Roblox on in-game edit. Long strings apply via file at next launch.
  Short strings still apply live.
- Launch Roblox actually opens Roblox. It was starting
  `RobloxPlayerBeta.exe` with no args, which modern Roblox exits
  immediately. Now uses `-app`, matching the official shortcut.
- Top-bar pill and Settings bar compare the installed build to FFM's
  target build and show the amber mismatch warning plus Fix Roblox when
  they differ. They were almost always green regardless.
- Window blank or gray after minimize. WebView2's native occlusion was
  suspending render on minimize. That is off. The view keeps painting and
  restores cleanly.
- Leftover flags on disk. With Auto Apply off and Roblox closed, every
  `ClientAppSettings.json` (each launcher's version folder plus the
  legacy global) is cleared immediately. Clearing only ran on specific
  events, so a manual apply or close-to-tray could leave flags behind.
- Editor tab uses a virtualized list. About 7x faster with hundreds of
  flags.
- Presets tab flicker. Cards no longer blank out then repopulate. One
  refresh update.
- Reordering presets auto-scrolls near the list edges.
- Launch failure messages distinguish "closed right after launch",
  "running but memory unreadable", and Windows-level errors (access
  denied, missing exe).
- Apply count for FPS flags. Output notes how many the FPS unlocker
  skipped, so the number does not look like a silent loss.
- Window resizing works again. Frameless edge and corner resizing was
  broken, and the window walked across the screen on scaled (HiDPI)
  displays.
- Console log no longer freezes after a lot of output.
- LIVE status dots no longer linger after Roblox is closed.
- CI: install `pytest` in the release workflow so the verify step no
  longer fails with `No module named pytest`. That is why 4.0.0 never
  shipped. 4.0.1 is the first published 4.x release.
- Mouse side-buttons (back / forward / media) now work while FFM is
  focused.
- Minor right-click menu glitches (duplicate remove button, stray `>`
  separator).

## [3.3.8] - 2026-05-22

### Changed

- Offset source priority: the GitHub mirror (`data/FFlags.hpp`) is now
  tried before `offsets.ntgetwritewatch.workers.dev` in the fetch chain
  (`offset_sources.py`). On Roblox builds where imtheo's dumper is
  offline, workers.dev serves a dump whose numeric (FInt/FFloat)
  pointers are wrong. They resolve into read-only `.rdata`, so those
  flags fell back to JSON-only instead of live memory. Prioritizing the
  verified mirror fixes this. Revert when imtheo's dumper is back for
  the current build.
- `data/FFlags.hpp` updated to a Polaris-format dump for
  `version-4b6315bf1f0a4dbb` (13,227 offsets). Every pointer was checked
  against the live executable to resolve to writable `.data` with the
  correct default value (e.g. CameraMaxZoomDistance=400,
  VoiceChatVolumeThousandths=1000). A small `FFlagOffsets` struct block
  is included so the existing loader accepts it with no code change. The
  bundled baseline is refreshed to match.
- Offset fetch chain now uses `offsets.imtheo.lol/FFlags.hpp` as the
  secondary imtheo source in place of `imtheo.lol/Offsets/FFlags.hpp`.
  Both serve byte-identical Format A content. The new host is the
  current canonical mirror. Applied to the in-app loader
  (`offset_sources.py`) and the `mirror-offsets.yml` GitHub Action
  (both the `.hpp` and `.json` chains).
- The logo's "NNK+ FastFlags Available!" count is generated from
  `data/FFlags.hpp` by `update_version.py` at release time (was a
  hardcoded "13K+"), so it stays in sync with the actual offset count.

### Fixed

- Numeric flags (FInt/FFloat: camera zoom, simulation radius, sender
  rates, and similar) apply via live memory again instead of being marked
  JSON-only. They were JSON-only because the workers.dev mirror pointed
  them at read-only `.rdata`. The corrected `data/FFlags.hpp` points them
  at the real writable storage. Boolean flags were unaffected. Their
  pointers were always correct.
- `JSON-ONLY` log lines now include region detail (flag type, address,
  page protection) instead of just the flag name, so an unwritable
  pointer can be diagnosed from the log (`flag_manager.py`).
- AOB scanner: `find_pattern` now walks committed, readable memory
  regions via `VirtualQueryEx` and tolerates partial reads
  (`STATUS_PARTIAL_COPY`) instead of skipping an entire 10 MB chunk when
  a single page in it is unreadable. The old all-or-nothing read skipped
  large spans of the Hyperion-protected Roblox image, which could hide
  valid signatures. Adds a `[scan]` coverage log line (regions scanned /
  read failures) to distinguish a missing pattern from a scan blocked by
  unreadable memory.
- FPS unlock (`TaskSchedulerTargetFps`) applies again. It now writes the
  flag's dumped offset via the normal live-memory path (a dynamic value
  Roblox re-reads at runtime) instead of a hardcoded byte-pattern hook
  whose signature went stale on current Hyperion builds. The stale hook
  made the flag show as failed / Unavailable even though the value is
  writable and takes effect. The JSON FFlag method for FPS no longer
  works on current Roblox. FFM applies this one via memory.
- Mirror workflow no longer commits truncated or stub offset dumps. When
  the upstream dumper serves a near-empty file mid-Roblox-update (only
  the 3 `FFlagList` struct offsets), auto-refresh used to accept it,
  wiping `data/FFlags.hpp` and collapsing the README badge to "3". Fetch
  now requires at least 500 offsets. The badge is derived from the
  committed `.hpp` (not the JSON, which some mirrors do not count).
  `update_version.py` refuses to bundle a baseline under 500 offsets at
  release.

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
  6. Bundled baseline (shipped inside the .exe, works on first run with
     no network)
- `data/FFlags.hpp` GitHub mirror, auto-refreshed every ~6 hours by
  `.github/workflows/mirror-offsets.yml`.
- `src/data/FFlags_baseline.hpp` shipped with every installer build,
  refreshed at release time by `scripts/update_version.py`.
- Captive-portal / proxy-error rejection: a fetched body must parse to
  at least 500 flags and a valid `FFlagList.Pointer` before being
  accepted, so AV intercept HTML cannot poison the disk cache.
- Per-source startup log line (`[OK] Offsets source: <id>, ...`) plus
  `offset_source` and `baseline_stale` fields on the loading status API
  for the UI.

### Changed

- Cache file moved from the install directory (`Program Files\...`) to
  `~/.FFlagManager/offsets_cache.json`. The old in-repo location was not
  writable by non-admin processes after Inno install, which silently
  disabled the cache fallback for many users. First run copies the old
  file forward once.
- Cache writes are atomic (write-to-tmp + `os.replace`) so a crash
  mid-write cannot corrupt the cache.
- Long `HTTPSConnectionPool(...)` tracebacks are replaced with short
  per-source `[!] host via path: reason` lines.
- GitHub and Discord buttons in Settings > About use SVG icons (Octocat
  and Discord mark) in a tall card layout.
- Developer avatar in About fetches the real GitHub profile picture,
  falling back to the static "4" if offline.

### Fixed

- White-on-white hover on all subtle buttons in light theme (text and
  SVG icons were invisible on hover).

## [3.3.6] - 2026-05-16

### Added

- "Clear allowed FFlags on exit / when Roblox closes" toggle in
  Settings (default ON for new installs). When enabled, FFM overwrites
  `ClientAppSettings.json` with `{}` across every detected Roblox
  version directory in three situations:
  - the app exits (UI exit button or tray Exit),
  - Auto Apply is turned OFF while Roblox is not running, and
  - the running Roblox process exits (one-shot transition detected by
    the background monitor).
  Leftover allowed FFlags then cannot take effect on the next Roblox
  launch when FFM is not applying.
- `RobloxManager.clear_fflags_json()` helper that mirrors the existing
  scatter-sync write path used by `apply_fflags_json`.

### Removed

- "Emergency Revert" / "Execute Panic Revert" button and the underlying
  `panic_revert` API method. Restoring original values of arbitrary
  FFlags needs a complete defaults table, which FFM does not have, so
  the button could not do what it claimed. The auto-clear toggle is the
  supported kill-switch.
- "Rescan FFlag Offsets" button (and its `rescan_offsets` API method).
  FFM has sourced offsets from Imtheo since 3.3.5, so the user-facing
  rescan no longer matches how the app finds flag locations. Settings →
  Safety & Reset is removed as a result. Internal scanning helpers used
  by the normal apply flow are unchanged.

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
