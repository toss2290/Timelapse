"""
Hotkey-triggered screenshot capture — Window mode.

Adds a fully user-rebindable keyboard shortcut that fires a single,
immediate window screenshot. It pairs with the existing
`window_capture_on_input_only` preference (see preferences.py /
ui/panels.py): when that preference is enabled, the periodic capture
timer skips WINDOW-mode captures entirely (operators/recording.py,
_tlx_capture_tick) — so this hotkey becomes the *only* way a frame gets
written. In other words: "capture only when I press a key".

The hotkey is NOT hard-coded. It is registered as a normal Blender addon
keymap item (wm.keyconfigs.addon), exactly like a built-in shortcut, so
the user can rebind it to any key / modifier combination they want —
either from Blender's own Preferences > Keymap search ("Timelapse X:
Capture Screenshot Now"), or from the small inline keymap widget drawn
directly in the Timelapse X sidebar panel (see get_keymap_item() below,
used by ui/panels.py).
"""

import bpy
import os
import logging
from bpy.types import Operator

from ..state_manager import StateManager
from .. import utils

logger = logging.getLogger(__name__)

try:
    from ..capture import window as window_capture
except ImportError as e:
    window_capture = None
    logger.error(f"Capture import failed: {e}")


# Starting point only — Ctrl+Shift+F9. Chosen to avoid clashing with
# Blender's default keymap. Fully reassignable by the user afterwards;
# nothing in this addon depends on it staying this exact combination.
DEFAULT_HOTKEY_TYPE  = 'F9'
DEFAULT_HOTKEY_CTRL  = True
DEFAULT_HOTKEY_SHIFT = True
DEFAULT_HOTKEY_ALT   = False


# ============================================================================
# Operator
# ============================================================================

class TLX_OT_hotkey_capture(Operator):
    """Capture a single Window-mode screenshot right now (bound to a user-defined hotkey)"""

    bl_idname  = 'tlx.hotkey_capture'
    bl_label   = 'Timelapse X: Capture Screenshot Now'
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        # False here means Blender treats the shortcut as "not claimed" and
        # passes the key event straight through to whatever's underneath —
        # so this hotkey never steals input outside of an active,
        # input-only, WINDOW-mode recording.
        mgr = StateManager()
        if not mgr.recording:
            return False
        snap = mgr.get_recording_snapshot()
        if snap.capture_mode != 'WINDOW' or snap.paused:
            return False
        prefs = utils.get_addon_preferences()
        return bool(prefs and prefs.window_capture_on_input_only)

    def execute(self, context):
        if utils.is_headless() or not window_capture:
            return {'CANCELLED'}

        mgr  = StateManager()
        snap = mgr.get_recording_snapshot()

        base_dir = snap.session_dir
        if not base_dir or not os.path.isdir(bpy.path.abspath(base_dir)):
            self.report({'WARNING'}, "Timelapse X: session directory missing")
            return {'CANCELLED'}

        # force_save=True: bypass idle-diff / cooldown suppression — a
        # deliberate keypress always produces a frame.
        try:
            window_capture.capture_window_async(context, base_dir, force_save=True)
        except Exception as e:
            logger.error(f"Hotkey capture failed: {e}", exc_info=True)
            self.report({'ERROR'}, "Timelapse X: capture failed")
            return {'CANCELLED'}

        logger.info("Hotkey capture triggered")
        return {'FINISHED'}


# ============================================================================
# Keymap  (fully user-customizable — Preferences > Keymap > Window)
# ============================================================================

_addon_keymaps = []  # [(km, kmi), ...] — items live in wm.keyconfigs.addon


