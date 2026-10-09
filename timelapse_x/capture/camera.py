"""
Camera rendering and capture logic for Timelapse X addon.

Fix (v2.6.10):
  MATERIAL branch was assigning render.engine = 'BLENDER_EEVEE_NEXT' which
  does not exist in Blender 5.1. The valid enum is 'BLENDER_EEVEE'.

  Log evidence:
    Engine assignment 'BLENDER_EEVEE_NEXT' failed: bpy_struct: item.attr = val:
    enum "BLENDER_EEVEE_NEXT" not found in ('BLENDER_EEVEE', 'BLENDER_WORKBENCH', 'CYCLES')

  Fix: query the actual available engine IDs at runtime and pick the best EEVEE
  variant. This is robust across Blender versions:
    Blender 5.1 → 'BLENDER_EEVEE'
    Blender 4.x → 'BLENDER_EEVEE_NEXT' or 'BLENDER_EEVEE'

Fix (v2.6.9 retained):
  Direct engine assignment per stype — no choose_best_engine() cache.
  SOLID=BLENDER_WORKBENCH, RENDERED=scene engine unchanged.
"""

import bpy
import os
import logging
from typing import Optional, Tuple
from math import radians
from mathutils import Vector, Matrix

from .. import constants
from .. import utils
from ..state_manager import StateManager

from . import scheduler
from . import shading

logger = logging.getLogger(__name__)

init_camera_schedulers     = scheduler.init_camera_schedulers
compute_min_timer_interval = scheduler.compute_min_timer_interval


def _rotate_camera_around_target(camera, target, angle_deg: float, axis: str = 'Z') -> None:
    """Orbit `camera` around `target`'s origin by angle_deg on the given
    world axis, keeping its current distance and re-aiming it at the
    target afterwards. No keyframes, no animation data — just a direct
    transform, called once per successful capture (see capture_cameras()).

    Works via matrix_world (rather than .location/.rotation_euler
    separately) so it stays correct even if the camera is parented —
    Blender resolves the parent-inverse for us when matrix_world is
    assigned directly.
    """
    if not camera or not target or not angle_deg:
        return
    try:
        target_loc = target.matrix_world.translation.copy()
        cam_world  = camera.matrix_world.copy()
        cam_loc    = cam_world.translation.copy()
        offset     = cam_loc - target_loc

        axis_vec = {'X': Vector((1, 0, 0)),
                    'Y': Vector((0, 1, 0)),
                    'Z': Vector((0, 0, 1))}[axis]
        rot = Matrix.Rotation(radians(angle_deg), 4, axis_vec)
        new_loc = target_loc + (rot @ offset)

        # Re-aim at the target so the shot stays framed on it, matching
        # the classic "turntable" look rather than just spinning in place.
        direction = (target_loc - new_loc)
        if direction.length_squared > 1e-9:
            new_rot = direction.to_track_quat('-Z', 'Y').to_matrix().to_4x4()
        else:
            new_rot = cam_world.to_3x3().to_4x4()

        new_world = new_rot
        new_world.translation = new_loc
        camera.matrix_world = new_world
    except Exception as e:
        logger.warning(f"Turntable rotation failed for '{camera.name}': {e}")


def clear_camera_settings_cache() -> None:
    """No-op kept for backward compatibility."""
    pass


# ============================================================================
# Engine helpers
# ============================================================================

def _get_eevee_engine_id() -> str:
    """
    Return the correct EEVEE engine identifier for the running Blender version.

    Blender 5.1 uses 'BLENDER_EEVEE'.
    Some Blender 4.x builds use 'BLENDER_EEVEE_NEXT'.
    Queried at call time (not cached) to avoid restricted-context issues.
    """
    try:
        available = {
            e.identifier
            for e in bpy.types.RenderSettings.bl_rna.properties['engine'].enum_items
        }
    except Exception:
        available = set()

    # Prefer EEVEE_NEXT if available (future-proof), fall back to EEVEE
    for candidate in ('BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE'):
        if candidate in available:
            logger.debug(f"EEVEE engine ID resolved: {candidate}")
            return candidate

    # Last resort — will produce a warning in _assign_engine but won't crash
    logger.warning("Cannot resolve EEVEE engine ID; defaulting to 'BLENDER_EEVEE'")
    return 'BLENDER_EEVEE'


