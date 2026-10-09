"""
Wireframe Rendering - Blender 5.2 compatible + LRU CACHE
=========================================================

Blender 5.2 changes:
- BLENDER_EEVEE removed; only BLENDER_EEVEE_NEXT available.
- bpy.ops.render.opengl removed; use bpy.ops.render.render.
- preferences.view.use_lock_interface → scene.render.use_lock_interface.
- preferences.view.render_display_type still present (guarded with getattr).
"""

import bpy
import logging
from typing import Tuple, Optional, Dict
from collections import OrderedDict
from threading import RLock
import time

from .. import utils

logger = logging.getLogger(__name__)


# ============================================================================
# Configuration
# ============================================================================

WIREFRAME_CACHE_MAX_SIZE = 100
WIREFRAME_CACHE_CLEANUP_THRESHOLD = 0.9


# ============================================================================
# Global Reusable Resources — LRU cache
# ============================================================================

_temp_scene_cache: Optional[object] = None
_material_cache: OrderedDict[Tuple[float, float, float], object] = OrderedDict()
_material_cache_lock = RLock()
_cache_max_size = WIREFRAME_CACHE_MAX_SIZE
_cache_stats = {'hits': 0, 'misses': 0, 'evictions': 0, 'created': 0}
_last_cleanup_time = 0.0


# ============================================================================
# Material Cache Management
# ============================================================================

def _get_or_create_material(color: Tuple[float, float, float]):
    global _material_cache, _cache_stats

    with _material_cache_lock:
        if color in _material_cache:
            mat = _material_cache[color]
            if mat and mat.name in bpy.data.materials:
                _material_cache.move_to_end(color)
                _cache_stats['hits'] += 1
                return mat
            else:
                del _material_cache[color]

        _cache_stats['misses'] += 1

        if len(_material_cache) >= _cache_max_size:
            _evict_lru_material()

        mat = _create_material(color)
        _material_cache[color] = mat
        _cache_stats['created'] += 1
        return mat


def _evict_lru_material():
    global _material_cache, _cache_stats

    if not _material_cache:
        return

    lru_color, lru_mat = _material_cache.popitem(last=False)

    if lru_mat and lru_mat.name in bpy.data.materials:
        try:
            if lru_mat.users == 0:
                bpy.data.materials.remove(lru_mat, do_unlink=True)
        except Exception as e:
            logger.warning(f"Failed to remove evicted material: {e}")

    _cache_stats['evictions'] += 1


def _create_material(color: Tuple[float, float, float]):
    mat_name = f"TLX_WF_Mat_{int(color[0]*255)}_{int(color[1]*255)}_{int(color[2]*255)}"

    existing = bpy.data.materials.get(mat_name)
    if existing:
        return existing

    mat = bpy.data.materials.new(name=mat_name)
    mat.use_nodes = True

    nodes = mat.node_tree.nodes
    nodes.clear()

    emission = nodes.new('ShaderNodeEmission')
    emission.inputs['Color'].default_value = (color[0], color[1], color[2], 1.0)
    emission.inputs['Strength'].default_value = 1.0

    output = nodes.new('ShaderNodeOutputMaterial')
    mat.node_tree.links.new(emission.outputs['Emission'], output.inputs['Surface'])

    return mat


def _cleanup_material_cache(force: bool = False):
    global _material_cache, _cache_stats

    with _material_cache_lock:
        if force:
            cleaned = 0
            for color, mat in list(_material_cache.items()):
                try:
                    if mat and mat.name in bpy.data.materials:
                        bpy.data.materials.remove(mat, do_unlink=True)
                        cleaned += 1
                except Exception as e:
                    logger.warning(f"Failed to remove material: {e}")
            _material_cache.clear()
        else:
            to_remove = []
            for color, mat in _material_cache.items():
                try:
                    if mat and mat.name in bpy.data.materials:
                        if mat.users == 0:
                            bpy.data.materials.remove(mat, do_unlink=True)
                            to_remove.append(color)
                    else:
                        to_remove.append(color)
                except Exception as e:
                    logger.warning(f"Failed to remove material: {e}")
            for color in to_remove:
                del _material_cache[color]


