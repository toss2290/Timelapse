"""
Action-count capture trigger — Window mode AND Camera List mode.

v4: counts completed OPERATORS instead of depsgraph updates.

WHY v3's "only count is_updated_geometry/is_updated_transform" STILL
MIS-FIRED IN EDIT MODE
----------------------------------------------------------------------
depsgraph_update_post fires for far more than deliberate edits. In Edit
Mode in particular, Blender re-evaluates the mesh (including the
modifier stack, for live "show in edit mode" previews) on ordinary
viewport redraws — and that re-evaluation is flagged with
is_updated_geometry=True even when nothing was actually changed by the
user. There's no depsgraph-update flag that reliably means "the user
just did something" as opposed to "Blender re-computed something for
display" — so no amount of update-flag filtering on top of
depsgraph_update_post was ever going to be fully reliable.

THE FIX
-------
bpy.context.window_manager.operators is the same list that backs
Blender's "Adjust Last Operation" (F9) panel and the Info editor's
operator log. An entry is appended to it ONLY when an operator actually
finishes running — a confirmed grab/rotate/scale/extrude, a sculpt
stroke, a mesh edit, anything that shows up in Undo/Redo — never from a
redraw or a passive re-evaluation. Polling its length is a precise,
noise-free signal for "the user just completed an action", and it isn't
tied to any particular key, so there's nothing to conflict with (unlike
the very first, keymap-based version of this feature) and no fixed key
list to maintain.

Still true to the addon's "no modal operators" rule: this is a plain
bpy.app.timers poll loop, nothing modal, and the actual screenshot is
deferred a tick past the point where the threshold is reached so it
never runs from inside a timer/handler edge case.

CAMERA LIST MODE
-----------------
The counting mechanism is identical for both modes — only what happens
once the threshold is reached differs:
  - WINDOW: takes a screenshot directly (force_save=True, bypassing
    idle-diff/cooldown, same as the hotkey trigger).
  - CAMERA_LIST: calls camera.capture_cameras(require_dirty=False),
    exactly what the periodic timer would call — action-count just
    replaces the *heartbeat* that decides when to check. Each camera's
    own interval/override (and round-robin/max-per-tick settings) still
    gate whether that particular camera actually renders on any given
    heartbeat, same as with the timer. Gated by
    prefs.camera_capture_on_action_count, independently of the Window-
    mode toggle.
"""

import bpy
import os
import time
import logging

from ..state_manager import StateManager
from .. import utils

logger = logging.getLogger(__name__)

try:
    from ..capture import window as window_capture
except ImportError as e:
    window_capture = None
    logger.error(f"Capture import failed: {e}")

try:
    from ..capture import camera as camera_capture
except ImportError as e:
    camera_capture = None
    logger.error(f"Camera capture import failed: {e}")


# ============================================================================
# State
# ============================================================================

POLL_INTERVAL = 0.2  # seconds between operator-history checks

_action_counter:    int = 0
_last_op_count:     int = 0
_last_counted_ts:   float = 0.0
_capture_scheduled: bool = False


def _current_op_count() -> int:
    try:
        return len(bpy.context.window_manager.operators)
    except Exception:
        return _last_op_count


def reset_counter() -> None:
    """Call when a recording starts/stops, or a trigger toggle flips, so
    a backlog of unrelated operators run beforehand (or while the
    feature was off) never gets counted as a burst of actions."""
    global _action_counter, _last_op_count, _last_counted_ts, _capture_scheduled
    _action_counter    = 0
    _last_counted_ts   = 0.0
    _capture_scheduled = False
    _last_op_count      = _current_op_count()