def _assign_engine(render, engine_id: str) -> str:
    """
    Directly assign render.engine = engine_id.
    Logs outcome; returns the engine actually set.
    """
    before = render.engine
    try:
        render.engine = engine_id
    except (AttributeError, RuntimeError, TypeError) as e:
        logger.warning(f"Engine assignment '{engine_id}' failed: {e}")

    after = render.engine
    if after == engine_id:
        logger.info(f"Engine set: {before} → {after}")
    else:
        logger.warning(
            f"Engine mismatch: requested '{engine_id}', got '{after}' "
            f"(was '{before}'). Rendering with '{after}'."
        )
    return after


# ============================================================================
# Edit-mode helpers
# ============================================================================

def _is_in_edit_mode() -> Tuple[bool, Optional[str]]:
    try:
        mode = bpy.context.mode
        if mode.startswith('EDIT_'):
            return True, mode
        return False, None
    except Exception as e:
        logger.debug(f"Error checking edit mode: {e}")
        return False, None


def _get_edit_mode_info() -> dict:
    is_edit, mode_name = _is_in_edit_mode()
    if not is_edit:
        return {'is_edit': False, 'mode': None, 'active_object': None, 'object_type': None}
    try:
        active_obj = bpy.context.active_object
        obj_type   = active_obj.type if active_obj else None
        return {'is_edit': True, 'mode': mode_name,
                'active_object': active_obj, 'object_type': obj_type}
    except Exception as e:
        logger.debug(f"Error getting edit mode info: {e}")
        return {'is_edit': True, 'mode': mode_name,
                'active_object': None, 'object_type': None}


class EditModeHandler:
    __slots__ = ('behavior', 'auto_save', '_original_mode', '_original_object', '_edit_info_cache')

    def __init__(self, behavior: str, auto_save: bool = True):
        self.behavior         = behavior
        self.auto_save        = auto_save
        self._original_mode   = None
        self._original_object = None
        self._edit_info_cache = None

    def _get_edit_info(self) -> dict:
        if self._edit_info_cache is None:
            self._edit_info_cache = _get_edit_mode_info()
        return self._edit_info_cache

    def should_skip_capture(self) -> bool:
        edit_info = self._get_edit_info()
        if not edit_info['is_edit']:
            return False
        if self.behavior == 'SKIP':
            return True
        return False

    def prepare_for_capture(self, context) -> bool:
        edit_info = self._get_edit_info()
        if not edit_info['is_edit']:
            return True
        if self.behavior == 'SKIP':
            return False
        if self.behavior == 'CAPTURE_ANYWAY':
            logger.warning(f"Capturing in {edit_info['mode']} - may cause instability!")
            return True
        if self.behavior == 'FORCE_OBJECT':
            return self._switch_to_object_mode(context, edit_info)
        return False

    def _switch_to_object_mode(self, context, edit_info: dict) -> bool:
        try:
            self._original_mode   = edit_info['mode']
            self._original_object = edit_info['active_object']
            if bpy.ops.object.mode_set.poll():
                bpy.ops.object.mode_set(mode='OBJECT')
                self._edit_info_cache = None
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to switch to Object mode: {e}")
            return False

    def restore_edit_mode(self, context):
        if self.behavior != 'FORCE_OBJECT' or not self._original_mode:
            return
        try:
            if self._original_object:
                context.view_layer.objects.active = self._original_object
            mode_to_set = self._original_mode.split('_')[0]
            if bpy.ops.object.mode_set.poll():
                bpy.ops.object.mode_set(mode=mode_to_set)
        except Exception as e:
            logger.error(f"Failed to restore edit mode: {e}")
        finally:
            self._original_mode   = None
            self._original_object = None
            self._edit_info_cache = None


