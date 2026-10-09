"""
Clean Window package for Timelapse X addon.
"""

import logging

logger = logging.getLogger(__name__)

try:
    from . import properties
    from . import operators
except ImportError as e:
    logger.warning(f"Some clean window modules not available: {e}")


def register_window_properties():
    try:
        properties.register_window_properties()
    except Exception as e:
        logger.error(f"Failed to register window properties: {e}")


def unregister_window_properties():
    try:
        properties.unregister_window_properties()
    except Exception as e:
        logger.warning(f"Failed to unregister window properties: {e}")


def register():
    logger.info("Registering clean window modules")
    for mod in (properties, operators):
        try:
            mod.register()
        except Exception as e:
            logger.error(f"Failed to register clean_window.{mod.__name__.split('.')[-1]}: {e}")


def unregister():
    logger.info("Unregistering clean window modules")
    for mod in (operators, properties):
        try:
            mod.unregister()
        except Exception as e:
            logger.warning(f"Failed to unregister clean_window.{mod.__name__.split('.')[-1]}: {e}")
