"""
Capture package: camera-list rendering, window screenshots, per-camera
scheduling, viewport shading control and wireframe rendering.
"""

import logging

logger = logging.getLogger(__name__)

try:
    from . import scheduler
    from . import camera
    from . import window
    from . import shading
    from . import wireframe
except ImportError as e:
    logger.warning(f"Some capture modules not available: {e}")

# ── Registration ─────────────────────────────────────────────────────────────

def register():
    """Register capture modules."""
    logger.info("Registering capture modules")
    for mod in (scheduler, camera, window, shading, wireframe):
        try:
            mod.register()
        except Exception as e:
            logger.warning(f"Failed to register capture.{mod.__name__.split('.')[-1]}: {e}")


def unregister():
    """Unregister capture modules (reverse order)."""
    logger.info("Unregistering capture modules")
    for mod in (wireframe, shading, window, camera, scheduler):
        try:
            mod.unregister()
        except Exception as e:
            logger.warning(f"Failed to unregister capture.{mod.__name__.split('.')[-1]}: {e}")
