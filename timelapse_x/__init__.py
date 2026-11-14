"""
Timelapse X — add-on entry point.

Registers and unregisters every sub-package in dependency order. The add-on
version is defined only by bl_info["version"] below; release history is in
CHANGELOG.md.
"""

bl_info = {
    "name": "Timelapse X",
    "author": "Toss",
    "version": (2, 6, 10),
    "blender": (5, 1, 0),
    "location": "View3D > Sidebar (N) > Timelapse X",
    "description": "Professional timelapse capture",
    "category": "System",
    "warning": "",
}

import bpy
import logging

logger = logging.getLogger(__name__)

_VERSION = ".".join(str(n) for n in bl_info["version"])

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


def register():
    logger.info(f"Timelapse X v{_VERSION}: registering")

    _safe_register(constants,      "constants")
    _safe_register(state_manager,  "state_manager")

    try:
        from .state_manager import StateManager
        mgr = StateManager()
        if mgr.recording:
            logger.warning("Stuck recording state detected — resetting")
            mgr.force_reset()
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
    except Exception as e:
        logger.error(f"scene properties registration failed: {e}")

    try:
        clean_window.register_window_properties()
    except Exception as e:
        logger.error(f"window properties registration failed: {e}")

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
    except Exception as e:
        logger.warning(f"Settings validation warning: {e}")

    op_count = sum(1 for n in dir(bpy.types) if n.startswith('TLX_OT_'))
    logger.info(f"Timelapse X v{_VERSION}: registered ({op_count} operators)")


def unregister():
    logger.info("Timelapse X: unregistering")

    # Stop any recording first (timer, headers, depsgraph handler, scene flag)
    try:
        from .operators.recording import stop_recording_session
        stop_recording_session(getattr(bpy.context, 'scene', None))
    except Exception as e:
        logger.warning(f"Stopping recording failed: {e}")

    try:
        from .state_manager import StateManager
        StateManager().cleanup()
    except Exception as e:
        logger.warning(f"Cleanup warning: {e}")

    try:
        clean_window.unregister_window_properties()
    except Exception as e:
        logger.warning(f"window properties unregister failed: {e}")

    try:
        properties.unregister_scene_properties()
    except Exception as e:
        logger.warning(f"scene properties unregister failed: {e}")

    for module in reversed([clean_window, ui, capture, operators, properties,
                             preferences, utils, progress, error_messages,
                             error_handling, state_manager, constants]):
        try:
            module.unregister()
        except Exception as e:
            logger.warning(f"{module.__name__.split('.')[-1]} unregister failed: {e}")

    try:
        if bpy.context and hasattr(bpy.context, 'scene') and bpy.context.scene:
            for camera_item in bpy.context.scene.tlx_cameras:
                for key in [k for k in camera_item.keys()
                            if k.startswith("_tlx_")]:
                    del camera_item[key]
    except Exception as e:
        logger.warning(f"Camera metadata cleanup failed: {e}")

    logger.info("Timelapse X: unregistered")


def _safe_register(module, name: str):
    try:
        module.register()
    except Exception as e:
        logger.error(f"{name} registration failed: {e}")


if __name__ == "__main__":
    try:
        unregister()
    except Exception:
        pass
    register()
