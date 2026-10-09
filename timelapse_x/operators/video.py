"""
TIMELAPSE X — Video operators
Blender 5.1+ compatible, v2.5.1 fixes applied.

Fix applied (v2.5.1):
  - TLX_OT_compile_video: stage StringProperty was persistent between invocations.
    If the user opened then cancelled the file picker, 'stage' stayed as
    'PICK_IMAGES' and the next invoke() would skip the dialog entirely.
    Fixed by resetting stage to '' at the start of every invoke() call.
"""

import bpy
import os
import logging
import subprocess
import tempfile
import shlex
import hashlib
from datetime import datetime
from bpy.types import Operator
from bpy.props import StringProperty, IntProperty, BoolProperty
from typing import Optional, List, Tuple

from .. import utils
from .. import constants
from ..state_manager import StateManager

logger = logging.getLogger(__name__)


# ========================================================================================
# SECURITY: FFMPEG Validation & Sanitization
# ========================================================================================

class FFMPEGSecurityError(Exception):
    pass


class FFMPEGValidator:
    """FFMPEG security validator — command injection prevention."""

    ALLOWED_ARGS = {
        '-f', '-safe', '-i', '-c:v', '-preset', '-crf',
        '-pix_fmt', '-r', '-y', '-loglevel', '-threads',
        '-movflags', '-g', '-keyint_min', '-b:v', '-vf'
    }
    ALLOWED_CODECS   = {'libx264', 'libx265', 'libvpx', 'libvpx-vp9'}
    ALLOWED_PRESETS  = {
        'ultrafast', 'superfast', 'veryfast', 'faster', 'fast',
        'medium', 'slow', 'slower', 'veryslow'
    }
    ALLOWED_PIXFMTS  = {'yuv420p', 'yuv422p', 'yuv444p', 'rgb24', 'rgba'}

    def __init__(self):
        self.verified_paths = {}

    def validate_ffmpeg_path(self, ffmpeg_path: str) -> str:
        if not ffmpeg_path:
            raise FFMPEGSecurityError("FFMPEG path is empty")

        path_hash = hashlib.sha256(ffmpeg_path.encode()).hexdigest()
        if path_hash in self.verified_paths:
            cached = self.verified_paths[path_hash]
            if os.path.exists(cached):
                return cached

        try:
            abs_path = os.path.abspath(os.path.expanduser(ffmpeg_path))
        except Exception as e:
            raise FFMPEGSecurityError(f"Invalid path format: {e}")

        if '..' in abs_path or abs_path.count(os.sep) > 20:
            raise FFMPEGSecurityError(f"Suspicious path: {abs_path}")

        if not os.path.isfile(abs_path):
            raise FFMPEGSecurityError(f"FFMPEG not found or not a file: {abs_path}")

        if not os.access(abs_path, os.X_OK):
            raise FFMPEGSecurityError(f"FFMPEG not executable: {abs_path}")

        if not self._verify_ffmpeg_signature(abs_path):
            raise FFMPEGSecurityError(f"Not a valid FFMPEG executable: {abs_path}")

        self.verified_paths[path_hash] = abs_path
        return abs_path

    def _verify_ffmpeg_signature(self, path: str) -> bool:
        try:
            result = subprocess.run(
                [path, '-version'], capture_output=True, timeout=5,
                text=True, encoding='utf-8', errors='replace'
            )
            output = result.stdout.lower()
            return all(s in output for s in ['ffmpeg version', 'configuration:', 'libavcodec'])
        except Exception:
            return False

    def validate_input_file(self, file_path: str) -> str:
        if not file_path:
            raise FFMPEGSecurityError("Input file path is empty")
        abs_path = os.path.abspath(file_path)
        for char in ['|', '&', ';', '$', '`', '\n', '\r']:
            if char in file_path:
                raise FFMPEGSecurityError(f"Suspicious character in path: {repr(char)}")
        if not os.path.isfile(abs_path):
            raise FFMPEGSecurityError(f"File not found: {abs_path}")
        if not os.access(abs_path, os.R_OK):
            raise FFMPEGSecurityError(f"File not readable: {abs_path}")
        return abs_path

    def validate_output_path(self, output_path: str) -> str:
        if not output_path:
            raise FFMPEGSecurityError("Output path is empty")
        abs_path = os.path.abspath(output_path)
        for char in ['|', '&', ';', '$', '`', '\n', '\r']:
            if char in output_path:
                raise FFMPEGSecurityError(f"Suspicious character in path: {repr(char)}")
        parent = os.path.dirname(abs_path)
        if not os.path.exists(parent):
            raise FFMPEGSecurityError(f"Output directory does not exist: {parent}")
        if not os.access(parent, os.W_OK):
            raise FFMPEGSecurityError(f"Output directory not writable: {parent}")
        ext = os.path.splitext(abs_path)[1].lower()
        if ext not in {'.mp4', '.mov', '.avi', '.mkv', '.webm'}:
            raise FFMPEGSecurityError(f"Unsafe output extension: {ext}")
        return abs_path

    def build_safe_command(self, ffmpeg, input_file, output_file, fps=30,
                           codec='libx264', preset='medium', crf=23,
                           pixfmt='yuv420p', video_filter=None) -> List[str]:
        ffmpeg      = self.validate_ffmpeg_path(ffmpeg)
        input_file  = self.validate_input_file(input_file)
        output_file = self.validate_output_path(output_file)

        if not (1 <= fps <= 240):
            raise FFMPEGSecurityError(f"Invalid FPS: {fps}")
        if codec not in self.ALLOWED_CODECS:
            raise FFMPEGSecurityError(f"Unsafe codec: {codec}")
        if preset not in self.ALLOWED_PRESETS:
            raise FFMPEGSecurityError(f"Invalid preset: {preset}")
        if not (0 <= crf <= 51):
            raise FFMPEGSecurityError(f"Invalid CRF: {crf}")
        if pixfmt not in self.ALLOWED_PIXFMTS:
            raise FFMPEGSecurityError(f"Invalid pixfmt: {pixfmt}")

        cmd = [ffmpeg, '-f', 'concat', '-safe', '0', '-i', input_file]
        if video_filter:
            cmd.extend(['-vf', video_filter])
        cmd.extend(['-c:v', codec, '-preset', preset, '-crf', str(crf),
                    '-pix_fmt', pixfmt, '-r', str(fps),
                    '-movflags', '+faststart', '-y', output_file])
        return cmd

    def execute_safe_command(self, cmd: List[str], timeout=300) -> Tuple[bool, str]:
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
                encoding='utf-8', errors='replace', shell=False
            )
            if result.returncode != 0:
                error_lines = result.stderr.strip().split('\n')[-10:]
                return False, f"FFMPEG failed (code {result.returncode})\n" + '\n'.join(error_lines)
            return True, ""
        except subprocess.TimeoutExpired:
            return False, f"FFMPEG timeout after {timeout}s"
        except Exception as e:
            return False, f"FFMPEG execution error: {e}"


