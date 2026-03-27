import os
import shutil
import subprocess
import sys
import json
import requests
import zipfile
import io
from src.utils.logger import log

OUTPUT_FILE = "FFlags.h"
VERSION_FILE = "version.json"
GITHUB_API = "https://api.github.com/repos/4anti/Roblox-Fastflag-Manager/releases/latest"

def get_current_version():
    """Read local version from version.json."""
    try:
        with open(VERSION_FILE, "r") as f:
            data = json.load(f)
            return data.get("version", "0.0.0")
    except:
        return "0.0.0"

def check_for_updates():
    """Check GitHub for a newer version. Returns (has_update, download_url, version_str)"""
    try:
        response = requests.get(GITHUB_API, timeout=5)
        if response.status_code == 200:
            data = response.json()
            remote_version = data.get("tag_name", "0.0.0").replace("v", "")
            local_version = get_current_version().replace("v", "")
            
            if remote_version != local_version:
                # We assume any different version is an update for simplicity, 
                # or you can do proper semantic version comparison
                zip_url = data.get("zipball_url")
                return True, zip_url, remote_version
    except Exception as e:
        log(f"[!] Update check failed: {e}", (255, 100, 100))
    return False, None, None

def perform_silent_update(zip_url, new_version):
    """Download and apply update silently."""
    try:
        log(f"[*] Downloading Update v{new_version}...", (100, 255, 100))
        r = requests.get(zip_url, timeout=30)
        if r.status_code != 200:
            return False

        # Use a temporary directory for extraction
        temp_dir = "update_tmp"
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        os.makedirs(temp_dir)

        z = zipfile.ZipFile(io.BytesIO(r.content))
        z.extractall(temp_dir)

        # ZIPs from GitHub usually have a root folder like 'repo-name-hash'
        root_folder = os.path.join(temp_dir, os.listdir(temp_dir)[0])
        
        # 1. Update the local version.json content before moving
        with open(os.path.join(root_folder, VERSION_FILE), "w") as f:
            json.dump({"version": new_version, "github_repo": "4anti/Roblox-Fastflag-Manager"}, f, indent=4)

        # 2. Preserve user settings if they exist
        if os.path.exists("settings.json"):
            shutil.copy("settings.json", os.path.join(root_folder, "settings.json"))

        # 3. Create a simple batch script to replace files and restart
        # This is the cleanest way to update a running python app on Windows
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        updater_bat = os.path.join(base_dir, "finish_update.bat")
        
        with open(updater_bat, "w") as f:
            f.write(f"@echo off\n")
            f.write(f"timeout /t 2 /nobreak > nul\n") # Wait for app to close
            f.write(f"xcopy /s /y /e \"{root_folder}\\*\" \"{base_dir}\\\"\n")
            f.write(f"rd /s /q \"{temp_dir}\"\n")
            f.write(f"start \"\" \"{sys.executable}\" \"{os.path.join(base_dir, 'main.pyw')}\"\n")
            f.write(f"del \"%~f0\"\n") # Self delete

        subprocess.Popen(["cmd", "/c", updater_bat], shell=True)
        log(f"[+] Update staged. Restarting...", (100, 255, 100))
        sys.exit(0)
        return True
    except Exception as e:
        log(f"[!] Update application failed: {e}", (255, 100, 100))
        return False

def update_fflags():
    """The existing local scanner logic (keep for functionality)"""
    log(f"[*] Executing Local FFlag Offset Scanner...", (100, 255, 255))
    try:
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
            shutil.copy(generated_file, os.path.join(base_dir, OUTPUT_FILE))
            return True
    except Exception as e:
        log(f"[!] FFlag update failed: {e}", (255, 100, 100))
    return False
