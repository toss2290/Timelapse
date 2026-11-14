"""
Recording operators: start/stop, pause/resume and capture interval.

Design: recording is NOT a modal operator.
  - A persistent bpy.app.timers callback (_tlx_capture_tick) drives all
    periodic capture.
  - StateManager is the single source of truth for recording state.
  - scene.tlx_is_recording is only a UI flag, set and cleared by Start/Stop.

Why no modal: Blender cancels every registered modal handler when
object.mode_set() runs (for example from the Ctrl+Tab object-mode pie), which
desynchronised or stopped recording. With no modal handler registered, mode
switches and context changes cannot interfere.

"Capture on input only" (operators/hotkey.py) and "capture every N actions"
(operators/action_count.py) are alternative triggers that replace the
periodic timer.
"""

import bpy
import os
import logging
from bpy.app.handlers import persistent
from bpy.types import Operator
from bpy.props import BoolProperty, FloatProperty, EnumProperty

from ..state_manager import StateManager
from .. import utils
from .. import constants
from .. import error_messages as err
from .. import progress

logger = logging.getLogger(__name__)

try:
    from ..capture import window as window_capture
    from ..capture import camera as camera_capture
    logger.info("Capture modules imported successfully")
except ImportError as e:
    window_capture = None
    camera_capture = None
    logger.error(f"Capture import failed: {e}")


# ============================================================================
# Persistent capture timer
# ============================================================================

_capture_interval: float = constants.DEFAULT_INTERVAL
_last_header_text: str   = ""


def _tlx_capture_tick() -> float | None:
    """
    bpy.app.timers callback — runs in the main thread on a fixed interval.

    Completely independent of modal operators, mode switches, or context
    changes.  Returns the next interval (float) to reschedule, or None
    to unregister the timer.
    """
    global _last_header_text

    try:
        mgr  = StateManager()
        snap = mgr.get_recording_snapshot()

        if not snap.recording:
            logger.info("Capture timer: recording ended, unregistering")
            _last_header_text = ""
            return None  # unregister

        # ── Header update ────────────────────────────────────────────────
        _update_header(snap)

        if snap.paused:
            return _capture_interval

        # ── Session directory check ──────────────────────────────────────
        base_dir = snap.session_dir
        if not base_dir or not os.path.exists(base_dir):
            logger.error("Session directory missing — stopping recording")
            _emergency_stop()
            return None

        # ── Capture ──────────────────────────────────────────────────────
        prefs = utils.get_addon_preferences()
        ctx   = bpy.context

        try:
            if snap.capture_mode == 'WINDOW':
                # window_capture_on_input_only (operators/hotkey.py) and
                # window_capture_on_action_count (operators/action_count.py)
                # are mutually-exclusive alternative triggers that replace
                # the periodic timer entirely — skip it here when either
                # is active, they drive their own captures.
                skip_timer = prefs and (prefs.window_capture_on_input_only
                                         or prefs.window_capture_on_action_count)
                if not skip_timer:
                    if window_capture and ctx:
                        window_capture.capture_window_async(ctx, base_dir,
                                                             force_save=False)
            else:
                # camera_capture_on_action_count (operators/action_count.py)
                # replaces the timer as the heartbeat that checks which
                # cameras are due — skip the timer-driven check here when
                # it's active, action_count.py drives it instead. Each
                # camera's own interval/override still gates whether it
                # actually captures on any given heartbeat, whichever one
                # is currently driving it.
                skip_timer = prefs and prefs.camera_capture_on_action_count
                if not skip_timer:
                    if camera_capture and ctx:
                        result = camera_capture.capture_cameras(
                            ctx, base_dir, require_dirty=True)
                        if result:
                            progress.get_recording_progress().add_frame()
        except Exception as e:
            logger.error(f"Capture tick failed: {e}", exc_info=True)

        return _capture_interval

    except Exception as e:
        logger.error(f"Capture timer unexpected error: {e}", exc_info=True)
        return _capture_interval   # keep running even on unexpected errors