_ffmpeg_validator = FFMPEGValidator()


# ========================================================================================
# FFMPEG Discovery
# ========================================================================================

def find_ffmpeg_safe() -> Optional[str]:
    candidates = []

    try:
        blender_dir = os.path.dirname(bpy.app.binary_path)
        for name in ('ffmpeg.exe', 'ffmpeg'):
            for d in (blender_dir, os.path.join(blender_dir, '..')):
                candidates.append(os.path.join(d, name))
    except Exception:
        pass

    try:
        import platform
        cmd    = ['where', 'ffmpeg'] if platform.system() == 'Windows' else ['which', 'ffmpeg']
        result = subprocess.run(cmd, capture_output=True, timeout=5, text=True)
        if result.returncode == 0:
            path = result.stdout.strip().split('\n')[0]
            if path:
                candidates.append(path)
    except Exception:
        pass

    candidates += [
        r'C:\ffmpeg\bin\ffmpeg.exe',
        r'C:\Program Files\ffmpeg\bin\ffmpeg.exe',
        '/usr/bin/ffmpeg', '/usr/local/bin/ffmpeg',
        '/opt/homebrew/bin/ffmpeg', '/opt/ffmpeg/bin/ffmpeg',
    ]

    for path in candidates:
        try:
            return _ffmpeg_validator.validate_ffmpeg_path(path)
        except FFMPEGSecurityError:
            continue
    return None


