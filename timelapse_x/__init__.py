"""
Timelapse X - Main Init File
Blender 5.1+ compatible, v2.6.5

Change (v2.6.5):
  - unregister(): calls _stop_capture_timer() to kill the persistent
    bpy.app.timers callback. No wm.event_timer_remove() needed anymore.
"""

bl_info = {
    "name": "Timelapse X",
    "author": "Toss",
    "version": (2, 6, 5),
    "blender": (5, 1, 0),
    "location": "View3D > Sidebar (N) > Timelapse X",
    "description": "Professional timelapse capture",
    "category": "System",
    "warning": "",
}

import bpy
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

print("\n" + "="*70)
print("TIMELAPSE X v2.6.5: Starting import...")
print("="*70)

from . import constants
from . import state_manager
from . import error_handling
from . import error_messages
from . import progress
from . import utils
from . import properties
from . import preferences
from . import operators
from . import capture
from . import ui
from . import clean_window

print("✓ All modules imported")
print("="*70 + "\n")


def register():
    print("\n" + "-"*70)
    print("TIMELAPSE X v2.6.5: Registering...")
    print("-"*70)

    _safe_register(constants,      "constants")
    _safe_register(state_manager,  "state_manager")

    try:
        from .state_manager import StateManager
        mgr = StateManager()
        if mgr.recording:
            print("⚠️  STUCK STATE DETECTED — resetting...")
            mgr.force_reset()
        else:
            print("✓ StateManager in clean state")
    except Exception as e:
        logger.warning(f"StateManager verification failed: {e}")

    _safe_register(error_handling, "error_handling")
    _safe_register(error_messages, "error_messages")
    _safe_register(progress,       "progress")
    _safe_register(utils,          "utils")
    _safe_register(preferences,    "preferences")
    _safe_register(properties,     "properties")
    _safe_register(operators,      "operators")
    _safe_register(capture,        "capture")
    _safe_register(ui,             "ui")
    _safe_register(clean_window,   "clean_window")

    try:
        properties.register_scene_properties()
        print("✓ scene properties")
    except Exception as e:
        print(f"✗ scene properties: {e}")

    try:
        clean_window.register_window_properties()
        print("✓ window properties")
    except Exception as e:
        print(f"✗ window properties: {e}")

    try:
        if bpy.context and hasattr(bpy.context, 'scene') and bpy.context.scene:
            scene = bpy.context.scene
            prefs = utils.get_addon_preferences()
            if hasattr(scene, 'tlx_capture_interval'):
                if float(getattr(scene, 'tlx_capture_interval',
                                  constants.DEFAULT_INTERVAL)) <= constants.MIN_INTERVAL:
                    scene.tlx_capture_interval = constants.DEFAULT_INTERVAL
            if prefs:
                if float(getattr(prefs, 'default_interval',
                                  constants.DEFAULT_INTERVAL)) <= constants.MIN_INTERVAL:
                    prefs.default_interval = constants.DEFAULT_INTERVAL
        print("✓ Settings validated")
    except Exception as e:
        logger.warning(f"Settings validation warning: {e}")

    op_count = sum(1 for n in dir(bpy.types) if n.startswith('TLX_OT_'))
    print(f"✓ Operators registered: {op_count}")
    print("-"*70)
    print("TIMELAPSE X v2.6.5: Registration complete!")
    print("-"*70 + "\n")


def unregister():
    print("\n" + "="*70)
    print("TIMELAPSE X: Unregistering...")
    print("="*70)

    # Stop the persistent app timer first
    try:
        from .operators.recording import _stop_capture_timer
        _stop_capture_timer()
        print("✓ Capture timer stopped")
    except Exception as e:
        logger.warning(f"Capture timer stop failed: {e}")

    try:
        from .state_manager import StateManager
        mgr = StateManager()
        mgr.cleanup()
        print("✓ StateManager cleaned up")
    except Exception as e:
        logger.warning(f"Cleanup warning: {e}")

    try:
        clean_window.unregister_window_properties()
        print("✓ window properties")
    except Exception as e:
        print(f"⚠️  window properties: {e}")

    try:
        properties.unregister_scene_properties()
        print("✓ scene properties")
    except Exception as e:
        print(f"⚠️  scene properties: {e}")

    for module in reversed([clean_window, ui, capture, operators, properties,
                             preferences, utils, progress, error_messages,
                             error_handling, state_manager, constants]):
        try:
            module.unregister()
            print(f"✓ {module.__name__.split('.')[-1]}")
        except Exception as e:
            print(f"⚠️  {module.__name__.split('.')[-1]}: {e}")

    try:
        if bpy.context and hasattr(bpy.context, 'scene') and bpy.context.scene:
            for camera_item in bpy.context.scene.tlx_cameras:
                for key in [k for k in camera_item.keys()
                            if k.startswith("_tlx_")]:
                    del camera_item[key]
        print("✓ Camera metadata cleaned")
    except Exception as e:
        print(f"⚠️  Camera metadata: {e}")

    print("="*70)
    print("TIMELAPSE X: Unregistered successfully")
    print("="*70 + "\n")


def _safe_register(module, name: str):
    try:
        module.register()
        print(f"✓ {name}")
    except Exception as e:
        logger.error(f"{name} registration failed: {e}")
        print(f"✗ {name}: {e}")


if __name__ == "__main__":
    try:
        unregister()
    except Exception:
        pass
    register()
