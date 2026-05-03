import os
import subprocess
import sys
import shutil


def build():
    print("[*] Starting FFlag Manager build...")

    # 1. Clean previous build output
    for folder in ['build', 'dist']:
        if os.path.exists(folder):
            print(f"[*] Removing old {folder} folder...")
            shutil.rmtree(folder, ignore_errors=True)

    # 2. Ensure PyInstaller is installed
    try:
        import PyInstaller
    except ImportError:
        print("[!] PyInstaller not found. Installing...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    # 4. Define paths
    script_path = "main.pyw"
    icon_file = "ffm_v3_logo.ico"
    
    # 5. Build command
    # On Windows, path separator for --add-data is ';'
    separator = ";"
    
    cmd = [
        "pyinstaller",
        "--noconsole",
        "--onedir",
        f"--icon={icon_file}",
        f"--add-data=src/gui/ui{separator}src/gui/ui",
        f"--add-data=version.json{separator}.",
        "--name=FFM",
        "--noconfirm",
        "--clean",
        script_path
    ]

    print(f"[*] Executing: {' '.join(cmd)}")
    subprocess.check_call(cmd)

    print("\n[+] Build Complete!")
    print(f"[+] Application folder: {os.path.abspath('dist/FFM')}")
    print("[+] EXE path: dist/FFM/FFM.exe")

if __name__ == "__main__":
    build()