# ========================================================================================
# Image Validation
# ========================================================================================

def validate_images_for_compilation(image_dir, image_extensions=('.png', '.jpg', '.jpeg')):
    files = sorted([
        f for f in os.listdir(image_dir)
        if f.lower().endswith(image_extensions)
    ])
    if not files:
        raise RuntimeError("No images found in directory")

    metadata = {
        'total_files': len(files), 'valid_files': [], 'issues': [],
        'width': None, 'height': None, 'has_alpha': False,
        'channels': 3, 'needs_scale': False,
    }

    first_path = os.path.join(image_dir, files[0])
    try:
        img = bpy.data.images.load(first_path, check_existing=False)
        metadata['width']      = img.size[0]
        metadata['height']     = img.size[1]
        metadata['channels']   = img.channels
        metadata['has_alpha']  = (img.channels == 4)
        metadata['needs_scale'] = (img.size[0] % 2 != 0 or img.size[1] % 2 != 0)
        bpy.data.images.remove(img, do_unlink=True)
    except Exception as e:
        metadata['issues'].append(f"Cannot read first image: {e}")

    for filename in files:
        filepath = os.path.join(image_dir, filename)
        if not os.path.isfile(filepath) or not os.access(filepath, os.R_OK):
            metadata['issues'].append(f"Not readable: {filename}")
            continue
        try:
            size = os.path.getsize(filepath)
            if size < 100:
                metadata['issues'].append(f"Too small: {filename} ({size} bytes)")
                continue
        except OSError as e:
            metadata['issues'].append(f"Cannot stat {filename}: {e}")
            continue
        metadata['valid_files'].append(filename)

    if not metadata['valid_files']:
        raise RuntimeError("No valid images found after validation")

    return metadata['valid_files'], metadata


# ========================================================================================
# Cleanup Manager
# ========================================================================================

class CompilationCleanupManager:
    """Manages cleanup of temp files created during video compilation."""

    def __init__(self):
        # NOTE: loaded_images removed — it was initialised but never populated.
        self.temp_files    = []
        self._cleanup_done = False

    def register_temp_file(self, filepath):
        if filepath:
            self.temp_files.append(filepath)

    def cleanup(self):
        if self._cleanup_done:
            return
        self._cleanup_done = True
        for filepath in self.temp_files:
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
            except Exception as e:
                logger.warning(f"Cannot remove temp file {filepath}: {e}")


# ========================================================================================
# Helpers
# ========================================================================================

def _folder_has_images(path: str) -> bool:
    try:
        return any(f.lower().endswith(constants.IMAGE_EXTENSIONS) for f in os.listdir(path))
    except (OSError, PermissionError):
        return False


def _find_images_folder(root_dir: str, max_depth: int = 3) -> Optional[str]:
    root_dir = bpy.path.abspath(root_dir)
    if not os.path.isdir(root_dir):
        return None
    if _folder_has_images(root_dir):
        return root_dir

    def _search(current_dir, depth):
        if depth > max_depth:
            return None
        try:
            subdirs = sorted(
                [os.path.join(current_dir, d) for d in os.listdir(current_dir)
                 if os.path.isdir(os.path.join(current_dir, d))],
                reverse=True
            )
            for subdir in subdirs:
                if _folder_has_images(subdir):
                    return subdir
                result = _search(subdir, depth + 1)
                if result:
                    return result
        except (OSError, PermissionError):
            pass
        return None

    return _search(root_dir, 1)