def get_cache_stats() -> dict:
    with _material_cache_lock:
        total = _cache_stats['hits'] + _cache_stats['misses']
        hit_rate = (_cache_stats['hits'] / total * 100) if total > 0 else 0
        return {
            'size': len(_material_cache),
            'max_size': _cache_max_size,
            'hits': _cache_stats['hits'],
            'misses': _cache_stats['misses'],
            'hit_rate': hit_rate,
            'evictions': _cache_stats['evictions'],
            'created': _cache_stats['created'],
        }


def print_cache_stats():
    stats = get_cache_stats()
    print("\n" + "="*70)
    print("WIREFRAME MATERIAL CACHE STATISTICS")
    print("="*70)
    print(f"Cache Size:        {stats['size']}/{stats['max_size']}")
    print(f"Hit Rate:          {stats['hit_rate']:.1f}%")
    print(f"Evictions:         {stats['evictions']}")
    print(f"Materials Created: {stats['created']}")
    print("="*70 + "\n")


# ============================================================================
# Temp Scene Management
# ============================================================================

def _get_or_create_temp_scene(name: str = "TLX_Wireframe_Temp"):
    global _temp_scene_cache

    if _temp_scene_cache and _temp_scene_cache.name in bpy.data.scenes:
        _clear_scene(_temp_scene_cache)
        return _temp_scene_cache

    try:
        scene = bpy.data.scenes.new(name)
        _temp_scene_cache = scene
        return scene
    except Exception as e:
        logger.error(f"Failed to create scene: {e}")
        raise


def _clear_scene(scene):
    try:
        for obj in list(scene.collection.objects):
            scene.collection.objects.unlink(obj)
    except Exception as e:
        logger.warning(f"Error clearing scene: {e}")


# ============================================================================
# Cleanup
# ============================================================================

def _cleanup_temp_resources(force: bool = False):
    global _temp_scene_cache, _last_cleanup_time

    current_time = time.time()
    if not force and (current_time - _last_cleanup_time) < 60:
        return

    _last_cleanup_time = current_time
    cleaned_count = 0

    if _temp_scene_cache:
        try:
            scene_name = _temp_scene_cache.name
            if scene_name in bpy.data.scenes:
                _clear_scene(_temp_scene_cache)
                bpy.data.scenes.remove(_temp_scene_cache, do_unlink=True)
                cleaned_count += 1
        except Exception as e:
            logger.warning(f"Failed to remove temp scene: {e}")
        finally:
            _temp_scene_cache = None

    _cleanup_material_cache(force=force)
    cleaned_count += _cleanup_orphan_data()


def _cleanup_orphan_data():
    count = 0

    for scene in list(bpy.data.scenes):
        if scene.name.startswith("TLX_"):
            try:
                bpy.data.scenes.remove(scene, do_unlink=True)
                count += 1
            except:
                pass

    for mesh in list(bpy.data.meshes):
        if mesh.name.startswith("TLX_WF_") and mesh.users == 0:
            try:
                bpy.data.meshes.remove(mesh, do_unlink=True)
                count += 1
            except:
                pass

    for obj in list(bpy.data.objects):
        if obj.name.startswith("TLX_WF_"):
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
                count += 1
            except:
                pass

    for mat in list(bpy.data.materials):
        if mat.name.startswith("TLX_WF_") and mat.users == 0:
            in_cache = any(m == mat for m in _material_cache.values())
            if not in_cache:
                try:
                    bpy.data.materials.remove(mat, do_unlink=True)
                    count += 1
                except:
                    pass

    return count


# ============================================================================
# Object Copying
# ============================================================================

