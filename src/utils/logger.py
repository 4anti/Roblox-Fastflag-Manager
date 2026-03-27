import logging
import threading
from datetime import datetime
import os
from pathlib import Path

class Logger:
    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        self.console_log = []
        self.lock = threading.Lock()
        
        # Setup file logging
        log_dir = Path(os.path.expanduser("~")) / ".FFlagManager" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "fflag_manager.log"

        logging.basicConfig(
            filename=str(log_file),
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            filemode='a'
        )

    @classmethod
    def get_instance(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
        return cls._instance

    def log(self, message, color=(255, 255, 255), level="INFO"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        formatted_msg = f"[{timestamp}] {message}"
        
        # File/Console log
        if level == "INFO":
            logging.info(message)
        elif level == "ERROR":
            logging.error(message)
        elif level == "WARNING":
            logging.warning(message)
            
        print(formatted_msg)

        with self.lock:
            self.console_log.append((formatted_msg, color))
            if len(self.console_log) > 1000:
                self.console_log.pop(0)

    def get_logs(self):
        with self.lock:
            return list(self.console_log)

    def clear_logs(self):
        with self.lock:
            self.console_log.clear()

# Global accessor
def log(message, color=(255, 255, 255)):
    Logger.get_instance().log(message, color)

def get_logs():
    return Logger.get_instance().get_logs()
