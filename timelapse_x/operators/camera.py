"""
Camera management operators for Timelapse X.

Operators that select or deselect objects require OBJECT mode (select_all /
select_set raise RuntimeError in Edit mode).
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

# One table drives both the dialog text and what is actually applied, so the
# two can never disagree. 'min_interval' only raises default_interval.
SPEED_PRESET_VALUES = {
    'ULTRA_FAST': dict(
        image_format='JPEG', jpeg_quality=80, camera_png_compress=3,
        perf_depsgraph_suppress_ms=160, min_interval=1.0,
        camera_low_overhead=True, camera_lock_interface=True,
        camera_max_per_tick=1, camera_round_robin=True),
    'BALANCED': dict(
        image_format='JPEG', jpeg_quality=90, camera_png_compress=5,
        perf_depsgraph_suppress_ms=140, min_interval=1.5,
        camera_low_overhead=True, camera_lock_interface=True,
        camera_max_per_tick=1, camera_round_robin=True),
    'QUALITY': dict(
        image_format='PNG', png_rgba=False, camera_png_compress=9,
        perf_depsgraph_suppress_ms=120, min_interval=2.0,
        camera_low_overhead=False, camera_lock_interface=False,
        camera_max_per_tick=2, camera_round_robin=True),
}


def describe_speed_preset(preset: str):
    """Human-readable lines for what `preset` will change."""
    v = SPEED_PRESET_VALUES[preset]
    fmt = v['image_format'] + (f" {v['jpeg_quality']}" if 'jpeg_quality' in v else '')
    on_off = lambda flag: 'ON' if flag else 'OFF'
    return [
        f"Image format: {fmt}",
        f"PNG compression: {v['camera_png_compress']}",
        f"Cooldown: {v['perf_depsgraph_suppress_ms']} ms",
        f"Default interval: at least {v['min_interval']:g} s",
        f"Cameras per tick: {v['camera_max_per_tick']}",
        f"Low-overhead render: {on_off(v['camera_low_overhead'])}",
        f"Lock interface: {on_off(v['camera_lock_interface'])}",
        f"Round-robin: {on_off(v['camera_round_robin'])}",
    ]


class TLX_OT_apply_speed_preset(Operator):
    """Apply performance preset to global settings and optionally all cameras."""

    bl_idname = 'tlx.apply_speed_preset'
    bl_label  = 'Apply Speed Preset'
    bl_options = {'REGISTER', 'UNDO'}

    preset: EnumProperty(name="Preset", items=constants.SPEED_PRESETS, default='BALANCED')
    apply_to_all_cameras: BoolProperty(
        name="Apply to All Cameras", default=False,
        description="Also give every camera its own performance override "
                    "(it stays on until you turn it off per camera)")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'preset', expand=True)
        layout.separator()
        layout.prop(self, 'apply_to_all_cameras')

        box = layout.box()
        box.label(text="Will change:", icon='INFO')
        for line in describe_speed_preset(self.preset):
            box.label(text=f"• {line}")
        if self.apply_to_all_cameras:
            box.label(text="• Every camera: performance override ON")

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
        for key, value in SPEED_PRESET_VALUES[self.preset].items():
            if key == 'min_interval':
                prefs.default_interval = max(value, prefs.default_interval)
            else:
                setattr(prefs, key, value)

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
        # Also require OBJECT mode — select_all/select_set raise in EDIT mode
        return camera_item.camera is not None and context.mode == 'OBJECT'

    def execute(self, context):
        scene       = context.scene
        camera_item = scene.tlx_cameras[scene.tlx_cameras_index]
        camera      = camera_item.camera

        # Guard the calls — the mode could change between poll() and execute()
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
    logger.info("Registering camera operators")
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