def _active_prefs_and_snap():
    """Return (prefs, snap) only if some action-count trigger should be
    live right now for the CURRENT capture mode, else (None, None)."""
    prefs = utils.get_addon_preferences()
    if not prefs:
        return None, None
    mgr = StateManager()
    if not mgr.recording:
        return None, None
    snap = mgr.get_recording_snapshot()
    if snap.paused:
        return None, None

    if snap.capture_mode == 'WINDOW':
        if not prefs.window_capture_on_action_count:
            return None, None
    elif snap.capture_mode == 'CAMERA_LIST':
        if not prefs.camera_capture_on_action_count:
            return None, None
    else:
        return None, None

    return prefs, snap


# ============================================================================
# Poll loop — runs for the lifetime of the addon; cheap no-op when not
# actively recording with a trigger enabled for the current mode.
# ============================================================================

def _poll_tick():
    global _last_op_count, _action_counter, _last_counted_ts, _capture_scheduled

    prefs, snap = _active_prefs_and_snap()
    if not prefs:
        return POLL_INTERVAL  # idle — keep the loop alive, do nothing

    current = _current_op_count()
    if current == _last_op_count:
        return POLL_INTERVAL

    delta = (current - _last_op_count) if current > _last_op_count else 1
    _last_op_count = current

    # Coalesce operators that land within the same short window into one
    # counted action (e.g. some tools push more than one history entry
    # for what's really a single user gesture).
    min_gap = max(0.0, float(prefs.action_count_debounce))
    now = time.time()
    if (now - _last_counted_ts) < min_gap:
        return POLL_INTERVAL
    _last_counted_ts = now

    _action_counter += delta
    batch = max(1, int(prefs.action_count_batch))
    logger.debug(f"Action-count [{snap.capture_mode}]: {_action_counter}/{batch}")

    if _action_counter >= batch and not _capture_scheduled:
        _action_counter    = 0
        _capture_scheduled = True
        bpy.app.timers.register(_deferred_capture, first_interval=0.05)

    return POLL_INTERVAL


def _deferred_capture():
    global _capture_scheduled
    _capture_scheduled = False
    try:
        prefs, snap = _active_prefs_and_snap()
        if not prefs:
            return None
        if snap.capture_mode == 'WINDOW':
            _fire_window_capture(snap)
        elif snap.capture_mode == 'CAMERA_LIST':
            _fire_camera_capture(snap)
    except Exception as e:
        logger.error(f"Action-count deferred capture failed: {e}", exc_info=True)
    return None  # one-shot


def _fire_window_capture(snap) -> None:
    try:
        if not window_capture:
            return
        base_dir = snap.session_dir
        if not base_dir or not os.path.isdir(bpy.path.abspath(base_dir)):
            logger.warning("Action-count capture skipped: session directory missing")
            return
        window_capture.capture_window_async(bpy.context, base_dir, force_save=True)
        logger.info("Action-count capture triggered (Window)")
    except Exception as e:
        logger.error(f"Action-count Window capture failed: {e}", exc_info=True)


def _fire_camera_capture(snap) -> None:
    try:
        if not camera_capture:
            return
        base_dir = snap.session_dir
        if not base_dir or not os.path.isdir(bpy.path.abspath(base_dir)):
            logger.warning("Action-count capture skipped: session directory missing")
            return
        result = camera_capture.capture_cameras(bpy.context, base_dir, require_dirty=False)
        if result:
            from .. import progress
            progress.get_recording_progress().add_frame()
        logger.info("Action-count capture triggered (Camera List)")
    except Exception as e:
        logger.error(f"Action-count Camera List capture failed: {e}", exc_info=True)


# ============================================================================
# Registration
# ============================================================================

def register():
    logger.info("Registering action-count trigger (operator-history polling)")
    reset_counter()
    if not bpy.app.timers.is_registered(_poll_tick):
        bpy.app.timers.register(_poll_tick, first_interval=POLL_INTERVAL)


def unregister():
    logger.info("Unregistering action-count trigger")
    if bpy.app.timers.is_registered(_poll_tick):
        try:
            bpy.app.timers.unregister(_poll_tick)
        except Exception:
            pass
    reset_counter()

