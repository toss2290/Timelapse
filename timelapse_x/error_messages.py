"""
User-friendly error message system for Timelapse X.

Fixes (v2.5.4):
  - report_validation_error(): fragile hand-written string matching
    (checking for "Camera list is empty" in str(e)) replaced with
    exception class inspection where possible, with clearer fallback.
  - report_success(): was printing str(context) (the kwargs dict) raw
    to the UI on unknown keys. Now logs a warning and shows a generic
    success message instead.
"""

import logging
from typing import Optional, Dict
from enum import Enum

logger = logging.getLogger(__name__)


# ========================================================================================
# Error Categories
# ========================================================================================

class ErrorCategory(Enum):
    SETUP      = "setup"
    CAMERA     = "camera"
    FILE_SYSTEM= "file_system"
    PERMISSION = "permission"
    DISK_SPACE = "disk_space"
    RENDER     = "render"
    VERSION    = "version"
    UI         = "ui"
    CAPTURE    = "capture"
    VIDEO      = "video"
    UNKNOWN    = "unknown"


# ========================================================================================
# Error Message Database
# ========================================================================================

ERROR_MESSAGES = {
    "already_recording": {
        "category": ErrorCategory.SETUP,
        "brief": "Already recording",
        "detailed": "A recording session is already in progress.",
        "solution": "Stop the current recording before starting a new one.",
        "icon": "INFO"
    },
    "no_preferences": {
        "category": ErrorCategory.SETUP,
        "brief": "Addon preferences not found",
        "detailed": "Could not access Timelapse X addon preferences.",
        "solution": "Try reloading the addon or restarting Blender.",
        "icon": "ERROR"
    },
    "session_dir_missing": {
        "category": ErrorCategory.FILE_SYSTEM,
        "brief": "Recording folder disappeared",
        "detailed": "The session folder was deleted or became inaccessible during recording.",
        "solution": "Check if the drive was unmounted or folder deleted. Recording will stop.",
        "icon": "ERROR"
    },
    "camera_list_empty": {
        "category": ErrorCategory.CAMERA,
        "brief": "No cameras in list",
        "detailed": "Camera List mode requires at least one camera, but the list is empty.",
        "solution": "Click the '+' button to add cameras to the list, or switch to Window mode.",
        "icon": "ERROR"
    },
    "camera_invalid": {
        "category": ErrorCategory.CAMERA,
        "brief": "Invalid camera",
        "detailed": "Camera '{name}' is not valid or has been deleted.",
        "solution": "Remove the invalid camera from the list and add a valid one.",
        "icon": "ERROR"
    },
    "no_valid_cameras": {
        "category": ErrorCategory.CAMERA,
        "brief": "No valid cameras found",
        "detailed": "All cameras in the list are invalid, deleted, or not accessible.",
        "solution": "Clear the camera list and add valid cameras, or create new cameras.",
        "icon": "ERROR"
    },
    "invalid_output_path": {
        "category": ErrorCategory.FILE_SYSTEM,
        "brief": "Invalid output folder",
        "detailed": "The output folder path is invalid: {path}",
        "solution": "Check the path in Preferences → Output Directory. Use '//' for relative paths.",
        "icon": "ERROR"
    },
    "cannot_create_directory": {
        "category": ErrorCategory.FILE_SYSTEM,
        "brief": "Cannot create folder",
        "detailed": "Failed to create output folder: {error}",
        "solution": "Check folder permissions and ensure the parent directory exists.",
        "icon": "ERROR"
    },
    "no_write_permission": {
        "category": ErrorCategory.PERMISSION,
        "brief": "No write permission",
        "detailed": "Cannot write to folder: {path}",
        "solution": "Choose a different folder.",
        "icon": "ERROR"
    },
    "insufficient_disk_space": {
        "category": ErrorCategory.DISK_SPACE,
        "brief": "Low disk space",
        "detailed": "Only {available}MB free. Timelapse recording needs at least {required}MB.",
        "solution": "Free up disk space or choose a different drive.",
        "icon": "ERROR"
    },
    "disk_almost_full": {
        "category": ErrorCategory.DISK_SPACE,
        "brief": "Disk space running low",
        "detailed": "Only {available}MB remaining. Recording may fail soon.",
        "solution": "Monitor disk space and free up storage if needed.",
        "icon": "WARNING"
    },
    "render_failed": {
        "category": ErrorCategory.RENDER,
        "brief": "Render failed",
        "detailed": "Camera '{camera}' failed to render: {error}",
        "solution": "Check render settings, memory usage, and console for details.",
        "icon": "ERROR"
    },
    "blender_version_old": {
        "category": ErrorCategory.VERSION,
        "brief": "Blender version too old",
        "detailed": "Timelapse X requires Blender {required} or newer. You're running {current}.",
        "solution": "Download the latest Blender from blender.org",
        "icon": "ERROR"
    },
    "headless_mode_window": {
        "category": ErrorCategory.UI,
        "brief": "Window mode needs GUI",
        "detailed": "Window capture mode cannot run without a GUI (headless/background mode).",
        "solution": "Use Camera List mode instead, or run Blender normally.",
        "icon": "ERROR"
    },
    "no_ui_context": {
        "category": ErrorCategory.UI,
        "brief": "No viewport available",
        "detailed": "Cannot find a 3D viewport to capture.",
        "solution": "Open a 3D viewport window, or use Camera List mode instead.",
        "icon": "ERROR"
    },
    "capture_failed": {
        "category": ErrorCategory.CAPTURE,
        "brief": "Capture failed",
        "detailed": "Failed to capture frame: {error}",
        "solution": "Check console for details. Try reducing quality settings or resolution.",
        "icon": "ERROR"
    },
    "timer_creation_failed": {
        "category": ErrorCategory.CAPTURE,
        "brief": "Cannot start recording timer",
        "detailed": "Failed to create recording timer: {error}",
        "solution": "Restart Blender or try a different interval setting.",
        "icon": "ERROR"
    },
    "invalid_interval": {
        "category": ErrorCategory.CAPTURE,
        "brief": "Invalid capture interval",
        "detailed": "Interval {value}s is outside valid range ({min}s - {max}s).",
        "solution": "Set an interval between {min}s and {max}s.",
        "icon": "ERROR"
    },
    "no_images_found": {
        "category": ErrorCategory.VIDEO,
        "brief": "No images to compile",
        "detailed": "No image files found in: {path}",
        "solution": "Record some frames first, or check that the folder contains PNG/JPEG images.",
        "icon": "ERROR"
    },
    "video_compilation_failed": {
        "category": ErrorCategory.VIDEO,
        "brief": "Video compilation failed",
        "detailed": "Failed to create MP4: {error}",
        "solution": "Check that FFMPEG is available and folder permissions are correct.",
        "icon": "ERROR"
    },
}


