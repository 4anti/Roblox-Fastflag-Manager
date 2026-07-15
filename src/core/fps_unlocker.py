"""Silent FPS unlock via Roblox's GlobalBasicSettings_13.xml.

Roblox stores the in-game framerate cap as `<int name="FramerateCap">N</int>` in
%LOCALAPPDATA%\\Roblox\\GlobalBasicSettings_13.xml and honours it at launch. We
set it very high (9999 = effectively uncapped) and mark the file **read-only** so
Roblox can't rewrite it back on exit.

This runs automatically and silently on FFM startup. Everything here is
best-effort: it never raises and never blocks startup. The flag-based FPS method
is deliberately NOT used (and known FPS flags are skipped elsewhere) because it
fights this file-based cap.
"""
import os
import re
import stat

UNLOCK_FPS_VALUE = 9999

_CAP_RE = re.compile(r'(<int name="FramerateCap">)\d+(</int>)')


def settings_path():
    """Path to GlobalBasicSettings_13.xml, or None if LOCALAPPDATA is unavailable."""
    local = os.environ.get("LOCALAPPDATA", "")
    if not local:
        return None
    return os.path.join(local, "Roblox", "GlobalBasicSettings_13.xml")


def set_framerate_cap(text, value=UNLOCK_FPS_VALUE):
    """Return `text` with FramerateCap set to `value`. Replaces an existing entry,
    or inserts one just inside <Properties> if absent. Pure — easy to unit test."""
    if _CAP_RE.search(text):
        return _CAP_RE.sub(rf"\g<1>{value}\g<2>", text)
    m = re.search(r"(<Properties>[^\n]*\n)", text)
    if m:
        insert = f'\t\t\t<int name="FramerateCap">{value}</int>\n'
        return text[:m.end()] + insert + text[m.end():]
    return text  # no place to put it — leave unchanged


def is_unlocked(text, value=UNLOCK_FPS_VALUE):
    """True if FramerateCap is already the target value."""
    return bool(re.search(rf'<int name="FramerateCap">{value}</int>', text))


def unlock_fps():
    """Apply the FPS unlock (set FramerateCap high + read-only). Idempotent and
    best-effort. Returns (changed: bool, message: str)."""
    path = settings_path()
    if not path or not os.path.isfile(path):
        return False, "GlobalBasicSettings not found"
    cleared_readonly = False
    try:
        was_readonly = not os.access(path, os.W_OK)
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        # Already done? (value set AND locked) -> nothing to do.
        if is_unlocked(text) and was_readonly:
            return False, "already unlocked"
        if was_readonly:
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
            cleared_readonly = True
        new_text = set_framerate_cap(text)
        if new_text != text:
            # Roblox may hold the file open for write (running) or an AV
            # scanner may briefly lock it. If the open fails and we've
            # already cleared the read-only bit, the `finally` below
            # reasserts it so Roblox can't rewrite FramerateCap=60 back
            # on its next launch, silently defeating the unlock.
            with open(path, "w", encoding="utf-8") as f:
                f.write(new_text)
        os.chmod(path, stat.S_IREAD)  # lock so Roblox can't revert it
        cleared_readonly = False       # successful lock — finally is a no-op
        return True, f"FramerateCap={UNLOCK_FPS_VALUE} (read-only)"
    except Exception as e:
        return False, str(e)
    finally:
        # Never leave the file writable after we cleared the read-only bit.
        # Without this, a write failure between `chmod(IWRITE)` and the
        # final `chmod(IREAD)` would let Roblox overwrite FramerateCap on
        # exit — the FPS unlock would silently regress with no user signal.
        if cleared_readonly:
            try:
                os.chmod(path, stat.S_IREAD)
            except OSError:
                pass


def restore_fps():
    """Undo the unlock: clear the read-only lock on GlobalBasicSettings_13.xml so
    Roblox (and the user's own FPS-cap flags) can write FramerateCap again.

    Used when the FPS-unlocker setting is turned OFF. We deliberately do NOT
    reset the cap value — Roblox rewrites it from its own settings on next launch
    once the file is writable. Idempotent and best-effort; returns
    (changed: bool, message: str).
    """
    path = settings_path()
    if not path or not os.path.isfile(path):
        return False, "GlobalBasicSettings not found"
    try:
        if os.access(path, os.W_OK):
            return False, "already writable"
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)  # remove the read-only lock
        return True, "FramerateCap lock released"
    except Exception as e:
        return False, str(e)
