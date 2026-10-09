"""
Camera management operators for Timelapse X addon.

Fix (v2.5.4):
  - TLX_OT_cam_select.execute() called bpy.ops.object.select_all() and
    camera.select_set() which require OBJECT mode — raising RuntimeError in
    EDIT mode. Added poll() guard and try/except in execute().
"""

import bpy
import logging
from bpy.types import Operator
from bpy.props import EnumProperty, BoolProperty

from .. import constants
from .. import utils

logger = logging.getLogger(__name__)


# ========================================================================================
# Add Camera
# ========================================================================================

class TLX_OT_cam_add(Operator):
    """Add a camera to the timelapse camera list."""

    bl_idname = "tlx.cam_add"
    bl_label  = "Add Camera"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene      = context.scene
        camera_obj = self._get_camera_to_add(context, scene)

        if not camera_obj:
            self.report({'ERROR'}, 'No camera available to add.')
            return {'CANCELLED'}

        camera_item        = scene.tlx_cameras.add()
        camera_item.camera = camera_obj
        scene.tlx_cameras_index = len(scene.tlx_cameras) - 1

        self.report({'INFO'}, f"Added camera: {camera_obj.name}")
        return {'FINISHED'}

    def _get_camera_to_add(self, context, scene):
        active_obj = getattr(context, 'object', None)
        if active_obj and active_obj.type == 'CAMERA':
            return active_obj
        if scene.camera:
            return scene.camera
        return self._create_new_camera(scene)

    def _create_new_camera(self, scene):
        try:
            camera_data = bpy.data.cameras.new("TLX_Camera")
            camera_obj  = bpy.data.objects.new("TLX_Camera", camera_data)
            scene.collection.objects.link(camera_obj)
            if not scene.camera:
                scene.camera = camera_obj
            return camera_obj
        except Exception as e:
            logger.error(f"Failed to create camera: {e}")
            return None


# ========================================================================================
# Remove Camera
# ========================================================================================

class TLX_OT_cam_remove(Operator):
    """Remove selected camera from the timelapse camera list."""

    bl_idname = "tlx.cam_remove"
    bl_label  = "Remove Camera"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        scene = context.scene
        return (len(scene.tlx_cameras) > 0 and
                0 <= scene.tlx_cameras_index < len(scene.tlx_cameras))

    def execute(self, context):
        scene       = context.scene
        index       = scene.tlx_cameras_index
        camera_item = scene.tlx_cameras[index]
        camera_name = camera_item.camera.name if camera_item.camera else '<None>'

        scene.tlx_cameras.remove(index)
        scene.tlx_cameras_index = max(0, min(index, len(scene.tlx_cameras) - 1))

        self.report({'INFO'}, f"Removed camera: {camera_name}")
        return {'FINISHED'}


# ========================================================================================
# Move Camera Up / Down
# ========================================================================================

class TLX_OT_cam_move_up(Operator):
    """Move selected camera up in the list."""

    bl_idname = "tlx.cam_move_up"
    bl_label  = "Move Camera Up"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        scene = context.scene
        return len(scene.tlx_cameras) > 1 and scene.tlx_cameras_index > 0

    def execute(self, context):
        scene = context.scene
        index = scene.tlx_cameras_index
        scene.tlx_cameras.move(index, index - 1)
        scene.tlx_cameras_index = index - 1
        return {'FINISHED'}


class TLX_OT_cam_move_down(Operator):
    """Move selected camera down in the list."""

    bl_idname = "tlx.cam_move_down"
    bl_label  = "Move Camera Down"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        scene = context.scene
        return (len(scene.tlx_cameras) > 1 and
                scene.tlx_cameras_index < len(scene.tlx_cameras) - 1)

    def execute(self, context):
        scene = context.scene
        index = scene.tlx_cameras_index
        scene.tlx_cameras.move(index, index + 1)
        scene.tlx_cameras_index = index + 1
        return {'FINISHED'}


# ========================================================================================
# Apply Speed Preset
# ========================================================================================