# ========================================================================================
# Error Message Formatter
# ========================================================================================

class ErrorMessage:
    def __init__(self, error_key: str, context: Optional[Dict] = None,
                 technical_details: Optional[str] = None):
        self.error_key        = error_key
        self.context          = context or {}
        self.technical_details = technical_details
        self.template = ERROR_MESSAGES.get(error_key, {
            "category": ErrorCategory.UNKNOWN,
            "brief": "Unexpected error",
            "detailed": str(technical_details) if technical_details else "An unknown error occurred.",
            "solution": "Check the console for details.",
            "icon": "ERROR"
        })

    def get_brief(self)    -> str: return self._fmt(self.template["brief"])
    def get_detailed(self) -> str: return self._fmt(self.template["detailed"])
    def get_solution(self) -> str: return self._fmt(self.template["solution"])
    def get_icon(self)     -> str: return self.template["icon"]
    def get_category(self):        return self.template["category"]

    def _fmt(self, template: str) -> str:
        try:
            return template.format(**self.context)
        except KeyError as e:
            logger.debug(f"Missing context variable for error template: {e}")
            return template

    def log(self, level: str = "error"):
        log_func = getattr(logger, level, logger.error)
        message  = f"[{self.error_key}] {self.get_detailed()}"
        if self.technical_details:
            message += f" | Technical: {self.technical_details}"
        log_func(message)

    def report_to_user(self, operator, report_type: Optional[str] = None):
        if report_type is None:
            report_type = self.get_icon()
        operator.report({report_type}, self.get_brief())
        self.log(level="error" if report_type == "ERROR" else "warning")
        print(f"\n{'='*60}\nTIMELAPSE X - {self.get_brief()}\n{'='*60}")
        print(f"Details:  {self.get_detailed()}")
        print(f"Solution: {self.get_solution()}")
        if self.technical_details:
            print(f"Technical: {self.technical_details}")
        print(f"{'='*60}\n")