# ============================================================================
# Main capture entry points
# ============================================================================

def capture_cameras(context, base_dir: str, require_dirty: bool = True) -> bool:
    scene = context.scene
    prefs = utils.get_addon_preferences()

    edit_handler = EditModeHandler(
        behavior  = prefs.edit_mode_behavior  if prefs else 'SKIP',
        auto_save = prefs.edit_mode_auto_save if prefs else True,
    )

    if edit_handler.should_skip_capture():
        return False
    if not edit_handler.prepare_for_capture(context):
        return False

    mgr = StateManager()

    if require_dirty and prefs and prefs.idle_detection:
        if not mgr.dirty:
            edit_handler.restore_edit_mode(context)
            return False

    if len(scene.tlx_cameras) == 0:
        edit_handler.restore_edit_mode(context)
        raise RuntimeError('Camera list is empty.')

    original_camera      = scene.camera
    original_filepath    = scene.render.filepath
    img_settings         = scene.render.image_settings
    original_format      = img_settings.file_format
    original_color_mode  = getattr(img_settings, 'color_mode',  'RGB')
    original_quality     = getattr(img_settings, 'quality',      90)
    original_compression = getattr(img_settings, 'compression',  15)
    original_media_type  = getattr(img_settings, 'media_type',   None)

    any_captured = False
    mgr.set_ignore_depsgraph(True)

    try:
        any_override = any(item.use_shading_override for item in scene.tlx_cameras)
        if not any_override:
            original_engine, _shading_backup = shading.lock_viewport_shading(scene, prefs)
        else:
            original_engine = None
            _shading_backup = []

        use_round_robin = bool(prefs.camera_round_robin) if prefs else True
        camera_indices  = scheduler.pick_due_cameras(scene, prefs, round_robin=use_round_robin)
        max_per_tick    = int(getattr(prefs, 'camera_max_per_tick', 1)) if prefs else 1
        camera_indices  = camera_indices[:max(1, max_per_tick)]

        if not camera_indices:
            return False

        for index in camera_indices:
            camera_item = scene.tlx_cameras[index]
            camera      = camera_item.camera
            if not camera or camera.type != 'CAMERA':
                scheduler.update_camera_due(camera_item)
                continue
            if edit_handler.behavior == 'FORCE_OBJECT':
                if bpy.context.mode.startswith('EDIT_'):
                    logger.warning("Still in edit mode. Skipping remaining cameras.")
                    break
            success = _render_camera(
                context, scene, camera_item, camera, base_dir, prefs, edit_handler
            )
            if success:
                any_captured = True
                if camera_item.use_turntable and camera_item.turntable_target:
                    _rotate_camera_around_target(
                        camera, camera_item.turntable_target,
                        camera_item.turntable_degrees, camera_item.turntable_axis)
            scheduler.update_camera_due(camera_item)

        if any_captured:
            mgr.set_dirty(False)
        return any_captured

    finally:
        mgr.set_ignore_depsgraph(False)
        if prefs:
            suppress_ms = getattr(prefs, 'perf_depsgraph_suppress_ms', constants.DEFAULT_SUPPRESS_MS)
            mgr.set_suppression(suppress_ms)
        scene.camera          = original_camera
        scene.render.filepath = original_filepath
        img_settings.file_format = original_format
        if hasattr(img_settings, 'color_mode'):  img_settings.color_mode  = original_color_mode
        if hasattr(img_settings, 'quality'):     img_settings.quality     = original_quality
        if hasattr(img_settings, 'compression'): img_settings.compression = original_compression
        if original_media_type is not None and hasattr(img_settings, 'media_type'):
            img_settings.media_type = original_media_type
        shading.restore_viewport_shading(scene, original_engine, _shading_backup)
        edit_handler.restore_edit_mode(context)