def _update_header(snap) -> None:
    """Update the 3D viewport header text (cached to avoid redundant calls)."""
    global _last_header_text
    try:
        prefs = utils.get_addon_preferences()
        show  = getattr(prefs, 'show_recording_header', True) if prefs else True
        wm    = bpy.context.window_manager if bpy.context else None
        if not wm:
            return

        if show:
            rec_progress = progress.get_recording_progress()
            header_text  = rec_progress.get_header_text(snap.capture_mode)
            if header_text != _last_header_text:
                _last_header_text = header_text
                for window in wm.windows:
                    for area in window.screen.areas:
                        if area.type == 'VIEW_3D':
                            area.header_text_set(header_text)
        else:
            if _last_header_text != "":
                _last_header_text = ""
                for window in wm.windows:
                    for area in window.screen.areas:
                        if area.type == 'VIEW_3D':
                            area.header_text_set(None)
    except Exception as e:
        logger.debug(f"Header update failed: {e}")


def _start_capture_timer(interval: float) -> None:
    global _capture_interval
    _capture_interval = max(constants.MIN_INTERVAL, interval)
    if bpy.app.timers.is_registered(_tlx_capture_tick):
        bpy.app.timers.unregister(_tlx_capture_tick)
    bpy.app.timers.register(_tlx_capture_tick,
                             first_interval=_capture_interval,
                             persistent=True)
    logger.info(f"Capture timer started (interval={_capture_interval:.2f}s, persistent=True)")


def _stop_capture_timer() -> None:
    """Stop the persistent capture timer. Safe to call even if not running."""
    global _last_header_text
    if bpy.app.timers.is_registered(_tlx_capture_tick):
        try:
            bpy.app.timers.unregister(_tlx_capture_tick)
            logger.info("Capture timer stopped")
        except Exception as e:
            logger.warning(f"Failed to unregister capture timer: {e}")
    _last_header_text = ""


def _emergency_stop() -> None:
    """Stop recording safely from within a timer callback."""
    try:
        stop_recording_session(getattr(bpy.context, 'scene', None))
    except Exception as e:
        logger.error(f"Emergency stop failed: {e}")


def _clear_all_headers() -> None:
    try:
        wm = bpy.context.window_manager if bpy.context else None
        if wm:
            for window in wm.windows:
                for area in window.screen.areas:
                    if area.type == 'VIEW_3D':
                        area.header_text_set(None)
    except Exception:
        pass


def stop_recording_session(scene=None) -> int:
    """Stop an active recording and release everything it holds.

    Needs no operator and no UI context, so it is safe to call from timers and
    handlers (load_pre, emergency stop) as well as from the Stop button. Safe
    to call when not recording. Returns the number of frames captured.
    """
    mgr         = StateManager()
    final_count = mgr.counter

    if window_capture:
        try:
            window_capture.cancel_async_capture()
        except Exception as e:
            logger.warning(f"Async cancel failed: {e}")

    _stop_capture_timer()

    try:
        from . import action_count
        action_count.reset_counter()
    except Exception as e:
        logger.debug(f"Action-count reset skipped: {e}")

    _clear_all_headers()

    mgr.stop_recording()
    if scene is not None:
        try:
            scene.tlx_is_recording = False
        except Exception as e:
            logger.debug(f"Could not clear tlx_is_recording: {e}")

    if mgr.handler_installed:
        try:
            from ..state_manager import mark_scene_dirty
            bpy.app.handlers.depsgraph_update_post.remove(mark_scene_dirty)
        except ValueError:
            pass
        finally:
            mgr.uninstall_handler()

    if scene is not None:
        try:
            for camera_item in scene.tlx_cameras:
                for key in [k for k in camera_item.keys()
                            if k.startswith("_tlx_")]:
                    del camera_item[key]
        except Exception as e:
            logger.warning(f"Camera metadata cleanup failed: {e}")

    return final_count


# ============================================================================
# File-load handlers
# ============================================================================
# Opening, creating or reverting a .blend replaces the scene that recording
# was started in. Without these handlers the recording would silently carry on
# against the wrong file (the persistent timer survives file loads).

@persistent
def _tlx_on_load_pre(*_args) -> None:
    """Stop an active recording before Blender replaces the current file."""
    if not StateManager().recording:
        return
    try:
        count = stop_recording_session(getattr(bpy.context, 'scene', None))
        logger.warning(
            f"Recording stopped because another file is being loaded "
            f"({count} frame(s) were captured)")
    except Exception as e:
        logger.error(f"Stopping recording on file load failed: {e}", exc_info=True)