# ========================================================================================
# Convenience Functions
# ========================================================================================

def create_error(error_key: str, **context) -> ErrorMessage:
    return ErrorMessage(error_key, context=context)


def create_technical_error(error_key: str, exception: Exception,
                            **context) -> ErrorMessage:
    return ErrorMessage(error_key, context=context, technical_details=str(exception))


def report_validation_error(operator, validation_error,
                             fallback_key: str = "unknown") -> None:
    """
    Report a ValidationError to user.

    FIX v2.5.4: Previously matched exception messages with fragile
    hand-written string checks. Now uses exception class name where
    possible, with a clearly labelled string fallback for legacy paths.
    """
    error_msg  = str(validation_error)
    error_key  = fallback_key
    context    = {}

    # Try class-based match first (most reliable)
    class_name = type(validation_error).__name__
    if class_name in ('CameraListEmpty', 'NoCamerasError'):
        error_key = "camera_list_empty"
    elif class_name in ('NoValidCameras',):
        error_key = "no_valid_cameras"
    elif class_name in ('DiskSpaceError',):
        error_key = "insufficient_disk_space"
    elif class_name in ('VersionError',):
        error_key = "blender_version_old"

    # Fallback: keyword match on message (kept but clearly labelled)
    elif "Camera list is empty" in error_msg or "camera_list_empty" in error_msg:
        error_key = "camera_list_empty"
    elif "No valid cameras" in error_msg:
        error_key = "no_valid_cameras"
    elif "Cannot create directory" in error_msg or "Cannot create" in error_msg:
        error_key = "cannot_create_directory"
        context   = {"error": error_msg}
    elif "not writable" in error_msg.lower():
        error_key = "no_write_permission"
    elif "Insufficient disk space" in error_msg:
        error_key = "insufficient_disk_space"
        import re as _re
        numbers = _re.findall(r'(\d+(?:\.\d+)?)MB', error_msg)
        if len(numbers) >= 2:
            context = {"available": numbers[0], "required": numbers[1]}
    elif "version" in error_msg.lower() and "required" in error_msg.lower():
        error_key = "blender_version_old"

    error = ErrorMessage(error_key, context=context, technical_details=error_msg)
    error.report_to_user(operator)


# ========================================================================================
# Success Messages
# ========================================================================================

SUCCESS_MESSAGES = {
    "recording_started":  "Recording started! Interval: {interval}s, Mode: {mode}",
    "recording_stopped":  "Recording stopped. Captured {count} frames.",
    "recording_paused":   "Recording paused.",
    "recording_resumed":  "Recording resumed. Interval: {interval}s",
    "camera_added":       "Camera '{name}' added to list.",
    "camera_removed":     "Camera '{name}' removed from list.",
    "video_compiled":     "Video exported successfully! Duration: {duration}s, Size: {size}",
    "session_compiled":   "Compiled {count} videos from session.",
    "interval_updated":   "Capture interval updated to {interval}s.",
    "preset_applied":     "Speed preset '{preset}' applied.",
    "folder_opened":      "Folder opened: {path}",
    "settings_saved":     "Settings saved.",
    "clean_window_created": "Clean window created for recording.",
    "clean_window_restored": "Original window restored.",
}


def report_success(operator, message_key: str, **context):
    """
    Report success message to user.

    FIX v2.5.4: On unknown key, log a warning and show a generic
    message instead of printing str(context) raw to the UI.
    """
    if message_key in SUCCESS_MESSAGES:
        template = SUCCESS_MESSAGES[message_key]
        try:
            message = template.format(**context)
        except KeyError as e:
            logger.warning(f"Missing context for success message '{message_key}': {e}")
            message = template  # show unformatted template
    else:
        logger.warning(f"Unknown success message key: '{message_key}'")
        message = "Operation completed successfully."

    operator.report({'INFO'}, message)
    logger.info(f"[SUCCESS] {message}")


# ========================================================================================
# Helper utilities
# ========================================================================================

def format_file_size(size_bytes: int) -> str:
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f}{unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f}PB"


def format_duration(seconds: float) -> str:
    if   seconds < 60:   return f"{seconds:.1f}s"
    elif seconds < 3600: return f"{seconds/60:.1f}m"
    else:                return f"{seconds/3600:.1f}h"


# ========================================================================================
# Registration
# ========================================================================================

def register():
    logger.info("Error message system loaded (v2.5.4: cleaner validation matching)")


def unregister():
    pass
