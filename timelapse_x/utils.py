"""
Utility functions for Timelapse X addon.

Optimizations (v2.6.0):
  - get_addon_preferences(): result now cached with a 5-second TTL.
    During recording this is called 4-10x per timer tick; the bpy.context
    addons dict walk is O(n) and not free.
  - get_available_engines(): result cached permanently. RNA enum introspection
    is expensive and the set of installed render engines never changes at
    runtime.
  - find_window_area_region(): result cached with a 3-second TTL and
    validated by checking the area type. The linear window/screen/area walk
    was executed on every single window capture.
  - count_session_stats() / _count_session_stats_panel(): use os.scandir()
    instead of os.walk() for a significant speedup on large trees.
  - Removed expandvars() (v2.5.4 fix kept).
  - validate kwarg removed from find_window_area_region() (v2.5.4 kept).
"""

import bpy
import os
import re
import time
import logging
from typing import Optional, Tuple, List
from datetime import datetime

from . import constants

logger = logging.getLogger(__name__)


# ========================================================================================
# Path Validation
# ========================================================================================

class ValidationError(Exception):
    pass


def validate_path_safe(path: str, must_exist: bool = False,
                       must_be_dir: bool = False, must_be_file: bool = False,
                       must_be_writable: bool = False) -> str:
    if not path or not isinstance(path, str):
        raise ValidationError("Path is empty or invalid")
    if '\x00' in path:
        raise ValidationError("Path contains null byte")

    try:
        expanded = os.path.expanduser(path)
        if expanded.startswith('//'):
            if bpy.data.is_saved:
                blend_dir = os.path.dirname(bpy.data.filepath)
                expanded  = os.path.join(blend_dir, expanded[2:])
            else:
                expanded  = os.path.join(os.path.expanduser('~'), expanded[2:])
        normalized = os.path.normpath(os.path.abspath(expanded))
    except Exception as e:
        raise ValidationError(f"Cannot normalize path: {e}")

    if must_exist and not os.path.exists(normalized):
        raise ValidationError(f"Path does not exist: {normalized}")
    if os.path.exists(normalized):
        if must_be_dir  and not os.path.isdir(normalized):
            raise ValidationError(f"Not a directory: {normalized}")
        if must_be_file and not os.path.isfile(normalized):
            raise ValidationError(f"Not a file: {normalized}")
        if must_be_writable and not os.access(normalized, os.W_OK):
            raise ValidationError(f"Not writable: {normalized}")
    elif must_be_writable:
        parent = os.path.dirname(normalized)
        if os.path.exists(parent) and not os.access(parent, os.W_OK):
            raise ValidationError(f"Parent directory not writable: {parent}")
    return normalized


def validate_input_path(path: str) -> str:
    return validate_path_safe(path, must_exist=True)

def validate_output_path(path: str) -> str:
    return validate_path_safe(path, must_be_writable=True)

def validate_directory(path: str, must_exist: bool = True,
                       must_be_writable: bool = False) -> str:
    return validate_path_safe(path, must_exist=must_exist,
                               must_be_dir=True, must_be_writable=must_be_writable)

def validate_file(path: str, must_exist: bool = True) -> str:
    return validate_path_safe(path, must_exist=must_exist, must_be_file=True)


# ========================================================================================
# File System Operations
# ========================================================================================

def ensure_directory(path: str, validate_writable: bool = False) -> str:
    abs_path = bpy.path.abspath(path)
    try:
        os.makedirs(abs_path, exist_ok=True)
    except OSError as e:
        raise ValidationError(f"Cannot create directory: {e}")
    if validate_writable and not os.access(abs_path, os.W_OK):
        raise ValidationError(f"Directory not writable: {abs_path}")
    return abs_path


def get_dated_folder(base_dir: str) -> str:
    validated_base = ensure_directory(base_dir)
    dated_path     = os.path.join(validated_base, datetime.now().strftime('%Y%m%d'))
    try:
        os.makedirs(dated_path, exist_ok=True)
        return dated_path
    except OSError as e:
        raise ValidationError(f"Cannot create dated folder: {e}")


def get_session_folder(base_dir: str) -> str:
    dated_folder = get_dated_folder(base_dir)
    session_path = os.path.join(dated_folder, datetime.now().strftime('%H%M%S'))
    try:
        os.makedirs(session_path, exist_ok=True)
        return session_path
    except OSError as e:
        raise ValidationError(f"Cannot create session folder: {e}")


