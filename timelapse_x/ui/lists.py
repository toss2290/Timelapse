"""
UI Lists for Timelapse X addon.

Fix (v2.5.4): removed TLX_UL_cameras_compact — it was registered but
never referenced by any panel (panels.py always uses 'TLX_UL_cameras').
"""

import bpy
import logging
from bpy.types import UIList

logger = logging.getLogger(__name__)


class TLX_UL_cameras(UIList):
    """
    UI List for displaying cameras with visual indicators for active overrides:
    interval ⏱ | shading 🎨 | image format 🖼 | performance ⚙
    """

    bl_idname = "TLX_UL_cameras"

    def draw_item(self, context, layout, data, item, icon,
                  active_data, active_propname, index):
        if not item:
            return

        camera = item.camera
        row    = layout.row(align=True)

        # Camera icon
        row.label(icon='CAMERA_DATA' if camera else 'ERROR')

        # Override indicators
        row.prop(item, 'use_interval_override', text='', emboss=False,
                 icon='TIME'            if item.use_interval_override else 'BLANK1')
        row.prop(item, 'use_shading_override',  text='', emboss=False,
                 icon='SHADING_RENDERED'if item.use_shading_override  else 'BLANK1')
        row.prop(item, 'use_image_override',    text='', emboss=False,
                 icon='IMAGE_DATA'      if item.use_image_override    else 'BLANK1')
        row.prop(item, 'perf_override',         text='', emboss=False,
                 icon='PREFERENCES'    if item.perf_override          else 'BLANK1')

        # Camera name
        row.label(text=camera.name if camera else '<None>')


# ========================================================================================
# Registration
# ========================================================================================

classes = (TLX_UL_cameras,)


def register():
    logger.info("Registering UI lists (v2.5.4: compact list removed)")
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except Exception as e:
            logger.error(f"Failed to register {cls.__name__}: {e}")


def unregister():
    logger.info("Unregistering UI lists")
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception as e:
            logger.warning(f"Failed to unregister {cls.__name__}: {e}")
