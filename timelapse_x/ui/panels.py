"""
Main UI panel for Timelapse X addon.

Fix (v2.6.5):
  _validate_state_consistency() and _draw_debug_panel() previously used
  `mgr.timer` (wm event timer) to decide if the recording state is stuck.
  Since v2.6.5 recording uses bpy.app.timers (mgr.timer is always None),
  this made EVERY active recording appear stuck — showing the red warning
  banner and "Force Reset State" button permanently.

  Fixed by replacing `not mgr.timer` with a helper _is_capture_timer_running()
  that checks bpy.app.timers.is_registered(_tlx_capture_tick).

Optimizations (v2.6.0, retained):
  - _count_session_stats(): 2-second TTL cache to avoid os.walk on every redraw.
  - draw(): StateManager instantiated once per draw call.
  - _draw_session_info(): uses cached stats.
  - _draw_video_tools(): local variable for collapsed flag.
"""

import bpy
import os
import time
import logging
from datetime import datetime
from bpy.types import Panel

from .. import constants
from .. import utils
from ..state_manager import StateManager
from .. import progress

logger = logging.getLogger(__name__)


# ========================================================================================
# Helper — check the actual capture timer (v2.6.5)
# ========================================================================================

def _is_capture_timer_running() -> bool:
    """
    Return True if the persistent bpy.app.timers capture callback is active.
    Used for stuck-state detection instead of the legacy mgr.timer (wm timer)
    which is always None since v2.6.5.
    """
    try:
        from ..operators.recording import _tlx_capture_tick
        return bpy.app.timers.is_registered(_tlx_capture_tick)
    except Exception:
        return False


# ========================================================================================
# Session stats cache
# ========================================================================================

_stats_cache: dict      = {}
_STATS_CACHE_TTL: float = 2.0


def _cached_session_stats(directory: str):
    now   = time.monotonic()
    entry = _stats_cache.get(directory)
    if entry is not None:
        cached_time, result = entry
        if (now - cached_time) < _STATS_CACHE_TTL:
            return result
    result = utils.count_session_stats(directory)
    _stats_cache[directory] = (now, result)
    return result


def _invalidate_stats_cache(directory: str = None):
    if directory is None:
        _stats_cache.clear()
    else:
        _stats_cache.pop(directory, None)


# ========================================================================================
# Panel
# ========================================================================================