_UNSAFE_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')

def sanitize_filename(name: str, replacement: str = '_', max_length: int = 200) -> str:
    if not name:
        return 'unnamed'
    sanitized = _UNSAFE_CHARS.sub(replacement, name)
    sanitized = sanitized.strip('. ')
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length]
    return sanitized or 'unnamed'


def generate_filename(base_dir: str, prefix: str, index: int,
                      extension: str, zero_padding: int = 4) -> str:
    safe_prefix = sanitize_filename(prefix)
    safe_ext    = extension.lower().strip('.')
    filename    = f"{safe_prefix}_{str(index).zfill(zero_padding)}.{safe_ext}"
    return os.path.join(base_dir, filename)


# ========================================================================================
# Scene / Camera Validation
# ========================================================================================

def validate_scene(scene) -> None:
    if not scene:
        raise ValidationError("Scene is None")
    if not hasattr(scene, 'render'):
        raise ValidationError("Scene has no render settings")
    render = scene.render
    if render.resolution_x <= 0 or render.resolution_y <= 0:
        raise ValidationError(f"Invalid resolution: {render.resolution_x}x{render.resolution_y}")


def validate_camera(camera) -> None:
    if not camera:
        raise ValidationError("Camera is None")
    if camera.type != 'CAMERA':
        raise ValidationError(f"Not a camera: {camera.type}")


def validate_camera_list(scene) -> None:
    if not hasattr(scene, 'tlx_cameras') or len(scene.tlx_cameras) == 0:
        raise ValidationError("Camera list is empty")
    valid_count = sum(1 for item in scene.tlx_cameras
                      if item.camera and item.camera.type == 'CAMERA')
    if valid_count == 0:
        raise ValidationError("No valid cameras in list")


def validate_blender_version(min_version: Tuple[int, int, int] = (4, 0, 0)) -> bool:
    if bpy.app.version < min_version:
        raise ValidationError(
            f"Blender {min_version[0]}.{min_version[1]} or higher required, "
            f"running {bpy.app.version[0]}.{bpy.app.version[1]}"
        )
    return True


def validate_disk_space(path: str, required_mb: int = 100) -> bool:
    try:
        import shutil
        stat    = shutil.disk_usage(bpy.path.abspath(path))
        free_mb = stat.free / (1024 * 1024)
        if free_mb < required_mb:
            raise ValidationError(
                f"Insufficient disk space: {free_mb:.1f}MB available, "
                f"{required_mb}MB required"
            )
        return True
    except Exception as e:
        logger.warning(f"Cannot check disk space: {e}")
        return True


# ========================================================================================
# Addon Preferences — CACHED
# ========================================================================================
# During recording the timer fires every N seconds and every branch of the
# modal + capture stack calls get_addon_preferences().  The bpy addons dict
# walk is not free; cache it with a short TTL.

_prefs_cache: object          = None
_prefs_cache_time: float      = 0.0
_PREFS_CACHE_TTL: float       = 5.0   # seconds — safe; prefs don't change during recording


def invalidate_prefs_cache() -> None:
    """Force re-lookup on the next get_addon_preferences() call.
    Call this after addon reload or prefs save."""
    global _prefs_cache, _prefs_cache_time
    _prefs_cache      = None
    _prefs_cache_time = 0.0


def get_addon_preferences(validate: bool = False):
    global _prefs_cache, _prefs_cache_time
    now = time.monotonic()
    if _prefs_cache is not None and (now - _prefs_cache_time) < _PREFS_CACHE_TTL:
        return _prefs_cache

    result = _lookup_addon_preferences()
    _prefs_cache      = result
    _prefs_cache_time = now
    return result


def _lookup_addon_preferences():
    try:
        addons     = bpy.context.preferences.addons
        addon_keys = ('timelapse_x', __package__, __name__.split('.')[0])
        for key in addon_keys:
            addon_module = addons.get(key)
            if addon_module and hasattr(addon_module, 'preferences'):
                return addon_module.preferences
        for _key, addon_module in addons.items():
            prefs = getattr(addon_module, 'preferences', None)
            if prefs and getattr(prefs, 'bl_idname', '') in addon_keys:
                return prefs
    except Exception:
        pass
    logger.warning("Addon preferences not found")
    return None


