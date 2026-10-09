"""
Utility operators for Timelapse X addon.

Fix (v2.6.5):
  - force_reset_state: removed wm.event_timer_remove(mgr.timer) entirely
    (no wm timer exists anymore). Calls _stop_capture_timer() to stop the
    persistent app timer. Also removed the modal-dependent timer guard.

Fix (v2.5.1):
  - TLX_OT_reveal_last_capture: uses @property mgr.last_window_image.
  - TLX_OT_show_debug_state: uses public properties.
"""

import bpy
import os
import logging
from bpy.types import Operator

from .. import utils
from ..state_manager import StateManager

logger = logging.getLogger(__name__)


class TLX_OT_open_images_folder(Operator):
    bl_idname      = 'tlx.open_images_folder'
    bl_label       = 'Open Images Folder'
    bl_description = "Open the timelapse images output folder"
    bl_options     = {'REGISTER'}

    def execute(self, context):
        prefs    = utils.get_addon_preferences()
        base_dir = bpy.path.abspath(prefs.output_dir if prefs else '//')
        try:
            os.makedirs(base_dir, exist_ok=True)
        except OSError as e:
            self.report({'ERROR'}, f"Cannot create folder: {e}")
            return {'CANCELLED'}
        try:
            bpy.ops.wm.path_open(filepath=base_dir)
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f'Cannot open folder: {e}')
            return {'CANCELLED'}


class TLX_OT_open_session_folder(Operator):
    bl_idname      = 'tlx.open_session_folder'
    bl_label       = 'Open Session Folder'
    bl_description = "Open the current session's output folder"
    bl_options     = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return bool(StateManager().session_dir and
                    os.path.isdir(StateManager().session_dir))

    def execute(self, context):
        session_dir = StateManager().session_dir
        if not session_dir or not os.path.isdir(session_dir):
            self.report({'WARNING'}, 'No active session folder.')
            return {'CANCELLED'}
        try:
            bpy.ops.wm.path_open(filepath=bpy.path.abspath(session_dir))
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f'Cannot open session folder: {e}')
            return {'CANCELLED'}


class TLX_OT_open_mp4_folder(Operator):
    bl_idname      = 'tlx.open_mp4_folder'
    bl_label       = 'Open MP4 Folder'
    bl_description = "Open the MP4 output folder"
    bl_options     = {'REGISTER'}

    def execute(self, context):
        prefs = utils.get_addon_preferences()
        if not prefs:
            self.report({'ERROR'}, 'Preferences not available')
            return {'CANCELLED'}
        mp4_dir = bpy.path.abspath(
            prefs.mp4_custom_dir if prefs.mp4_output_mode == 'CUSTOM_DIR'
            else prefs.output_dir)
        try:
            os.makedirs(mp4_dir, exist_ok=True)
        except OSError as e:
            self.report({'ERROR'}, f"Cannot create folder: {e}")
            return {'CANCELLED'}
        try:
            bpy.ops.wm.path_open(filepath=mp4_dir)
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f'Cannot open folder: {e}')
            return {'CANCELLED'}


class TLX_OT_reveal_last_capture(Operator):
    bl_idname      = 'tlx.reveal_last_capture'
    bl_label       = 'Reveal Last Capture'
    bl_description = "Show the last captured image in file browser"
    bl_options     = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        mgr = StateManager()
        if mgr.capture_mode == 'WINDOW':
            return bool(mgr.last_window_image)
        return mgr.counter > 0

    def execute(self, context):
        mgr = StateManager()
        if mgr.capture_mode == 'WINDOW':
            last_path = mgr.last_window_image
            if last_path and os.path.isfile(last_path):
                try:
                    bpy.ops.wm.path_open(filepath=os.path.dirname(last_path))
                    return {'FINISHED'}
                except Exception as e:
                    self.report({'ERROR'}, f'Cannot reveal file: {e}')
                    return {'CANCELLED'}
        session_dir = mgr.session_dir
        if session_dir and os.path.isdir(session_dir):
            try:
                bpy.ops.wm.path_open(filepath=session_dir)
                return {'FINISHED'}
            except Exception as e:
                self.report({'ERROR'}, f'Cannot open folder: {e}')
                return {'CANCELLED'}
        self.report({'WARNING'}, 'No captures to reveal.')
        return {'CANCELLED'}


class TLX_OT_copy_output_path(Operator):
    bl_idname      = 'tlx.copy_output_path'
    bl_label       = 'Copy Output Path'
    bl_description = "Copy the output directory path to clipboard"
    bl_options     = {'REGISTER'}

    path_type: bpy.props.EnumProperty(
        name="Path Type",
        items=[
            ('BASE',    'Base Folder',    ''),
            ('SESSION', 'Session Folder', ''),
            ('MP4',     'MP4 Folder',     ''),
        ],
        default='BASE'
    )

    def execute(self, context):
        prefs = utils.get_addon_preferences()
        if self.path_type == 'SESSION':
            path = StateManager().session_dir
            if not path:
                self.report({'WARNING'}, 'No active session.')
                return {'CANCELLED'}
        elif self.path_type == 'MP4':
            if not prefs:
                self.report({'ERROR'}, 'Preferences not available')
                return {'CANCELLED'}
            path = (prefs.mp4_custom_dir if prefs.mp4_output_mode == 'CUSTOM_DIR'
                    else prefs.output_dir)
        else:
            path = prefs.output_dir if prefs else '//'
        try:
            context.window_manager.clipboard = bpy.path.abspath(path)
            self.report({'INFO'}, f'Copied: {bpy.path.abspath(path)}')
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f'Cannot copy to clipboard: {e}')
            return {'CANCELLED'}


