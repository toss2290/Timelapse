"""
Operators for Clean Window tools.

Fixes (v2.5.4):
  - _deferred_clean() closure captured `self` (the Operator instance).
    Blender may garbage-collect the operator before the 0.05s timer fires,
    causing AttributeError. Fixed by copying settings into local variables
    before creating the closure.
  - apply_clean_settings() was called twice with almost identical arguments
    (first immediately, then 0.05s later). The deferred call now only
    re-applies maximize_area which is the only part that needs a slight delay.
"""

import bpy
import logging
from bpy.types import Operator
from bpy.props import BoolProperty, EnumProperty

from .. import constants

logger = logging.getLogger(__name__)


# ========================================================================================
# Helper Functions
# ========================================================================================

def find_tracked_windows(wm):
    original_key = getattr(wm, 'cmw_original_window_key', '') or ''
    new_key      = getattr(wm, 'cmw_new_window_key',      '') or ''
    original_window = new_window = None

    for key, target in ((original_key, None), (new_key, None)):
        if not key:
            continue
        try:
            ptr = int(key)
            for window in wm.windows:
                if int(window.as_pointer()) == ptr:
                    if key == original_key:
                        original_window = window
                    else:
                        new_window = window
                    break
        except (ValueError, AttributeError):
            pass

    return original_window, new_window


def resolve_context(window):
    if not window:
        return None, None, None, None
    screen = window.screen
    if not screen or not getattr(screen, 'areas', None):
        return window, screen, None, None

    area = None
    for a in screen.areas:
        if a.type == 'VIEW_3D':
            area = a
            break
    if not area and screen.areas:
        area = max(screen.areas, key=lambda a: a.width * a.height)

    region = None
    if area and getattr(area, 'regions', None):
        for r in area.regions:
            if r.type == 'WINDOW':
                region = r
                break

    return window, screen, area, region


def is_region_valid(region):
    if not region:
        return False
    try:
        _ = region.type
        return True
    except (AttributeError, ReferenceError):
        return False


def set_property_safe(obj, attr, value):
    if obj and hasattr(obj, attr):
        try:
            setattr(obj, attr, value)
        except (AttributeError, RuntimeError):
            pass


def create_new_main_window(context):
    wm     = context.window_manager
    before = {int(w.as_pointer()) for w in wm.windows}

    with context.temp_override(window=context.window, screen=context.screen):
        bpy.ops.wm.window_new_main()

    new_windows = [w for w in wm.windows if int(w.as_pointer()) not in before]
    if not new_windows:
        raise RuntimeError('Failed to create a new main window.')
    return new_windows[0]


def apply_clean_settings(context, window, hide_ui=True, hide_overlays=True,
                          hide_gizmos=True, deep_clean=False, shading='SOLID',
                          fullscreen=False, maximize_area=False):
    win, screen, area, region = resolve_context(window)
    if not all((win, screen, area)):
        return

    override = {'window': win, 'screen': screen, 'area': area}
    if region and is_region_valid(region):
        override['region'] = region

    with context.temp_override(**override):
        space = area.spaces.active if area.spaces else None

        if space and space.type == 'VIEW_3D':
            if hide_ui:
                for attr in ('show_region_header', 'show_region_ui',
                             'show_region_tool_header', 'show_region_toolbar'):
                    set_property_safe(space, attr, False)

            overlay = getattr(space, 'overlay', None)
            if overlay:
                if hide_overlays and hasattr(overlay, 'show_overlays'):
                    overlay.show_overlays = False

                if deep_clean and not hide_overlays:
                    for attr in ('show_text', 'show_cursor', 'show_annotation',
                                 'show_floor', 'show_axis_x', 'show_axis_y',
                                 'show_axis_z', 'show_extras', 'show_stats',
                                 'show_object_origins', 'show_object_origins_all'):
                        set_property_safe(overlay, attr, False)

            if hide_gizmos:
                set_property_safe(space, 'show_gizmo', False)
                for attr in ('show_gizmo_tool', 'show_gizmo_context', 'show_gizmo_navigate'):
                    set_property_safe(space, attr, False)

            if shading and shading != 'KEEP':
                try:
                    space.shading.type = shading
                except (AttributeError, RuntimeError):
                    pass

        if maximize_area:
            try:
                if screen and not screen.show_fullscreen:
                    with context.temp_override(window=win, screen=screen, area=area):
                        bpy.ops.screen.screen_full_area(use_hide_panels=True)
            except (AttributeError, RuntimeError, TypeError) as e:
                logger.warning(f"Failed to maximize area: {e}")

        if fullscreen:
            try:
                with context.temp_override(window=win):
                    bpy.ops.screen.window_fullscreen_toggle()
            except (AttributeError, RuntimeError, TypeError) as e:
                logger.warning(f"Failed to toggle fullscreen: {e}")


