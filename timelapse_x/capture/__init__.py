"""
Capture package for Timelapse X addon.

Fix (v2.5.4): bare `except: pass` replaced with logged warnings
so errors during registration are visible in the console.
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

# ── Public API ───────────────────────────────────────────────────────────────

try:
    from .camera    import capture_cameras, init_camera_schedulers, compute_min_timer_interval
    from .window    import capture_window, capture_window_async
    from .scheduler import pick_due_cameras, update_camera_due
except ImportError:
    pass

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
