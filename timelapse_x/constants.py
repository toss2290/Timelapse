"""
Constants and configuration values for Timelapse X addon.
Blender 5.1+ compatible.

Cleanup (v2.5.4):
  - Removed WIREFRAME_STRATEGIES: was only used by preferences.wireframe_strategy
    which itself was dead (the renderer always uses Freestyle regardless of
    that setting, and the setting value was never read).
"""

from typing import Final

# Timing
MIN_INTERVAL:       Final[float] = 0.2
DEFAULT_INTERVAL:   Final[float] = 2.0
DEFAULT_SUPPRESS_MS: Final[int]  = 120

# File Format
IMAGE_EXTENSIONS:    Final[tuple] = ('.png', '.jpg', '.jpeg')
DEFAULT_ZERO_PADDING: Final[int]  = 4

# Performance
DEFAULT_DOWNSCALE_SIZE:  Final[int]   = 64
DEFAULT_IDLE_THRESHOLD:  Final[float] = 0.01

# Video Export
DEFAULT_VIDEO_FPS:          Final[int] = 30
DEFAULT_VIDEO_BITRATE:      Final[int] = 6000
DEFAULT_GOP_SIZE_MULTIPLIER: Final[int] = 2

# UI
ADDON_NAME:    Final[str] = "Timelapse X"
PANEL_CATEGORY: Final[str] = "Timelapse X"

# Wireframe defaults
DEFAULT_WIREFRAME_BG_COLOR:  Final[tuple] = (0.0, 0.0, 0.0)
DEFAULT_WIREFRAME_LINE_COLOR: Final[tuple] = (0.0, 0.0, 0.0)
DEFAULT_WIREFRAME_THICKNESS:  Final[float] = 1.0
DEFAULT_WIREFRAME_BG_STRENGTH: Final[float] = 1.0

# Render Engine Preferences
# BLENDER_EEVEE removed in Blender 5.x — only BLENDER_EEVEE_NEXT valid.
RENDER_ENGINE_PREFERENCES: Final[dict] = {
    'RENDERED': ['CYCLES', 'BLENDER_EEVEE_NEXT'],
    'MATERIAL': ['BLENDER_EEVEE_NEXT', 'CYCLES'],
    'SOLID':    ['BLENDER_WORKBENCH', 'CYCLES'],
    'WIREFRAME':['BLENDER_WORKBENCH', 'CYCLES', 'BLENDER_EEVEE_NEXT'],
}

# Shading Type Enums
SHADING_TYPES: Final[list] = [
    ('SOLID',     'Solid',     'Solid shading'),
    ('MATERIAL',  'Material',  'Material preview'),
    ('RENDERED',  'Rendered',  'Rendered view'),
    ('WIREFRAME', 'Wireframe', 'Wireframe view'),
]

# Capture Mode Enums
CAPTURE_MODES: Final[list] = [
    ('WINDOW',      'Blender Window', 'Capture the Blender window/viewport'),
    ('CAMERA_LIST', 'Camera List',    'Render from multiple cameras'),
]

# Image Format Enums
IMAGE_FORMATS: Final[list] = [
    ('PNG',  'PNG (lossless)', 'PNG format with lossless compression'),
    ('JPEG', 'JPEG (small)',   'JPEG format with lossy compression'),
]

# Speed Preset Enums
SPEED_PRESETS: Final[list] = [
    ('ULTRA_FAST', 'Ultra Fast', 'Maximum speed, lower quality'),
    ('BALANCED',   'Balanced',   'Good balance between speed and quality'),
    ('QUALITY',    'Quality',    'Best quality, slower capture'),
]

# MP4 Output Mode Enums
MP4_OUTPUT_MODES: Final[list] = [
    ('SAME_AS_IMAGES', 'Same as Images', 'Save MP4 in same folder as images'),
    ('CUSTOM_DIR',     'Custom Folder',  'Save MP4 in custom folder'),
]

# Window Capture Scope Enums
WINDOW_CAPTURE_SCOPES: Final[list] = [
    ('VIEW3D', 'VIEW_3D Area',        'Capture only the 3D viewport area'),
    ('FULL',   'Full Blender Window', 'Capture the entire Blender window'),
]

# Clean Window Shading Enums
CLEAN_WINDOW_SHADING: Final[list] = [
    ('KEEP',      'Keep',      'Keep current shading'),
    ('SOLID',     'Solid',     'Solid shading'),
    ('MATERIAL',  'Material',  'Material Preview'),
    ('RENDERED',  'Rendered',  'Rendered view'),
    ('WIREFRAME', 'Wireframe', 'Wireframe'),
]

# Edit Mode Behavior Enums
EDIT_MODE_BEHAVIORS: Final[list] = [
    ('CAPTURE_ANYWAY', 'Capture Anyway',
     'Capture even in Edit mode (may cause crashes — USE AT YOUR OWN RISK)'),
    ('SKIP',           'Skip Capture',
     'Skip capturing when in Edit mode (safe, recommended)'),
    ('FORCE_OBJECT',   'Force Object Mode',
     'Temporarily switch to Object mode for capture'),
]


# ========================================================================================
# Helper Functions
# ========================================================================================

def get_engine_preference_for_shading(shading_type: str) -> list:
    return RENDER_ENGINE_PREFERENCES.get(shading_type, ['BLENDER_WORKBENCH', 'CYCLES'])


def validate_interval(interval: float) -> float:
    return max(MIN_INTERVAL, float(interval))


def validate_threshold(threshold: float) -> float:
    return max(0.0, min(0.2, float(threshold)))


def register():
    print("constants.py: register() called")


def unregister():
    print("constants.py: unregister() called")