def _render_camera(context, scene, camera_item, camera, base_dir, prefs, edit_handler):
    try:
        if edit_handler.behavior != 'CAPTURE_ANYWAY':
            edit_info = _get_edit_mode_info()
            if edit_info['is_edit']:
                logger.warning(f"Edit mode before render. Skipping '{camera.name}'.")
                return False

        settings     = _get_camera_settings(camera_item, prefs)
        camera_name  = utils.sanitize_filename(camera.name)
        camera_dir   = utils.ensure_directory(os.path.join(base_dir, f"CAM_{camera_name}"))

        if "_tlx_idx" not in camera_item:
            try:
                existing = sum(
                    1 for entry in os.scandir(camera_dir)
                    if entry.is_file(follow_symlinks=False)
                    and entry.name.lower().endswith(constants.IMAGE_EXTENSIONS)
                )
                camera_item["_tlx_idx"] = existing
            except OSError:
                camera_item["_tlx_idx"] = 0

        frame_index  = int(camera_item.get("_tlx_idx", 0))
        zero_padding = prefs.zero_padding if prefs else constants.DEFAULT_ZERO_PADDING
        output_path  = os.path.join(
            camera_dir,
            f"TLX_CAM_{camera_name}_{str(frame_index).zfill(zero_padding)}"
        )

        scene.camera          = camera
        scene.render.filepath = output_path

        img_settings = scene.render.image_settings
        fmt          = settings['format']
        img_settings.file_format = fmt
        if fmt == 'PNG':
            if hasattr(img_settings, 'color_mode'):  img_settings.color_mode  = 'RGBA' if settings['png_rgba'] else 'RGB'
            if hasattr(img_settings, 'compression'): img_settings.compression = 15
        else:
            if hasattr(img_settings, 'color_mode'):  img_settings.color_mode  = 'RGB'
            if hasattr(img_settings, 'quality'):     img_settings.quality     = settings['jpeg_quality']

        render           = scene.render
        prev_engine      = render.engine
        prev_lock_render = getattr(render, 'use_lock_interface', False)

        if settings['lock_interface'] and hasattr(render, 'use_lock_interface'):
            try:
                render.use_lock_interface = True
            except (AttributeError, RuntimeError):
                pass

        png_comp_backup = None
        if fmt == 'PNG' and hasattr(img_settings, 'compression'):
            png_comp_backup          = img_settings.compression
            img_settings.compression = int(settings['png_compress'])

        low_backup = None
        blueprint_hidden = []
        collection_backup = None

        try:
            if getattr(scene, 'tlx_auto_hide_blueprints', False):
                cam_projection = 'ORTHO' if getattr(camera.data, 'type', 'PERSP') == 'ORTHO' else 'PERSP'
                blueprint_hidden = utils.apply_blueprint_auto_hide(
                    context, cam_projection, visibility_attr='hide_render')

            if getattr(scene, 'tlx_hide_collection_enabled', False):
                target_collection = getattr(scene, 'tlx_hide_collection', None)
                collection_backup = utils.apply_collection_isolation(
                    target_collection, visibility_attr='hide_render')

            stype, want_xray, want_no_shadows = _get_shading_target(camera_item, prefs)
            logger.info(
                f"[{camera.name}] stype={stype}, prev_engine={prev_engine}, "
                f"xray={want_xray}, no_shadows={want_no_shadows}"
            )

            if edit_handler.behavior != 'CAPTURE_ANYWAY':
                edit_info = _get_edit_mode_info()
                if edit_info['is_edit']:
                    logger.warning(f"Edit mode just before render. Aborting '{camera.name}'")
                    return False

            # ── WIREFRAME ─────────────────────────────────────────────────────
            if stype == 'WIREFRAME':
                _render_wireframe(scene, camera, output_path, prefs)

            # ── SOLID (Workbench clay render) ─────────────────────────────────
            elif stype == 'SOLID':
                _assign_engine(render, 'BLENDER_WORKBENCH')

                # Low-overhead safe here: Workbench ignores EEVEE props.
                if settings['low_overhead']:
                    low_backup = shading.apply_low_overhead_settings(scene)

                wb_backup = shading.apply_workbench_solid_overrides(
                    scene, want_xray, want_no_shadows)
                try:
                    try:
                        bpy.context.view_layer.update()
                    except Exception:
                        pass
                    bpy.ops.render.render(write_still=True, use_viewport=False)
                    logger.info(f"[{camera.name}] SOLID rendered with engine={render.engine}")
                except RuntimeError as e:
                    logger.error(f"[{camera.name}] SOLID render failed: {e}")
                    return False
                finally:
                    shading.restore_workbench_display_overrides(scene, wb_backup)

            # ── MATERIAL (EEVEE material-preview equivalent) ───────────────────
            elif stype == 'MATERIAL':
                # FIX v2.6.10: resolve EEVEE engine ID at runtime.
                # Blender 5.1 → 'BLENDER_EEVEE'  (not 'BLENDER_EEVEE_NEXT')
                # Future Blender → 'BLENDER_EEVEE_NEXT' if available
                eevee_id = _get_eevee_engine_id()
                _assign_engine(render, eevee_id)
                try:
                    bpy.context.view_layer.update()
                except Exception:
                    pass
                try:
                    bpy.ops.render.render(write_still=True, use_viewport=False)
                    logger.info(f"[{camera.name}] MATERIAL rendered with engine={render.engine}")
                except RuntimeError as e:
                    logger.error(f"[{camera.name}] MATERIAL render failed: {e}")
                    return False

            # ── RENDERED (full quality — user's configured engine) ─────────────
            else:
                logger.info(f"[{camera.name}] RENDERED with engine={render.engine} (unchanged)")
                try:
                    bpy.context.view_layer.update()
                except Exception:
                    pass
                try:
                    bpy.ops.render.render(write_still=True, use_viewport=False)
                except RuntimeError as e:
                    logger.error(f"[{camera.name}] RENDERED render failed: {e}")
                    return False

            camera_item["_tlx_idx"] = frame_index + 1
            logger.info(
                f"[{camera.name}] ✓ frame {frame_index} saved | "
                f"stype={stype} | final_engine={render.engine}"
            )
            return True

        except Exception as e:
            logger.error(f"Camera render failed for '{camera.name}': {e}", exc_info=True)
            return False

        finally:
            if blueprint_hidden:
                utils.restore_blueprint_auto_hide(blueprint_hidden)
            if collection_backup:
                utils.restore_collection_isolation(collection_backup)
            if low_backup is not None:
                shading.restore_low_overhead_settings(scene, low_backup)
            if png_comp_backup is not None:
                try:
                    img_settings.compression = png_comp_backup
                except (AttributeError, RuntimeError):
                    pass
            try:
                render.engine = prev_engine
            except (AttributeError, RuntimeError):
                pass
            if hasattr(render, 'use_lock_interface'):
                try:
                    render.use_lock_interface = prev_lock_render
                except (AttributeError, RuntimeError):
                    pass

    except Exception as e:
        logger.error(f"Render camera exception for '{camera.name}': {e}", exc_info=True)
        return False


