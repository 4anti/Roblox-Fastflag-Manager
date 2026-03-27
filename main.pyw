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
        except:
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
        
        main()
    except Exception as e:
        with open("startup_error.log", "a") as f:
            import traceback
            f.write(f"Startup CRASH: {e}\n")
            f.write(traceback.format_exc())
