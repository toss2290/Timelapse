"""
Viewport shading lock/restore for Timelapse X addon.
Blender 5.1+ compatible.

Fix (v2.6.9):
  apply_workbench_solid_overrides() sets color_type='SINGLE' + neutral grey
  so that Workbench renders produce a flat clay appearance regardless of
  what materials are assigned (including Principled BSDF node materials).

  In Blender Workbench, scene.display.shading.color_type='SINGLE' overrides
  ALL object/material colors — shader nodes are completely bypassed.

  The v2.6.8 material diffuse_color fallback was removed because:
    • Principled BSDF and all node-based materials ignore diffuse_color.
    • Temporarily corrupting diffuse_color could cause side-effects.

Fix (v2.6.1 retained):
  light='FLAT', show_cavity=False, show_object_outline=False when no_shadows.
"""

import bpy
import logging
from typing import Optional, List, Tuple

from .. import utils
from ..state_manager import StateManager

logger = logging.getLogger(__name__)

_SpaceBackup = Tuple

# Standard Blender Solid-mode grey
_SOLID_GREY = (0.800, 0.800, 0.800)


# ============================================================================
# Internal helper
# ============================================================================

def _set(obj, attr: str, value) -> bool:
    """Set obj.attr = value, silently skip on error. Returns True on success."""
    if not hasattr(obj, attr):
        return False
    try:
        setattr(obj, attr, value)
        return True
    except (AttributeError, RuntimeError, TypeError) as e:
        logger.debug(f"Cannot set {attr}={value!r}: {e}")
        return False


# ============================================================================
# Shading Lock (Window Mode)
# ============================================================================

def lock_viewport_shading(scene, prefs) -> Tuple[Optional[str], List[_SpaceBackup]]:
    """Lock viewport shading for consistent Window-mode capture."""
    if not prefs or not prefs.lock_shading:
        return None, []

    original_engine = scene.render.engine
    backup: List[_SpaceBackup] = []

    if prefs.shading_type != 'RENDERED':
        scene.render.engine = 'BLENDER_WORKBENCH'

    wm = bpy.context.window_manager
    if not wm:
        return original_engine, backup

    for window in wm.windows:
        screen = window.screen
        if not screen:
            continue
        for area in screen.areas:
            if area.type != 'VIEW_3D':
                continue
            for space in area.spaces:
                if space.type != 'VIEW_3D':
                    continue
                sh = space.shading
                ov = getattr(space, 'overlay', None)
                backup.append((
                    space,
                    sh.type if sh else None,
                    getattr(sh, 'show_xray',       None) if sh else None,
                    getattr(sh, 'shadow_intensity', None) if sh else None,
                    getattr(ov, 'show_overlays',    None) if ov else None,
                    getattr(ov, 'show_wireframes',  None)
                        if ov and hasattr(ov, 'show_wireframes') else None,
                    getattr(space, 'show_gizmo',    None),
                ))
                try:
                    sh.type = prefs.shading_type
                except (AttributeError, RuntimeError):
                    pass
                if ov:
                    hide = prefs.perf_hide_overlays_during_capture
                    _set(ov, 'show_overlays', not hide)
                    if hasattr(ov, 'show_wireframes'):
                        _set(ov, 'show_wireframes', prefs.shading_type == 'SOLID' and not hide)
                _set(space, 'show_gizmo', False)
                _set(sh, 'show_xray', prefs.xray)
                if hasattr(sh, 'cavity_type'):
                    _set(sh, 'cavity_type', 'BOTH')
                if prefs.disable_shadows:
                    _set(sh, 'shadow_intensity', 0.0)

    return original_engine, backup


def restore_viewport_shading(scene, original_engine: Optional[str],
                              backup: Optional[List[_SpaceBackup]] = None):
    if original_engine is not None:
        try:
            scene.render.engine = original_engine
        except (AttributeError, RuntimeError) as e:
            logger.warning(f"Cannot restore engine: {e}")

    if not backup:
        return

    for (space, shading_type, xray, shadow, overlays, wireframes, gizmo) in backup:
        try:
            sh = space.shading
            if shading_type is not None:
                sh.type = shading_type
            if xray   is not None: _set(sh, 'show_xray',       xray)
            if shadow is not None: _set(sh, 'shadow_intensity', shadow)
            ov = getattr(space, 'overlay', None)
            if ov:
                if overlays   is not None: _set(ov, 'show_overlays',   overlays)
                if wireframes is not None: _set(ov, 'show_wireframes', wireframes)
            if gizmo is not None: _set(space, 'show_gizmo', gizmo)
        except (AttributeError, RuntimeError, ReferenceError) as e:
            logger.warning(f"Cannot restore shading: {e}")


# ============================================================================
# Workbench Solid Overrides  (Camera List — SOLID mode only)
# ============================================================================