# ========================================================================================
# Main Compilation (RGBA + Resolution fix)
# ========================================================================================

def _compile_images_to_mp4_safe(image_dir, output_path, fps=30,
                                  codec='libx264', preset='medium', crf=23):
    valid_files, metadata = validate_images_for_compilation(image_dir)

    has_alpha   = metadata.get('has_alpha', False)
    needs_scale = metadata.get('needs_scale', False)

    cleanup_mgr    = CompilationCleanupManager()
    filelist_path  = None

    try:
        ffmpeg = find_ffmpeg_safe()
        if not ffmpeg:
            raise RuntimeError(
                "FFMPEG not found.\n"
                "Download from: https://ffmpeg.org/download.html\n"
                "Add to PATH or place ffmpeg.exe in Blender's folder."
            )

        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt',
                                         delete=False, encoding='utf-8') as f:
            filelist_path = f.name
            cleanup_mgr.register_temp_file(filelist_path)

            for filename in valid_files:
                filepath = os.path.join(image_dir, filename)
                try:
                    filepath = _ffmpeg_validator.validate_input_file(filepath)
                except FFMPEGSecurityError as e:
                    logger.warning(f"Skipping {filename}: {e}")
                    continue
                filepath = filepath.replace('\\', '/').replace("'", "'\\''")
                f.write(f"file '{filepath}'\n")
                f.write(f"duration {1/fps}\n")

            if valid_files:
                last = _ffmpeg_validator.validate_input_file(
                    os.path.join(image_dir, valid_files[-1])
                )
                last = last.replace('\\', '/').replace("'", "'\\''")
                f.write(f"file '{last}'\n")

        filters = []
        if needs_scale:
            filters.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")
        if has_alpha:
            filters.append("format=yuv420p")
        video_filter = ','.join(filters) if filters else None

        cmd = _ffmpeg_validator.build_safe_command(
            ffmpeg=ffmpeg, input_file=filelist_path,
            output_file=bpy.path.abspath(output_path),
            fps=fps, codec=codec, preset=preset, crf=crf,
            pixfmt='yuv420p', video_filter=video_filter
        )

        success, error_msg = _ffmpeg_validator.execute_safe_command(cmd, timeout=300)
        if not success:
            raise RuntimeError(f"FFMPEG encoding failed: {error_msg}")

        output_abs = bpy.path.abspath(output_path)
        if not os.path.exists(output_abs):
            raise RuntimeError(f"Output file not created: {output_abs}")

        logger.info(f"✓ Video created: {os.path.getsize(output_abs)/(1024*1024):.2f} MB")

    finally:
        cleanup_mgr.cleanup()


# ========================================================================================
# Operators
# ========================================================================================