def register_keymap():
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if not kc:
        # Happens when running with --factory-startup in some batch
        # contexts; keymaps just won't be available, capture-by-hotkey
        # simply won't be offered.
        logger.warning("No addon keyconfig available; hotkey not registered")
        return

    # kc.keymaps.new() returns the existing 'Window' keymap if one is
    # already present in the addon keyconfig (shared across addons), or
    # creates it — either way it's safe to call directly.
    km = kc.keymaps.new(name='Window', space_type='EMPTY', region_type='WINDOW')

    kmi = km.keymap_items.new(
        TLX_OT_hotkey_capture.bl_idname,
        type=DEFAULT_HOTKEY_TYPE, value='PRESS',
        ctrl=DEFAULT_HOTKEY_CTRL, shift=DEFAULT_HOTKEY_SHIFT, alt=DEFAULT_HOTKEY_ALT,
    )
    _addon_keymaps.append((km, kmi))
    logger.info("Hotkey registered: Ctrl+Shift+F9 (rebindable in Preferences > Keymap)")


def unregister_keymap():
    for km, kmi in _addon_keymaps:
        try:
            km.keymap_items.remove(kmi)
        except Exception as e:
            logger.warning(f"Failed to remove hotkey keymap item: {e}")
    _addon_keymaps.clear()


def get_keymap_item():
    """
    Return the LIVE (user-editable) copy of the keymap item, i.e. the one
    Blender actually reads at runtime when matching key events, so UI code
    can draw an editable widget for it that really takes effect.

    IMPORTANT: this looks the item up by operator idname inside
    wm.keyconfigs.user — the same keyconfig (and the same lookup strategy)
    Blender's own Preferences > Keymap page uses. An earlier version of
    this addon matched by KeyMapItem.id via from_id(), which can resolve
    to a stale/incorrect entry once the user keyconfig has been synced or
    saved to a startup file — that's what made the in-panel widget look
    editable but not actually change the active binding. Matching by
    idname avoids that entirely. Returns (None, None) if unavailable.
    """
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.user
    if not kc:
        return None, None
    km = kc.keymaps.get('Window')
    if not km:
        return None, None
    for kmi in km.keymap_items:
        if kmi.idname == TLX_OT_hotkey_capture.bl_idname:
            return km, kmi
    return None, None


def _cleanup_duplicate_keymap_items():
    """
    Defensive cleanup: if a previous session (e.g. an earlier build of
    this addon, or an unclean reload) left more than one 'Window' keymap
    item bound to tlx.hotkey_capture in the user keyconfig, only the
    FIRST one is reachable/editable from get_keymap_item(), and any extra
    ones keep firing on their old key (e.g. the Ctrl+Shift+F9 default)
    even after the user rebinds the one shown in the panel. Remove all
    but the first match so there's exactly one, single source of truth.
    """
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.user
    if not kc:
        return
    km = kc.keymaps.get('Window')
    if not km:
        return
    matches = [kmi for kmi in km.keymap_items
               if kmi.idname == TLX_OT_hotkey_capture.bl_idname]
    if len(matches) <= 1:
        return
    logger.warning(f"Found {len(matches)} duplicate hotkey bindings for "
                    f"{TLX_OT_hotkey_capture.bl_idname}; removing extras")
    for kmi in matches[1:]:
        try:
            km.keymap_items.remove(kmi)
        except Exception as e:
            logger.warning(f"Failed to remove duplicate keymap item: {e}")


# ============================================================================
# Registration
# ============================================================================

classes = (TLX_OT_hotkey_capture,)


def register():
    logger.info("Registering hotkey capture operator + keymap")
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except Exception as e:
            logger.error(f"Failed to register {cls.__name__}: {e}")
    try:
        register_keymap()
    except Exception as e:
        logger.error(f"Keymap registration failed: {e}")
    try:
        _cleanup_duplicate_keymap_items()
    except Exception as e:
        logger.warning(f"Duplicate keymap cleanup failed: {e}")


def unregister():
    logger.info("Unregistering hotkey capture operator + keymap")
    try:
        unregister_keymap()
    except Exception as e:
        logger.warning(f"Keymap unregistration failed: {e}")
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception as e:
            logger.warning(f"Failed to unregister {cls.__name__}: {e}")
