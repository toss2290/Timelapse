"""
Addon preferences for Timelapse X.
Blender 5.1+ compatible.

Fix (v2.6.2):
  - Added show_recording_header BoolProperty so users can toggle the
    "RECORDING [WINDOW]" status bar on/off without stopping recording.

Fix (v2.6.1):
  - Added camera_disable_shadows BoolProperty for Camera List SOLID renders.
"""

import bpy
import logging
from bpy.types import AddonPreferences
from bpy.props import (
    BoolProperty, EnumProperty, StringProperty,
    IntProperty, FloatProperty, FloatVectorProperty, PointerProperty,
)

from . import constants
from . import utils

logger = logging.getLogger(__name__)


# ========================================================================================
# Update Callbacks

# ========================================================================================

def _update_show_freestyle_marks(self, context):
    try:
        utils.apply_freestyle_marks_visibility(bool(self.show_freestyle_marks_in_edit))
    except Exception as e:
        logger.error(f"Failed to update freestyle marks: {e}")


def _update_edit_mode_behavior(self, context):
    if self.edit_mode_behavior == 'CAPTURE_ANYWAY':
        logger.warning("Edit mode capture enabled — may cause crashes!")


# ========================================================================================
# Action-count trigger — update callbacks
#
# Deferred (function-body) imports here, NOT at module load time: this
# module (preferences.py) is imported by timelapse_x/__init__.py BEFORE
# the operators package, so a top-level "from .operators import
# action_count" would be a circular / load-order problem. By the time a
# user actually toggles one of these properties in the UI, registration
# has long finished and the import resolves normally.
# ========================================================================================

def _on_input_only_toggle(self, context):
    if self.window_capture_on_input_only:
        self.window_capture_on_action_count = False


def _on_action_count_toggle(self, context):
    if self.window_capture_on_action_count:
        self.window_capture_on_input_only = False
    try:
        from .operators import action_count
        action_count.reset_counter()
    except Exception as e:
        logger.debug(f"Action-count reset skipped: {e}")


def _on_camera_action_count_toggle(self, context):
    try:
        from .operators import action_count
        action_count.reset_counter()
    except Exception as e:
        logger.debug(f"Action-count reset skipped: {e}")


# ========================================================================================
# Addon Preferences
# ========================================================================================