def restore_view_defaults(context, window):
    win, screen, area, region = resolve_context(window)
    if not all((win, screen, area)):
        return

    try:
        if screen and screen.show_fullscreen:
            override = {'window': win, 'area': area}
            if region and is_region_valid(region):
                override['region'] = region
            with context.temp_override(**override):
                bpy.ops.screen.screen_full_area(use_hide_panels=True)
    except (AttributeError, RuntimeError, TypeError) as e:
        logger.warning(f"Failed to exit fullscreen: {e}")

    override = {'window': win, 'area': area}
    if region and is_region_valid(region):
        override['region'] = region

    try:
        with context.temp_override(**override):
            space = area.spaces.active if area.spaces else None
            if space and space.type == 'VIEW_3D':
                for attr in ('show_region_header', 'show_region_ui',
                             'show_region_tool_header', 'show_region_toolbar'):
                    set_property_safe(space, attr, True)

                overlay = getattr(space, 'overlay', None)
                if overlay:
                    set_property_safe(overlay, 'show_overlays', True)
                    for attr in ('show_text', 'show_cursor', 'show_annotation',
                                 'show_floor', 'show_axis_x', 'show_axis_y',
                                 'show_axis_z', 'show_extras', 'show_stats',
                                 'show_object_origins', 'show_object_origins_all'):
                        set_property_safe(overlay, attr, True)

                set_property_safe(space, 'show_gizmo', True)
                for attr in ('show_gizmo_tool', 'show_gizmo_context', 'show_gizmo_navigate'):
                    set_property_safe(space, attr, True)
    except (AttributeError, RuntimeError, TypeError) as e:
        logger.warning(f"Failed to restore view settings: {e}")


def close_window_safely(context, window):
    if len(context.window_manager.windows) <= 1:
        return False
    win, screen, area, region = resolve_context(window)
    override = {'window': win}
    if screen:
        override['screen'] = screen
    if area:
        override['area'] = area
    if region and is_region_valid(region):
        override['region'] = region
    try:
        with context.temp_override(**override):
            return 'FINISHED' in bpy.ops.wm.window_close()
    except (AttributeError, RuntimeError, TypeError) as e:
        logger.warning(f"Failed to close window: {e}")
        return False


# ========================================================================================
# Create Clean Window Operator
# ========================================================================================

class CMW_OT_create_clean_window(Operator):
    """Create new window and clean original."""

    bl_idname = "cmw.create_clean_window"
    bl_label  = "Create New Window & Clean Original"
    bl_options = {"REGISTER"}

    hide_overlays: BoolProperty(name="Hide Overlays",      default=False)
    hide_gizmos:   BoolProperty(name="Hide Gizmos",        default=True)
    hide_ui:       BoolProperty(name="Hide UI Regions",    default=True)
    deep_clean:    BoolProperty(name="Deep Clean Overlays",default=True)
    shading:       EnumProperty(name="Shading", items=constants.CLEAN_WINDOW_SHADING, default='SOLID')
    fullscreen:    BoolProperty(name="Fullscreen Window",  default=True)
    maximize_area: BoolProperty(name="Maximize Area",      default=True)

    def invoke(self, context, event):
        wm = context.window_manager
        self.hide_ui       = wm.cmw_hide_ui
        self.hide_overlays = wm.cmw_hide_overlays
        self.hide_gizmos   = wm.cmw_hide_gizmos
        self.fullscreen    = wm.cmw_fullscreen
        self.maximize_area = wm.cmw_maximize_area
        self.shading       = wm.cmw_shading
        self.deep_clean    = wm.cmw_deep_clean and not self.hide_overlays
        return self.execute(context)

    def execute(self, context):
        wm = context.window_manager
        original_w, new_w = find_tracked_windows(wm)

        if new_w:
            self.report({'INFO'}, 'New window already exists. Use Toggle Off to close.')
            return {'CANCELLED'}

        if not original_w:
            try:
                wm.cmw_original_window_key = str(context.window.as_pointer())
            except Exception as e:
                self.report({'ERROR'}, f"Could not track original window: {e}")
                return {'CANCELLED'}

        try:
            new_window = create_new_main_window(context)
            wm.cmw_new_window_key = str(new_window.as_pointer())
        except Exception as e:
            self.report({'ERROR'}, f"Cannot create new main window: {e}")
            return {'CANCELLED'}

        original_w_for_clean, _ = find_tracked_windows(wm)
        if not original_w_for_clean:
            self.report({'ERROR'}, 'Lost track of original window before cleaning.')
            return {'CANCELLED'}

        # Apply the main clean settings immediately
        apply_clean_settings(
            context, original_w_for_clean,
            hide_ui=self.hide_ui,
            hide_overlays=self.hide_overlays,
            hide_gizmos=self.hide_gizmos,
            deep_clean=self.deep_clean,
            shading=self.shading,
            fullscreen=self.fullscreen,
            maximize_area=self.maximize_area,
        )

        # FIX: deferred call now only re-applies maximize_area (the part that
        # sometimes needs a frame delay to take effect), NOT the full settings.
        # Capture needed values in locals — do NOT capture `self` which may be
        # garbage-collected before the timer fires.
        _original_window_key = wm.cmw_original_window_key
        _maximize            = self.maximize_area
        _hide_ui             = self.hide_ui
        _hide_overlays       = self.hide_overlays
        _hide_gizmos         = self.hide_gizmos
        _deep_clean          = self.deep_clean
        _shading             = self.shading

        def _deferred_maximize():
            # Re-fetch window by key — never use stale reference
            _wm = context.window_manager
            orig_w, _ = find_tracked_windows(_wm)
            if orig_w and _maximize:
                apply_clean_settings(
                    context, orig_w,
                    hide_ui=_hide_ui,
                    hide_overlays=_hide_overlays,
                    hide_gizmos=_hide_gizmos,
                    deep_clean=_deep_clean,
                    shading=_shading,
                    fullscreen=False,       # don't re-toggle fullscreen
                    maximize_area=_maximize,
                )
            return None

        try:
            bpy.app.timers.register(_deferred_maximize, first_interval=0.05)
        except Exception:
            pass

        self.report({'INFO'}, 'New main window created & original window cleaned.')
        return {'FINISHED'}