# ============================================================================
# Settings helpers
# ============================================================================

def _get_camera_settings(camera_item, prefs) -> dict:
    settings: dict = {}
    if camera_item.use_image_override:
        settings['format']       = camera_item.image_format
        settings['png_rgba']     = camera_item.png_rgba
        settings['jpeg_quality'] = camera_item.jpeg_quality
    else:
        settings['format']       = prefs.image_format  if prefs else 'PNG'
        settings['png_rgba']     = prefs.png_rgba       if prefs else False
        settings['jpeg_quality'] = prefs.jpeg_quality   if prefs else 90
    if camera_item.perf_override:
        settings['low_overhead']   = camera_item.perf_low_overhead
        settings['lock_interface'] = camera_item.perf_lock_interface
        settings['png_compress']   = camera_item.perf_png_compress
    else:
        settings['low_overhead']   = getattr(prefs, 'camera_low_overhead',  True) if prefs else True
        settings['lock_interface'] = getattr(prefs, 'camera_lock_interface', True) if prefs else True
        settings['png_compress']   = getattr(prefs, 'camera_png_compress',    3)  if prefs else 3
    return settings


def _get_shading_target(camera_item, prefs) -> Tuple[str, bool, bool]:
    """Return (shading_type, want_xray, want_no_shadows)."""
    if camera_item.use_shading_override:
        return (camera_item.shading_type, camera_item.xray, camera_item.disable_shadows)

    shading_type = prefs.shading_type if prefs else 'SOLID'
    xray         = prefs.xray         if prefs else False

    if prefs and hasattr(prefs, 'disable_shadows'):
        disable_shadows = prefs.disable_shadows
    else:
        disable_shadows = getattr(prefs, 'camera_disable_shadows', True) if prefs else True

    return (shading_type, xray, disable_shadows)