@persistent
def _tlx_on_load_post(*_args) -> None:
    """Tidy up after a file load.

    - A file saved *while recording* carries tlx_is_recording=True; clear it,
      or the UI would show a recording that does not exist.
    - Timers that are not persistent are dropped by Blender on file load; bring
      the action-count poll back so that trigger keeps working.
    """
    try:
        if not StateManager().recording:
            for scene in bpy.data.scenes:
                if scene.tlx_is_recording:
                    scene.tlx_is_recording = False
    except Exception as e:
        logger.warning(f"Clearing stale recording flag failed: {e}")
    try:
        from . import action_count
        action_count.ensure_polling()
    except Exception as e:
        logger.debug(f"Action-count poll restore skipped: {e}")


def _install_load_handlers() -> None:
    for handlers, fn in ((bpy.app.handlers.load_pre,  _tlx_on_load_pre),
                         (bpy.app.handlers.load_post, _tlx_on_load_post)):
        if fn not in handlers:
            handlers.append(fn)


def _remove_load_handlers() -> None:
    for handlers, fn in ((bpy.app.handlers.load_pre,  _tlx_on_load_pre),
                         (bpy.app.handlers.load_post, _tlx_on_load_post)):
        if fn in handlers:
            handlers.remove(fn)


# ============================================================================
# Main Recording Operator  (pure execute — NO modal)
# ============================================================================