class TLX_OT_clean_empty_folders(Operator):
    bl_idname      = 'tlx.clean_empty_folders'
    bl_label       = 'Clean Empty Folders'
    bl_description = "Remove empty session and dated folders"
    bl_options     = {'REGISTER'}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        prefs = utils.get_addon_preferences()
        if not prefs:
            self.report({'ERROR'}, 'Preferences not available')
            return {'CANCELLED'}
        base_dir = bpy.path.abspath(prefs.output_dir)
        if not os.path.isdir(base_dir):
            self.report({'WARNING'}, 'Output directory does not exist.')
            return {'CANCELLED'}
        removed_count = 0
        try:
            for root, dirs, files in os.walk(base_dir, topdown=False):
                for dir_name in dirs:
                    dir_path = os.path.join(root, dir_name)
                    try:
                        if not os.listdir(dir_path):
                            os.rmdir(dir_path)
                            removed_count += 1
                    except OSError:
                        pass
        except Exception as e:
            self.report({'ERROR'}, f'Error: {e}')
            return {'CANCELLED'}
        self.report({'INFO'}, f'Removed {removed_count} empty folders')
        return {'FINISHED'}


class TLX_OT_force_reset_state(Operator):
    bl_idname      = 'tlx.force_reset_state'
    bl_label       = 'Force Reset Recording State'
    bl_description = "Force reset stuck recording state"
    bl_options     = {'REGISTER'}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        logger.warning("Force resetting StateManager...")
        mgr   = StateManager()
        scene = context.scene
        wm    = context.window_manager

        # v2.6.5: stop the persistent app timer (no wm timer)
        try:
            from .recording import _stop_capture_timer
            _stop_capture_timer()
            print("✓ App capture timer stopped")
        except Exception as e:
            logger.warning(f"App timer stop failed: {e}")

        if mgr.handler_installed:
            try:
                from ..state_manager import mark_scene_dirty
                bpy.app.handlers.depsgraph_update_post.remove(mark_scene_dirty)
            except (ValueError, Exception) as e:
                logger.warning(f"Handler removal: {e}")

        try:
            from ..capture import window
            if hasattr(window, 'cancel_async_capture'):
                window.cancel_async_capture()
        except Exception as e:
            logger.warning(f"Async cancel failed: {e}")

        try:
            for win in wm.windows:
                for area in win.screen.areas:
                    if area.type == 'VIEW_3D':
                        area.header_text_set(None)
        except Exception:
            pass

        try:
            mgr.stop_recording()
            mgr.reset()
        except Exception as e:
            logger.error(f"StateManager reset failed: {e}")

        scene.tlx_is_recording = False

        if mgr.recording or scene.tlx_is_recording:
            self.report({'ERROR'}, '⚠ Reset incomplete')
            return {'CANCELLED'}

        self.report({'INFO'}, '✓ Recording state reset successfully')
        return {'FINISHED'}


class TLX_OT_show_debug_state(Operator):
    bl_idname      = 'tlx.show_debug_state'
    bl_label       = 'Show Debug State'
    bl_description = "Show detailed StateManager state (for debugging)"
    bl_options     = {'REGISTER'}

    def execute(self, context):
        mgr   = StateManager()
        scene = context.scene

        try:
            from .recording import _capture_interval, _tlx_capture_tick
            timer_active = bpy.app.timers.is_registered(_tlx_capture_tick)
            timer_info   = (f"persistent app timer: active={timer_active}, "
                            f"interval={_capture_interval:.2f}s")
        except Exception:
            timer_info = "unknown"

        print("\n" + "="*70)
        print("TIMELAPSE X - STATE DEBUG INFO (v2.6.5)")
        print("="*70)
        print(f"\n  recording:              {mgr.recording}")
        print(f"  paused:                 {mgr.paused}")
        print(f"  capture_mode:           {mgr.capture_mode}")
        print(f"  counter:                {mgr.counter}")
        print(f"  scene.tlx_is_recording: {scene.tlx_is_recording}")
        print(f"  session_dir:            {mgr.session_dir}")
        print(f"  {timer_info}")
        print(f"  dirty:                  {mgr.dirty}")
        print(f"  ignore_depsgraph:       {mgr.ignore_depsgraph}")
        print(f"  handler_installed:      {mgr.handler_installed}")
        wa = mgr.window_async_context
        print(f"  window_async state:     {wa.state.value}")
        print(f"  last_window_image:      {mgr.last_window_image}")
        cam_map = mgr.camera_schedulers_map
        print(f"  camera schedulers:      {len(cam_map)}")
        print("="*70 + "\n")

        self.report({'INFO'}, 'Debug info printed to console')
        return {'FINISHED'}


classes = (
    TLX_OT_open_images_folder,
    TLX_OT_open_session_folder,
    TLX_OT_open_mp4_folder,
    TLX_OT_reveal_last_capture,
    TLX_OT_copy_output_path,
    TLX_OT_clean_empty_folders,
    TLX_OT_force_reset_state,
    TLX_OT_show_debug_state,
)


def register():
    logger.info("Registering utility operators (v2.6.5)")
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except Exception as e:
            logger.error(f"Failed to register {cls.__name__}: {e}")


def unregister():
    logger.info("Unregistering utility operators")
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception as e:
            logger.warning(f"Failed to unregister {cls.__name__}: {e}")
