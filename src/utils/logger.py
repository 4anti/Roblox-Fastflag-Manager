import logging
import sys
import threading
from collections import deque
from datetime import datetime
import os
from pathlib import Path

# Windows consoles default to cp1252 or cp437 depending on locale — neither
# can encode arrows, box-drawing, non-Latin, or emoji. A single non-ASCII
# character in ANY log message then raises UnicodeEncodeError inside
# print() and (worse) whatever caller emitted the log sees the exception
# and can misclassify the situation (e.g. the ad-network probe caught the
# charmap error and reported "UNREACHABLE — unclassified network
# failure"). Reconfiguring stdout/stderr to UTF-8 with errors='replace'
# once at import time makes prints safe forever. Python 3.7+.
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass


def _safe_print(msg):
    """Never let stdout kill a log call. If reconfigure() didn't take
    (pythonw.exe under some launchers has stdout=None), fall back to
    ASCII replacement so a non-ASCII payload can't propagate an
    exception up to the caller."""
    try:
        print(msg)
    except (UnicodeEncodeError, OSError):
        try:
            print(msg.encode('ascii', 'replace').decode('ascii'))
        except Exception:
            pass

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
        # text without the leading "[HH:MM:SS] " timestamp) arrives twice in
        # a row, we mutate the last deque entry to append " xN" and update
        # its timestamp instead of appending another line. Reset the moment
        # a DIFFERENT core message arrives.
        self._last_core = None
        self._repeat_count = 1
        # Monotonic tail-mutation counter, bumped every time the last deque
        # entry is mutated in place (dedup). Distinct from `_total`, which
        # only advances when a NEW entry is APPENDED. Together they let
        # `get_logs_since` correctly signal "the tail changed" without
        # re-emitting entries the client already rendered.
        self._tail_epoch = 0
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
            if message == self._last_core and self.console_log:
                # Back-to-back duplicate with a visible previous entry:
                # mutate the tail in place. Do NOT bump `_total` — the total
                # counts APPENDS, not mutations; bumping it here would shift
                # every earlier entry's implicit sequence number forward and
                # cause `get_logs_since` to re-emit entries the client had
                # already rendered (bug: identical logs duplicated in the
                # console whenever 2+ dedups fired between polls). Bump the
                # separate `_tail_epoch` so pollers detect the mutation.
                self._repeat_count += 1
                collapsed_msg = (
                    f"[{timestamp}] {message} x{self._repeat_count}"
                )
                self.console_log[-1] = (collapsed_msg, color, True)
                self._tail_epoch += 1
                _safe_print(collapsed_msg)
            else:
                # First occurrence, or previous occurrence dropped from the
                # ring buffer / cleared: treat as a fresh append.
                self._last_core = message
                self._repeat_count = 1
                formatted_msg = f"[{timestamp}] {message}"
                self.console_log.append((formatted_msg, color, False))
                self._total += 1
                _safe_print(formatted_msg)

    def get_logs(self):
        with self.lock:
            return list(self.console_log)

    def get_logs_since(self, since_seq, since_tail_epoch=0):
        """Return (new_entries, total_seq, tail_epoch).

        - Any entries APPENDED since `since_seq` are returned in order.
        - If nothing new was appended but the tail was mutated in place
          (a dedup collapse fired since `since_tail_epoch`), the tail
          entry alone is returned so the client's most-recent line is
          replaced with the fresh " xN" version.
        - `total_seq` is the append-only monotonic counter — safe to use
          as the client's cursor without any dedup-induced drift.
        """
        with self.lock:
            buf = list(self.console_log)
            total = self._total
            tail_epoch = self._tail_epoch
        start = total - len(buf)          # sequence number of buf[0]
        offset = since_seq - start
        if offset < 0:
            offset = 0                    # caller missed dropped lines — resync
        new = buf[offset:]
        if not new and buf and tail_epoch > since_tail_epoch:
            # Nothing appended, but the tail was mutated (dedup). Return
            # just the tail with replace=True so the client swaps its
            # last-rendered line for the updated one.
            tail = buf[-1]
            if len(tail) == 3:
                tail = (tail[0], tail[1], True)
            new = [tail]
        return new, total, tail_epoch

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


def get_logs_since(since_seq, since_tail_epoch=0):
    return Logger.get_instance().get_logs_since(since_seq, since_tail_epoch)


# ─── S6: periodic polyfill re-check (sealed at build) ───
import hashlib as _hashlib_s6
from src.utils import helpers as _helpers_s6


_SHARD_S6_A = bytes([25, 84, 248, 72, 27, 100, 32, 48, 235, 225, 154, 54, 226, 94, 60, 167, 20, 215, 235, 48, 12, 151, 175, 106, 30, 191, 111, 240, 145, 109, 83, 117])
_SHARD_S6_B = bytes([116, 94, 123, 87, 218, 255, 107, 158, 110, 246, 13, 155, 209, 205, 41, 217, 146, 205, 16, 72, 110, 210, 51, 123, 60, 220, 54, 66, 239, 65, 16, 66])
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
    path = _helpers_s6.get_resource_path('src/gui/ui/Sortable.min.js')
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
