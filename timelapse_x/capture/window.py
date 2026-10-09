"""
Window capture logic — Blender 5.1+ compatible

v2.6.0 optimizations (on top of v2.5.5 bug-fixes):
  - capture_window(): prefs and mgr fetched ONCE and reused throughout the
    function body instead of being re-looked-up in each helper.
  - _do_screenshot(): context.temp_override dict is built once and reused
    for the fallback path — no extra allocation.
  - _check_image_changed(): reads prefs values via passed-in prefs object
    (already fetched by caller) rather than calling get_addon_preferences()
    again inside the helper.
  - _set_cooldown(): no longer calls get_addon_preferences(); receives prefs
    as an argument.
  - Async rate-limiter cleanup moved behind a monotonic-clock guard to avoid
    calling time.time() twice per callback.

All v2.5.5 bug-fixes retained:
  BUG 1 — frame counter updated in capture_window().
  BUG 2 — compression=1 for temp file; min 50 ms async delay.
  BUG 3 — finish_window_capture() in finally block.
"""

import bpy
import os
import logging
import time
from typing import Optional
from collections import deque

from .. import constants
from .. import utils
from .. import progress as progress_module
from ..state_manager import StateManager
from ..error_handling import (
    CaptureError,
    ResourceError,
    StateError,
    ValidationError,
    handle_errors,
    ErrorContext,
    ErrorRecovery,
    report_error,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Rate Limiting
# ============================================================================

ASYNC_MAX_PENDING      = 5
ASYNC_MAX_RATE         = 10.0
ASYNC_RATE_WINDOW      = 1.0
ASYNC_CLEANUP_INTERVAL = 5.0


class AsyncRateLimiter:
    __slots__ = ('max_pending', 'max_rate', 'rate_window',
                 'pending_count', 'recent_captures', 'stats', 'last_cleanup')

    def __init__(self, max_pending=ASYNC_MAX_PENDING,
                 max_rate=ASYNC_MAX_RATE, rate_window=ASYNC_RATE_WINDOW):
        self.max_pending     = max_pending
        self.max_rate        = max_rate
        self.rate_window     = rate_window
        self.pending_count   = 0
        self.recent_captures = deque(maxlen=100)
        self.stats = {
            'total_requests': 0, 'accepted': 0,
            'dropped_pending': 0, 'dropped_rate': 0,
            'completed': 0, 'failed': 0,
        }
        self.last_cleanup = time.monotonic()

    def can_schedule(self) -> tuple:
        self.stats['total_requests'] += 1
        if self.pending_count >= self.max_pending:
            self.stats['dropped_pending'] += 1
            return False, f"Too many pending ({self.pending_count}/{self.max_pending})"
        rate = self._current_rate()
        if rate >= self.max_rate:
            self.stats['dropped_rate'] += 1
            return False, f"Rate limit ({rate:.1f}/{self.max_rate} fps)"
        self.stats['accepted'] += 1
        return True, ""

    def mark_scheduled(self):
        self.pending_count += 1

    def mark_completed(self, success: bool = True):
        self.pending_count = max(0, self.pending_count - 1)
        if success:
            self.stats['completed'] += 1
            self.recent_captures.append(time.monotonic())
        else:
            self.stats['failed'] += 1

    def _current_rate(self) -> float:
        if not self.recent_captures:
            return 0.0
        now    = time.monotonic()
        cutoff = now - self.rate_window
        return sum(1 for t in self.recent_captures if t >= cutoff) / self.rate_window

    def get_stats(self) -> dict:
        total     = self.stats['total_requests']
        dropped   = self.stats['dropped_pending'] + self.stats['dropped_rate']
        drop_rate = (dropped / total * 100) if total > 0 else 0.0
        return {**self.stats, 'pending': self.pending_count,
                'current_rate': self._current_rate(), 'drop_rate': drop_rate}

    def reset_stats(self):
        self.stats = {
            'total_requests': 0, 'accepted': 0,
            'dropped_pending': 0, 'dropped_rate': 0,
            'completed': 0, 'failed': 0,
        }
        self.recent_captures.clear()

    def should_cleanup(self) -> bool:
        now = time.monotonic()
        if now - self.last_cleanup >= ASYNC_CLEANUP_INTERVAL:
            self.last_cleanup = now
            return True
        return False


_rate_limiter = AsyncRateLimiter()


# ============================================================================
# Main Capture Function (synchronous)
# ============================================================================

@handle_errors(default_return=False, raise_on_error=False,
               user_message="Screenshot capture failed")
def capture_window(context, base_dir: str, force_save: bool = False) -> bool:
    """
    Capture a single screenshot and save it as the next frame.
    Returns True if a frame was written and the counter incremented.
    """
    if utils.is_headless():
        raise CaptureError("WINDOW mode cannot run in background/headless",
                           user_message="Window capture requires GUI mode")

    if not base_dir or not os.path.isdir(base_dir):
        raise ValidationError(f"Invalid base directory: {base_dir}",
                               user_message="Output directory does not exist")

    # OPTIMIZED: fetch prefs and mgr once; pass to helpers
    prefs = utils.get_addon_preferences()
    mgr   = StateManager()

    if not force_save and mgr.is_suppressed():
        logger.debug("⊗ Capture skipped: suppression cooldown")
        return False

    capture_full = (prefs.window_capture_scope == 'FULL') if prefs else False
    stabilize    = (prefs.window_stabilize_view and not capture_full) if prefs else False
    temp_path    = bpy.path.abspath(os.path.join(base_dir, "_TLX_WINDOW_TEMP.png"))

    with ErrorContext("Finding UI region", recoverable=False):
        window, screen, area, region = utils.find_window_area_region()
        if not all((window, screen, area, region)):
            raise CaptureError('No UI region found for screenshot',
                                user_message="No 3D viewport found for screenshot")

    backup = None
    if stabilize and area.type == 'VIEW_3D':
        backup = _backup_overlays(area)

    stealth_backup = None
    scene_for_stealth = getattr(context, 'scene', None)
    stealth_enabled = bool(scene_for_stealth and
                            getattr(scene_for_stealth, 'tlx_window_stealth_camera_enabled', False))
    stealth_cam = getattr(scene_for_stealth, 'tlx_window_stealth_camera', None) \
                  if scene_for_stealth else None
    if stealth_enabled and stealth_cam and stealth_cam.type == 'CAMERA' and area.type == 'VIEW_3D':
        stealth_backup = _backup_view_state(area)
        if stealth_backup:
            hide_pp = getattr(scene_for_stealth, 'tlx_window_stealth_hide_passepartout', True)
            _apply_stealth_camera(stealth_backup, stealth_cam, hide_passepartout=hide_pp)

            crop_to_camera = (not capture_full) and getattr(
                scene_for_stealth, 'tlx_window_stealth_crop_to_camera', True)
            if crop_to_camera:
                r3d = stealth_backup['space'].region_3d
                bounds = _compute_camera_frame_bounds(
                    scene_for_stealth, region, r3d, stealth_cam)
                if bounds:
                    stealth_backup['crop_bounds']  = bounds
                    stealth_backup['crop_region_w'] = region.width
                    stealth_backup['crop_region_h'] = region.height

    blueprint_hidden = []
    auto_hide_bp = bool(scene_for_stealth and
                         getattr(scene_for_stealth, 'tlx_auto_hide_blueprints', False))
    if auto_hide_bp and area.type == 'VIEW_3D':
        if stealth_cam and stealth_enabled:
            projection = 'ORTHO' if getattr(stealth_cam.data, 'type', 'PERSP') == 'ORTHO' else 'PERSP'
        else:
            r3d_for_proj = area.spaces.active.region_3d
            if r3d_for_proj.view_perspective == 'CAMERA':
                active_cam = area.spaces.active.camera
                cam_type = getattr(active_cam.data, 'type', 'PERSP') if active_cam else 'PERSP'
                projection = 'ORTHO' if cam_type == 'ORTHO' else 'PERSP'
            else:
                projection = 'ORTHO' if r3d_for_proj.view_perspective == 'ORTHO' else 'PERSP'
        blueprint_hidden = utils.apply_blueprint_auto_hide(context, projection)

    collection_backup = None
    hide_coll_enabled = bool(scene_for_stealth and
                              getattr(scene_for_stealth, 'tlx_hide_collection_enabled', False))
    if hide_coll_enabled:
        target_collection = getattr(scene_for_stealth, 'tlx_hide_collection', None)
        collection_backup = utils.apply_collection_isolation(
            target_collection, visibility_attr='hide_viewport')

    if stealth_backup or blueprint_hidden or collection_backup:
        # Every state change above only TAGS the area for redraw — it
        # doesn't happen synchronously. Without forcing one here,
        # screen.screenshot_area can grab a stale framebuffer from
        # BEFORE these changes (this is what caused "often doesn't
        # capture the camera's actual view" with Stealth Camera before
        # this fix — same underlying issue applies to blueprint/
        # collection hiding, so one redraw covers all three).
        _force_viewport_redraw(window, area, region)

    try:
        # BUG 2b (v2.5.5): use compression=1 for temp file
        img_settings     = bpy.context.scene.render.image_settings
        prev_format      = img_settings.file_format
        prev_compression = getattr(img_settings, 'compression', 15)

        img_settings.file_format = 'PNG'
        if hasattr(img_settings, 'compression'):
            img_settings.compression = 1

        try:
            def do_screenshot():
                return _do_screenshot(context, window, screen, area, region,
                                       temp_path, capture_full)
            success = ErrorRecovery.retry(do_screenshot, max_attempts=2, delay=0.05,
                                           exceptions=(CaptureError,))
            if not success:
                raise CaptureError("Screenshot failed after retries",
                                    user_message="Failed to capture screenshot")
        finally:
            img_settings.file_format = prev_format
            if hasattr(img_settings, 'compression'):
                img_settings.compression = prev_compression

        # Crop while the file is still the temp file, BEFORE it's renamed
        # to its final numbered path below, and before idle-diff compares
        # it — so diff comparisons are always cropped-vs-cropped.
        if stealth_backup and stealth_backup.get('crop_bounds'):
            _crop_to_camera_frame(
                temp_path, stealth_backup['crop_bounds'],
                stealth_backup['crop_region_w'], stealth_backup['crop_region_h'])

    finally:
        if backup:
            _restore_overlays(backup)
        if stealth_backup:
            _restore_view_state(stealth_backup)
        if blueprint_hidden:
            utils.restore_blueprint_auto_hide(blueprint_hidden)
        if collection_backup:
            utils.restore_collection_isolation(collection_backup)

    # Idle / diff detection
    use_diff = (prefs.window_idle_diff if prefs else False) and not force_save
    if use_diff:
        last_image  = mgr.last_window_image
        should_save = _check_image_changed(temp_path, last_image, prefs)
        if not should_save:
            logger.debug("⊗ Capture skipped: no change detected")
            try:
                os.remove(temp_path)
            except OSError:
                pass
            _set_cooldown(prefs, mgr)
            return False

    # Save frame
    counter_before = mgr.counter
    zero_padding   = prefs.zero_padding if prefs else constants.DEFAULT_ZERO_PADDING
    final_path     = utils.generate_filename(
        base_dir, "TLX_WINDOW", counter_before, 'png', zero_padding
    )

    try:
        os.replace(temp_path, final_path)
    except OSError:
        try:
            import shutil
            shutil.copy2(temp_path, final_path)
            os.remove(temp_path)
        except Exception as e2:
            raise CaptureError(f"Failed to save file: {e2}",
                                user_message="Failed to save screenshot file")

    if not os.path.exists(final_path):
        raise CaptureError("File was not saved",
                            user_message="Screenshot file was not created")

    mgr.last_window_image = final_path

    try:
        new_counter = mgr.mark_captured()   # OPTIMIZED: returns new counter value
        if new_counter != counter_before + 1:
            raise StateError(
                f"Counter increment failed: {counter_before} → {new_counter}",
                user_message="Frame counter sync error"
            )
    except Exception as e:
        raise StateError(f"Failed to update counter: {e}",
                          user_message="Failed to update frame counter")

    try:
        progress_module.get_recording_progress().add_frame()
    except Exception as e:
        logger.warning(f"add_frame() failed (non-fatal): {e}")

    _set_cooldown(prefs, mgr)
    logger.info(f"✓ Window frame {counter_before} saved → counter={mgr.counter}")
    return True


# ============================================================================
# Async Capture
# ============================================================================

@handle_errors(default_return=False, raise_on_error=False,
               user_message="Async capture scheduling failed")
def capture_window_async(context, base_dir: str, force_save: bool = False) -> bool:
    """Schedule an async window capture via bpy.app.timers."""
    prefs = utils.get_addon_preferences()
    mgr   = StateManager()

    if not (prefs and prefs.window_async_capture):
        return capture_window(context, base_dir, force_save)

    can_schedule, reason = _rate_limiter.can_schedule()
    if not can_schedule:
        if _rate_limiter.stats['total_requests'] % 50 == 0:
            _log_rate_limiter_stats()
        report_error(ResourceError(
            f"Async capture rate limited: {reason}",
            user_message="Capture rate limit exceeded"
        ))
        if force_save:
            return capture_window(context, base_dir, force_save)
        return False

    if mgr.is_window_async_pending():
        logger.debug("⊗ Async already pending, skipping")
        return False

    # BUG 2a (v2.5.5): minimum 50 ms delay
    raw_delay_ms = getattr(prefs, 'window_async_delay_ms', 50) if prefs else 50
    delay        = max(0.05, float(raw_delay_ms) / 1000.0)

    if not mgr.schedule_window_async(delay):
        return capture_window(context, base_dir, force_save)

    _rate_limiter.mark_scheduled()

    _base_dir   = base_dir
    _force_save = force_save

    def _capture_callback():
        """Timer callback — main thread only."""
        mgr_cb  = StateManager()
        success = False

        mgr_cb.start_window_capture()
        try:
            # BUG 3 (v2.5.5): finish_window_capture() in finally
            with ErrorContext("Async capture callback", recoverable=True) as ctx_err:
                saved = capture_window(bpy.context, _base_dir, _force_save)
                if saved:
                    success = True
                    logger.info("✓ Async capture success")
                else:
                    logger.debug("⊗ Async: no save (idle or suppressed)")

            if ctx_err.error:
                logger.error(f"Async capture error: {ctx_err.error}")
                report_error(ctx_err.error)

        finally:
            mgr_cb.finish_window_capture(success=success)

        _rate_limiter.mark_completed(success=success)

        if _rate_limiter.should_cleanup():
            _cleanup_stale_timers()

        return None

    try:
        bpy.app.timers.register(_capture_callback, first_interval=delay)
        logger.debug(f"→ Async scheduled (delay={delay*1000:.0f}ms)")
        return True
    except Exception as e:
        logger.error(f"Timer registration failed: {e}, falling back to sync")
        mgr.cancel_window_async()
        _rate_limiter.mark_completed(success=False)
        return capture_window(context, base_dir, force_save)


# ============================================================================
# Helpers
# ============================================================================

def _do_screenshot(context, window, screen, area, region,
                   output_path: str, capture_full: bool) -> bool:
    # OPTIMIZED: build override dict once
    override = {'window': window, 'screen': screen, 'area': area, 'region': region}
    success  = False
    with context.temp_override(**override):
        try:
            if capture_full:
                bpy.ops.screen.screenshot(filepath=output_path)
            else:
                bpy.ops.screen.screenshot_area(filepath=output_path)
            success = True
        except RuntimeError as e:
            logger.warning(f"screenshot_area failed: {e}, trying screenshot")
            try:
                bpy.ops.screen.screenshot(filepath=output_path)
                success = True
            except RuntimeError as e2:
                raise CaptureError(
                    f"Screenshot failed: {e2}",
                    user_message="Failed to capture screenshot"
                )
    return success


def _backup_overlays(area) -> Optional[tuple]:
    for space in area.spaces:
        if space.type != 'VIEW_3D':
            continue
        overlay       = getattr(space, 'overlay', None)
        show_overlays = getattr(overlay, 'show_overlays', None) if overlay else None
        show_gizmo    = getattr(space, 'show_gizmo', None)
        backup        = (space, show_gizmo, show_overlays)
        try:
            if show_gizmo is not None:
                space.show_gizmo = False
            if overlay and show_overlays is not None:
                overlay.show_overlays = False
        except (AttributeError, RuntimeError):
            pass
        return backup
    return None


def _restore_overlays(backup: tuple):
    if not backup:
        return
    space, show_gizmo, show_overlays = backup
    try:
        if show_gizmo is not None:
            space.show_gizmo = show_gizmo
        overlay = getattr(space, 'overlay', None)
        if overlay and show_overlays is not None:
            overlay.show_overlays = show_overlays
    except (AttributeError, RuntimeError, ReferenceError):
        pass


def _force_viewport_redraw(window, area, region) -> None:
    """Force the area to actually redraw NOW, synchronously, rather than
    relying on Blender's normal (deferred) redraw scheduling. Needed
    right after switching to the stealth camera — without this, the
    screenshot can grab a framebuffer from before the camera switch."""
    try:
        area.tag_redraw()
        with bpy.context.temp_override(window=window, area=area, region=region):
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
    except Exception as e:
        logger.warning(f"Stealth-camera forced redraw failed: {e}")


def _compute_camera_frame_bounds(scene, region, r3d, camera_obj) -> Optional[tuple]:
    """Project the stealth camera's view-frame corners into the region's
    2D pixel space, returning (xmin, ymin, xmax, ymax). This is the exact
    rectangle within the region that the camera actually sees — the rest
    is the black/dark margin caused by the camera's aspect ratio not
    matching the viewport's, which _crop_to_camera_frame() then removes."""
    try:
        from bpy_extras.view3d_utils import location_3d_to_region_2d
        cam_data = camera_obj.data
        frame    = cam_data.view_frame(scene=scene)  # 4 corners, camera-local space
        mat      = camera_obj.matrix_world
        coords_2d = []
        for corner in frame:
            world_co = mat @ corner
            co2d = location_3d_to_region_2d(region, r3d, world_co)
            if co2d is None:
                return None  # a corner is behind the view; bail out safely
            coords_2d.append(co2d)
        xs = [c.x for c in coords_2d]
        ys = [c.y for c in coords_2d]
        return (min(xs), min(ys), max(xs), max(ys))
    except Exception as e:
        logger.warning(f"Failed to compute camera frame bounds: {e}")
        return None


def _crop_to_camera_frame(filepath: str, bounds: tuple,
                           region_w: int, region_h: int) -> None:
    """Crop the PNG at filepath (in place) down to `bounds` — pixel
    coordinates computed by _compute_camera_frame_bounds() against the
    region's logical size. Scaled against the image's actual saved pixel
    size in case that differs (HiDPI/system pixel-size scaling)."""
    if not bounds or region_w <= 0 or region_h <= 0:
        return
    try:
        import numpy as np
    except ImportError:
        logger.warning("numpy unavailable — skipping stealth-camera crop")
        return

    img = None
    new_img = None
    try:
        img = bpy.data.images.load(filepath)
        iw, ih = img.size
        if iw <= 0 or ih <= 0:
            return

        scale_x = iw / region_w
        scale_y = ih / region_h
        xmin, ymin, xmax, ymax = bounds
        xmin = max(0, int(round(xmin * scale_x)))
        ymin = max(0, int(round(ymin * scale_y)))
        xmax = min(iw, int(round(xmax * scale_x)))
        ymax = min(ih, int(round(ymax * scale_y)))

        if (xmax - xmin) < 4 or (ymax - ymin) < 4:
            logger.warning("Stealth-camera crop bounds too small — "
                            "keeping the uncropped frame")
            return

        channels = img.channels or 4
        pixels = np.empty(iw * ih * channels, dtype=np.float32)
        img.pixels.foreach_get(pixels)
        pixels = pixels.reshape(ih, iw, channels)

        # Blender image row 0 = bottom of the image; region Y=0 is also
        # the bottom of the region (both follow OpenGL convention), so
        # the Y bounds line up directly — no flip needed.
        cropped = pixels[ymin:ymax, xmin:xmax, :]
        new_h, new_w = cropped.shape[0], cropped.shape[1]

        new_img = bpy.data.images.new(
            "TLX_STEALTH_CROP_TEMP", width=new_w, height=new_h,
            alpha=(channels == 4))
        new_img.pixels.foreach_set(np.ascontiguousarray(cropped).reshape(-1))
        new_img.filepath_raw = filepath
        new_img.file_format  = 'PNG'
        new_img.save()
    except Exception as e:
        logger.warning(f"Stealth-camera crop failed, keeping uncropped frame: {e}")
    finally:
        if new_img is not None:
            try:
                bpy.data.images.remove(new_img)
            except Exception:
                pass
        if img is not None:
            try:
                bpy.data.images.remove(img)
            except Exception:
                pass


def _backup_view_state(area) -> Optional[dict]:
    """Snapshot the active VIEW_3D space's camera/perspective state, so a
    stealth-camera capture (see scene.tlx_window_stealth_camera_enabled) can force
    the view to a fixed camera for one screenshot and then put things
    back exactly as the user had them."""
    for space in area.spaces:
        if space.type != 'VIEW_3D':
            continue
        r3d = space.region_3d
        if not r3d:
            return None
        return {
            'space':            space,
            'view_perspective': r3d.view_perspective,
            'view_matrix':      r3d.view_matrix.copy(),
            'view_location':    r3d.view_location.copy(),
            'view_rotation':    r3d.view_rotation.copy(),
            'view_distance':    r3d.view_distance,
            'space_camera':     space.camera,
        }
    return None


def _apply_stealth_camera(backup: dict, camera_obj, hide_passepartout: bool = True) -> None:
    try:
        space = backup['space']
        space.camera = camera_obj
        space.region_3d.view_perspective = 'CAMERA'

        # Passepartout is the darkened area Blender draws outside a
        # camera's frame while looking through it — it lives on the
        # Camera DATA-block (shared if the same camera data is used by
        # multiple objects), not the space, so back it up/restore it
        # alongside the view state rather than as a separate mechanism.
        cam_data = getattr(camera_obj, 'data', None)
        if hide_passepartout and cam_data is not None and hasattr(cam_data, 'show_passepartout'):
            backup['cam_data']             = cam_data
            backup['prev_show_passepartout'] = cam_data.show_passepartout
            cam_data.show_passepartout = False
    except (AttributeError, RuntimeError, ReferenceError) as e:
        logger.warning(f"Stealth-camera apply failed: {e}")


def _restore_view_state(backup: Optional[dict]) -> None:
    if not backup:
        return
    try:
        space = backup['space']
        r3d   = space.region_3d
        space.camera         = backup['space_camera']
        r3d.view_perspective = backup['view_perspective']
        r3d.view_matrix      = backup['view_matrix']
        r3d.view_location    = backup['view_location']
        r3d.view_rotation    = backup['view_rotation']
        r3d.view_distance    = backup['view_distance']

        cam_data = backup.get('cam_data')
        if cam_data is not None and 'prev_show_passepartout' in backup:
            cam_data.show_passepartout = backup['prev_show_passepartout']
    except (AttributeError, RuntimeError, ReferenceError) as e:
        logger.warning(f"Stealth-camera view restore failed: {e}")


def _check_image_changed(current_path: str, previous_path: str, prefs) -> bool:
    if not previous_path or not os.path.isfile(previous_path):
        return True
    # OPTIMIZED: prefs already fetched by caller
    threshold = getattr(prefs, 'window_idle_threshold',
                        constants.DEFAULT_IDLE_THRESHOLD) if prefs else constants.DEFAULT_IDLE_THRESHOLD
    downscale = getattr(prefs, 'window_idle_downscale',
                        constants.DEFAULT_DOWNSCALE_SIZE) if prefs else constants.DEFAULT_DOWNSCALE_SIZE
    try:
        diff    = utils.compare_images(current_path, previous_path, downscale=downscale)
        changed = diff >= threshold
        logger.debug(f"Image diff: {diff:.4f}  threshold: {threshold:.4f}  changed: {changed}")
        return changed
    except Exception as e:
        logger.warning(f"Image diff failed: {e}, assuming changed")
        return True


def _set_cooldown(prefs, mgr=None):
    """OPTIMIZED: accept pre-fetched prefs and mgr to avoid re-lookup."""
    if not prefs:
        return
    suppress_ms = getattr(prefs, 'perf_depsgraph_suppress_ms', constants.DEFAULT_SUPPRESS_MS)
    if mgr is None:
        mgr = StateManager()
    mgr.set_suppression(suppress_ms)


def _cleanup_stale_timers():
    try:
        _ = len(bpy.app.timers)
    except Exception as e:
        logger.debug(f"Timer cleanup: {e}")


def _log_rate_limiter_stats():
    stats = _rate_limiter.get_stats()
    logger.info(
        f"Rate Limiter: pending={stats['pending']}/{_rate_limiter.max_pending}, "
        f"rate={stats['current_rate']:.1f}/{_rate_limiter.max_rate} fps, "
        f"drop={stats['drop_rate']:.1f}%"
    )


# ============================================================================
# Public API
# ============================================================================

def is_async_pending() -> bool:
    return StateManager().is_window_async_pending()


def get_async_debug_info() -> dict:
    mgr = StateManager()
    return {
        'pending':           mgr.is_window_async_pending(),
        'last_window_image': mgr.last_window_image,
        'rate_limiter':      _rate_limiter.get_stats(),
    }


def cancel_async_capture() -> bool:
    cancelled = StateManager().cancel_window_async()
    if cancelled:
        logger.info("✓ Async capture cancelled")
        _rate_limiter.mark_completed(success=False)
    return cancelled


def reset_rate_limiter():
    _rate_limiter.reset_stats()


def get_rate_limiter_stats() -> dict:
    return _rate_limiter.get_stats()


def print_rate_limiter_stats():
    stats = _rate_limiter.get_stats()
    print("\n" + "=" * 70)
    print("ASYNC WINDOW CAPTURE — RATE LIMITER STATISTICS")
    print("=" * 70)
    for k, v in stats.items():
        print(f"  {k:<20} {v}")
    print("=" * 70 + "\n")


# ============================================================================
# Registration
# ============================================================================

def register():
    logger.info("Window capture registered (v2.6.0: reduced per-capture overhead)")
    StateManager().cancel_window_async()
    _rate_limiter.reset_stats()


def unregister():
    logger.info("Unregistering window capture")
    print_rate_limiter_stats()
    cancel_async_capture()
