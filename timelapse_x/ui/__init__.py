"""
UI package for Timelapse X.

Uses logger.debug() rather than print(), so nothing is printed on every
Blender startup or add-on reload.
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
