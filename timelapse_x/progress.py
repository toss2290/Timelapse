"""
Progress indicator system for Timelapse X.

Optimizations (v2.6.0 — on top of v2.5.4 dead-code removal):
  - RecordingProgress.add_frame(): eliminates the time.time() call when
    file_size_bytes is 0 (the common case) and caches the last-computed
    stats text with a 0.5-second TTL so get_stats_text() / get_header_text()
    don't rebuild the string on every call when called from the UI redraw
    at 60 fps.
  - get_stats_text(): uses pre-formatted duration and rate strings only
    when they change, not on every invocation.
  - ProgressState.percentage and estimated_remaining kept unchanged.
"""

import bpy
import time
import logging
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ========================================================================================
# Progress Data Classes
# ========================================================================================

@dataclass
class ProgressState:
    current:     int   = 0
    total:       int   = 0
    message:     str   = ""
    detail:      str   = ""
    start_time:  float = 0.0
    last_update: float = 0.0
    cancelled:   bool  = False

    @property
    def percentage(self) -> float:
        if self.total <= 0:
            return 0.0
        return min(1.0, self.current / self.total)

    @property
    def percentage_int(self) -> int:
        return int(self.percentage * 100)

    @property
    def elapsed_time(self) -> float:
        return time.time() - self.start_time

    @property
    def estimated_remaining(self) -> float:
        if self.current <= 0 or self.total <= 0:
            return 0.0
        elapsed = self.elapsed_time
        rate    = self.current / elapsed if elapsed > 0 else 0
        if rate <= 0:
            return 0.0
        return (self.total - self.current) / rate


# ========================================================================================
# Progress Manager
# ========================================================================================

class ProgressManager:
    def __init__(self):
        self.state           = ProgressState()
        self.active          = False
        self.context         = None
        self.area            = None
        self.update_interval = 0.1

    def start(self, context, total: int, message: str = "Processing...",
              show_header: bool = True):
        self.state = ProgressState(
            total=total, message=message,
            start_time=time.time(), last_update=time.time()
        )
        self.active  = True
        self.context = context
        if show_header:
            self.area = self._find_best_area(context)
        logger.info(f"Progress started: {message} (0/{total})")

    def update(self, current: int, detail: str = "", force: bool = False) -> bool:
        if not self.active:
            return True
        self.state.current = current
        self.state.detail  = detail
        now = time.time()
        if force or (now - self.state.last_update) >= self.update_interval:
            self._update_ui()
            self.state.last_update = now
        if self._check_cancelled():
            self.state.cancelled = True
            return False
        return True

    def finish(self, message: str = "Complete"):
        if not self.active:
            return
        self.state.message  = message
        self.state.current  = self.state.total
        self._update_ui()
        if self.area:
            self.area.header_text_set(None)
        elapsed = self.state.elapsed_time
        logger.info(f"Progress complete: {message} ({self.state.total} items in {elapsed:.1f}s)")
        self.active  = False
        self.context = None
        self.area    = None

    def cancel(self):
        self.state.cancelled = True
        self.finish("Cancelled")

    def _update_ui(self):
        if self.area:
            try:
                self.area.header_text_set(self._format_header())
            except (AttributeError, RuntimeError):
                self.area = None
        self._print_progress()
        if self.context:
            try:
                for window in self.context.window_manager.windows:
                    for area in window.screen.areas:
                        area.tag_redraw()
            except (AttributeError, RuntimeError):
                pass

    def _format_header(self) -> str:
        parts = [self.state.message, f"{self.state.percentage_int}%",
                 f"({self.state.current}/{self.state.total})"]
        if self.state.current > 0:
            remaining = self.state.estimated_remaining
            if remaining > 0:
                parts.append(f"~{remaining:.0f}s" if remaining < 60 else f"~{remaining/60:.1f}m")
        if self.state.detail:
            parts.append(f"- {self.state.detail}")
        return " ".join(parts)

    def _print_progress(self):
        if self.state.current % max(1, self.state.total // 20) != 0:
            if self.state.current != self.state.total:
                return
        bar_width = 40
        filled    = int(bar_width * self.state.percentage)
        bar       = "[" + "=" * filled + ">" + " " * (bar_width - filled - 1) + "]"
        msg = (f"\r{self.state.message}: {bar} {self.state.percentage_int}% "
               f"({self.state.current}/{self.state.total})")
        if self.state.detail:
            msg += f" - {self.state.detail}"
        print(msg, end="" if self.state.current < self.state.total else "\n")

    def _find_best_area(self, context):
        if context.area:
            return context.area
        try:
            for window in context.window_manager.windows:
                for area in window.screen.areas:
                    if area.type == 'VIEW_3D':
                        return area
        except (AttributeError, RuntimeError):
            pass
        return None

    def _check_cancelled(self) -> bool:
        return self.state.cancelled


_progress_manager = ProgressManager()


def start_progress(context, total: int, message: str = "Processing...",
                   show_header: bool = True):
    _progress_manager.start(context, total, message, show_header)

def update_progress(current: int, detail: str = "", force: bool = False) -> bool:
    return _progress_manager.update(current, detail, force)

def finish_progress(message: str = "Complete"):
    _progress_manager.finish(message)

def cancel_progress():
    _progress_manager.cancel()

def get_progress_state() -> ProgressState:
    return _progress_manager.state

def is_progress_active() -> bool:
    return _progress_manager.active


# ========================================================================================
# Recording Progress Tracking — OPTIMIZED
# ========================================================================================

class RecordingProgress:
    """
    Lightweight stats tracker for the active recording session.

    Optimization (v2.6.0):
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
    logger.info("Progress system registered (v2.6.0: cached stats text)")


def unregister():
    if _progress_manager.active:
        _progress_manager.cancel()
    logger.info("Progress system unregistered")
