"""
Blender property definitions for Timelapse X addon.

Fix applied (v2.5.1):
  - MIN_INTERVAL, SHADING_TYPES, IMAGE_FORMATS now imported from constants.py
    instead of being redefined inline. Previously these could drift out of
    sync if constants.py was updated.
"""

import bpy
import logging
from bpy.types import PropertyGroup
from bpy.props import (
    BoolProperty, EnumProperty, IntProperty,
    FloatProperty, PointerProperty, CollectionProperty,
)

# FIX: import from the single source of truth
from . import constants

logger = logging.getLogger(__name__)


def _stealth_camera_poll(self, obj):
    return bool(obj and obj.type == 'CAMERA')


class TLX_CameraItem(PropertyGroup):
    camera: PointerProperty(
        name='Camera',
        type=bpy.types.Object,
        description="Camera object to render from"
    )

    use_interval_override: BoolProperty(name="Override Interval", default=False)
    interval_override: FloatProperty(
        name="Interval (s)",
        # FIX: use constants.MIN_INTERVAL instead of local literal
        min=constants.MIN_INTERVAL, max=3600.0, default=2.0
    )

    use_shading_override: BoolProperty(name="Override Shading", default=False)
    shading_type: EnumProperty(
        name="Shading",
        # FIX: use constants.SHADING_TYPES instead of local copy
        items=constants.SHADING_TYPES,
        default='SOLID'
    )
    xray:            BoolProperty(name="X-Ray",            default=False)
    disable_shadows: BoolProperty(name="Disable Shadows",  default=True)

    use_image_override: BoolProperty(name="Override Image Format", default=False)
    image_format: EnumProperty(
        name="Image Format",
        # FIX: use constants.IMAGE_FORMATS instead of local copy
        items=constants.IMAGE_FORMATS,
        default='PNG'
    )
    png_rgba:     BoolProperty(name="PNG RGBA (alpha)", default=False)
    jpeg_quality: IntProperty(name="JPEG Quality", min=1, max=100, default=90)

    perf_override:        BoolProperty(name="Override Performance",   default=False)
    perf_low_overhead:    BoolProperty(name="Low-Overhead Render",    default=True)
    perf_lock_interface:  BoolProperty(name="Lock UI During Render",  default=True)
    perf_png_compress:    IntProperty(name="PNG Compression",         min=0, max=15, default=3)

    # ── Turntable ────────────────────────────────────────────────────────────
    # Auto-orbit this camera around a target object by N degrees after
    # every successful capture — a "progress turntable" with no manual
    # keyframing. Per-camera (not global) since CAMERA_LIST mode can hold
    # several cameras at once and each may want its own orbit, or none.
    use_turntable: BoolProperty(
        name="Turntable Rotation", default=False,
        description="Orbit this camera around a target object by a fixed "
                    "number of degrees after every capture, building up "
                    "a full rotation over the course of the recording")
    turntable_target: PointerProperty(
        name="Rotation Target", type=bpy.types.Object,
        description="Object the camera orbits around. The camera keeps "
                    "its current distance and height relative to this "
                    "object's origin")
    turntable_degrees: FloatProperty(
        name="Degrees / Capture", default=5.0, soft_min=-90.0, soft_max=90.0,
        description="Rotation step applied after each capture of this "
                    "camera. Negative values orbit the other way")
    turntable_axis: EnumProperty(
        name="Axis",
        items=(('X', "X", ""), ('Y', "Y", ""), ('Z', "Z", "")),
        default='Z',
        description="World axis to orbit around (Z = orbit around the "
                    "vertical axis, the usual 'turntable' look)")


