"""
StateManager - THREAD-SAFE SINGLETON
=====================================
Optimizations (v2.6.0):
  - __slots__ added to CameraScheduler and WindowAsyncContext — reduces
    per-instance memory and speeds up attribute access in the hot path.
  - get_recording_snapshot() — reads all values the modal loop needs
    under a SINGLE lock acquisition instead of N separate acquisitions.
  - get_modal_state() — same idea for the full capture tick.
  - is_suppressed() inlined into the snapshot to avoid a second lock.
  - mark_captured() returns the new counter so callers don't need a
    second property read.

All v2.5.1 fixes (properties, public accessors) retained.
"""

import time
import logging
from typing import Optional, Dict, List, NamedTuple
from threading import RLock
from enum import Enum

logger = logging.getLogger(__name__)


# ============================================================================
# Simple Enums
# ============================================================================

class RecordingState(Enum):
    IDLE      = "idle"
    RECORDING = "recording"
    PAUSED    = "paused"


class AsyncState(Enum):
    IDLE      = "idle"
    SCHEDULED = "scheduled"
    CAPTURING = "capturing"


# ============================================================================
# Camera Scheduler  —  __slots__ for speed
# ============================================================================

class CameraScheduler:
    __slots__ = ('camera_name', 'interval', 'next_due', 'last_capture', 'total_captures')

    def __init__(self, camera_name: str, interval: float, start_index: int = 0):
        self.camera_name    = camera_name
        self.interval       = interval
        self.next_due       = time.time()
        self.last_capture   = 0.0
        self.total_captures = start_index

    def is_due(self, now: float) -> bool:
        return now >= self.next_due

    def mark_captured(self, now: float):
        self.last_capture    = now
        self.next_due        = now + self.interval
        self.total_captures += 1


# ============================================================================
# Window Async Context  —  __slots__ for speed
# ============================================================================

class WindowAsyncContext:
    __slots__ = ('state', 'scheduled_time', 'attempt_count', 'total_captures', 'last_error')

    def __init__(self):
        self.state          = AsyncState.IDLE
        self.scheduled_time = 0.0
        self.attempt_count  = 0
        self.total_captures = 0
        self.last_error     = ""

    def reset(self):
        self.state          = AsyncState.IDLE
        self.scheduled_time = 0.0
        self.attempt_count  = 0
        self.last_error     = ""


# ============================================================================
# Snapshot type — cheap named-tuple passed out of the lock
# ============================================================================

class RecordingSnapshot(NamedTuple):
    """Atomic snapshot of all fields the modal loop needs, read under one lock."""
    recording:    bool
    paused:       bool
    capture_mode: str
    session_dir:  str
    dirty:        bool
    suppressed:   bool   # True  → still in cooldown window
    counter:      int


# ============================================================================
# THREAD-SAFE SINGLETON — DOUBLE-CHECKED LOCKING
# ============================================================================

