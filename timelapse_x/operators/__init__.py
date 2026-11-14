"""
Operators package: recording, camera list, video, utility, capture hotkey,
emergency-stop hotkey and action-count operators.
"""

import logging

logger = logging.getLogger(__name__)

try:
    from . import recording
    from . import camera
    from . import video
    from . import utils_ops
    from . import hotkey
    from . import action_count
    from . import stop_hotkey
except ImportError as e:
    logger.warning(f"Some operator modules not available: {e}")


def register():
    """Register all operators."""
    logger.info("Registering operators")
    for module in (recording, camera, video, utils_ops, hotkey, action_count, stop_hotkey):
        try:
            module.register()
        except Exception as e:
            logger.warning(f"Failed to register {module.__name__}: {e}")


def unregister():
    """Unregister all operators (reverse order)."""
    logger.info("Unregistering operators")
    for module in reversed([recording, camera, video, utils_ops, hotkey, action_count, stop_hotkey]):
        try:
            module.unregister()
        except Exception as e:
            logger.warning(f"Failed to unregister {module.__name__}: {e}")
