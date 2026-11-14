"""
Emergency Stop hotkey — works in ANY capture mode, not just Window.

Timelapse X already has an internal _emergency_stop() in
operators/recording.py, but that's only ever called automatically when
something goes wrong (e.g. the session directory disappears mid-timer
tick) — there was no user-facing way to just hit a key and stop right
now. This adds one, built the same way as the capture hotkey
(operators/hotkey.py): a normal, fully rebindable Blender addon keymap
item, not a modal operator.

Unlike the capture hotkey (Window mode only) and the action-count
trigger (also Window mode only), this is deliberately mode-agnostic —
recording in CAMERA_LIST or wireframe modes can just as easily need a
panic button, so poll() only checks that a recording is actually running
and the feature is enabled, not the capture mode.

Stopping is delegated to bpy.ops.tlx.record(start=False) — the exact
same code path the normal Stop button uses — rather than reimplementing
cleanup here, so timer teardown, action-count reset, header clearing,
depsgraph handler removal, etc. all happen exactly as they should.
"""

import bpy
import logging
from bpy.types import Operator

from ..state_manager import StateManager
from .. import utils

logger = logging.getLogger(__name__)


# Starting point only — Ctrl+Shift+F10. Deliberately different from the
# capture hotkey's default (Ctrl+Shift+F9) so the two don't collide.
# Fully reassignable afterwards.
DEFAULT_HOTKEY_TYPE  = 'F10'
DEFAULT_HOTKEY_CTRL  = True
DEFAULT_HOTKEY_SHIFT = True
DEFAULT_HOTKEY_ALT   = False


# ============================================================================
# Operator
# ============================================================================

class TLX_OT_emergency_stop_hotkey(Operator):
    """Immediately stop the current recording, in any capture mode (bound to a user-defined hotkey)"""

    bl_idname  = 'tlx.emergency_stop_hotkey'
    bl_label   = 'Timelapse X: Emergency Stop Recording'
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        # False = "not claimed", event passes straight through, so this
        # never steals input except while a recording is actually live.
        prefs = utils.get_addon_preferences()
        if not (prefs and getattr(prefs, 'emergency_stop_enabled', True)):
            return False
        return StateManager().recording

    def execute(self, context):
        try:
            result = bpy.ops.tlx.record(start=False)
        except Exception as e:
            logger.error(f"Emergency stop failed: {e}", exc_info=True)
            self.report({'ERROR'}, "Timelapse X: emergency stop failed")
            return {'CANCELLED'}

        if 'CANCELLED' in result:
            return {'CANCELLED'}

        logger.info("Emergency stop hotkey triggered")
        self.report({'WARNING'}, "Timelapse X: recording stopped (emergency hotkey)")
        return {'FINISHED'}


# ============================================================================
# Keymap  (fully user-customizable — Preferences > Keymap > Window)
# ============================================================================

_addon_keymaps = []  # [(km, kmi), ...] — items live in wm.keyconfigs.addon


def register_keymap():
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if not kc:
        logger.warning("No addon keyconfig available; emergency-stop hotkey not registered")
        return

    km = kc.keymaps.new(name='Window', space_type='EMPTY', region_type='WINDOW')

    kmi = km.keymap_items.new(
        TLX_OT_emergency_stop_hotkey.bl_idname,
        type=DEFAULT_HOTKEY_TYPE, value='PRESS',
        ctrl=DEFAULT_HOTKEY_CTRL, shift=DEFAULT_HOTKEY_SHIFT, alt=DEFAULT_HOTKEY_ALT,
    )
    _addon_keymaps.append((km, kmi))
    logger.info("Emergency-stop hotkey registered: Ctrl+Shift+F10 (rebindable)")


def unregister_keymap():
    for km, kmi in _addon_keymaps:
        try:
            km.keymap_items.remove(kmi)
        except Exception as e:
            logger.warning(f"Failed to remove emergency-stop keymap item: {e}")
    _addon_keymaps.clear()


def get_keymap_item():
    """
    Return the LIVE (user-editable) keymap item — looked up by idname in
    wm.keyconfigs.user, the same lookup Blender's own Preferences >
    Keymap page uses (see hotkey.py for why this matters more than it
    sounds: matching by KeyMapItem.id can resolve to a stale entry).
    """
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.user
    if not kc:
        return None, None
    km = kc.keymaps.get('Window')
    if not km:
        return None, None
    for kmi in km.keymap_items:
        if kmi.idname == TLX_OT_emergency_stop_hotkey.bl_idname:
            return km, kmi
    return None, None


# ============================================================================
# Registration
# ============================================================================

classes = (TLX_OT_emergency_stop_hotkey,)


def register():
    logger.info("Registering emergency-stop hotkey operator + keymap")
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except Exception as e:
            logger.error(f"Failed to register {cls.__name__}: {e}")
    try:
        register_keymap()
    except Exception as e:
        logger.error(f"Emergency-stop keymap registration failed: {e}")


def unregister():
    logger.info("Unregistering emergency-stop hotkey operator + keymap")
    try:
        unregister_keymap()
    except Exception as e:
        logger.warning(f"Emergency-stop keymap unregistration failed: {e}")
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception as e:
            logger.warning(f"Failed to unregister {cls.__name__}: {e}")
