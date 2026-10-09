"""
Camera scheduling — Using Unified StateManager

Fix (v2.5.4):
  - `import re` moved from inside function body to top-level.
  - init_camera_schedulers() used re.sub() directly to sanitize camera names,
    duplicating utils.sanitize_filename(). Replaced with that function.
"""

import os
import re
import logging
from typing import List

from ..state_manager import StateManager
from .. import constants
from .. import utils

logger = logging.getLogger(__name__)


def init_camera_schedulers(scene, base_dir: str, prefs):
    """
    Initialize camera schedulers (using StateManager).
    """
    StateManager().clear_camera_schedulers()

    # Resolve default interval
    default_interval = constants.DEFAULT_INTERVAL
    if prefs:
        default_interval = max(constants.MIN_INTERVAL,
                               float(getattr(prefs, 'default_interval', constants.DEFAULT_INTERVAL)))
    scene_interval = getattr(scene, 'tlx_capture_interval', None)
    if scene_interval is not None:
        default_interval = max(constants.MIN_INTERVAL, float(scene_interval))

    for camera_item in scene.tlx_cameras:
        camera = camera_item.camera
        if not camera or camera.type != 'CAMERA':
            continue

        # FIX: use utils.sanitize_filename() instead of inline re.sub()
        camera_name = utils.sanitize_filename(camera.name)
        camera_dir  = os.path.join(base_dir, f"CAM_{camera_name}")
        os.makedirs(camera_dir, exist_ok=True)

        try:
            existing    = [f for f in os.listdir(camera_dir)
                           if f.lower().endswith(constants.IMAGE_EXTENSIONS)]
            start_index = len(existing)
        except OSError as e:
            logger.warning(f"Cannot read camera dir {camera_dir}: {e}")
            start_index = 0

        if camera_item.use_interval_override:
            interval = float(camera_item.interval_override)
        else:
            interval = default_interval

        interval = max(constants.MIN_INTERVAL, interval)

        StateManager().init_camera_scheduler(
            camera_name=camera.name,
            interval=interval,
            start_index=start_index
        )
        logger.debug(f"Initialized {camera.name}: interval={interval}, start={start_index}")


def pick_due_cameras(scene, prefs, round_robin: bool = False) -> List[int]:
    """Pick cameras that are due for capture."""
    if len(scene.tlx_cameras) == 0:
        return []

    camera_names   = []
    camera_indices = {}

    for index, camera_item in enumerate(scene.tlx_cameras):
        cam = camera_item.camera
        if cam and cam.type == 'CAMERA':
            camera_names.append(cam.name)
            camera_indices[cam.name] = index

    if not camera_names:
        return []

    due_names = StateManager().get_due_cameras(camera_names, round_robin=round_robin)
    return [camera_indices[name] for name in due_names if name in camera_indices]


def update_camera_due(camera_item, now: float = None):
    """Update camera's next due time."""
    cam = camera_item.camera
    if not cam:
        return
    StateManager().update_camera_due(cam.name)


def compute_min_timer_interval(scene, prefs) -> float:
    """Compute minimum timer interval from camera schedulers."""
    default_interval = constants.DEFAULT_INTERVAL

    if prefs:
        default_interval = float(getattr(prefs, 'default_interval', constants.DEFAULT_INTERVAL))

    scene_interval = getattr(scene, 'tlx_capture_interval', None)
    if scene_interval is not None:
        default_interval = float(scene_interval)

    min_interval = max(constants.MIN_INTERVAL, default_interval)

    for camera_item in scene.tlx_cameras:
        if camera_item.use_interval_override:
            interval = float(camera_item.interval_override)
            if constants.MIN_INTERVAL <= interval < min_interval:
                min_interval = interval

    return max(constants.MIN_INTERVAL, min_interval)


def register():
    logger.info("Scheduler module registered (v2.5.4: uses sanitize_filename)")


def unregister():
    logger.info("Scheduler module unregistered")