def register_scene_properties():
    logger.info("Registering scene properties")
    Scene = bpy.types.Scene

    Scene.tlx_cameras = CollectionProperty(type=TLX_CameraItem, name="Camera List")
    Scene.tlx_cameras_index = IntProperty(name="Active Camera Index", default=0)
    Scene.tlx_is_recording  = BoolProperty(name="Is Recording",        default=False)
    Scene.tlx_capture_interval = FloatProperty(
        name='Interval (s)',
        min=constants.MIN_INTERVAL, max=3600.0, default=2.0
    )
    Scene.tlx_capture_mode = EnumProperty(
        name='Capture Mode',
        items=[
            ('WINDOW',      'Blender Window', ''),
            ('CAMERA_LIST', 'Camera List',    ''),
        ],
        default='CAMERA_LIST'
    )
    Scene.tlx_ui_cam_editor_collapsed = BoolProperty(
        name="Camera Settings Collapsed", default=False
    )

    # Stealth Camera (Window mode) — lives on the Scene, not
    # AddonPreferences, because it's a PointerProperty to an Object (an
    # ID datablock). AddonPreferences is user-level and outlives any
    # particular .blend file, so Blender does not support registering
    # ID-datablock pointers on it; Scene-attached data (like this, and
    # like TLX_CameraItem.turntable_target above) is the correct place.
    Scene.tlx_window_stealth_camera_enabled = BoolProperty(
        name="Stealth Camera (Window)", default=False,
        description="Force every Window-mode capture to briefly switch "
                    "the viewport to a fixed camera's angle, then switch "
                    "back to whatever you were actually looking at right "
                    "after. Keeps the timelapse framing stable even if "
                    "you orbit/pan the viewport constantly while working. "
                    "Still a viewport screenshot (not a render) — for a "
                    "clean rendered shot from a fixed camera instead, use "
                    "Camera List mode with a single camera")
    Scene.tlx_window_stealth_camera = PointerProperty(
        name="Stealth Camera", type=bpy.types.Object,
        poll=_stealth_camera_poll,
        description="Camera object whose angle is used for every "
                    "capture while Stealth Camera is enabled")
    Scene.tlx_window_stealth_hide_passepartout = BoolProperty(
        name="Hide Black Border (Passepartout)", default=True,
        description="Blender darkens everything outside a camera's frame "
                    "while looking through it ('Passepartout'). With this "
                    "on, Timelapse X hides that darkened border for the "
                    "instant of the capture (restoring the camera's own "
                    "Passepartout setting right after) so captured frames "
                    "show the actual scene outside the frame instead of "
                    "black. Turn off to keep whatever the camera's own "
                    "Passepartout setting normally shows")
    Scene.tlx_window_stealth_crop_to_camera = BoolProperty(
        name="Crop to Camera Frame", default=True,
        description="The black border is usually just the camera's "
                    "aspect ratio not matching the viewport's — turning "
                    "off Passepartout alone doesn't remove that gap. "
                    "With this on, Timelapse X crops each captured frame "
                    "down to exactly the camera's visible frame, removing "
                    "that border entirely rather than just recoloring it. "
                    "Only applies when Capture Scope is 'Viewport' "
                    "(cropping a Full-window capture isn't supported)")

    # Auto-hide reference blueprints (Empty Image objects) by projection.
    # Blender already has a per-object toggle for this
    # (show_empty_image_perspective / show_empty_image_orthographic) that
    # the live viewport respects on its own — but Camera List mode's
    # OpenGL renders and Window mode's Stealth Camera / Full-window
    # captures don't reliably honor it, so this enforces it explicitly
    # during capture instead of trusting it happens automatically.
    Scene.tlx_auto_hide_blueprints = BoolProperty(
        name="Auto-Hide Reference Blueprints", default=False,
        description="Respect each reference image Empty's own "
                    "Perspective/Orthographic display toggle during "
                    "capture — hiding it if it shouldn't show for the "
                    "current view (or, in Camera List mode, for that "
                    "camera's own Perspective/Orthographic type). Set "
                    "per-object in the Object Data Properties tab for "
                    "an Empty (Image) — 'Show In: Perspective / "
                    "Orthographic'")

    # Isolate/hide a collection just for the instant of each capture —
    # handy for rig controls, measurement guides, blueprint planes, or
    # anything else you want visible while you work but never in the
    # actual timelapse frames. Uses Collection.hide_viewport / hide_render
    # (the datablock-level flags, which already cascade to everything
    # nested inside the collection) rather than LayerCollection.exclude —
    # much lighter-weight, appropriate for toggling on every single
    # capture rather than a one-off structural change.
    Scene.tlx_hide_collection_enabled = BoolProperty(
        name="Hide Collection During Capture", default=False,
        description="Temporarily hide an entire collection (and "
                    "everything nested inside it) for the instant of "
                    "each capture, then restore it right after — useful "
                    "for rig controls, guides, or reference planes you "
                    "want visible while working but never in the "
                    "captured frames")
    Scene.tlx_hide_collection = PointerProperty(
        name="Collection to Hide", type=bpy.types.Collection,
        description="Collection to hide during capture. Nested "
                    "sub-collections are hidden along with it "
                    "automatically")


def unregister_scene_properties():
    logger.info("Unregistering scene properties")
    Scene = bpy.types.Scene
    for prop in ['tlx_cameras', 'tlx_cameras_index', 'tlx_is_recording',
                 'tlx_capture_interval', 'tlx_capture_mode',
                 'tlx_ui_cam_editor_collapsed',
                 'tlx_window_stealth_camera_enabled', 'tlx_window_stealth_camera',
                 'tlx_window_stealth_hide_passepartout', 'tlx_window_stealth_crop_to_camera',
                 'tlx_auto_hide_blueprints',
                 'tlx_hide_collection_enabled', 'tlx_hide_collection']:
        if hasattr(Scene, prop):
            delattr(Scene, prop)


classes = (TLX_CameraItem,)


def register():
    logger.info("Registering properties module (v2.5.1: imports from constants)")
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except Exception as e:
            logger.error(f"Failed to register {cls.__name__}: {e}")


def unregister():
    logger.info("Unregistering properties module")
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception as e:
            logger.warning(f"Failed to unregister {cls.__name__}: {e}")