def _copy_object_to_scene(src_obj, temp_scene) -> Optional[object]:
    try:
        if src_obj.type == 'MESH':
            new_mesh = src_obj.data.copy()
            new_mesh.name = f"TLX_WF_{src_obj.data.name}"
            new_obj = bpy.data.objects.new(f"TLX_WF_{src_obj.name}", new_mesh)
        else:
            new_obj = bpy.data.objects.new(src_obj.name, src_obj.data)

        temp_scene.collection.objects.link(new_obj)
        new_obj.matrix_world = src_obj.matrix_world.copy()
        new_obj.hide_render = src_obj.hide_render
        return new_obj
    except Exception as e:
        logger.error(f"Failed to copy object {src_obj.name}: {e}")
        return None


def _apply_object_colors(temp_scene, use_object_colors: bool = False,
                         default_color: Tuple[float, float, float] = (1.0, 1.0, 1.0)):
    colored_count = 0
    for obj in temp_scene.objects:
        if obj.type != 'MESH':
            continue
        color = obj.color[:3] if use_object_colors else default_color
        mat = _get_or_create_material(color)
        if len(obj.data.materials) == 0:
            obj.data.materials.append(mat)
        else:
            obj.data.materials[0] = mat
        colored_count += 1


# ============================================================================
# Freestyle Edge Marking
# ============================================================================

def _mark_freestyle_edges(temp_scene):
    for obj in temp_scene.objects:
        if obj.type != 'MESH' or obj.hide_render:
            continue
        mesh = obj.data
        num_edges = len(mesh.edges)
        if num_edges == 0:
            continue
        if "freestyle_edge" not in mesh.attributes:
            try:
                attr = mesh.attributes.new(
                    name="freestyle_edge", type='BOOLEAN', domain='EDGE'
                )
            except Exception as e:
                logger.warning(f"Cannot create attribute for {obj.name}: {e}")
                continue
        else:
            attr = mesh.attributes["freestyle_edge"]
        try:
            attr.data.foreach_set("value", [True] * num_edges)
        except Exception as e:
            logger.warning(f"Cannot mark edges for {obj.name}: {e}")


# ============================================================================
# Freestyle Setup
# ============================================================================

def _setup_freestyle(temp_scene, thickness: float = 1.0,
                     color: Tuple[float, float, float] = (0.0, 0.0, 0.0)):
    if not hasattr(temp_scene.render, 'use_freestyle'):
        raise RuntimeError("Freestyle not available")

    temp_scene.render.use_freestyle = True

    for view_layer in temp_scene.view_layers:
        view_layer.use_freestyle = True
        fs = view_layer.freestyle_settings

        while fs.linesets:
            fs.linesets.remove(fs.linesets[0])

        lineset = fs.linesets.new(name="TLX_EdgeMarks")
        lineset.select_edge_mark = True
        lineset.select_silhouette = False
        lineset.select_border = False
        lineset.select_crease = False
        lineset.select_contour = False
        lineset.select_external_contour = False
        lineset.select_material_boundary = False
        lineset.select_suggestive_contour = False
        lineset.select_ridge_valley = False

        ls = lineset.linestyle
        ls.use_chaining = True
        ls.chaining = 'PLAIN'
        ls.use_same_object = False
        ls.thickness = float(thickness)
        ls.thickness_position = 'CENTER'
        ls.color = (float(color[0]), float(color[1]), float(color[2]))
        ls.alpha = 1.0
        ls.use_nodes = False


# ============================================================================
# Background Setup
# ============================================================================

def _setup_background(temp_scene, transparent_bg: bool = False,
                      bg_color: Tuple[float, float, float] = (0.0, 0.0, 0.0),
                      bg_strength: float = 1.0):
    temp_scene.render.film_transparent = transparent_bg

    if transparent_bg:
        temp_scene.world = None
        return

    world_name = "TLX_WF_World"
    world = bpy.data.worlds.get(world_name)

    if not world:
        world = bpy.data.worlds.new(world_name)
        world.use_nodes = True
        world_nodes = world.node_tree.nodes
        world_nodes.clear()
        bg_node = world_nodes.new('ShaderNodeBackground')
        output_node = world_nodes.new('ShaderNodeOutputWorld')
        world.node_tree.links.new(bg_node.outputs['Background'], output_node.inputs['Surface'])

    bg_node = world.node_tree.nodes.get('Background')
    if bg_node:
        bg_node.inputs['Color'].default_value = (bg_color[0], bg_color[1], bg_color[2], 1.0)
        bg_node.inputs['Strength'].default_value = bg_strength

    temp_scene.world = world