# ============================================================================
# Wireframe fallback
# ============================================================================

def _render_wireframe(scene, camera, output_path: str, prefs):
    try:
        from . import wireframe
        wireframe.render_freestyle(
            output_path_noext    = output_path,
            camera_obj           = camera,
            thickness            = prefs.wireframe_thickness          if prefs else 1.0,
            color                = tuple(prefs.wireframe_color)       if prefs else (0., 0., 0.),
            bg_color             = tuple(prefs.wireframe_bg_color)    if prefs else (1., 1., 1.),
            bg_strength          = prefs.wireframe_bg_strength        if prefs else 1.0,
            transparent_bg       = prefs.wireframe_transparent_bg     if prefs else False,
            disable_shadows      = prefs.wireframe_disable_shadows    if prefs else True,
            use_object_colors    = prefs.wireframe_use_object_colors  if prefs else False,
            default_object_color = tuple(prefs.wireframe_default_object_color)
                                   if prefs else (1., 1., 1.),
        )
        return
    except ImportError as e:
        logger.error(f"Freestyle not available: {e}")
    except Exception as e:
        logger.error(f"Freestyle failed: {e}")

    # Tier 2: Workbench wireframe overlay
    try:
        prev_engine         = scene.render.engine
        scene.render.engine = 'BLENDER_WORKBENCH'
        saved_shading       = []
        for area in bpy.context.screen.areas:
            if area.type == 'VIEW_3D':
                for space in area.spaces:
                    if space.type == 'VIEW_3D':
                        overlay = getattr(space, 'overlay', None)
                        saved_shading.append((space, space.shading.type,
                            getattr(overlay, 'show_wireframes', False) if overlay else False))
                        try:
                            space.shading.type = 'SOLID'
                            if overlay and hasattr(overlay, 'show_wireframes'):
                                overlay.show_wireframes = True
                        except Exception:
                            pass
                        break
                break
        try:
            bpy.context.view_layer.update()
            bpy.ops.render.render(write_still=True, use_viewport=False)
            return
        finally:
            for space, orig_shading, orig_wf in saved_shading:
                try:
                    space.shading.type = orig_shading
                    overlay = getattr(space, 'overlay', None)
                    if overlay and hasattr(overlay, 'show_wireframes'):
                        overlay.show_wireframes = orig_wf
                except Exception:
                    pass
            try:
                scene.render.engine = prev_engine
            except Exception:
                pass
    except Exception as e:
        raise RuntimeError(f"Wireframe render failed: {e}")


# ============================================================================
# Registration
# ============================================================================

def register():
    logger.info(
        "Camera capture module registered "
        "(v2.6.10: runtime EEVEE engine ID — 'BLENDER_EEVEE' on Blender 5.1)"
    )


def unregister():
    logger.info("Camera capture module unregistered")