class TLX_OT_record(Operator):
    """Start or stop timelapse recording."""

    bl_idname  = 'tlx.record'
    bl_label   = 'Start / Stop Recording'
    bl_options = {'REGISTER'}

    start:    BoolProperty(default=True)
    interval: FloatProperty(name='Interval (s)', min=0.0, max=3600.0, default=0.0)
    mode: EnumProperty(
        name='Mode',
        items=[
            ('DEFAULT',     'Use Scene Setting', ''),
            ('WINDOW',      'Blender Window',    ''),
            ('CAMERA_LIST', 'Camera List',       ''),
        ],
        default='DEFAULT'
    )

    def execute(self, context):
        logger.info(f"Recording operator: start={self.start}, mode={self.mode}")
        scene = context.scene
        prefs = utils.get_addon_preferences()
        wm    = context.window_manager

        if not prefs:
            err.create_error('no_preferences').report_to_user(self)
            return {'CANCELLED'}

        try:
            utils.validate_blender_version((5, 1, 0))
        except utils.ValidationError as e:
            err.report_validation_error(self, e, fallback_key='blender_version_old')
            return {'CANCELLED'}

        self._validate_intervals(scene, prefs)

        if self.start:
            return self._start_recording(context, scene, prefs, wm)
        else:
            return self._stop_recording(context, scene, wm)

    # -----------------------------------------------------------------------
    # Start  —  registers bpy.app.timers, NO modal
    # -----------------------------------------------------------------------

    def _start_recording(self, context, scene, prefs, wm):
        mgr = StateManager()

        if mgr.recording:
            err.create_error('already_recording').report_to_user(self, report_type='INFO')
            return {'CANCELLED'}

        try:
            utils.validate_scene(scene)
        except utils.ValidationError as e:
            err.report_validation_error(self, e)
            return {'CANCELLED'}

        capture_mode = self._get_capture_mode(scene)

        if not self._validate_mode_requirements(capture_mode, scene, prefs):
            return {'CANCELLED'}

        try:
            session_dir = self._setup_session(prefs)
        except utils.ValidationError as e:
            err.create_technical_error('cannot_create_directory', e,
                                        error=str(e)).report_to_user(self)
            return {'CANCELLED'}

        base_interval = self._calculate_base_interval(scene, prefs, capture_mode)
        if base_interval < constants.MIN_INTERVAL:
            err.create_error('invalid_interval', value=base_interval,
                              min=constants.MIN_INTERVAL, max=3600.0).report_to_user(self)
            return {'CANCELLED'}

        try:
            mgr.start_recording(capture_mode, session_dir)
        except Exception as e:
            err.create_technical_error('capture_failed', e,
                                        error=str(e)).report_to_user(self)
            return {'CANCELLED'}

        progress.get_recording_progress().start()

        if capture_mode == 'CAMERA_LIST':
            try:
                self._init_camera_schedulers(scene, session_dir, prefs)
                if camera_capture:
                    cam_iv = camera_capture.compute_min_timer_interval(scene, prefs)
                    base_interval = min(base_interval,
                                        max(constants.MIN_INTERVAL, cam_iv))
            except Exception as e:
                err.create_technical_error('capture_failed', e,
                                            error=str(e)).report_to_user(self)
                mgr.stop_recording()
                return {'CANCELLED'}

        base_interval = max(constants.MIN_INTERVAL, base_interval)

        # ── Idle detection depsgraph handler ────────────────────────────
        if prefs and prefs.idle_detection and not mgr.handler_installed:
            try:
                from ..state_manager import mark_scene_dirty
                bpy.app.handlers.depsgraph_update_post.append(mark_scene_dirty)
                mgr.install_handler()
            except Exception as e:
                logger.warning(f"Depsgraph handler failed: {e}")

        # ── Start persistent capture timer ──────────────────────────────
        # persistent=True: survives file load, mode switches, everything.
        # NO wm.modal_handler_add() — zero modal involvement.
        _start_capture_timer(base_interval)

        scene.tlx_is_recording = True

        try:
            from . import action_count
            action_count.reset_counter()
        except Exception as e:
            logger.debug(f"Action-count reset skipped: {e}")

        if prefs and getattr(prefs, 'capture_immediate_on_start', True):
            try:
                self._immediate_capture(context, capture_mode, session_dir, prefs)
            except Exception as e:
                logger.error(f"Immediate capture failed: {e}", exc_info=True)

        utils.invalidate_window_cache()

        err.report_success(self, 'recording_started',
                           interval=f"{base_interval:.1f}", mode=capture_mode)
        return {'FINISHED'}   # <── FINISHED, not RUNNING_MODAL

    # -----------------------------------------------------------------------
    # Stop
    # -----------------------------------------------------------------------

    def _stop_recording(self, context, scene, wm):
        final_count = stop_recording_session(scene)
        err.report_success(self, 'recording_stopped', count=final_count)
        return {'FINISHED'}

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _validate_intervals(self, scene, prefs):
        if hasattr(scene, 'tlx_capture_interval'):
            try:
                interval = float(scene.tlx_capture_interval)
                if not (constants.MIN_INTERVAL < interval <= 3600):
                    scene.tlx_capture_interval = constants.DEFAULT_INTERVAL
            except (TypeError, ValueError):
                scene.tlx_capture_interval = constants.DEFAULT_INTERVAL
        if prefs:
            try:
                default_interval = float(getattr(prefs, 'default_interval',
                                                   constants.DEFAULT_INTERVAL))
                if not (constants.MIN_INTERVAL < default_interval <= 3600):
                    prefs.default_interval = constants.DEFAULT_INTERVAL
            except (TypeError, ValueError):
                prefs.default_interval = constants.DEFAULT_INTERVAL

    def _validate_mode_requirements(self, capture_mode, scene, prefs):
        if capture_mode == 'CAMERA_LIST':
            try:
                utils.validate_camera_list(scene)
            except utils.ValidationError as e:
                err.report_validation_error(self, e,
                                             fallback_key='camera_list_empty')
                return False
        if capture_mode == 'WINDOW':
            if utils.is_headless():
                err.create_error('headless_mode_window').report_to_user(self)
                return False
            try:
                win, screen, area, region = utils.find_window_area_region()
                if not all((win, screen, area, region)):
                    raise utils.ValidationError("No suitable UI context found")
            except utils.ValidationError:
                err.create_error('no_ui_context').report_to_user(self)
                return False
        return True

    def _setup_session(self, prefs):
        base_root = (prefs.output_dir if (prefs and prefs.output_dir)
                     else '//Timelapse_Images')
        try:
            base_root = utils.ensure_directory(base_root, validate_writable=True)
        except utils.ValidationError as e:
            raise utils.ValidationError(f"Cannot create base directory: {e}")
        try:
            utils.validate_disk_space(base_root, required_mb=100)
        except utils.ValidationError as e:
            logger.warning(f"Disk space warning: {e}")
            import re
            match = re.search(r'(\d+(?:\.\d+)?)MB available', str(e))
            if match:
                err.create_error('disk_almost_full',
                                  available=match.group(1)).report_to_user(
                    self, report_type='WARNING')
        return utils.get_session_folder(base_root)

    def _init_camera_schedulers(self, scene, session_dir, prefs):
        if not camera_capture:
            raise RuntimeError("Camera capture module not available")
        camera_capture.init_camera_schedulers(scene, session_dir, prefs)

    def _get_capture_mode(self, scene):
        return (self.mode if self.mode != 'DEFAULT'
                else getattr(scene, 'tlx_capture_mode', 'CAMERA_LIST'))

    def _calculate_base_interval(self, scene, prefs, capture_mode):
        if self.interval > 0.0:
            return max(constants.MIN_INTERVAL, min(3600.0, float(self.interval)))
        scene_iv = getattr(scene, 'tlx_capture_interval', None)
        pref_iv  = prefs.default_interval if prefs else constants.DEFAULT_INTERVAL
        return max(constants.MIN_INTERVAL,
                   min(3600.0,
                       float(scene_iv or pref_iv or constants.DEFAULT_INTERVAL)))

    def _immediate_capture(self, context, capture_mode, session_dir, prefs):
        try:
            if capture_mode == 'WINDOW':
                if window_capture:
                    logger.info("Immediate window capture on start...")
                    window_capture.capture_window_async(
                        context, session_dir, force_save=True)
            else:
                if camera_capture:
                    result = camera_capture.capture_cameras(
                        context, session_dir, require_dirty=False)
                    if result:
                        progress.get_recording_progress().add_frame()
        except Exception as e:
            logger.error(f"Immediate capture failed: {e}", exc_info=True)
            raise