# ============================================================================
# Main Render Function
# ============================================================================

def render_freestyle(
    output_path_noext: str,
    camera_obj,
    thickness: float = 1.0,
    color: Tuple[float, float, float] = (0.0, 0.0, 0.0),
    bg_color: Optional[Tuple[float, float, float]] = None,
    bg_strength: float = 1.0,
    transparent_bg: bool = False,
    disable_shadows: bool = True,
    use_object_colors: bool = False,
    default_object_color: Tuple[float, float, float] = (1.0, 1.0, 1.0)
):
    """Render wireframe with Freestyle (Blender 5.2 compatible)."""

    if bg_color is None:
        bg_color = (0.0, 0.0, 0.0)

    source_scene = bpy.context.scene
    prefs = utils.get_addon_preferences()

    # ----------------------------------------------------------------
    # Blender 5.x: use_lock_interface lives on scene.render, not
    # preferences.view.  render_display_type is still on prefs.view.
    # ----------------------------------------------------------------
    viewport_prefs = bpy.context.preferences.view
    prev_display = getattr(viewport_prefs, 'render_display_type', None)

    prev_lock_render = getattr(source_scene.render, 'use_lock_interface', False)

    try:
        temp_scene = _get_or_create_temp_scene()

        temp_scene.render.resolution_x = source_scene.render.resolution_x
        temp_scene.render.resolution_y = source_scene.render.resolution_y
        temp_scene.render.resolution_percentage = source_scene.render.resolution_percentage

        _setup_background(temp_scene, transparent_bg, bg_color, bg_strength)

        object_map = {}
        success_count = 0

        for src_obj in source_scene.objects:
            if src_obj.type not in ('MESH', 'CAMERA', 'LIGHT', 'EMPTY'):
                continue
            if src_obj.hide_render and src_obj != camera_obj:
                continue
            new_obj = _copy_object_to_scene(src_obj, temp_scene)
            if new_obj:
                object_map[src_obj.name] = new_obj
                success_count += 1

        if success_count == 0:
            raise RuntimeError("No objects copied")

        _apply_object_colors(temp_scene, use_object_colors, default_object_color)
        _mark_freestyle_edges(temp_scene)

        # Choose render engine — BLENDER_EEVEE removed in Blender 5.x
        available_engines = utils.get_available_engines()

        if 'CYCLES' in available_engines:
            selected_engine = 'CYCLES'
        elif 'BLENDER_EEVEE_NEXT' in available_engines:
            selected_engine = 'BLENDER_EEVEE_NEXT'
        else:
            selected_engine = 'BLENDER_WORKBENCH'

        temp_scene.render.engine = selected_engine

        _setup_freestyle(temp_scene, thickness, color)

        source_img = source_scene.render.image_settings
        temp_img = temp_scene.render.image_settings
        temp_img.file_format = source_img.file_format

        for attr in ['color_mode', 'quality', 'compression']:
            if hasattr(source_img, attr) and hasattr(temp_img, attr):
                try:
                    setattr(temp_img, attr, getattr(source_img, attr))
                except:
                    pass

        if transparent_bg and hasattr(temp_img, 'color_mode'):
            temp_img.color_mode = 'RGBA'

        temp_scene.render.use_file_extension = True
        temp_scene.render.filepath = bpy.path.abspath(output_path_noext)

        temp_camera = object_map.get(camera_obj.name) if camera_obj else None
        if temp_camera:
            temp_scene.camera = temp_camera

        temp_scene.render.use_simplify = True
        temp_scene.render.simplify_subdivision = 0

        if selected_engine == 'CYCLES':
            cycles = getattr(temp_scene, 'cycles', None)
            if cycles:
                if hasattr(cycles, 'samples'):
                    cycles.samples = 32
                if hasattr(cycles, 'use_denoising'):
                    cycles.use_denoising = False

        elif selected_engine == 'BLENDER_EEVEE_NEXT':
            eevee = getattr(temp_scene, 'eevee', None)
            if eevee:
                # Blender 5.x EEVEE Next properties (guarded with hasattr)
                if hasattr(eevee, 'taa_render_samples'):
                    eevee.taa_render_samples = 16

        if disable_shadows:
            if selected_engine == 'CYCLES':
                cycles = getattr(temp_scene, 'cycles', None)
                if cycles and hasattr(cycles, 'max_bounces'):
                    cycles.max_bounces = 2
            elif selected_engine == 'BLENDER_EEVEE_NEXT':
                eevee = getattr(temp_scene, 'eevee', None)
                if eevee and hasattr(eevee, 'use_shadows'):
                    eevee.use_shadows = False

        # Lock interface on scene.render (Blender 5.x)
        if hasattr(source_scene.render, 'use_lock_interface'):
            try:
                source_scene.render.use_lock_interface = True
            except (AttributeError, RuntimeError):
                pass

        if prev_display is not None and hasattr(viewport_prefs, 'render_display_type'):
            try:
                viewport_prefs.render_display_type = 'NONE'
            except (AttributeError, RuntimeError):
                pass

        with bpy.context.temp_override(scene=temp_scene):
            try:
                bpy.context.view_layer.update()
            except Exception as e:
                logger.warning(f"View layer update: {e}")

            # bpy.ops.render.opengl removed in Blender 4.1+ — use render.render
            bpy.ops.render.render(write_still=True, use_viewport=False)

    except Exception as e:
        logger.error(f"Wireframe render failed: {e}")
        import traceback
        traceback.print_exc()
        raise RuntimeError(f"Wireframe render failed: {e}")

    finally:
        # Restore render_display_type
        if prev_display is not None and hasattr(viewport_prefs, 'render_display_type'):
            try:
                viewport_prefs.render_display_type = prev_display
            except:
                pass

        # Restore lock interface
        if hasattr(source_scene.render, 'use_lock_interface'):
            try:
                source_scene.render.use_lock_interface = prev_lock_render
            except:
                pass


