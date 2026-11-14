"""
Recording progress tracking for Timelapse X.

RecordingProgress keeps frame count, size and rate statistics. The stats and
header strings are cached with a short TTL because the UI can redraw at up to
60 fps.
"""

import time
import logging

logger = logging.getLogger(__name__)


# ========================================================================================
# Recording Progress Tracking
# ========================================================================================

class RecordingProgress:
    """
    Lightweight stats tracker for the active recording session.

    Caching:
      - _stats_text and _header_text are cached with a 0.5 s TTL.
        The UI panel calls get_header_text() at up to 60 fps; rebuilding
        the f-string every time is wasteful.
      - add_frame() does one time.time() call and reuses it.
    """

    _CACHE_TTL = 0.5   # seconds — stats string stays valid this long

    def __init__(self):
        self.frame_count        = 0
        self.start_time         = 0.0
        self.last_capture_time  = 0.0
        self.session_size_bytes = 0
        self._stats_cache_time  = 0.0
        self._stats_cache_text  = ""
        self._header_cache      = {}   # mode → (time, text)

    def start(self):
        self.frame_count        = 0
        self.start_time         = time.time()
        self.last_capture_time  = self.start_time
        self.session_size_bytes = 0
        self._stats_cache_time  = 0.0
        self._stats_cache_text  = ""
        self._header_cache.clear()

    def add_frame(self, file_size_bytes: int = 0):
        now = time.time()
        self.frame_count       += 1
        self.last_capture_time  = now
        if file_size_bytes:
            self.session_size_bytes += file_size_bytes
        # Invalidate caches
        self._stats_cache_time = 0.0
        self._header_cache.clear()

    def get_stats_text(self) -> str:
        now = time.time()
        if (now - self._stats_cache_time) < self._CACHE_TTL and self._stats_cache_text:
            return self._stats_cache_text

        elapsed = now - self.start_time
        if   elapsed < 60:   duration = f"{elapsed:.0f}s"
        elif elapsed < 3600: duration = f"{elapsed/60:.1f}m"
        else:                duration = f"{elapsed/3600:.1f}h"

        size_mb = self.session_size_bytes / (1024 * 1024)
        parts   = [f"Frames: {self.frame_count}", f"Duration: {duration}"]

        if size_mb > 0:
            parts.append(f"Size: {size_mb:.1f}MB")
        if elapsed > 0:
            fps = self.frame_count / elapsed
            parts.append(f"Rate: {fps:.2f} fps")

        text = " | ".join(parts)
        self._stats_cache_text = text
        self._stats_cache_time = now
        return text

    def get_header_text(self, mode: str = "CAMERA_LIST") -> str:
        now    = time.time()
        cached = self._header_cache.get(mode)
        if cached is not None:
            cached_time, cached_text = cached
            if (now - cached_time) < self._CACHE_TTL:
                return cached_text
        text  = f"RECORDING [{mode}] - {self.get_stats_text()}"
        self._header_cache[mode] = (now, text)
        return text


_recording_progress = RecordingProgress()


def get_recording_progress() -> RecordingProgress:
    return _recording_progress


# ========================================================================================
# Registration
# ========================================================================================

def register():
    logger.info("Progress system registered")


def unregister():
    logger.info("Progress system unregistered")
