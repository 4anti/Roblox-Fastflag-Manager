import os
import subprocess
import sys
import json
import requests
from src.utils.logger import log

VERSION_FILE = "version.json"
GITHUB_API = "https://api.github.com/repos/4anti/Roblox-Fastflag-Manager/releases/latest"

def get_current_version():
    """Read local version from version.json."""
    try:
        # If in EXE, version.json is bundled
        from src.utils.helpers import get_resource_path
        v_path = get_resource_path(VERSION_FILE)
        with open(v_path, "r") as f:
            data = json.load(f)
            return data.get("version", "0.0.0")
    except:
        return "0.0.0"

def check_for_updates():
    """Check GitHub for a newer version. Returns (has_update, exe_url, version_str, changelog)"""
    try:
        response = requests.get(GITHUB_API, timeout=5)
        if response.status_code == 200:
            data = response.json()
            remote_version = data.get("tag_name", "0.0.0").replace("v", "")
            local_version = get_current_version().replace("v", "")
            
            try:
                remote_parts = tuple(map(int, remote_version.split('.')))
                local_parts = tuple(map(int, local_version.split('.')))
                has_update = remote_parts > local_parts
            except Exception:
                has_update = remote_version != local_version
            
            if has_update:
                # Look for the Setup.exe in assets (case-insensitive)
                exe_url = None
                for asset in data.get("assets", []):
                    asset_name = asset.get("name", "").lower()
                    if asset_name.endswith(".exe") and ("setup" in asset_name or "installer" in asset_name):
                        exe_url = asset.get("browser_download_url")
                        break
                
                if not exe_url:
                    log(f"[*] Update v{remote_version} found, but no Setup.exe asset was found on GitHub.", (255, 200, 100))
                
                return True, exe_url, remote_version, data.get("body", "")
    except Exception as e:
        log(f"[!] Update check failed: {e}", (255, 100, 100))
    return False, None, None, None

# Inno Setup flags for both Auto Update and Update Now. /VERYSILENT keeps
# this a one-click install (no wizard). /FORCECLOSEAPPLICATIONS lets Setup
# replace FFM.exe even if this process has not fully unwound yet.
_INSTALLER_ARGS = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /FORCECLOSEAPPLICATIONS"


def _launch_installer_and_exit(setup_path):
    """Hand the installer to the Windows shell, then exit immediately.

    ``ShellExecuteW("open", ...)`` routes through Explorer, which owns the
    user's interactive session and handles UAC. Verb is ``open`` (not
    ``runas``) because Inno Setup already carries its own UAC manifest;
    ``runas`` fails silently from background threads.

    Returns False if the launch failed so the caller can keep running (UAC
    cancel, missing file, etc.). On success this never returns in production
    — ``os._exit(0)`` runs immediately so Setup can replace FFM.exe.
    """
    import ctypes
    result = ctypes.windll.shell32.ShellExecuteW(
        None, "open", setup_path, _INSTALLER_ARGS, None, 1
    )
    log(f"[*] ShellExecuteW returned: {result} (>32 = success)", (100, 255, 255))
    if result <= 32:
        log(f"[!] ShellExecuteW failed with code {result}. Installer not launched.", (255, 100, 100))
        return False
    log("[*] Restarting app to apply update...", (100, 255, 100))
    os._exit(0)
    return True


def download_update(exe_url, new_version, progress_callback=None):
    """Download the installer with progress reporting, then hand it to the
    Windows shell.

    Launch pattern matches ``perform_silent_update`` on purpose: the previous
    approach wrote a batch script and spawned it with ``DETACHED_PROCESS``,
    which stripped the interactive session token from the child. The Inno
    Setup installer installs into Program Files (``{autopf}``) and therefore
    needs UAC elevation — a detached-cmd child has no station/desktop for the
    consent prompt, so elevation silently failed and the installer never ran.
    ``ShellExecuteW("open", ...)`` routes through Explorer, which owns the
    user's interactive session and handles elevation correctly.
    """
    if not exe_url:
        log("[!] Update URL not found.", (255, 100, 100))
        return False

    try:
        log(f"[*] Downloading installer for v{new_version}...", (100, 255, 100))
        r = requests.get(exe_url, stream=True, timeout=120)
        if r.status_code != 200:
            log(f"[!] Download failed: HTTP {r.status_code}", (255, 100, 100))
            return False

        total = int(r.headers.get('content-length', 0))
        downloaded = 0
        temp_setup = os.path.join(os.environ.get("TEMP", "."), f"Setup_FFM_{new_version}.exe")

        with open(temp_setup, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback and total > 0:
                        progress_callback(downloaded, total)

        file_size = os.path.getsize(temp_setup)
        log(f"[+] Download complete ({file_size} bytes). Launching installer...", (100, 255, 100))

        if file_size < 100000:
            log(f"[!] Downloaded file is suspiciously small ({file_size} bytes). Aborting.", (255, 100, 100))
            return False

        return _launch_installer_and_exit(temp_setup)

    except Exception as e:
        log(f"[!] Download failed: {e}", (255, 100, 100))
        return False

def perform_silent_update(exe_url, new_version):
    """Download the Setup.exe and launch it for a one-click update."""
    if not exe_url:
        log("[!] Update URL not found. Manual update required.", (255, 100, 100))
        return False

    try:
        log(f"[*] Downloading Setup for v{new_version}...", (100, 255, 100))
        r = requests.get(exe_url, timeout=120)
        if r.status_code != 200:
            return False

        # Save to Temp folder
        temp_setup = os.path.join(os.environ.get("TEMP", "."), f"Setup_FFM_{new_version}.exe")
        with open(temp_setup, "wb") as f:
            f.write(r.content)
        
        log(f"[+] Launching One-Click Installer...", (100, 255, 100))
        return _launch_installer_and_exit(temp_setup)
        
    except Exception as e:
        log(f"[!] Update failed: {e}", (255, 100, 100))
        return False

def apply_staged_update():
    """Legacy function - not needed with Inno Setup but kept for main.pyw compatibility."""
    return False

def update_fflags():
    """Restored function for local scanning. Returns False in EXE mode as scripts aren't bundled."""
    if getattr(sys, 'frozen', False):
        return False
        
    log(f"[*] Executing Local FFlag Offset Scanner...", (100, 255, 255))
    try:
        import shutil
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        script_dir = os.path.join(base_dir, "Roblox FFlags Offset Finder")
        script_path = os.path.join(script_dir, "fflag_discovery.py")
        
        if not os.path.exists(script_path):
            return False
            
        process = subprocess.Popen(
            [sys.executable, script_path, "--no-admin"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=script_dir
        )
        process.wait()
        
        generated_file = os.path.join(script_dir, "Offsets.h")
        if os.path.exists(generated_file):
            from src.utils.config import Config
            shutil.copy(generated_file, str(Config.FFLAGS_FILE))
            return True
    except Exception as e:
        log(f"[!] FFlag update failed: {e}", (255, 100, 100))
    return False