# ============================================================================
# Utility Functions
# ============================================================================

def check_for_leaks() -> Dict[str, int]:
    leaks = {'scenes': 0, 'meshes': 0, 'objects': 0,
              'materials': 0, 'worlds': 0, 'cached_materials': 0, 'total': 0}
    try:
        for scene in bpy.data.scenes:
            if scene.name.startswith("TLX_"): leaks['scenes'] += 1
        for mesh in bpy.data.meshes:
            if mesh.name.startswith("TLX_WF_"): leaks['meshes'] += 1
        for obj in bpy.data.objects:
            if obj.name.startswith("TLX_WF_"): leaks['objects'] += 1
        for mat in bpy.data.materials:
            if mat.name.startswith("TLX_WF_"): leaks['materials'] += 1
        for world in bpy.data.worlds:
            if world.name.startswith("TLX_"): leaks['worlds'] += 1
        leaks['cached_materials'] = len(_material_cache)
        leaks['total'] = sum(v for k, v in leaks.items()
                             if k not in ['total', 'cached_materials'])
    except Exception as e:
        logger.error(f"Leak check error: {e}")
    return leaks


def force_cleanup():
    logger.info("Force cleanup...")
    _cleanup_temp_resources(force=True)
    print_cache_stats()


# ============================================================================
# Registration
# ============================================================================

def register():
    logger.info("Wireframe module registered (Blender 5.2 compatible)")
    leaks = check_for_leaks()
    if leaks['total'] > 0:
        logger.warning(f"Found {leaks['total']} existing TLX data blocks")
        force_cleanup()


def unregister():
    logger.info("Unregistering wireframe module")
    print_cache_stats()
    force_cleanup()
    leaks = check_for_leaks()
    if leaks['total'] > 0:
        logger.warning(f"⚠ {leaks['total']} data blocks remain after cleanup")
    else:
        logger.info("✓ Clean unregister, zero leaks")
    logger.info("Wireframe module unregistered")