# ========================================================================================
# Environment Detection
# ========================================================================================

def is_headless() -> bool:
    try:
        wm = getattr(bpy.context, "window_manager", None)
        if not wm:
            return True
        return len(getattr(wm, "windows", [])) == 0
    except (AttributeError, RuntimeError):
        return True


# ========================================================================================
# Render Engine Management — CACHED
# ========================================================================================
# RNA enum introspection is expensive; available engines never change at runtime.

_engines_cache: Optional[List[str]] = None


def invalidate_engine_cache() -> None:
    global _engines_cache
    _engines_cache = None


def get_available_engines() -> List[str]:
    global _engines_cache
    if _engines_cache is not None:
        return _engines_cache
    try:
        _engines_cache = [
            e.identifier for e in
            bpy.types.RenderSettings.bl_rna.properties['engine'].enum_items
        ]
    except (AttributeError, KeyError):
        _engines_cache = ['BLENDER_WORKBENCH', 'BLENDER_EEVEE_NEXT', 'CYCLES']
    return _engines_cache


def choose_best_engine(preferences: List[str]) -> str:
    available = set(get_available_engines())
    for engine in preferences:
        if engine in available:
            return engine
    current = getattr(bpy.context.scene.render, "engine", None)
    if current and current in available:
        return current
    return next(iter(available)) if available else 'BLENDER_WORKBENCH'


def get_engine_for_shading(shading_type: str) -> str:
    pref_list = constants.get_engine_preference_for_shading(shading_type)
    return choose_best_engine(pref_list)


# ========================================================================================
# Window / Area / Region Finding — CACHED
# ========================================================================================
# find_window_area_region() is called on every window capture tick.  Caching
# the result with a short TTL (~3 s) avoids the linear window/screen/area
# walk almost entirely under normal usage.

_winar_cache: Optional[Tuple] = None
_winar_cache_time: float      = 0.0
_WINAR_CACHE_TTL: float       = 3.0


def invalidate_window_cache() -> None:
    global _winar_cache, _winar_cache_time
    _winar_cache      = None
    _winar_cache_time = 0.0


def _validate_winar_cache(cache: Tuple) -> bool:
    """Return True if the cached (window, screen, area, region) is still valid."""
    if cache is None:
        return False
    window, screen, area, region = cache
    try:
        if window is None or area is None or region is None:
            return False
        # Light validity check — accessing .type is a fast C property
        _ = area.type
        _ = region.type
        return True
    except (AttributeError, ReferenceError, RuntimeError):
        return False


def find_window_area_region() -> Tuple[Optional[object], ...]:
    """
    Find a suitable window, screen, area, and WINDOW region for screenshot.
    Result is cached for _WINAR_CACHE_TTL seconds.
    """
    global _winar_cache, _winar_cache_time
    now = time.monotonic()
    if (now - _winar_cache_time) < _WINAR_CACHE_TTL and _validate_winar_cache(_winar_cache):
        return _winar_cache

    result = _find_window_area_region_uncached()
    _winar_cache      = result
    _winar_cache_time = now
    return result


def _find_window_area_region_uncached() -> Tuple[Optional[object], ...]:
    wm = bpy.context.window_manager if bpy.context else None
    if not wm:
        return None, None, None, None

    for window in wm.windows:
        screen = window.screen
        if not screen:
            continue
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                region = next((r for r in area.regions if r.type == 'WINDOW'), None)
                if region:
                    return window, screen, area, region

    for window in wm.windows:
        screen = window.screen
        if screen and screen.areas:
            area   = screen.areas[0]
            region = next((r for r in area.regions if r.type == 'WINDOW'), None)
            if region:
                return window, screen, area, region

    return (
        bpy.context.window,
        bpy.context.window.screen if bpy.context.window else None,
        bpy.context.area,
        bpy.context.region,
    )


# ========================================================================================
# Image Format Configuration
# ========================================================================================

def configure_image_format(scene, format_type: str, png_rgba: bool = False,
                            jpeg_quality: int = 90):
    img = scene.render.image_settings
    if hasattr(img, 'media_type'):
        img.media_type = 'IMAGE'
    img.file_format = format_type
    if format_type == 'PNG':
        if hasattr(img, 'color_mode'):
            img.color_mode = 'RGBA' if png_rgba else 'RGB'
        if hasattr(img, 'compression'):
            img.compression = 15
    else:
        if hasattr(img, 'color_mode'):
            img.color_mode = 'RGB'
        if hasattr(img, 'quality'):
            img.quality = int(jpeg_quality)