class TLX_AddonPrefs(AddonPreferences):
    bl_idname = __name__.split('.')[0]

    # ── Output ──────────────────────────────────────────────────────────────
    output_dir: StringProperty(
        name='Output Directory', subtype='DIR_PATH', default='//Timelapse_Images')
    image_format: EnumProperty(
        name='Image Format', items=constants.IMAGE_FORMATS, default='PNG')
    png_rgba:     BoolProperty(name='PNG RGBA (alpha)', default=False)
    jpeg_quality: IntProperty(name='JPEG Quality', min=1, max=100, default=90)
    zero_padding: IntProperty(
        name='Zero Padding', min=2, max=8, default=constants.DEFAULT_ZERO_PADDING)

    # ── Capture ─────────────────────────────────────────────────────────────
    default_interval: FloatProperty(
        name='Default Interval (s)',
        min=constants.MIN_INTERVAL, max=3600.0, default=constants.DEFAULT_INTERVAL)
    idle_detection: BoolProperty(name='Idle Detection (Camera List)', default=True)
    capture_immediate_on_start: BoolProperty(
        name='Capture Immediately on Start', default=True)

    edit_mode_behavior: EnumProperty(
        name='Edit Mode Behavior', items=constants.EDIT_MODE_BEHAVIORS,
        default='CAPTURE_ANYWAY', update=_update_edit_mode_behavior)
    edit_mode_auto_save: BoolProperty(name='Auto-save Before Capture', default=True)

    # ── Header Display ───────────────────────────────────────────────────────
    show_recording_header: BoolProperty(
        name="Show Recording Status in Header",
        description=(
            "Show or hide the 'RECORDING [WINDOW]' status bar "
            "at the top of the 3D viewport while recording"
        ),
        default=True,
    )

    # ── Emergency Stop Hotkey ────────────────────────────────────────────────
    # Mode-agnostic panic button (see operators/stop_hotkey.py): works in
    # CAMERA_LIST / WIREFRAME / WINDOW alike, unlike the capture hotkey
    # and action-count trigger which only apply to Window mode.
    emergency_stop_enabled: BoolProperty(
        name="Emergency Stop Hotkey", default=True,
        description="Enable a rebindable keyboard shortcut that "
                    "immediately stops the current recording — same "
                    "effect as pressing Stop, works in any capture "
                    "mode. Rebind it in the panel below or via "
                    "Preferences > Keymap > Window > 'Timelapse X: "
                    "Emergency Stop Recording'")

    # ── Viewport Shading (Window mode) ──────────────────────────────────────
    lock_shading:    BoolProperty(name='Lock Viewport Shading (Window)', default=True)
    shading_type:    EnumProperty(
        name='Viewport Shading', items=constants.SHADING_TYPES, default='SOLID')
    xray:            BoolProperty(name='X-Ray (Window)', default=False)
    disable_shadows: BoolProperty(name='Disable Shadows in Viewport (Window)', default=True)

    # ── Camera List render settings ─────────────────────────────────────────
    camera_disable_shadows: BoolProperty(
        name="Disable Shadows (Camera List SOLID)",
        description=(
            "Disable directional shading and cast shadows when rendering "
            "SOLID captures in Camera List mode. "
            "Produces flat, uniform shading across all faces."
        ),
        default=True,
    )

    # ── Wireframe (Camera List) ─────────────────────────────────────────────
    wireframe_thickness: FloatProperty(
        name="Line Thickness", min=0.1, max=10.0, default=1.0)
    wireframe_color: FloatVectorProperty(
        name="Line Color", subtype='COLOR', size=3,
        min=0.0, max=1.0, default=(0.0, 0.0, 0.0))
    wireframe_bg_color: FloatVectorProperty(
        name="Background Color", subtype='COLOR', size=3,
        min=0.0, max=1.0, default=(0.0, 0.0, 0.0))
    wireframe_bg_strength: FloatProperty(
        name="Background Strength", min=0.0, max=2.0, default=1.0)
    wireframe_disable_shadows: BoolProperty(name="Disable Shadows (Wireframe)", default=True)
    wireframe_transparent_bg:  BoolProperty(name="Transparent Background", default=False)
    wireframe_use_object_colors: BoolProperty(name="Use Object Colors", default=False)
    wireframe_default_object_color: FloatVectorProperty(
        name="Default Object Color", subtype='COLOR', size=3,
        min=0.0, max=1.0, default=(1.0, 1.0, 1.0))
    wireframe_render_engine: EnumProperty(
        name="Wireframe Render Engine",
        items=[
            ('BLENDER_WORKBENCH', 'Workbench',  'Fast workbench engine'),
            ('BLENDER_EEVEE_NEXT', 'Eevee Next', 'Modern Eevee (Blender 5.x)'),
            ('CYCLES',            'Cycles',     'Cycles raytracing'),
        ],
        default='BLENDER_WORKBENCH')
    show_freestyle_marks_in_edit: BoolProperty(
        name='Show Freestyle Marks in Edit', default=False,
        update=_update_show_freestyle_marks)

    # ── Performance ─────────────────────────────────────────────────────────
    perf_hide_overlays_during_capture: BoolProperty(
        name="Hide Overlays During Capture (Window)", default=True)
    perf_depsgraph_suppress_ms: IntProperty(
        name="Depsgraph/Window Cooldown (ms)", min=0, max=1000,
        default=constants.DEFAULT_SUPPRESS_MS)

    # ── Camera List performance ──────────────────────────────────────────────
    camera_round_robin:    BoolProperty(name="Round-robin (Camera List)", default=True)
    camera_max_per_tick:   IntProperty(name="Max Cameras per Tick", min=1, max=32, default=1)
    camera_lock_interface: BoolProperty(name="Lock UI During Render (Camera List)", default=True)
    camera_low_overhead:   BoolProperty(name="Low-Overhead Render (Camera List)", default=True)
    camera_png_compress:   IntProperty(name="PNG Compression (Camera List)", min=0, max=15, default=3)

    # ── Video Export ─────────────────────────────────────────────────────────
    mp4_output_mode: EnumProperty(
        name='MP4 Output', items=constants.MP4_OUTPUT_MODES, default='SAME_AS_IMAGES')
    mp4_custom_dir: StringProperty(
        name='MP4 Custom Folder', subtype='DIR_PATH', default='//Timelapse_Videos')

    # ── Window Capture ───────────────────────────────────────────────────────
    window_idle_diff: BoolProperty(name="Window Idle via Image Diff", default=False)
    window_idle_threshold: FloatProperty(
        name="Window Idle Threshold", min=0.0, max=0.2,
        default=constants.DEFAULT_IDLE_THRESHOLD)
    window_idle_downscale: IntProperty(
        name="Diff Downscale (px)", min=8, max=256,
        default=constants.DEFAULT_DOWNSCALE_SIZE)
    window_capture_scope: EnumProperty(
        name="Window Capture Scope", items=constants.WINDOW_CAPTURE_SCOPES, default='VIEW3D')
    window_stabilize_view: BoolProperty(
        name="Stabilize 3D View (Window)", default=True,
        description="Hide the gizmo and viewport overlays for the instant "
                    "each screenshot is taken, then restore them right "
                    "after — keeps captured frames clean of selection "
                    "gizmos, grid, and other overlay clutter. Trade-off: "
                    "the hide/restore causes a brief visible flicker in "
                    "the viewport every time a frame is captured. Turn "
                    "this off for a perfectly smooth viewport while "
                    "working, at the cost of overlays sometimes showing "
                    "up in captured frames. Only applies when Capture "
                    "Scope is 'Viewport' (Full-window captures are never "
                    "stabilized)")
    window_async_capture:  BoolProperty(name="Window Async Capture", default=True)
    window_async_delay_ms: IntProperty(
        name="Window Async Delay (ms)", min=0, max=200, default=2)

    # NOTE: Stealth Camera (which camera + whether it's enabled) lives on
    # the SCENE now (scene.tlx_window_stealth_camera_enabled / ...camera
    # in properties.py), not here. AddonPreferences cannot hold a
    # PointerProperty to an ID datablock like Object — preferences are
    # user-level and outlive any particular .blend file, so Blender
    # rejects registering that kind of property on an AddonPreferences
    # subclass. Putting it here previously caused registration of this
    # entire class to silently fail partway through, taking every
    # property declared after it down with it (that's what caused the
    # "no attribute 'ui_video_tools_collapsed'" crash — nothing to do
    # with that property itself, it was just unlucky enough to be
    # declared after the broken one).

    window_input_suppress_ms: IntProperty(
        name="Window Input Cooldown (ms)", min=0, max=2147483647, default=250)
    window_capture_on_input_only: BoolProperty(
        name="Capture on Input Only (Window)", default=False,
        description="Disable the periodic timer for Window-mode captures "
                    "and only take a screenshot when the Timelapse X "
                    "hotkey is pressed. The hotkey is fully rebindable — "
                    "see the panel below or Preferences > Keymap > "
                    "Window > 'Timelapse X: Capture Screenshot Now'",
        update=_on_input_only_toggle)

    # ── Action-Count Trigger ────────────────────────────────────────────────
    # Capture automatically after every N completed operators (grab,
    # extrude, sculpt stroke, render-camera setup — anything that shows
    # up in Blender's own operator/redo history), instead of on a fixed
    # schedule or a manual keypress. See operators/action_count.py for
    # why this counts completed operators rather than raw keypresses or
    # depsgraph updates (both proved unreliable — see that module's
    # docstring for the full story).
    #
    # Window mode: replaces the periodic timer entirely (mutually
    # exclusive with the hotkey trigger above).
    # Camera List mode: replaces the periodic *heartbeat* that checks
    # which cameras are due — each per-camera interval/override still
    # applies on top, exactly as it does with the timer-driven heartbeat.
    window_capture_on_action_count: BoolProperty(
        name="Capture Every N Actions (Window)", default=False,
        description="Disable the periodic timer for Window-mode captures "
                    "and instead take a screenshot automatically after "
                    "every N completed operators (transform, extrude, "
                    "sculpt stroke, etc). Mutually exclusive with "
                    "'Capture on Input Only' — enabling this disables that one",
        update=_on_action_count_toggle)
    camera_capture_on_action_count: BoolProperty(
        name="Capture Every N Actions (Camera List)", default=False,
        description="Disable the periodic timer as the heartbeat for "
                    "Camera List captures and instead check which "
                    "cameras are due after every N completed operators. "
                    "Per-camera interval overrides still apply on top of "
                    "this — a camera only actually captures once both "
                    "its own interval AND the action count have elapsed",
        update=_on_camera_action_count_toggle)
    action_count_batch: IntProperty(
        name="Every N Actions", min=1, soft_max=200, default=5,
        description="Number of completed operators between automatic captures")
    action_count_debounce: FloatProperty(
        name="Min. Gap Between Actions (s)", min=0.0, soft_max=5.0, default=0.2,
        description="Merge operators completed within this many seconds "
                    "of each other into a single counted action — useful "
                    "since some tools report more than one operation for "
                    "what's really one gesture. 0 disables merging")

    # ── UI State ─────────────────────────────────────────────────────────────
    ui_video_tools_collapsed:    BoolProperty(name="Video Tools Collapsed",    default=False)
    ui_quick_settings_collapsed: BoolProperty(name="Quick Settings Collapsed", default=False)
    ui_clean_window_collapsed:   BoolProperty(name="Clean Window Collapsed",   default=True)

    # ── Debug ────────────────────────────────────────────────────────────────
    show_debug_panel: BoolProperty(name="Show Debug Panel", default=False)

    # ========================================================================================
    # Preferences UI
    # ========================================================================================

    def _draw_stop_hotkey_widget(self, layout, context):
        """Inline, rebindable widget for tlx.emergency_stop_hotkey."""
        try:
            from .operators import stop_hotkey
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

    def draw(self, context):
        layout = self.layout

        # Output
        box = layout.box()
        box.label(text="Output Settings", icon='FILE')
        box.prop(self, 'output_dir')
        row = box.row(align=True)
        row.prop(self, 'image_format')
        if self.image_format == 'PNG':
            row.prop(self, 'png_rgba')
        else:
            row.prop(self, 'jpeg_quality')
        box.prop(self, 'zero_padding')

        # Capture
        box = layout.box()
        box.label(text="Capture Settings", icon='RENDER_ANIMATION')
        box.prop(self, 'default_interval')
        box.prop(self, 'idle_detection')
        box.prop(self, 'capture_immediate_on_start')
        box.separator()

        edit_box = box.box()
        edit_box.label(text="Edit Mode Behavior:", icon='EDITMODE_HLT')
        edit_box.prop(self, 'edit_mode_behavior', text='')
        if self.edit_mode_behavior == 'CAPTURE_ANYWAY':
            w = edit_box.box()
            w.alert = True
            col = w.column(align=True)
            col.label(text="⚠ WARNING: May cause crashes!", icon='ERROR')
            col.label(text="Switch to 'Skip Capture' for safer operation.")
        elif self.edit_mode_behavior == 'FORCE_OBJECT':
            edit_box.prop(self, 'edit_mode_auto_save')
        elif self.edit_mode_behavior == 'SKIP':
            info = edit_box.box()
            col  = info.column(align=True)
            col.scale_y = 0.8
            col.label(text="✓ Safest option: No crashes.", icon='CHECKMARK')

        # Header Display
        box = layout.box()
        box.label(text="UI Display", icon='WINDOW')
        box.prop(self, 'show_recording_header')

        # Emergency Stop Hotkey (mode-agnostic — not under Window Capture)
        box = layout.box()
        box.label(text="Emergency Stop Hotkey", icon='CANCEL')
        box.prop(self, 'emergency_stop_enabled')
        if self.emergency_stop_enabled:
            self._draw_stop_hotkey_widget(box, bpy.context)
            box.label(text="Works in any capture mode — stops "
                           "recording immediately, like pressing Stop.",
                      icon='INFO')

        # Auto-Hide Reference Blueprints (mode-agnostic)
        box = layout.box()
        box.label(text="Reference Blueprints", icon='IMAGE_REFERENCE')
        if context.scene:
            box.prop(context.scene, 'tlx_auto_hide_blueprints')
            if context.scene.tlx_auto_hide_blueprints:
                box.label(text="Per-Empty: Object Data Properties > "
                               "Show In: Perspective / Orthographic.",
                          icon='INFO')
        else:
            box.label(text="No active scene.", icon='ERROR')

        # Hide Collection During Capture (mode-agnostic)
        box = layout.box()
        box.label(text="Hide Collection During Capture", icon='OUTLINER_COLLECTION')
        if context.scene:
            box.prop(context.scene, 'tlx_hide_collection_enabled')
            if context.scene.tlx_hide_collection_enabled:
                box.prop(context.scene, 'tlx_hide_collection')
                if not context.scene.tlx_hide_collection:
                    box.label(text="Pick a collection — nothing is "
                                   "hidden until you do.", icon='ERROR')
        else:
            box.label(text="No active scene.", icon='ERROR')

        # Viewport Shading (Window)
        box = layout.box()
        box.label(text="Viewport Shading (Window Mode)", icon='SHADING_RENDERED')
        box.prop(self, 'lock_shading')
        if self.lock_shading:
            col = box.column(align=True)
            col.prop(self, 'shading_type')
            col.prop(self, 'xray')
            col.prop(self, 'disable_shadows')

        # Camera List Render
        box = layout.box()
        box.label(text="Camera List Render Settings (SOLID)", icon='CAMERA_DATA')
        box.prop(self, 'camera_disable_shadows')

        # Wireframe
        box = layout.box()
        box.label(text="Wireframe Rendering (Freestyle, Camera List)", icon='MOD_WIREFRAME')
        col = box.column(align=True)
        col.label(text="Line Settings:")
        row = col.row(align=True)
        row.prop(self, 'wireframe_color', text="")
        row.prop(self, 'wireframe_thickness', text="Thickness")
        box.separator()
        col = box.column(align=True)
        col.label(text="Background:")
        col.prop(self, 'wireframe_transparent_bg', text="Transparent Background")
        sub = col.column(align=True)
        sub.enabled = not self.wireframe_transparent_bg
        row = sub.row(align=True)
        row.prop(self, 'wireframe_bg_color', text="")
        row.prop(self, 'wireframe_bg_strength', text="Strength", slider=True)
        box.separator()
        col = box.column(align=True)
        col.label(text="Object Color:")
        col.prop(self, 'wireframe_use_object_colors', text="Use Object Viewport Colors")
        if not self.wireframe_use_object_colors:
            row = col.row(align=True)
            row.label(text="Default Color:")
            row.prop(self, 'wireframe_default_object_color', text="")
        box.separator()
        col = box.column(align=True)
        col.label(text="Render Settings:")
        col.prop(self, 'wireframe_disable_shadows')
        col.prop(self, 'wireframe_render_engine', text="Engine")
        col.prop(self, 'show_freestyle_marks_in_edit')

        # Performance
        box = layout.box()
        box.label(text="Performance", icon='PREFERENCES')
        box.prop(self, 'perf_hide_overlays_during_capture')
        box.prop(self, 'perf_depsgraph_suppress_ms')
        col = box.column(align=True)
        col.label(text="Camera List Performance:")
        col.prop(self, 'camera_round_robin')
        col.prop(self, 'camera_max_per_tick')
        col.prop(self, 'camera_lock_interface')
        col.prop(self, 'camera_low_overhead')
        col.prop(self, 'camera_png_compress')

        # Camera List — Action-Count Trigger
        box = layout.box()
        box.label(text="Camera List: Action-Count Trigger", icon='CAMERA_DATA')
        box.prop(self, 'camera_capture_on_action_count')
        if self.camera_capture_on_action_count:
            sub = box.box()
            sub.prop(self, 'action_count_batch')
            sub.prop(self, 'action_count_debounce')
            sub.label(text="Counts completed operators (transform, "
                           "extrude, sculpt stroke...), not raw redraws.",
                      icon='INFO')
            sub.label(text="Per-camera interval overrides still apply "
                           "on top of this.", icon='INFO')

        # Video Export
        box = layout.box()
        box.label(text='MP4 Output', icon='FILE_MOVIE')
        box.prop(self, 'mp4_output_mode', expand=True)
        row = box.row()
        row.enabled = (self.mp4_output_mode == 'CUSTOM_DIR')
        row.prop(self, 'mp4_custom_dir')

        # Window Capture
        box = layout.box()
        box.label(text="Window Capture Settings", icon='WINDOW')
        box.prop(self, 'window_capture_scope')
        box.prop(self, 'window_stabilize_view')
        if self.window_stabilize_view and self.window_capture_scope != 'FULL':
            box.label(text="Clean frames, but a brief viewport flicker "
                           "on every capture (hover the checkbox above "
                           "for details).", icon='INFO')
        box.prop(self, 'window_async_capture')
        if self.window_async_capture:
            box.prop(self, 'window_async_delay_ms')
        box.separator()
        scene = context.scene
        if scene:
            box.prop(scene, 'tlx_window_stealth_camera_enabled')
            if scene.tlx_window_stealth_camera_enabled:
                sub = box.box()
                sub.prop(scene, 'tlx_window_stealth_camera')
                if not scene.tlx_window_stealth_camera:
                    sub.label(text="Pick a camera — captures fall back to "
                                   "your actual view until you do.",
                              icon='ERROR')
                else:
                    sub.label(text="Viewport briefly snaps to this camera "
                                   "each capture, then returns to your view.",
                              icon='INFO')
                    sub.prop(scene, 'tlx_window_stealth_hide_passepartout')
                    sub.prop(scene, 'tlx_window_stealth_crop_to_camera')
        box.separator()
        row = box.row()
        row.enabled = not self.window_capture_on_action_count
        row.prop(self, 'window_capture_on_input_only')
        box.prop(self, 'window_input_suppress_ms')
        box.separator()
        row = box.row()
        row.enabled = not self.window_capture_on_input_only
        row.prop(self, 'window_capture_on_action_count')
        if self.window_capture_on_action_count:
            sub = box.box()
            sub.prop(self, 'action_count_batch')
            sub.prop(self, 'action_count_debounce')
            sub.label(text="Counts completed operators (transform, "
                           "extrude, sculpt stroke...), not raw redraws.",
                      icon='INFO')
        box.separator()
        box.prop(self, 'window_idle_diff')
        if self.window_idle_diff:
            row = box.row(align=True)
            row.prop(self, 'window_idle_threshold')
            row.prop(self, 'window_idle_downscale')

        # Debug
        box = layout.box()
        box.label(text="Developer / Debug", icon='CONSOLE')
        box.prop(self, 'show_debug_panel')


# ========================================================================================
# Registration
# ========================================================================================

classes = (TLX_AddonPrefs,)


def register():
    logger.info("Registering preferences (v2.6.2: show_recording_header toggle)")
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except Exception as e:
            logger.error(f"Failed to register {cls.__name__}: {e}")


def unregister():
    logger.info("Unregistering preferences")
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception as e:
            logger.warning(f"Failed to unregister {cls.__name__}: {e}")