class TLX_OT_apply_speed_preset(Operator):
    """Apply performance preset to global settings and optionally all cameras."""

    bl_idname = 'tlx.apply_speed_preset'
    bl_label  = 'Apply Speed Preset'
    bl_options = {'REGISTER', 'UNDO'}

    preset: EnumProperty(name="Preset", items=constants.SPEED_PRESETS, default='BALANCED')
    apply_to_all_cameras: BoolProperty(name="Apply to All Cameras", default=False)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'preset', expand=True)
        layout.separator()
        layout.prop(self, 'apply_to_all_cameras')

        box = layout.box()
        box.label(text="Will change:", icon='INFO')
        if self.preset == 'ULTRA_FAST':
            for t in ("Format: JPEG 80", "Cooldown: 160ms", "PNG Compression: 3"):
                box.label(text=f"• {t}")
        elif self.preset == 'BALANCED':
            for t in ("Format: JPEG 90", "Cooldown: 140ms", "PNG Compression: 5"):
                box.label(text=f"• {t}")
        else:
            for t in ("Format: PNG", "Cooldown: 120ms", "PNG Compression: 9"):
                box.label(text=f"• {t}")
        box.label(text="• Round-robin: ON")
        box.label(text="• Lock interface: ON")

    def execute(self, context):
        prefs = utils.get_addon_preferences()
        if not prefs:
            self.report({'ERROR'}, 'Preferences not available')
            return {'CANCELLED'}

        self._apply_global_preset(prefs)
        if self.apply_to_all_cameras:
            self._apply_to_cameras(context.scene, prefs)

        self.report({'INFO'}, f"Applied preset: {self.preset}")
        return {'FINISHED'}

    def _apply_global_preset(self, prefs):
        prefs.camera_round_robin    = True
        prefs.camera_max_per_tick   = 1
        prefs.camera_lock_interface = True

        if self.preset == 'ULTRA_FAST':
            prefs.camera_low_overhead          = True
            prefs.image_format                 = 'JPEG'
            prefs.jpeg_quality                 = 80
            prefs.perf_depsgraph_suppress_ms   = 160
            prefs.default_interval             = max(1.0, prefs.default_interval)
            prefs.camera_png_compress          = 3
        elif self.preset == 'BALANCED':
            prefs.camera_low_overhead          = True
            prefs.image_format                 = 'JPEG'
            prefs.jpeg_quality                 = 90
            prefs.perf_depsgraph_suppress_ms   = 140
            prefs.default_interval             = max(1.5, prefs.default_interval)
            prefs.camera_png_compress          = 5
        else:  # QUALITY
            prefs.camera_low_overhead          = False
            prefs.camera_max_per_tick          = 2
            prefs.camera_lock_interface        = False
            prefs.image_format                 = 'PNG'
            prefs.png_rgba                     = False
            prefs.perf_depsgraph_suppress_ms   = 120
            prefs.default_interval             = max(2.0, prefs.default_interval)
            prefs.camera_png_compress          = 9

    def _apply_to_cameras(self, scene, prefs):
        for camera_item in scene.tlx_cameras:
            camera_item.perf_override        = True
            camera_item.perf_low_overhead    = bool(prefs.camera_low_overhead)
            camera_item.perf_lock_interface  = bool(prefs.camera_lock_interface)
            camera_item.perf_png_compress    = int(prefs.camera_png_compress)


# ========================================================================================
# Clear All Cameras
# ========================================================================================

class TLX_OT_cam_clear_all(Operator):
    """Clear all cameras from the list."""

    bl_idname = "tlx.cam_clear_all"
    bl_label  = "Clear All Cameras"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return len(context.scene.tlx_cameras) > 0

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        scene = context.scene
        count = len(scene.tlx_cameras)
        scene.tlx_cameras.clear()
        scene.tlx_cameras_index = 0
        self.report({'INFO'}, f"Cleared {count} cameras")
        return {'FINISHED'}


# ========================================================================================
# Select Camera in Viewport
# ========================================================================================

class TLX_OT_cam_select(Operator):
    """Select the camera object in the viewport."""

    bl_idname = "tlx.cam_select"
    bl_label  = "Select Camera"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        scene = context.scene
        if not (len(scene.tlx_cameras) > 0 and
                0 <= scene.tlx_cameras_index < len(scene.tlx_cameras)):
            return False
        camera_item = scene.tlx_cameras[scene.tlx_cameras_index]
        # FIX: also require OBJECT mode — select_all/select_set crash in EDIT mode
        return camera_item.camera is not None and context.mode == 'OBJECT'

    def execute(self, context):
        scene       = context.scene
        camera_item = scene.tlx_cameras[scene.tlx_cameras_index]
        camera      = camera_item.camera

        # FIX: wrap in try/except — mode could change between poll() and execute()
        try:
            bpy.ops.object.select_all(action='DESELECT')
            camera.select_set(True)
            context.view_layer.objects.active = camera
            self.report({'INFO'}, f"Selected: {camera.name}")
        except RuntimeError as e:
            self.report({'WARNING'}, f"Could not select camera: {e}")
            return {'CANCELLED'}

        return {'FINISHED'}


# ========================================================================================
# Registration
# ========================================================================================

classes = (
    TLX_OT_cam_add,
    TLX_OT_cam_remove,
    TLX_OT_cam_move_up,
    TLX_OT_cam_move_down,
    TLX_OT_apply_speed_preset,
    TLX_OT_cam_clear_all,
    TLX_OT_cam_select,
)


def register():
    logger.info("Registering camera operators (v2.5.4: cam_select OBJECT mode guard)")
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except Exception as e:
            logger.error(f"Failed to register {cls.__name__}: {e}")


def unregister():
    logger.info("Unregistering camera operators")
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception as e:
            logger.warning(f"Failed to unregister {cls.__name__}: {e}")