def apply_workbench_solid_overrides(
    scene, want_xray: bool, want_no_shadows: bool
) -> Optional[dict]:
    """
    Configure scene.display.shading for a Workbench SOLID-mode render.

    Sets color_type='SINGLE' + single_color=grey so the render produces
    a neutral clay/grey appearance regardless of materials. Workbench with
    color_type='SINGLE' completely bypasses shader nodes.
    """
    display = getattr(scene, 'display', None)
    if not display:
        return None
    s = getattr(display, 'shading', None)
    if not s:
        return None

    # Backup
    backup = {}
    for attr in ('color_type', 'single_color',
                 'show_shadows', 'shadow_intensity',
                 'light', 'show_cavity', 'show_object_outline',
                 'show_xray', 'xray_alpha'):
        if hasattr(s, attr):
            try:
                backup[attr] = getattr(s, attr)
            except Exception:
                pass

    # Force SINGLE grey
    ct_ok   = _set(s, 'color_type',   'SINGLE')
    grey_ok = _set(s, 'single_color', _SOLID_GREY)

    logger.info(
        f"Workbench: color_type={'SINGLE' if ct_ok else '(failed)'} | "
        f"single_color={_SOLID_GREY if grey_ok else '(failed)'} | "
        f"actual color_type={getattr(s, 'color_type', '?')} | "
        f"actual single_color={getattr(s, 'single_color', '?')}"
    )

    # X-Ray
    if want_xray:
        _set(s, 'show_xray', True)
        if hasattr(s, 'xray_alpha') and getattr(s, 'xray_alpha', 1.0) < 0.5:
            _set(s, 'xray_alpha', 0.5)

    # Shadows / directional shading
    if want_no_shadows:
        _set(s, 'show_shadows',        False)
        _set(s, 'shadow_intensity',    0.0)
        _set(s, 'light',               'FLAT')   # v2.6.1: no directional shading
        _set(s, 'show_cavity',         False)    # v2.6.1: no AO darkening
        _set(s, 'show_object_outline', False)    # avoid edge bleed

    return backup if backup else None


# Backward-compat alias
def apply_workbench_display_overrides(scene, want_xray: bool, want_no_shadows: bool) -> Optional[dict]:
    return apply_workbench_solid_overrides(scene, want_xray, want_no_shadows)


def restore_workbench_display_overrides(scene, backup: Optional[dict]):
    if not backup:
        return
    display = getattr(scene, 'display', None)
    if not display:
        return
    s = getattr(display, 'shading', None)
    if not s:
        return
    for key, value in backup.items():
        if hasattr(s, key):
            try:
                setattr(s, key, value)
            except (AttributeError, RuntimeError) as e:
                logger.warning(f"Cannot restore {key}: {e}")


# ============================================================================
# Low-Overhead Settings  (SOLID / Workbench ONLY — see camera.py)
# ============================================================================

def apply_low_overhead_settings(scene) -> dict:
    """
    Simplified settings for faster Workbench renders.
    Must only be called in the SOLID branch. Modifying EEVEE settings
    here would corrupt MATERIAL/RENDERED renders.
    """
    backup = {}
    render = scene.render

    def _setb(obj, attr, value):
        if obj and hasattr(obj, attr):
            key = (obj.as_pointer(), attr)
            try:
                backup[key] = getattr(obj, attr)
                setattr(obj, attr, value)
            except (AttributeError, RuntimeError):
                pass

    _setb(render, 'use_simplify',                    True)
    _setb(render, 'simplify_subdivision_render',     0)
    _setb(render, 'simplify_child_particles_render', 0)
    _setb(render, 'simplify_volumes',                0.0)

    cycles = getattr(scene, 'cycles', None)
    if cycles:
        _setb(cycles, 'samples',               1)
        _setb(cycles, 'preview_samples',       1)
        _setb(cycles, 'use_adaptive_sampling', False)
        _setb(cycles, 'use_denoising',         False)
        _setb(cycles, 'use_motion_blur',       False)

    # EEVEE properties intentionally NOT modified — Workbench ignores them,
    # and corrupting them breaks subsequent MATERIAL/RENDERED renders.

    return backup


def restore_low_overhead_settings(scene, backup: dict):
    if not backup:
        return
    objects = [scene.render]
    cycles = getattr(scene, 'cycles', None)
    if cycles:
        objects.append(cycles)
    by_ptr = {obj.as_pointer(): obj for obj in objects if obj}
    for (ptr, attr), value in backup.items():
        obj = by_ptr.get(ptr)
        if obj and hasattr(obj, attr):
            try:
                setattr(obj, attr, value)
            except (AttributeError, RuntimeError):
                pass


# ============================================================================
# Registration
# ============================================================================

def register():
    logger.info(
        "Shading module registered "
        "(v2.6.9: SINGLE grey via scene.display.shading; no EEVEE corruption)"
    )


def unregister():
    logger.info("Shading module unregistered")