class TLX_PT_panel(Panel):
    bl_label       = 'Timelapse X'
    bl_idname      = 'TLX_PT_panel'
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = 'Timelapse X'

    def draw(self, context):
        layout = self.layout
        scene  = context.scene

        prefs = utils.get_addon_preferences()
        mgr   = StateManager()

        self._validate_state_consistency(layout, scene, mgr)
        self._draw_output_folder(layout, prefs)
        self._draw_capture_mode(layout, scene)
        self._draw_interval_settings(layout, scene, prefs)

        if scene.tlx_capture_mode == 'CAMERA_LIST':
            self._draw_camera_list(layout, scene, prefs)

        self._draw_recording_controls(layout, scene, mgr)

        if mgr.recording:
            layout.separator()
            self._draw_recording_progress(layout, context, mgr)

        self._draw_session_info(layout, prefs, mgr)
        self._draw_video_tools(layout, scene, prefs, mgr)
        self._draw_clean_window(layout, context, prefs)

        if prefs:
            self._draw_quick_settings(layout, context, scene, prefs)

        if prefs and getattr(prefs, 'show_debug_panel', False):
            self._draw_debug_panel(layout, context, mgr, scene)

    # -----------------------------------------------------------------------
    # Stuck-state detection  (FIX v2.6.5)
    # -----------------------------------------------------------------------

    def _validate_state_consistency(self, layout, scene, mgr):
        """
        Show a warning banner when the recording flag is set but no
        actual capture activity is running.

        v2.6.5 FIX: check bpy.app.timers via _is_capture_timer_running()
        instead of mgr.timer (which is always None since v2.6.5).
        """
        if not mgr.recording:
            return

        timer_ok   = _is_capture_timer_running()
        session_ok = bool(mgr.session_dir)

        # Only truly stuck when the recording flag is ON but neither the
        # capture timer nor the session directory exists.
        is_stuck = not timer_ok or not session_ok
        if not is_stuck:
            return

        box = layout.box()
        box.alert = True
        col = box.column(align=True)
        col.label(text="⚠ STUCK RECORDING STATE DETECTED", icon='ERROR')
        col.label(text="Recording flag is ON but no session active")
        col.separator()
        col.label(text=f"• recording={mgr.recording}")
        col.label(text=f"• capture_timer={timer_ok}")
        col.label(text=f"• session_dir={session_ok}")
        col.label(text=f"• scene.tlx_is_recording={scene.tlx_is_recording}")
        col.separator()
        col.operator('tlx.force_reset_state', text='Force Reset State',
                     icon='FILE_REFRESH')
        layout.separator()

    # -----------------------------------------------------------------------
    # Output folder
    # -----------------------------------------------------------------------

    def _draw_output_folder(self, layout, prefs):
        if not prefs:
            box = layout.box()
            box.alert = True
            box.label(text="Preferences not found!", icon='ERROR')
            return
        box = layout.box()
        box.label(text='Output Images Folder', icon='FILE')
        box.prop(prefs, 'output_dir', text='')
        row = box.row(align=True)
        row.operator('tlx.open_images_folder', text='Open',  icon='FILE')
        row.operator('tlx.clean_empty_folders', text='Clean', icon='BRUSH_DATA')

    def _draw_capture_mode(self, layout, scene):
        box = layout.box()
        box.row().prop(scene, 'tlx_capture_mode', expand=True)

    def _draw_interval_settings(self, layout, scene, prefs):
        box = layout.box()
        row = box.row(align=True)
        row.prop(scene, 'tlx_capture_interval', text='Interval (s)')
        row.operator('tlx.update_interval', text='', icon='CHECKMARK')
        row = box.row(align=True)
        for value in (0.5, 1.0, 2.0, 5.0):
            op       = row.operator('tlx.set_interval', text=f'{value:.1f}s')
            op.value = value

    def _draw_camera_list(self, layout, scene, prefs):
        box    = layout.box()
        header = box.row(align=True)
        icon   = 'TRIA_RIGHT' if scene.tlx_ui_cam_editor_collapsed else 'TRIA_DOWN'
        header.prop(scene, 'tlx_ui_cam_editor_collapsed',
                    text='', icon=icon, emboss=False)
        header.label(text="Camera Settings")

        if not scene.tlx_ui_cam_editor_collapsed:
            row = box.row()
            row.template_list('TLX_UL_cameras', '', scene, 'tlx_cameras',
                               scene, 'tlx_cameras_index', rows=4)
            col = row.column(align=True)
            col.enabled = not scene.tlx_is_recording
            col.operator('tlx.cam_add',       text='', icon='ADD')
            col.operator('tlx.cam_remove',    text='', icon='REMOVE')
            col.separator()
            col.operator('tlx.cam_move_up',   text='', icon='TRIA_UP')
            col.operator('tlx.cam_move_down', text='', icon='TRIA_DOWN')

            if 0 <= scene.tlx_cameras_index < len(scene.tlx_cameras):
                self._draw_camera_editor(
                    box, scene.tlx_cameras[scene.tlx_cameras_index])

            box.separator()
            box.operator('tlx.apply_speed_preset',
                         text='Apply Speed Preset', icon='PREFERENCES')

    def _draw_camera_editor(self, layout, camera_item):
        if not camera_item or not camera_item.camera:
            return
        editor = layout.box()
        editor.label(text=f"Camera: {camera_item.camera.name}",
                     icon='CAMERA_DATA')

        editor.prop(camera_item, 'use_interval_override')
        col = editor.column(align=True)
        col.enabled = camera_item.use_interval_override
        col.prop(camera_item, 'interval_override', slider=True)

        editor.separator()
        editor.prop(camera_item, 'use_shading_override')
        col = editor.column(align=True)
        col.enabled = camera_item.use_shading_override
        col.prop(camera_item, 'shading_type')
        col.prop(camera_item, 'xray')
        col.prop(camera_item, 'disable_shadows')

        editor.separator()
        editor.prop(camera_item, 'use_image_override')
        col = editor.column(align=True)
        col.enabled = camera_item.use_image_override
        col.prop(camera_item, 'image_format')
        if camera_item.image_format == 'PNG':
            col.prop(camera_item, 'png_rgba')
        else:
            col.prop(camera_item, 'jpeg_quality')

        editor.separator()
        editor.prop(camera_item, 'use_turntable', icon='ORIENTATION_GIMBAL')
        col = editor.column(align=True)
        col.enabled = camera_item.use_turntable
        col.prop(camera_item, 'turntable_target')
        row = col.row(align=True)
        row.prop(camera_item, 'turntable_degrees')
        row.prop(camera_item, 'turntable_axis', expand=True)
        if camera_item.use_turntable and not camera_item.turntable_target:
            col.label(text="Set a target — orbit needs something to "
                           "rotate around.", icon='ERROR')
        elif camera_item.use_turntable:
            steps = abs(360.0 / camera_item.turntable_degrees) \
                    if camera_item.turntable_degrees else 0
            col.label(text=f"Full 360° orbit ≈ {steps:.0f} captures "
                           "of this camera.", icon='INFO')

    def _draw_recording_controls(self, layout, scene, mgr):
        box = layout.box()
        row = box.row(align=True)
        if not scene.tlx_is_recording:
            op       = row.operator('tlx.record',
                                    text='Start Recording', icon='PLAY')
            op.start = True
            op.interval = 0.0
            op.mode  = 'DEFAULT'
        else:
            paused     = mgr.paused
            pause_text = 'Resume' if paused else 'Pause'
            pause_icon = 'PLAY'   if paused else 'PAUSE'
            row.operator('tlx.pause_resume',
                         text=pause_text, icon=pause_icon)
            op       = row.operator('tlx.record',
                                    text='Stop', icon='SNAP_FACE')
            op.start = False

    def _draw_recording_progress(self, layout, context, mgr):
        box = layout.box()
        box.label(text="Recording Progress", icon='TIME')

        rec_progress = progress.get_recording_progress()
        col          = box.column(align=True)

        if rec_progress.frame_count > 0:
            row = col.row()
            row.label(text="", icon='IMAGE_DATA')
            row.label(text="Frames:")
            row.label(text=str(rec_progress.frame_count))

            elapsed = time.time() - rec_progress.start_time
            if   elapsed < 60:   dur = f"{elapsed:.0f}s"
            elif elapsed < 3600: dur = f"{elapsed/60:.1f}m"
            else:                dur = f"{elapsed/3600:.1f}h"

            row = col.row()
            row.label(text="", icon='TIME')
            row.label(text="Duration:")
            row.label(text=dur)

            if rec_progress.session_size_bytes > 0:
                row = col.row()
                row.label(text="", icon='DISK_DRIVE')
                row.label(text="Size:")
                row.label(
                    text=f"{rec_progress.session_size_bytes/(1024*1024):.1f} MB")

            if elapsed > 10:
                row = col.row()
                row.label(text="", icon='CURVE_DATA')
                row.label(text="Rate:")
                row.label(text=f"{rec_progress.frame_count/elapsed:.2f} fps")

            session_dir = mgr.session_dir
            if session_dir and os.path.isdir(session_dir):
                col.separator()
                col.operator('tlx.open_session_folder',
                              text='Open Session Folder',
                              icon='FOLDER_REDIRECT')
        else:
            col.label(text="Waiting for first capture...", icon='INFO')

        col.separator()
        status_row = col.row()
        if mgr.paused:
            status_row.label(text="STATUS: PAUSED", icon='PAUSE')
        else:
            mode_name = mgr.capture_mode.replace('_', ' ').title()
            status_row.label(
                text=f"STATUS: RECORDING ({mode_name})", icon='REC')

    def _draw_session_info(self, layout, prefs, mgr):
        box = layout.box()
        box.label(text="Session Info", icon='INFO')
        row = box.row()
        row.label(text=f'Frames: {mgr.counter}')

        if mgr.recording:
            status = "PAUSED" if mgr.paused else "RECORDING"
            row.label(text=status,
                      icon='REC' if not mgr.paused else 'PAUSE')

        last_ct = mgr.last_capture_time
        if last_ct:
            try:
                time_str = datetime.fromtimestamp(last_ct).strftime("%H:%M:%S")
                box.label(text=f'Last capture: {time_str}')
            except Exception:
                pass

        session_dir = mgr.session_dir
        if session_dir and os.path.isdir(session_dir):
            try:
                count, size = _cached_session_stats(session_dir)
                if count > 0:
                    box.label(
                        text=f"Total: {count} images, {self._format_bytes(size)}")
            except Exception:
                pass

    def _draw_video_tools(self, layout, scene, prefs, mgr):
        box       = layout.box()
        header    = box.row(align=True)
        collapsed = prefs.ui_video_tools_collapsed if prefs else False
        icon      = 'TRIA_RIGHT' if collapsed else 'TRIA_DOWN'
        if prefs:
            header.prop(prefs, 'ui_video_tools_collapsed',
                        text='', icon=icon, emboss=False)
        header.label(text="Video Tools", icon='FILE_MOVIE')

        if collapsed:
            return

        col            = box.column(align=True)
        session_dir    = mgr.session_dir
        session_active = bool(session_dir and os.path.isdir(session_dir))

        row = col.row()
        row.enabled = session_active
        row.operator('tlx.compile_session_all',
                     text='Compile Current Session',
                     icon='RENDER_ANIMATION')

        if not session_active:
            col.label(text="No active session", icon='INFO')

        col.separator()
        col.operator('tlx.compile_video',
                     text='Compile from Folder...', icon='FILEBROWSER')

        if prefs:
            col.separator()
            col.label(text="MP4 Output:")
            col.prop(prefs, 'mp4_output_mode', text='')
            if prefs.mp4_output_mode == 'CUSTOM_DIR':
                col.prop(prefs, 'mp4_custom_dir', text='')
            col.operator('tlx.open_mp4_folder',
                         text='Open MP4 Folder', icon='FILE_FOLDER')

    def _draw_clean_window(self, layout, context, prefs):
        if not prefs:
            return
        box      = layout.box()
        header   = box.row(align=True)
        collapsed = prefs.ui_clean_window_collapsed
        icon     = 'TRIA_RIGHT' if collapsed else 'TRIA_DOWN'
        header.prop(prefs, 'ui_clean_window_collapsed',
                    text='', icon=icon, emboss=False)
        header.label(text="Clean Window Tools", icon='WINDOW')

        if collapsed:
            return

        wm = context.window_manager
        try:
            from ..clean_window.operators import find_tracked_windows
            original_w, new_w = find_tracked_windows(wm)
            is_active         = original_w is not None
            has_new_window    = new_w is not None
        except Exception:
            is_active = has_new_window = False

        col = box.column(align=True)
        if has_new_window:
            col.operator('cmw.toggle_clean_window',
                          text='Turn OFF (Close Window)', icon='CANCEL')
            if is_active:
                col.operator('cmw.restore_original_window',
                              text='Restore Original Only', icon='RECOVER_LAST')
        else:
            col.operator('cmw.toggle_clean_window',
                          text='Create Clean Window', icon='WINDOW')

        box_settings = box.box()
        box_settings.label(text="Settings:")
        row = box_settings.row(align=True)
        row.prop(wm, 'cmw_hide_ui',       text='Hide UI')
        row.prop(wm, 'cmw_hide_overlays', text='Overlays')
        row = box_settings.row(align=True)
        row.prop(wm, 'cmw_hide_gizmos',  text='Gizmos')
        row.prop(wm, 'cmw_fullscreen',   text='Fullscreen')
        box_settings.prop(wm, 'cmw_maximize_area')
        box_settings.prop(wm, 'cmw_shading')
        row = box_settings.row()
        row.enabled = not wm.cmw_hide_overlays
        row.prop(wm, 'cmw_deep_clean')

        box.separator()
        box.label(text="Wireframe Display:", icon='MOD_WIREFRAME')
        col = box.column(align=True)
        col.operator('cmw.enable_wireframe',  icon='CHECKBOX_HLT')
        col.operator('cmw.disable_wireframe', icon='CHECKBOX_DEHLT')
        col.operator('cmw.toggle_wireframe',  icon='MOD_WIREFRAME')

        if context.selected_objects:
            enabled_count = sum(
                1 for o in context.selected_objects if o.show_wire)
            box.label(text=f"Selected: {len(context.selected_objects)} | "
                           f"Wireframe: {enabled_count}")

    def _draw_hotkey_widget(self, layout, context):
        """Inline, rebindable hotkey widget for tlx.hotkey_capture."""
        try:
            from ..operators import hotkey as hotkey_ops
        except ImportError:
            return

        km, kmi = hotkey_ops.get_keymap_item()
        if not (km and kmi):
            layout.label(text="Hotkey unavailable", icon='ERROR')
            return

        kc = context.window_manager.keyconfigs.user
        layout.context_pointer_set('keymap', km)
        try:
            from rna_keymap_ui import draw_kmi
            draw_kmi([], kc, km, kmi, layout, 0)
        except Exception as e:
            logger.debug(f"draw_kmi unavailable, using fallback: {e}")
            row = layout.row(align=True)
            row.prop(kmi, 'type', text='', full_event=True)
            row.prop(kmi, 'active', text='')

    def _draw_stop_hotkey_widget(self, layout, context):
        """Inline, rebindable widget for tlx.emergency_stop_hotkey."""
        try:
            from ..operators import stop_hotkey
        except ImportError:
            return

        km, kmi = stop_hotkey.get_keymap_item()
        if not (km and kmi):
            layout.label(text="Hotkey unavailable", icon='ERROR')
            return

        kc = context.window_manager.keyconfigs.user
        layout.context_pointer_set('keymap', km)
        try:
            from rna_keymap_ui import draw_kmi
            draw_kmi([], kc, km, kmi, layout, 0)
        except Exception as e:
            logger.debug(f"draw_kmi unavailable, using fallback: {e}")
            row = layout.row(align=True)
            row.prop(kmi, 'type', text='', full_event=True)
            row.prop(kmi, 'active', text='')

    def _draw_quick_settings(self, layout, context, scene, prefs):
        box       = layout.box()
        header    = box.row(align=True)
        collapsed = prefs.ui_quick_settings_collapsed
        icon      = 'TRIA_RIGHT' if collapsed else 'TRIA_DOWN'
        header.prop(prefs, 'ui_quick_settings_collapsed',
                    text='', icon=icon, emboss=False)
        header.label(text="Quick Settings", icon='SETTINGS')

        if collapsed:
            return

        mode = scene.tlx_capture_mode
        col  = box.column(align=True)
        col.prop(prefs, 'capture_immediate_on_start')
        col.prop(prefs, 'show_recording_header')
        col.prop(prefs, 'perf_depsgraph_suppress_ms')
        box.separator()

        box.label(text="Emergency Stop:", icon='CANCEL')
        col = box.column(align=True)
        col.prop(prefs, 'emergency_stop_enabled')
        if prefs.emergency_stop_enabled:
            sub = box.box()
            self._draw_stop_hotkey_widget(sub, context)
            sub.label(text="Works in any mode — stops recording "
                           "immediately.", icon='INFO')
        box.separator()

        col = box.column(align=True)
        col.prop(scene, 'tlx_auto_hide_blueprints', icon='IMAGE_REFERENCE')
        if scene.tlx_auto_hide_blueprints:
            box.label(text="Per-Empty: Object Data Properties > "
                           "Show In: Perspective / Orthographic.",
                      icon='INFO')
        box.separator()

        col = box.column(align=True)
        col.prop(scene, 'tlx_hide_collection_enabled', icon='OUTLINER_COLLECTION')
        if scene.tlx_hide_collection_enabled:
            sub = box.box()
            sub.prop(scene, 'tlx_hide_collection')
            if not scene.tlx_hide_collection:
                sub.label(text="Pick a collection — nothing is hidden "
                               "until you do.", icon='ERROR')
        box.separator()

        if mode == 'WINDOW':
            box.label(text="Window Capture:", icon='WINDOW')
            col = box.column(align=True)

            row = col.row()
            row.enabled = not prefs.window_capture_on_action_count
            row.prop(prefs, 'window_capture_on_input_only')
            if prefs.window_capture_on_input_only:
                sub = box.box()
                sub.label(text="Capture Hotkey (click key to rebind):",
                          icon='EVENT_F9')
                self._draw_hotkey_widget(sub, context)
                sub.label(text="Only this key triggers a frame — the "
                               "timer stays idle.", icon='INFO')

            row = col.row()
            row.enabled = not prefs.window_capture_on_input_only
            row.prop(prefs, 'window_capture_on_action_count')
            if prefs.window_capture_on_action_count:
                sub = box.box()
                sub.prop(prefs, 'action_count_batch')
                sub.prop(prefs, 'action_count_debounce')
                sub.label(text="Counts completed operators (transform, "
                               "extrude, sculpt stroke...), not raw "
                               "redraws.", icon='INFO')
                sub.label(text="A frame is captured every "
                               f"{prefs.action_count_batch} tracked "
                               "action(s) — the timer stays idle.",
                          icon='INFO')

            col.prop(prefs, 'window_idle_diff')
            if prefs.window_idle_diff:
                row = col.row(align=True)
                row.prop(prefs, 'window_idle_threshold')
                row.prop(prefs, 'window_idle_downscale')
            col.prop(prefs, 'window_capture_scope')
            col.prop(prefs, 'window_stabilize_view')
            if prefs.window_stabilize_view and prefs.window_capture_scope != 'FULL':
                col.label(text="Clean frames, but a brief flicker each "
                               "capture (hover for details).", icon='INFO')

            col.prop(scene, 'tlx_window_stealth_camera_enabled')
            if scene.tlx_window_stealth_camera_enabled:
                sub = box.box()
                sub.prop(scene, 'tlx_window_stealth_camera')
                if not scene.tlx_window_stealth_camera:
                    sub.label(text="Pick a camera — captures fall back "
                                   "to your actual view until you do.",
                              icon='ERROR')
                else:
                    sub.label(text="Viewport snaps to this camera each "
                                   "capture, then returns to your view.",
                              icon='INFO')
                    sub.prop(scene, 'tlx_window_stealth_hide_passepartout')
                    sub.prop(scene, 'tlx_window_stealth_crop_to_camera')

            box.separator()
            box.label(text="Viewport Lock:", icon='LOCKED')
            col = box.column(align=True)
            col.prop(prefs, 'lock_shading')
            if prefs.lock_shading:
                sub = col.column(align=True)
                sub.prop(prefs, 'shading_type')
                sub.prop(prefs, 'xray')
                sub.prop(prefs, 'disable_shadows')
        else:
            box.label(text="Camera List:", icon='CAMERA_DATA')
            col = box.column(align=True)
            col.prop(prefs, 'idle_detection')
            col.prop(prefs, 'camera_round_robin')
            col.prop(prefs, 'camera_max_per_tick')
            col.prop(prefs, 'camera_lock_interface')
            col.prop(prefs, 'camera_low_overhead')
            box.separator()

            col = box.column(align=True)
            col.prop(prefs, 'camera_capture_on_action_count')
            if prefs.camera_capture_on_action_count:
                sub = box.box()
                sub.prop(prefs, 'action_count_batch')
                sub.prop(prefs, 'action_count_debounce')
                sub.label(text="Counts completed operators (transform, "
                               "extrude, sculpt stroke...), not raw "
                               "redraws.", icon='INFO')
                sub.label(text="Checks which cameras are due every "
                               f"{prefs.action_count_batch} tracked "
                               "action(s), instead of on a timer. "
                               "Per-camera intervals still apply.",
                          icon='INFO')
            box.separator()

            render_box = box.box()
            render_box.label(text="SOLID Render Settings:",
                             icon='SHADING_SOLID')
            col = render_box.column(align=True)
            col.prop(prefs, 'camera_disable_shadows',
                     text="Disable Shadows / Flat Shading")
            if prefs.camera_disable_shadows:
                info = render_box.box()
                info.scale_y = 0.75
                info.label(text="Renders with flat uniform lighting.",
                           icon='INFO')
                info.label(text="Uncheck to keep directional face shading.")
            box.separator()

            wf_box = box.box()
            wf_box.label(text="Wireframe Rendering:", icon='MOD_WIREFRAME')
            col = wf_box.column(align=True)
            col.label(text="Line Settings:")
            row = col.row(align=True)
            row.prop(prefs, 'wireframe_color', text="")
            row.prop(prefs, 'wireframe_thickness', text="Thickness")
            col.separator()
            col.label(text="Background Settings:")
            col.prop(prefs, 'wireframe_transparent_bg',
                     text="Transparent Background")
            if not prefs.wireframe_transparent_bg:
                sub = col.column(align=True)
                row = sub.row(align=True)
                row.prop(prefs, 'wireframe_bg_color', text="")
                row.prop(prefs, 'wireframe_bg_strength',
                         text="Brightness", slider=True)
            col.separator()
            col.label(text="Object Color Settings:")
            col.prop(prefs, 'wireframe_use_object_colors',
                     text="Use Object Viewport Colors")
            if not prefs.wireframe_use_object_colors:
                row = col.row(align=True)
                row.label(text="Default Color:")
                row.prop(prefs, 'wireframe_default_object_color', text="")
            col.separator()
            col.label(text="Render Settings:")
            col.prop(prefs, 'wireframe_disable_shadows',
                     text="Disable Shadows (Wireframe only)")
            col.prop(prefs, 'wireframe_render_engine', text="Engine")

    # -----------------------------------------------------------------------
    # Debug panel  (FIX v2.6.5: uses _is_capture_timer_running())
    # -----------------------------------------------------------------------

    def _draw_debug_panel(self, layout, context, mgr, scene):
        box = layout.box()
        box.label(text="Debug Info", icon='CONSOLE')
        col = box.column(align=True)

        timer_ok = _is_capture_timer_running()
        for label, value in (
            ("Recording:",     str(mgr.recording)),
            ("Scene Flag:",    str(scene.tlx_is_recording)),
            ("App Timer:",     "Running" if timer_ok else "Stopped"),
            ("Session:",       "Yes" if mgr.session_dir else "No"),
        ):
            row = col.row()
            row.label(text=label)
            row.label(text=value)

        col.separator()
        col.operator('tlx.show_debug_state',
                     text='Show Full State', icon='CONSOLE')

        # Stuck = recording flag ON but app timer stopped OR no session
        is_stuck = mgr.recording and (not timer_ok or not mgr.session_dir)
        if is_stuck:
            col.separator()
            col.alert = True
            col.operator('tlx.force_reset_state',
                         text='Force Reset', icon='FILE_REFRESH')

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _format_bytes(self, size_bytes: int) -> str:
        for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} PB"


# ========================================================================================
# Registration
# ========================================================================================

classes = (TLX_PT_panel,)


def register():
    logger.info(
        "Registering UI panels "
        "(v2.6.5: stuck-check uses app timer, not mgr.timer)"
    )
    _invalidate_stats_cache()
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except Exception as e:
            logger.error(f"Failed to register {cls.__name__}: {e}")


def unregister():
    logger.info("Unregistering UI panels")
    _invalidate_stats_cache()
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception as e:
            logger.warning(f"Failed to unregister {cls.__name__}: {e}")