class TLX_OT_compile_video(Operator):
    """Compile image sequence to MP4 video (auto-fix RGBA + odd resolution)"""

    bl_idname  = 'tlx.compile_video'
    bl_label   = 'Make Video from Images'
    bl_options = {'REGISTER'}

    input_dir:   StringProperty(name='Images Folder',       subtype='DIR_PATH',  default='')
    output_path: StringProperty(name='Video Output (MP4)',   subtype='FILE_PATH', default='')
    filepath:    StringProperty(subtype='FILE_PATH',  default='', options={'HIDDEN'})
    directory:   StringProperty(subtype='DIR_PATH',   default='', options={'HIDDEN'})
    ask_output:  BoolProperty(name='Choose Output via Dialog', default=False)
    fps:         IntProperty(name='FPS', min=1, max=240, default=constants.DEFAULT_VIDEO_FPS)

    # FIX: stage is now a plain instance attribute, not a StringProperty,
    #      so it resets to '' on every new Python object creation (i.e. every invoke).
    #      A StringProperty survives between operator invocations via RNA,
    #      which caused stage to stay 'PICK_IMAGES' after a cancel.
    stage: StringProperty(default='', options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        prefs = utils.get_addon_preferences()

        # FIX: always reset stage at the start of invoke so a previous
        # cancelled run cannot leave us in a broken state
        self.stage = 'PICK_IMAGES'

        if prefs and prefs.output_dir:
            self.input_dir = bpy.path.abspath(prefs.output_dir)
        else:
            self.input_dir = '//Timelapse_Images'

        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        if self.stage == 'PICK_IMAGES':
            candidate = bpy.path.abspath(self.directory or self.filepath or self.input_dir)
            if not os.path.isdir(candidate):
                # User cancelled or gave invalid path — reset stage and bail
                self.stage = ''
                self.report({'WARNING'}, 'No folder selected.')
                return {'CANCELLED'}

            self.input_dir = candidate
            self.stage     = ''  # reset before potential second dialog

            if self.ask_output:
                prefs = utils.get_addon_preferences()
                if prefs and prefs.mp4_output_mode == 'CUSTOM_DIR':
                    output_dir = bpy.path.abspath(prefs.mp4_custom_dir)
                else:
                    output_dir = self.input_dir
                os.makedirs(output_dir, exist_ok=True)
                self.output_path = os.path.join(output_dir, 'timelapse.mp4')
                self.stage       = 'PICK_OUTPUT'
                context.window_manager.fileselect_add(self)
                return {'RUNNING_MODAL'}

            return self._do_compile(context)

        elif self.stage == 'PICK_OUTPUT':
            self.stage = ''  # reset before compile
            if self.filepath:
                self.output_path = self.filepath
            return self._do_compile(context)

        else:
            return self._do_compile(context)

    def _do_compile(self, context):
        images_folder = _find_images_folder(self.input_dir)
        if not images_folder:
            self.report({'ERROR'}, f'No images found under: {self.input_dir}')
            return {'CANCELLED'}

        files = sorted([
            f for f in os.listdir(images_folder)
            if f.lower().endswith(constants.IMAGE_EXTENSIONS)
        ])
        if not files:
            self.report({'ERROR'}, 'No images found in selected folder')
            return {'CANCELLED'}

        output_path = bpy.path.abspath(self.output_path) if self.output_path else ''
        if not output_path:
            base_name   = os.path.basename(os.path.normpath(images_folder)) or 'timelapse'
            timestamp   = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = os.path.join(images_folder, f'{base_name}_{timestamp}.mp4')

        if not output_path.lower().endswith('.mp4'):
            output_path += '.mp4'

        output_dir = os.path.dirname(output_path)
        if output_dir:
            try:
                os.makedirs(output_dir, exist_ok=True)
            except OSError as e:
                self.report({'ERROR'}, f'Cannot create output directory: {e}')
                return {'CANCELLED'}

        try:
            _compile_images_to_mp4_safe(
                images_folder, output_path,
                fps=self.fps, codec='libx264', preset='medium', crf=23
            )
            duration   = len(files) / self.fps
            output_abs = bpy.path.abspath(output_path)
            size_mb    = os.path.getsize(output_abs) / (1024 * 1024)
            self.report({'INFO'},
                        f'✓ Video exported! Duration: {duration:.1f}s | Size: {size_mb:.1f}MB')
            return {'FINISHED'}

        except FFMPEGSecurityError as e:
            self.report({'ERROR'}, f'Security error: {e}')
            return {'CANCELLED'}
        except Exception as e:
            logger.error(str(e), exc_info=True)
            if "FFMPEG not found" in str(e):
                self.report({'ERROR'}, 'FFMPEG not found. Download: https://ffmpeg.org/download.html')
            else:
                self.report({'ERROR'}, f'Compilation failed: {e}')
            return {'CANCELLED'}


class TLX_OT_compile_session_all(Operator):
    """Compile all cameras from current session"""

    bl_idname  = 'tlx.compile_session_all'
    bl_label   = 'Compile Current Session'
    bl_options = {'REGISTER'}

    fps: IntProperty(name='FPS', min=1, max=240, default=constants.DEFAULT_VIDEO_FPS)

    @classmethod
    def poll(cls, context):
        session_dir = StateManager().session_dir
        return bool(session_dir and os.path.isdir(session_dir))

    def execute(self, context):
        prefs       = utils.get_addon_preferences()
        session_dir = StateManager().session_dir

        if not (session_dir and os.path.isdir(session_dir)):
            self.report({'ERROR'}, 'No active session folder')
            return {'CANCELLED'}

        camera_dirs = []
        try:
            for item in os.listdir(session_dir):
                path = os.path.join(session_dir, item)
                if os.path.isdir(path) and item.startswith('CAM_') and _folder_has_images(path):
                    camera_dirs.append(path)
        except (OSError, PermissionError) as e:
            logger.error(f"Cannot read session directory: {e}")

        flat_session = _folder_has_images(session_dir)
        targets      = camera_dirs if camera_dirs else ([session_dir] if flat_session else [])

        if not targets:
            self.report({'ERROR'}, 'No camera folders or images to compile')
            return {'CANCELLED'}

        def _get_output_dir(base):
            if prefs and prefs.mp4_output_mode == 'CUSTOM_DIR':
                return utils.ensure_directory(prefs.mp4_custom_dir)
            return utils.ensure_directory(base)

        date_dir   = os.path.basename(os.path.dirname(session_dir)) or 'session'
        time_dir   = os.path.basename(session_dir) or 'time'
        tag        = f"{date_dir}_{time_dir}"
        timestamp  = datetime.now().strftime('%Y%m%d_%H%M%S')

        exported_count = failed_count = 0

        for target_dir in sorted(targets):
            folder_name = os.path.basename(target_dir)
            output_path = os.path.join(
                _get_output_dir(target_dir),
                f"{tag}_{folder_name}_{timestamp}.mp4"
            )
            try:
                _compile_images_to_mp4_safe(
                    target_dir, output_path,
                    fps=self.fps, codec='libx264', preset='medium', crf=23
                )
                exported_count += 1
            except Exception as e:
                failed_count += 1
                logger.error(f"✗ Failed: {folder_name}: {e}")

        if exported_count == 0:
            self.report({'ERROR'}, 'No MP4s exported (all failed)')
            return {'CANCELLED'}

        msg = f'✓ Exported {exported_count} MP4(s)'
        if failed_count > 0:
            msg += f' ({failed_count} failed)'
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class TLX_OT_test_ffmpeg_security(Operator):
    """Test FFMPEG availability"""

    bl_idname  = 'tlx.test_ffmpeg_security'
    bl_label   = 'Test FFMPEG'
    bl_options = {'REGISTER'}

    def execute(self, context):
        ffmpeg = find_ffmpeg_safe()
        if ffmpeg:
            self.report({'INFO'}, f'✓ FFMPEG found: {ffmpeg}')
        else:
            self.report({'WARNING'}, '⚠ FFMPEG not found — install from https://ffmpeg.org')
        return {'FINISHED'}


# ========================================================================================
# Registration
# ========================================================================================

classes = (
    TLX_OT_compile_video,
    TLX_OT_compile_session_all,
    TLX_OT_test_ffmpeg_security,
)


def register():
    logger.info("Registering video operators (v2.5.1: stage machine fix)")
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except Exception as e:
            logger.error(f"Failed to register {cls.__name__}: {e}")


def unregister():
    logger.info("Unregistering video operators")
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception as e:
            logger.warning(f"Failed to unregister {cls.__name__}: {e}")