# ============================================================================
# Pause / Resume
# ============================================================================

class TLX_OT_pause_resume(Operator):
    """Pause or resume recording."""

    bl_idname  = 'tlx.pause_resume'
    bl_label   = 'Pause / Resume'
    bl_options = {'REGISTER'}

    def execute(self, context):
        mgr = StateManager()

        if not mgr.recording:
            self.report({'WARNING'}, 'Not recording.')
            return {'CANCELLED'}

        if not mgr.paused:
            mgr.pause_recording()
            err.report_success(self, 'recording_paused')
        else:
            mgr.resume_recording()
            err.report_success(self, 'recording_resumed',
                               interval=f"{_capture_interval:.1f}")

        return {'FINISHED'}


# ============================================================================
# Update / Set Interval
# ============================================================================

class TLX_OT_update_interval(Operator):
    """Apply the current scene interval to the running timer."""

    bl_idname  = 'tlx.update_interval'
    bl_label   = 'Apply Interval'
    bl_options = {'REGISTER'}

    def execute(self, context):
        global _capture_interval
        scene = context.scene
        mgr   = StateManager()

        try:
            interval = max(constants.MIN_INTERVAL,
                           min(3600.0,
                               float(getattr(scene,
                                             'tlx_capture_interval', 2.0))))
        except (TypeError, ValueError):
            err.create_error('invalid_interval', value="invalid",
                              min=constants.MIN_INTERVAL,
                              max=3600.0).report_to_user(self)
            return {'CANCELLED'}

        # Update module-level interval so _tlx_capture_tick uses it on
        # the next reschedule. Restart the timer immediately if recording.
        _capture_interval = interval
        if mgr.recording:
            _start_capture_timer(interval)
            err.report_success(self, 'interval_updated',
                               interval=f"{interval:.1f}")
        else:
            self.report({'INFO'}, f"Interval set to {interval:.1f}s")

        return {'FINISHED'}


class TLX_OT_set_interval(Operator):
    """Set a preset interval value."""

    bl_idname  = 'tlx.set_interval'
    bl_label   = 'Set Interval Preset'
    bl_options = {'REGISTER'}

    value: FloatProperty(name='Value', min=constants.MIN_INTERVAL,
                         max=3600.0, default=constants.DEFAULT_INTERVAL)

    def execute(self, context):
        value = max(constants.MIN_INTERVAL, min(3600.0, float(self.value)))
        context.scene.tlx_capture_interval = value
        mgr = StateManager()
        if mgr.recording and not mgr.paused:
            bpy.ops.tlx.update_interval()
        return {'FINISHED'}


# ============================================================================
# Registration
# ============================================================================

classes = (
    TLX_OT_record,
    TLX_OT_pause_resume,
    TLX_OT_update_interval,
    TLX_OT_set_interval,
)


def register():
    logger.info("Registering recording operators")
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except Exception as e:
            logger.error(f"Failed to register {cls.__name__}: {e}")
    _install_load_handlers()


def unregister():
    logger.info("Unregistering recording operators")
    _remove_load_handlers()
    _stop_capture_timer()
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception as e:
            logger.warning(f"Failed to unregister {cls.__name__}: {e}")
