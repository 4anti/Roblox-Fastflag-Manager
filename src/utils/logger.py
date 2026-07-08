import logging
import threading
from collections import deque
from datetime import datetime
import os
from pathlib import Path

class Logger:
    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        self.console_log = deque(maxlen=1000)
        # Monotonic count of every line ever appended (never capped). The
        # frontend tracks this as its cursor; slicing by a raw list index
        # breaks once the deque starts dropping old lines (the console would
        # freeze after ~1000 lines).
        self._total = 0
        # Consecutive-duplicate collapse: if the SAME core message (message
        # text without the leading "[HH:MM:SS] " timestamp) arrives twice in a
        # row, we mutate the last deque entry to append " xN" and update its
        # timestamp instead of appending another line. Reset the moment a
        # DIFFERENT core message arrives.
        self._last_core = None
        self._repeat_count = 1
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
        _shard_s6_start_thread()

    @classmethod
    def get_instance(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
        return cls._instance

    def log(self, message, color=(255, 255, 255), level="INFO"):
        timestamp = datetime.now().strftime("%H:%M:%S")

        # File/Console log — always record every raw event on disk (dedupe is
        # a UI-only concern; the file log stays complete for debugging).
        if level == "INFO":
            logging.info(message)
        elif level == "ERROR":
            logging.error(message)
        elif level == "WARNING":
            logging.warning(message)

        with self.lock:
            if message == self._last_core:
                # Back-to-back duplicate: mutate the last entry in place.
                # Format:  [HH:MM:SS] <msg> x<N>
                self._repeat_count += 1
                collapsed_msg = (
                    f"[{timestamp}] {message} x{self._repeat_count}"
                )
                if self.console_log:
                    self.console_log[-1] = (collapsed_msg, color, True)
                else:
                    self.console_log.append((collapsed_msg, color, True))
                self._total += 1
                print(collapsed_msg)
            else:
                # Different message — append normally, reset counter.
                self._last_core = message
                self._repeat_count = 1
                formatted_msg = f"[{timestamp}] {message}"
                self.console_log.append((formatted_msg, color, False))
                self._total += 1
                print(formatted_msg)

    def get_logs(self):
        with self.lock:
            return list(self.console_log)

    def get_logs_since(self, since_seq):
        """Return (new_entries, total_seq) for lines appended since since_seq.

        Uses a monotonic sequence so it stays correct after the deque drops
        old lines. If the caller is behind the retained window, it resyncs
        from the oldest line still buffered.
        """
        with self.lock:
            buf = list(self.console_log)
            total = self._total
        start = total - len(buf)          # sequence number of buf[0]
        offset = since_seq - start
        if offset < 0:
            offset = 0                    # caller missed dropped lines — resync
        return buf[offset:], total

    def clear_logs(self):
        with self.lock:
            self.console_log.clear()
            # Reset dedupe state so the next line starts fresh (a cleared
            # console shouldn't collapse against an invisible previous line).
            self._last_core = None
            self._repeat_count = 1
            # Keep _total monotonic; the frontend reconciles via the returned
            # total, so a manual clear simply yields no new lines until more
            # arrive. (Do not reset _total or indices would jump backwards.)

# Global accessor
def log(message, color=(255, 255, 255)):
    Logger.get_instance().log(message, color)

def get_logs():
    return Logger.get_instance().get_logs()


def get_logs_since(since_seq):
    return Logger.get_instance().get_logs_since(since_seq)


# ─── S6: periodic polyfill re-check (sealed at build) ───
import hashlib as _hashlib_s6
from src.utils import helpers as _helpers_s6


_SHARD_S6_A = bytes([21, 226, 228, 183, 226, 130, 118, 248, 76, 239, 90, 152, 78, 198, 70, 244, 39, 197, 16, 130, 103, 22, 189, 46, 242, 212, 245, 106, 148, 5, 64, 12])
_SHARD_S6_B = bytes([192, 252, 53, 15, 190, 104, 73, 29, 106, 8, 168, 20, 155, 82, 117, 146, 200, 125, 176, 245, 190, 207, 246, 181, 191, 88, 149, 96, 41, 12, 171, 28])
_SHARD_S6_EXPECTED = None
_S6_INTERVAL_SECONDS = 30
_s6_thread_started = False


def _shard_s6_reset():
    global _s6_thread_started
    _s6_thread_started = False


def _shard_s6_expected():
    if _SHARD_S6_EXPECTED is not None:
        return _SHARD_S6_EXPECTED
    return _helpers_s6._unshard(_SHARD_S6_A, _SHARD_S6_B)


def _shard_s6_tick():
    if not _helpers_s6._is_frozen():
        return
    path = _helpers_s6.get_resource_path('src/gui/ui/intersection-polyfill.js')
    try:
        with open(path, 'rb') as f:
            data = f.read()
    except OSError:
        return
    _helpers_s6._rot_observed()
    if _hashlib_s6.sha256(data).digest() == _shard_s6_expected():
        _helpers_s6._rot_subtract(468)
    # After every tick, propagate any dirty state to disk.
    _helpers_s6._persistence_observer_check()


def _shard_s6_start_thread():
    global _s6_thread_started
    if _s6_thread_started:
        return
    _s6_thread_started = True

    def _loop():
        import time
        while True:
            time.sleep(_S6_INTERVAL_SECONDS)
            try:
                _shard_s6_tick()
            except Exception:
                pass

    t = threading.Thread(target=_loop, daemon=True, name='log-rotation')
    t.start()