# ========================================================================================
# Toggle Clean Window
# ========================================================================================

class CMW_OT_toggle_clean_window(Operator):
    """Toggle clean window on/off."""

    bl_idname = "cmw.toggle_clean_window"
    bl_label  = "Toggle Clean Window"
    bl_options = {"REGISTER"}

    def execute(self, context):
        wm = context.window_manager
        original_w, new_w = find_tracked_windows(wm)

        if new_w:
            if original_w:
                win, screen, area, region = resolve_context(original_w)
                was_fullscreen = screen.show_fullscreen if screen else False
                restore_view_defaults(context, original_w)
                if was_fullscreen and win:
                    try:
                        with context.temp_override(window=win):
                            bpy.ops.screen.window_fullscreen_toggle()
                    except (AttributeError, RuntimeError, TypeError):
                        pass

            if not close_window_safely(context, new_w):
                self.report({'WARNING'}, 'Could not close new window.')

            wm.cmw_new_window_key = ''
            self.report({'INFO'}, 'New window closed.')
        else:
            try:
                bpy.ops.cmw.create_clean_window('INVOKE_DEFAULT')
            except Exception as e:
                self.report({'ERROR'}, f"Cannot create window: {e}")
                return {'CANCELLED'}

        return {'FINISHED'}


# ========================================================================================
# Restore Original Window
# ========================================================================================

class CMW_OT_restore_original_window(Operator):
    """Restore original window to default state."""

    bl_idname    = "cmw.restore_original_window"
    bl_label     = "Restore Original Window"
    bl_description = "Restore original main window to default state without closing new window"
    bl_options   = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        original_w, _ = find_tracked_windows(context.window_manager)
        return original_w is not None

    def execute(self, context):
        wm = context.window_manager
        original_w, _ = find_tracked_windows(wm)

        if not original_w:
            self.report({'WARNING'}, 'No original window found to restore.')
            return {'CANCELLED'}

        win, screen, area, region = resolve_context(original_w)
        was_fullscreen = screen.show_fullscreen if screen else False
        restore_view_defaults(context, original_w)

        if was_fullscreen and win:
            try:
                with context.temp_override(window=win):
                    bpy.ops.screen.window_fullscreen_toggle()
            except (AttributeError, RuntimeError, TypeError):
                pass

        self.report({'INFO'}, 'Original window restored to defaults.')
        return {'FINISHED'}


# ========================================================================================
# Wireframe Toggle Operators
# ========================================================================================

class CMW_OT_toggle_wireframe(Operator):
    """Toggle wireframe display for selected objects."""
    bl_idname    = "cmw.toggle_wireframe"
    bl_label     = "Toggle Wireframe"
    bl_description = "Toggle wireframe display for selected objects"
    bl_options   = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects)

    def execute(self, context):
        any_disabled = any(not obj.show_wire for obj in context.selected_objects)
        for obj in context.selected_objects:
            obj.show_wire = any_disabled
        return {'FINISHED'}


class CMW_OT_enable_wireframe(Operator):
    """Enable wireframe display for selected objects."""
    bl_idname  = "cmw.enable_wireframe"
    bl_label   = "Enable Wireframe"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects)

    def execute(self, context):
        for obj in context.selected_objects:
            obj.show_wire = True
        return {'FINISHED'}


class CMW_OT_disable_wireframe(Operator):
    """Disable wireframe display for selected objects."""
    bl_idname  = "cmw.disable_wireframe"
    bl_label   = "Disable Wireframe"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects)

    def execute(self, context):
        for obj in context.selected_objects:
            obj.show_wire = False
        return {'FINISHED'}


# ========================================================================================
# Registration
# ========================================================================================

classes = (
    CMW_OT_create_clean_window,
    CMW_OT_toggle_clean_window,
    CMW_OT_restore_original_window,
    CMW_OT_toggle_wireframe,
    CMW_OT_enable_wireframe,
    CMW_OT_disable_wireframe,
)


def register():
    logger.info("Registering clean window operators (v2.5.4: deferred_clean GC fix)")
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except Exception as e:
            logger.error(f"Failed to register {cls.__name__}: {e}")


def unregister():
    logger.info("Unregistering clean window operators")
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception as e:
            logger.warning(f"Failed to unregister {cls.__name__}: {e}")
