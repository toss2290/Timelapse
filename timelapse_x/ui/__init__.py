"""
UI package for Timelapse X addon.

Fix (v2.5.4): replaced print() calls with logger.debug() —
print() at module level fires on every Blender startup; inside
register() it fires every reload. logger.debug() is silent unless
debug logging is enabled.
"""

import bpy
import logging

logger = logging.getLogger(__name__)

from . import lists
from . import panels


def register():
    logger.info("Registering UI modules")
    lists.register()
    panels.register()

    if 'TLX_PT_panel' in dir(bpy.types):
        logger.debug("UI: Panel registered successfully")
    else:
        logger.warning("UI: Panel not found in bpy.types after registration!")


def unregister():
    logger.info("Unregistering UI modules")
    panels.unregister()
    lists.unregister()
