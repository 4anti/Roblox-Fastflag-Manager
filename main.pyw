import sys
import ctypes
import os

# Ensure we can import from src
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.gui.main_window import MainWindow
from src.utils.logger import log
from src.utils.updater import check_for_updates, perform_silent_update

def main():
    try:
        # --- Silent Update Check ---
        try:
            has_update, zip_url, new_version = check_for_updates()
            if has_update and zip_url:
                log(f"[*] Update available: v{new_version}. Applying silently...", (100, 255, 100))
                perform_silent_update(zip_url, new_version)
        except Exception as update_err:
            log(f"[!] Update check skipped: {update_err}", (255, 100, 100))

        # --- Normal Startup ---
        app = MainWindow()
        app.run()
    except Exception as e:
        # Fallback logging if GUI fails
        try:
             log(f"Critical Error: {e}", (255, 100, 100))
        except Exception:
             print(f"Critical Error: {e}")
             
        # Create a simple error file if everything fails
        with open("error.log", "w") as f:
            f.write(str(e))

if __name__ == "__main__":
    try:
        # Single Instance Check
        mutex_name = "FFlagManager_SingleInstance_Mutex"
        # 0x01 = MUTEX_ALL_ACCESS (not needed, 0 is fine for just checking existence)
        mutex = ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
        if ctypes.windll.kernel32.GetLastError() == 183: # ERROR_ALREADY_EXISTS
            ctypes.windll.user32.MessageBoxW(0, "Another instance of FFlag Manager is already running.", "FFlag Manager", 0x10) # 0x10 = MB_ICONERROR
            sys.exit(0)

        # Check admin privileges
        if not ctypes.windll.shell32.IsUserAnAdmin():
            self_path = sys.executable if getattr(sys, 'frozen', False) else __file__
            ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable, 
                f'"{self_path}"', None, 0
            )
            sys.exit()

        # --- Bootstrapper: Auto-install dependencies ---
        try:
            from src.utils.updater import apply_staged_update
            # Apply any background-downloaded updates first
            if apply_staged_update():
                sys.exit(0)

            import subprocess
            import importlib.metadata
            
            # Packages to check (matching requirements.txt exactly)
            required = ["requests", "pywebview", "pystray", "Pillow"]
            missing = []
            
            for pkg in required:
                try:
                    importlib.metadata.version(pkg)
                except importlib.metadata.PackageNotFoundError:
                    missing.append(pkg)
            
            if missing:
                # Use a simple messagebox to inform user (native win32)
                ctypes.windll.user32.MessageBoxW(0, 
                    f"First-time setup: Installing missing components ({', '.join(missing)}).\n\nPlease wait a moment...", 
                    "FFlag Manager - Setup", 0x40) # 0x40 = MB_ICONINFORMATION
                
                # Run pip install silently
                # --no-warn-script-location and --disable-pip-version-check to reduce noise
                subprocess.check_call([sys.executable, "-m", "pip", "install", *missing, "--quiet", "--no-warn-script-location"])
                
                # Restart app to ensure all new modules are available in current process
                # Use subprocess instead of os.execv to avoid Windows mutex race condition
                # (os.execv spawns before the old process dies, triggering the single-instance check)
                if mutex:
                    ctypes.windll.kernel32.CloseHandle(mutex)
                subprocess.Popen([sys.executable] + sys.argv)
                sys.exit(0)
        except Exception as boot_err:
            # If bootstrapper fails, we still try to run main() (it might just be a metadata check error)
            with open("bootstrapper_error.log", "w") as f:
                f.write(f"Bootstrapper Error: {boot_err}\n")
        
        main()
    except Exception as e:
        with open("startup_error.log", "a") as f:
            import traceback
            f.write(f"Startup CRASH: {e}\n")
            f.write(traceback.format_exc())