# ========================================================================================
# Image Comparison
# ========================================================================================

def compare_images(path_a: str, path_b: str, downscale: int = 64,
                   early_exit_threshold: Optional[float] = None) -> float:
    try:
        from PIL import Image
        import numpy as np
        img_a = Image.open(bpy.path.abspath(path_a)).resize((downscale, downscale))
        img_b = Image.open(bpy.path.abspath(path_b)).resize((downscale, downscale))
        arr_a = np.array(img_a.convert('RGB'), dtype=np.float32) / 255.0
        arr_b = np.array(img_b.convert('RGB'), dtype=np.float32) / 255.0
        return float(np.abs(arr_a - arr_b).mean())
    except ImportError:
        return _compare_images_blender(path_a, path_b, downscale)


def _compare_images_blender(path_a: str, path_b: str, downscale: int) -> float:
    img_a = img_b = None
    try:
        img_a = bpy.data.images.load(bpy.path.abspath(path_a), check_existing=False)
        img_b = bpy.data.images.load(bpy.path.abspath(path_b), check_existing=False)
        img_a.scale(downscale, downscale)
        img_b.scale(downscale, downscale)
        from array import array
        n     = downscale * downscale * 4
        pix_a = array('f', [0.0] * n)
        pix_b = array('f', [0.0] * n)
        img_a.pixels.foreach_get(pix_a)
        img_b.pixels.foreach_get(pix_b)
        total = sum(abs(pix_a[i] - pix_b[i]) for i in range(0, n, 4))
        return total / (n // 4)
    except Exception as e:
        logger.error(f"Image comparison failed: {e}")
        return 1.0
    finally:
        for img in (img_a, img_b):
            if img:
                try:
                    bpy.data.images.remove(img, do_unlink=True)
                except Exception:
                    pass


# ========================================================================================
# Freestyle Edge Marks
# ========================================================================================

def apply_freestyle_marks_visibility(enabled: bool):
    for obj in bpy.data.objects:
        if obj.type == 'MESH' and obj.data:
            if hasattr(obj.data, 'show_freestyle_edge_marks'):
                try:
                    obj.data.show_freestyle_edge_marks = enabled
                except (AttributeError, RuntimeError):
                    pass


def get_blueprint_projection_flags(obj) -> Tuple[bool, bool]:
    """Return (show_in_persp, show_in_ortho) for an Empty-Image reference
    object, per its own show_empty_image_perspective/orthographic toggle.
    Defaults to (True, True) — always visible — for anything that isn't
    an Empty-Image, or on Blender versions without these properties."""
    if obj.type != 'EMPTY' or getattr(obj, 'empty_display_type', None) != 'IMAGE':
        return True, True
    show_persp = getattr(obj, 'show_empty_image_perspective', None)
    show_ortho = getattr(obj, 'show_empty_image_orthographic', None)
    if show_persp is not None and show_ortho is not None:
        return bool(show_persp), bool(show_ortho)
    return True, True


def apply_blueprint_auto_hide(context, projection: str,
                               visibility_attr: str = 'hide_viewport') -> List:
    """Temporarily hide Empty-Image reference blueprints that shouldn't
    be visible for `projection` ('PERSP' or 'ORTHO'), per each object's
    own show_empty_image_perspective/orthographic toggle. Live viewport
    display already respects these automatically, but OpenGL renders
    and forced-camera screenshots don't reliably — hence enforcing it
    explicitly here rather than trusting it happens on its own.

    visibility_attr picks WHICH visibility flag to toggle:
      - 'hide_viewport' (default) for viewport-shown captures — Window
        mode's screenshot, Stealth Camera.
      - 'hide_render' for actual bpy.ops.render.render() calls — Camera
        List mode, which renders through the real render pipeline, not
        the viewport, so hide_viewport has no effect on its output.

    Returns [(obj, visibility_attr), ...] for objects this call actually
    hid (already-hidden ones are left alone and excluded), so the caller
    can restore exactly what it changed via restore_blueprint_auto_hide()."""
    hidden = []
    try:
        for obj in context.view_layer.objects:
            if obj.type != 'EMPTY' or getattr(obj, 'empty_display_type', None) != 'IMAGE':
                continue
            show_persp, show_ortho = get_blueprint_projection_flags(obj)
            should_hide = (projection == 'PERSP' and not show_persp) or \
                          (projection == 'ORTHO' and not show_ortho)
            if should_hide and not getattr(obj, visibility_attr, False):
                setattr(obj, visibility_attr, True)
                hidden.append((obj, visibility_attr))
    except Exception as e:
        logger.warning(f"Blueprint auto-hide failed: {e}")
    return hidden


def restore_blueprint_auto_hide(hidden: List) -> None:
    for obj, attr in hidden:
        try:
            setattr(obj, attr, False)
        except (ReferenceError, RuntimeError):
            pass  # object may have been deleted mid-recording


def apply_collection_isolation(collection, visibility_attr: str = 'hide_viewport') -> Optional[Tuple]:
    """Temporarily hide `collection` via the given visibility flag —
    Collection.hide_viewport/hide_render cascade automatically to
    everything nested inside it, so no manual recursion is needed.

    visibility_attr picks WHICH flag to toggle, same reasoning as
    apply_blueprint_auto_hide(): 'hide_viewport' for viewport-shown
    captures (Window mode), 'hide_render' for actual render calls
    (Camera List mode).

    Returns (collection, visibility_attr, previous_value) to restore
    later via restore_collection_isolation(), or None if there's
    nothing to do (no collection given, or it's already hidden that
    way — in which case leave it alone rather than "restoring" it to
    visible when this call ends)."""
    if not collection:
        return None
    try:
        prev = getattr(collection, visibility_attr, None)
        if prev is None or prev:
            return None
        setattr(collection, visibility_attr, True)
        return (collection, visibility_attr, prev)
    except (AttributeError, RuntimeError, ReferenceError) as e:
        logger.warning(f"Collection isolation failed: {e}")
        return None


def restore_collection_isolation(backup: Optional[Tuple]) -> None:
    if not backup:
        return
    collection, attr, prev = backup
    try:
        setattr(collection, attr, prev)
    except (ReferenceError, RuntimeError):
        pass  # collection may have been deleted/unlinked mid-recording


# ========================================================================================
# Misc Helpers
# ========================================================================================

def format_file_size(size_bytes: int) -> str:
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"


def folder_has_images(path: str) -> bool:
    """Fast check using scandir — avoids building a full listdir list."""
    ext = constants.IMAGE_EXTENSIONS
    try:
        with os.scandir(path) as it:
            for entry in it:
                if entry.is_file(follow_symlinks=False) and entry.name.lower().endswith(ext):
                    return True
    except (OSError, PermissionError):
        pass
    return False


def count_session_stats(directory: str) -> Tuple[int, int]:
    """Count image files and total size using scandir (faster than os.walk on flat trees)."""
    if not directory or not os.path.isdir(directory):
        return 0, 0
    ext   = constants.IMAGE_EXTENSIONS
    count = total_size = 0
    try:
        _scandir_count(directory, ext, count_ref=[count], size_ref=[total_size])
        # Use the mutable-list trick to pass back by reference
        # Simpler: just call the helper and return its result
        return _scandir_count_r(directory, ext)
    except (OSError, PermissionError):
        pass
    return count, total_size


def _scandir_count_r(directory: str, ext: tuple) -> Tuple[int, int]:
    """Recursive scandir-based count — ~2x faster than os.walk on CPython."""
    count      = 0
    total_size = 0
    stack      = [directory]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                    elif entry.is_file(follow_symlinks=False) and entry.name.lower().endswith(ext):
                        count += 1
                        try:
                            total_size += entry.stat(follow_symlinks=False).st_size
                        except OSError:
                            pass
        except (OSError, PermissionError):
            pass
    return count, total_size


def _scandir_count(directory, ext, count_ref, size_ref):
    pass  # unused; kept to avoid NameError in older call sites


# ========================================================================================
# Registration
# ========================================================================================

def register():
    invalidate_prefs_cache()
    invalidate_engine_cache()
    invalidate_window_cache()
    logger.info("Utils registered (v2.6.0: prefs/engine/window caches, scandir)")


def unregister():
    invalidate_prefs_cache()
    invalidate_engine_cache()
    invalidate_window_cache()
    logger.info("Utils unregistered")