class StateManager:
    """
    Thread-safe singleton state manager.

    CHANGELOG v2.6.0:
    - __slots__ on inner data classes (CameraScheduler, WindowAsyncContext)
    - get_recording_snapshot() — reads all modal-loop state under one lock
    - mark_captured() returns new counter value to avoid second lock
    - CHANGELOG v2.5.1 retained (properties, public accessors)
    """

    _class_lock              = RLock()
    _instance: Optional['StateManager'] = None

    def __new__(cls):
        if cls._instance is not None:
            return cls._instance
        with cls._class_lock:
            if cls._instance is None:
                instance              = super().__new__(cls)
                instance._lock        = RLock()
                instance._initialized = False
                cls._instance         = instance
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return

            self._state              = RecordingState.IDLE
            self._capture_mode       = ""
            self._session_dir        = ""
            self._counter            = 0
            self._start_time         = 0.0
            self._last_capture_time  = 0.0

            self._timer              = None

            self._last_window_image  = ""
            self._window_async       = WindowAsyncContext()

            self._camera_schedulers: Dict[str, CameraScheduler] = {}
            self._rr_index           = 0

            self._dirty              = True
            self._ignore_depsgraph   = False
            self._handler_installed  = False
            self._suppress_until     = 0.0

            self._initialized        = True
            logger.info("StateManager initialized (THREAD-SAFE SINGLETON v2.6.0)")

    # ========================================================================
    # OPTIMIZED: batch snapshot — single lock acquisition for modal loop
    # ========================================================================

    def get_recording_snapshot(self) -> RecordingSnapshot:
        """
        Return all recording state the modal loop needs under ONE lock.
        Replaces 5-6 separate property reads (each acquiring the lock).
        """
        with self._lock:
            now = time.time()
            return RecordingSnapshot(
                recording    = self._state != RecordingState.IDLE,
                paused       = self._state == RecordingState.PAUSED,
                capture_mode = self._capture_mode,
                session_dir  = self._session_dir,
                dirty        = self._dirty,
                suppressed   = now < self._suppress_until,
                counter      = self._counter,
            )

    # ========================================================================
    # Core read-only properties
    # ========================================================================

    @property
    def recording(self) -> bool:
        with self._lock:
            return self._state != RecordingState.IDLE

    @property
    def paused(self) -> bool:
        with self._lock:
            return self._state == RecordingState.PAUSED

    @property
    def state(self) -> RecordingState:
        with self._lock:
            return self._state

    @property
    def capture_mode(self) -> str:
        with self._lock:
            return self._capture_mode

    @property
    def session_dir(self) -> str:
        with self._lock:
            return self._session_dir

    @property
    def counter(self) -> int:
        with self._lock:
            return self._counter

    @property
    def last_capture_time(self) -> float:
        with self._lock:
            return self._last_capture_time

    @property
    def dirty(self) -> bool:
        with self._lock:
            return self._dirty

    @property
    def handler_installed(self) -> bool:
        with self._lock:
            return self._handler_installed

    @property
    def suppress_until(self) -> float:
        with self._lock:
            return self._suppress_until

    @property
    def rr_index(self) -> int:
        with self._lock:
            return self._rr_index

    @property
    def timer(self):
        with self._lock:
            return self._timer

    # ========================================================================
    # last_window_image  (v2.5.1 @property)
    # ========================================================================

    @property
    def last_window_image(self) -> str:
        with self._lock:
            return self._last_window_image

    @last_window_image.setter
    def last_window_image(self, path: str):
        with self._lock:
            self._last_window_image = str(path) if path else ""

    # ========================================================================
    # ignore_depsgraph  (v2.5.1 @property)
    # ========================================================================

    @property
    def ignore_depsgraph(self) -> bool:
        with self._lock:
            return self._ignore_depsgraph

    @ignore_depsgraph.setter
    def ignore_depsgraph(self, value: bool):
        with self._lock:
            self._ignore_depsgraph = bool(value)

    # ========================================================================
    # Public read-only views of internal collections  (v2.5.1)
    # ========================================================================

    @property
    def window_async_context(self) -> WindowAsyncContext:
        with self._lock:
            return self._window_async

    @property
    def camera_schedulers_map(self) -> Dict[str, CameraScheduler]:
        with self._lock:
            return dict(self._camera_schedulers)

    # ========================================================================
    # Backward-compat aliases
    # ========================================================================

    def get_last_window_image(self) -> str:
        return self.last_window_image

    def get_ignore_depsgraph(self) -> bool:
        return self.ignore_depsgraph

    # ========================================================================
    # Setters
    # ========================================================================

    def set_last_window_image(self, path: str):
        self.last_window_image = path

    def set_ignore_depsgraph(self, value: bool):
        with self._lock:
            prev = self._ignore_depsgraph
            self._ignore_depsgraph = bool(value)
            if prev != self._ignore_depsgraph:
                logger.debug(f"Ignore depsgraph: {prev} → {self._ignore_depsgraph}")

    def set_dirty(self, value: bool):
        with self._lock:
            self._dirty = bool(value)

    def set_handler_installed(self, value: bool):
        with self._lock:
            self._handler_installed = bool(value)

    def install_handler(self):
        self.set_handler_installed(True)

    def uninstall_handler(self):
        self.set_handler_installed(False)

    def set_suppression(self, milliseconds: int):
        with self._lock:
            self._suppress_until = time.time() + (milliseconds / 1000.0)

    def is_suppressed(self) -> bool:
        with self._lock:
            return time.time() < self._suppress_until

    def set_timer(self, timer):
        with self._lock:
            self._timer = timer

    # ========================================================================
    # Recording Control
    # ========================================================================

    def start_recording(self, mode: str, session_dir: str):
        with self._lock:
            if self._state != RecordingState.IDLE:
                raise RuntimeError(f"Already recording (state={self._state.value})")
            self._state             = RecordingState.RECORDING
            self._capture_mode      = mode
            self._session_dir       = session_dir
            self._counter           = 0
            self._start_time        = time.time()
            self._last_capture_time = 0.0
            self._dirty             = True
            self._last_window_image = ""
            self._rr_index          = 0
            self._suppress_until    = 0.0
            self._window_async.reset()
            logger.info(f"Recording started: {mode}, session: {session_dir}")

    def stop_recording(self):
        with self._lock:
            if self._state == RecordingState.IDLE:
                return
            final_count        = self._counter
            self._state        = RecordingState.IDLE
            self._capture_mode = ""
            self._session_dir  = ""
            self._counter      = 0
            self._timer        = None
            logger.info(f"Recording stopped, total frames: {final_count}")

    def pause_recording(self):
        with self._lock:
            if self._state == RecordingState.RECORDING:
                self._state = RecordingState.PAUSED
                logger.info("Recording paused")

    def resume_recording(self):
        with self._lock:
            if self._state == RecordingState.PAUSED:
                self._state = RecordingState.RECORDING
                logger.info("Recording resumed")

    def mark_captured(self) -> int:
        """
        Increment counter and update timestamps.
        Returns the new counter value so callers don't need a second lock.
        """
        with self._lock:
            if self._state in (RecordingState.RECORDING, RecordingState.PAUSED):
                self._counter          += 1
                self._last_capture_time = time.time()
                self._dirty             = False
                new_count               = self._counter
                logger.debug(f"Frame captured: {new_count}")
                return new_count
            return self._counter

    # ========================================================================
    # Window Async Management
    # ========================================================================

    def is_window_async_pending(self) -> bool:
        with self._lock:
            return self._window_async.state != AsyncState.IDLE

    def schedule_window_async(self, delay: float) -> bool:
        with self._lock:
            if self._window_async.state != AsyncState.IDLE:
                return False
            self._window_async.state          = AsyncState.SCHEDULED
            self._window_async.scheduled_time = time.time() + delay
            return True

    def start_window_capture(self):
        with self._lock:
            self._window_async.state = AsyncState.CAPTURING

    def finish_window_capture(self, success: bool = True, error: Optional[str] = None):
        with self._lock:
            if success:
                self._window_async.total_captures += 1
            else:
                self._window_async.last_error = error or ""
            self._window_async.state = AsyncState.IDLE

    def cancel_window_async(self) -> bool:
        with self._lock:
            if self._window_async.state == AsyncState.IDLE:
                return False
            self._window_async.reset()
            return True

    # ========================================================================
    # Camera Schedulers
    # ========================================================================

    def clear_camera_schedulers(self):
        with self._lock:
            self._camera_schedulers.clear()
            self._rr_index = 0

    def init_camera_scheduler(self, camera_name: str, interval: float, start_index: int = 0):
        with self._lock:
            self._camera_schedulers[camera_name] = CameraScheduler(
                camera_name, interval, start_index)

    def get_due_cameras(self, camera_names: List[str], round_robin: bool = False) -> List[str]:
        with self._lock:
            if not camera_names:
                return []
            now  = time.time()
            due  = []
            scheds = self._camera_schedulers
            if round_robin:
                start = self._rr_index
                n     = len(camera_names)
                for offset in range(n):
                    idx   = (start + offset) % n
                    name  = camera_names[idx]
                    sched = scheds.get(name)
                    if sched and sched.is_due(now):
                        due.append(name)
                        self._rr_index = (idx + 1) % n
                        break
            else:
                for name in camera_names:
                    sched = scheds.get(name)
                    if sched and sched.is_due(now):
                        due.append(name)
            return due

    def update_camera_due(self, camera_name: str):
        with self._lock:
            sched = self._camera_schedulers.get(camera_name)
            if sched:
                sched.mark_captured(time.time())

    def get_camera_scheduler(self, camera_name: str) -> Optional[CameraScheduler]:
        with self._lock:
            return self._camera_schedulers.get(camera_name)

    # ========================================================================
    # Diagnostics
    # ========================================================================

    def get_status(self) -> dict:
        with self._lock:
            return {
                'state':     self._state.value,
                'recording': self._state != RecordingState.IDLE,
                'paused':    self._state == RecordingState.PAUSED,
            }

    def validate_state(self) -> bool:
        with self._lock:
            if self._state == RecordingState.IDLE:
                if self._session_dir or self._timer:
                    logger.error("Invalid state: IDLE but has session/timer")
                    return False
            else:
                if not self._session_dir:
                    logger.error(f"Invalid state: {self._state.value} but no session")
                    return False
            return True

    def get_info(self) -> dict:
        with self._lock:
            return {
                'state':              self._state.value,
                'recording':          self._state != RecordingState.IDLE,
                'paused':             self._state == RecordingState.PAUSED,
                'mode':               self._capture_mode,
                'session':            self._session_dir,
                'counter':            self._counter,
                'dirty':              self._dirty,
                'ignore_depsgraph':   self._ignore_depsgraph,
                'timer':              self._timer is not None,
                'cameras':            len(self._camera_schedulers),
                'last_window_image':  self._last_window_image,
                'window_async_state': self._window_async.state.value,
            }

    def print_info(self):
        info = self.get_info()
        print("\n" + "=" * 70)
        print("StateManager Info")
        print("=" * 70)
        for k, v in info.items():
            print(f"  {k:<25} {v}")
        print("=" * 70 + "\n")

    # ========================================================================
    # Reset & Cleanup
    # ========================================================================

    def force_reset(self):
        with self._lock:
            logger.warning("Force resetting StateManager...")
            self._state             = RecordingState.IDLE
            self._capture_mode      = ""
            self._session_dir       = ""
            self._counter           = 0
            self._start_time        = 0.0
            self._last_capture_time = 0.0
            self._timer             = None
            self._last_window_image = ""
            self._window_async.reset()
            self._camera_schedulers.clear()
            self._rr_index          = 0
            self._dirty             = True
            self._ignore_depsgraph  = False
            self._handler_installed = False
            self._suppress_until    = 0.0
            logger.info("Force reset complete")

    def reset(self):
        self.force_reset()

    def cleanup(self):
        with self._lock:
            if self._state != RecordingState.IDLE:
                self.stop_recording()
            self.cancel_window_async()
            self.clear_camera_schedulers()
            logger.info("StateManager cleaned up")

    @classmethod
    def reset_instance(cls):
        with cls._class_lock:
            if cls._instance:
                try:
                    cls._instance.cleanup()
                except Exception as e:
                    logger.warning(f"Cleanup error during reset: {e}")
                cls._instance = None
            logger.info("StateManager instance reset")


# ============================================================================
# Module-level Helper
# ============================================================================

def mark_scene_dirty(scene, depsgraph):
    mgr = StateManager()
    if mgr.ignore_depsgraph or mgr.is_suppressed():
        return
    mgr.set_dirty(True)


# ============================================================================
# Registration
# ============================================================================

def register():
    logger.info("StateManager registered (THREAD-SAFE SINGLETON v2.6.0)")
    StateManager()


def unregister():
    logger.info("Unregistering StateManager")
    try:
        StateManager().cleanup()
    except Exception as e:
        logger.warning(f"Cleanup error: {e}")
    try:
        import bpy
        bpy.app.handlers.depsgraph_update_post.remove(mark_scene_dirty)
    except (ValueError, ImportError):
        pass
    except Exception as e:
        logger.warning(f"Handler removal error: {e}")
    logger.info("StateManager unregistered")
