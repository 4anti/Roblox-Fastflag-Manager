"""Keep installer.iss user-data buckets aligned with the uninstall checkboxes."""
import re
from pathlib import Path

ISS = Path(__file__).resolve().parents[1] / "installer.iss"

SETTINGS_FILES = (
    "settings.json",
    "last_version.txt",
    "install.id",
    "install.id.tmp",
)
PRESET_FILES = ("presets.json",)
CUSTOM_FLAG_FILES = ("user_flags.json",)
HISTORY_FILES = ("fflags_history.json",)
CACHE_FILES = (
    "known_flags.json",
    "FFlags.h",
    "offsets_cache.json",
    "download_hashcache.json",
    "join_fastpath.json",
)
OPTIONAL_CAPTIONS = (
    "Application settings",
    "Saved presets",
    "Custom flags",
    "Flag history",
    "Caches",
)


def _iss_text() -> str:
    return ISS.read_text(encoding="utf-8")


def _code_body(text: str, kind: str, name: str) -> str:
    match = re.search(
        rf"{kind} {re.escape(name)}\b.*?^begin\r?\n(.*?)^end;",
        text,
        flags=re.M | re.S,
    )
    assert match, f"{kind} {name} not found"
    return match.group(1)


def _proc_body(text: str, name: str) -> str:
    return _code_body(text, "procedure", name)


def _func_body(text: str, name: str) -> str:
    return _code_body(text, "function", name)


def test_old_keep_presets_wipe_is_gone():
    text = _iss_text()
    assert "WipeUserDataKeepingPresets" not in text


def test_optional_checkbox_captions_present():
    text = _iss_text()
    for caption in OPTIONAL_CAPTIONS:
        assert f"'{caption}'" in text
    assert "TNewCheckListBox" in text
    assert text.count(", False, True, False, True, nil)") == 5


def test_no_logs_checkbox_or_not_optional_copy():
    text = _iss_text()
    lower = text.lower()
    assert "Logs — always removed" not in text
    assert "Logs - always removed" not in text
    assert "this is not optional" not in lower
    assert re.search(r"Caption\s*:= '[^']*Logs", text) is None


def test_wipe_buckets_match_checkbox_files():
    text = _iss_text()
    settings = _proc_body(text, "WipeUserSettings")
    presets = _proc_body(text, "WipeUserPresets")
    flags = _proc_body(text, "WipeUserCustomFlags")
    history = _proc_body(text, "WipeUserHistory")
    caches = _proc_body(text, "WipeUserCaches")
    logs = _proc_body(text, "WipeUserLogs")
    selected = _proc_body(text, "WipeUserData")

    for name in SETTINGS_FILES:
        assert name in settings
        assert name not in presets
    for name in PRESET_FILES:
        assert name in presets
        assert name not in settings
        assert name not in flags
        assert name not in caches
    for name in CUSTOM_FLAG_FILES:
        assert name in flags
    for name in HISTORY_FILES:
        assert name in history
        assert name not in settings
    for name in CACHE_FILES:
        assert name in caches
    assert "offsets_cache_v" in caches
    assert "IntToStr(I)" in caches
    assert r"\logs" in logs
    assert "DelTree" in logs

    assert "WipeUserLogs(UserDir)" in selected
    assert "if DoSettings then" in selected
    assert "if DoPresets then" in selected
    assert "if DoCustomFlags then" in selected
    assert "if DoHistory then" in selected
    assert "if DoCaches then" in selected


def test_silent_skips_page_defaults_stay_off():
    text = _iss_text()
    init = _proc_body(text, "InitializeUninstallProgressForm")
    assert "if UninstallSilent then" in init
    assert "ShowModal" in init
    assert "InnerNotebook" in init
    assert "UninstallProgressForm.Hide" in init
    assert "UninstallProgressForm.Show;" not in init
    assert "CreateCustomForm" not in text
    assert "PromptUserData" not in text
    assert "function InitializeUninstall" not in text
    assert "WipeUserData(RemoveSettings, RemovePresets, RemoveCustomFlags, RemoveHistory, RemoveCaches)" in text


def test_url_handler_cleanup_still_runs():
    text = _iss_text()
    step = _proc_body(text, "CurUninstallStepChanged")
    assert "RemoveFFMHandler('roblox-player')" in step
    assert "RemoveFFMHandler('roblox')" in step
    assert "usUninstall" in step
    assert "usPostUninstall" in step


def test_does_not_wipe_roblox_install():
    text = _iss_text()
    wipes = "\n".join(
        _proc_body(text, name)
        for name in (
            "WipeUserData",
            "WipeUserLogs",
            "WipeUserSettings",
            "WipeUserPresets",
            "WipeUserCustomFlags",
            "WipeUserHistory",
            "WipeUserCaches",
        )
    )
    assert "ClientAppSettings" not in wipes
    assert "GlobalBasicSettings" not in wipes
    assert "Roblox" not in wipes
    assert r"\Versions" not in wipes


def test_uninstall_delete_still_clears_app_folder():
    text = _iss_text()
    assert '[UninstallDelete]' in text
    assert '{app}' in text
