"""
Properties for Clean Window tools.
"""

import bpy
import logging
from bpy.types import WindowManager
from bpy.props import BoolProperty, EnumProperty, StringProperty

logger = logging.getLogger(__name__)


def _update_hide_overlays(self, context):
    if self.cmw_hide_overlays:
        self.cmw_deep_clean = False


def register_window_properties():
    """Register WindowManager properties for clean window settings."""
    logger.info("Registering window manager properties")

    WindowManager.cmw_hide_ui = BoolProperty(name="Hide UI", default=True)
    WindowManager.cmw_hide_overlays = BoolProperty(
        name="Hide Overlays", default=False, update=_update_hide_overlays)
    WindowManager.cmw_hide_gizmos  = BoolProperty(name="Hide Gizmos",  default=True)
    WindowManager.cmw_fullscreen   = BoolProperty(name="Fullscreen",   default=True)
    WindowManager.cmw_maximize_area= BoolProperty(name="Maximize Area",default=True)
    WindowManager.cmw_shading = EnumProperty(
        name="Shading",
        items=[
            ('KEEP',      'Keep',      ''),
            ('SOLID',     'Solid',     ''),
            ('MATERIAL',  'Material',  ''),
            ('RENDERED',  'Rendered',  ''),
            ('WIREFRAME', 'Wireframe', ''),
        ],
        default='SOLID'
    )
    WindowManager.cmw_deep_clean = BoolProperty(name="Deep Clean Overlays", default=True)
    WindowManager.cmw_original_window_key = StringProperty(
        name="Original Window Key", default="", options={'HIDDEN'})
    WindowManager.cmw_new_window_key = StringProperty(
        name="New Window Key", default="", options={'HIDDEN'})


def unregister_window_properties():
    """Unregister WindowManager properties."""
    logger.info("Unregistering window manager properties")
    for prop in ['cmw_hide_ui', 'cmw_hide_overlays', 'cmw_hide_gizmos',
                 'cmw_fullscreen', 'cmw_maximize_area', 'cmw_shading',
                 'cmw_deep_clean', 'cmw_original_window_key', 'cmw_new_window_key']:
        if hasattr(WindowManager, prop):
            try:
                delattr(WindowManager, prop)
            except Exception as e:
                logger.warning(f"Failed to remove property {prop}: {e}")


def register():
    # Properties are registered via register_window_properties() called from
    # clean_window/__init__.py — this stub exists so the module can be iterated
    # uniformly in the register/unregister loops.
    logger.debug("clean_window/properties module registered")


def unregister():
    logger.debug("clean_window/properties module unregistered")
