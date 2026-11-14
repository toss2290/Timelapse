"""
Video operators: compile an image sequence (or every camera of the current
session) to MP4 with FFmpeg, and test that FFmpeg is available.

Encoding runs in the background: FFmpeg is a subprocess polled from a
bpy.app.timers callback, so Blender's UI never waits on it. Jobs are queued
one at a time, report progress, and can be cancelled.

FFmpeg is run with shell=False, an argument list, validated paths and an
allow-list of codecs, presets and pixel formats. Odd resolutions and RGBA
frames are fixed automatically.
"""

import bpy
import os
import logging
import subprocess
import tempfile
import hashlib
import time
from collections import deque
from datetime import datetime
from bpy.types import Operator
from bpy.props import StringProperty, IntProperty, BoolProperty
from typing import Optional, List

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
        # FFmpeg runs with shell=False and an argument list, so shell
        # metacharacters ('&', ';', '$' ...) are harmless and legal in folder
        # names. Only characters that would corrupt the concat list are refused.
        for char in ('\x00', '\n', '\r'):
            if char in file_path:
                raise FFMPEGSecurityError(f"Unsupported character in path: {repr(char)}")
        if not os.path.isfile(abs_path):
            raise FFMPEGSecurityError(f"File not found: {abs_path}")
        if not os.access(abs_path, os.R_OK):
            raise FFMPEGSecurityError(f"File not readable: {abs_path}")
        return abs_path

    def validate_output_path(self, output_path: str) -> str:
        if not output_path:
            raise FFMPEGSecurityError("Output path is empty")
        abs_path = os.path.abspath(output_path)
        for char in ('\x00', '\n', '\r'):
            if char in output_path:
                raise FFMPEGSecurityError(f"Unsupported character in path: {repr(char)}")
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

        # -progress pipe:1 → machine-readable progress on stdout (redirected to a file by
        # the caller); -loglevel error keeps stderr down to real problems.
        cmd = [ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error',
               '-nostats', '-progress', 'pipe:1',
               '-f', 'concat', '-safe', '0', '-i', input_file]
        if video_filter:
            cmd.extend(['-vf', video_filter])
        cmd.extend(['-c:v', codec, '-preset', preset, '-crf', str(crf),
                    '-pix_fmt', pixfmt, '-r', str(fps),
                    '-movflags', '+faststart', '-y', output_file])
        return cmd


_ffmpeg_validator = FFMPEGValidator()


# ========================================================================================
# FFMPEG Discovery
# ========================================================================================

_ffmpeg_path_cache: Optional[str] = None


def find_ffmpeg_safe(refresh: bool = False) -> Optional[str]:
    """Locate a verified FFmpeg. The result is cached (re-checked with a cheap
    isfile) so the 'which' / '-version' subprocesses don't run on every call."""
    global _ffmpeg_path_cache
    if not refresh and _ffmpeg_path_cache and os.path.isfile(_ffmpeg_path_cache):
        return _ffmpeg_path_cache
    _ffmpeg_path_cache = _discover_ffmpeg()
    return _ffmpeg_path_cache


def _discover_ffmpeg() -> Optional[str]:
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
    ], key=utils.natural_sort_key)
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
# Background encoding (does not block Blender's UI)
# ========================================================================================
# FFmpeg runs as a subprocess. A persistent bpy.app.timers callback polls it every
# _POLL_INTERVAL seconds, reads its progress, and starts the next queued job when one
# finishes. Nothing here waits on FFmpeg, so the UI stays responsive for the whole encode.
#
# FFmpeg's stdout (machine-readable `-progress` output) and stderr go to temp FILES, not
# pipes: nobody has to drain a pipe, so the process can never stall on a full buffer.

_POLL_INTERVAL = 0.25   # seconds between polls while a job is running
_STALL_TIMEOUT = 120.0  # kill a job whose frame counter has not advanced for this long
_STATUS_CLEAR_AFTER = 10.0


class _JobSpec:
    __slots__ = ('label', 'image_dir', 'output_path', 'fps', 'codec', 'preset', 'crf')

    def __init__(self, label, image_dir, output_path, fps,
                 codec='libx264', preset='medium', crf=23):
        self.label       = label
        self.image_dir   = image_dir
        self.output_path = output_path
        self.fps         = fps
        self.codec       = codec
        self.preset      = preset
        self.crf         = crf


class _Job:
    """One running FFmpeg process plus everything needed to monitor and clean it up."""

    def __init__(self, spec, proc, cleanup, total_frames,
                 progress_path, stderr_path, handles):
        self.spec          = spec
        self.proc          = proc
        self.cleanup       = cleanup
        self.total_frames  = max(1, total_frames)
        self.progress_path = progress_path
        self.stderr_path   = stderr_path
        self._handles      = handles
        self.last_frame    = 0
        self.last_advance  = time.monotonic()

    @property
    def fraction(self) -> float:
        return min(1.0, self.last_frame / self.total_frames)

    def read_progress(self) -> None:
        """Update last_frame from the tail of FFmpeg's `-progress` output."""
        try:
            with open(self.progress_path, 'rb') as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - 2048))
                tail = f.read().decode('utf-8', 'replace')
        except OSError:
            return
        frame = None
        for line in tail.splitlines():
            if line.startswith('frame='):
                try:
                    frame = int(line[6:].strip())
                except ValueError:
                    pass
        if frame is not None and frame > self.last_frame:
            self.last_frame   = frame
            self.last_advance = time.monotonic()

    def stderr_tail(self, lines: int = 10) -> str:
        try:
            with open(self.stderr_path, 'r', encoding='utf-8', errors='replace') as f:
                return '\n'.join(f.read().strip().split('\n')[-lines:])
        except OSError:
            return ''

    def close_handles(self) -> None:
        for h in self._handles:
            try:
                h.close()
            except OSError:
                pass
        self._handles = []


_queue: deque = deque()
_current: Optional[_Job] = None
_batch = {'total': 0, 'done': 0, 'failed': 0, 'outputs': []}
_last_result = {'text': '', 'ok': True}
_progress_active = False


def _reset_batch() -> None:
    _batch.update(total=0, done=0, failed=0, outputs=[])


def _start_job(spec: _JobSpec) -> _Job:
    """Validate the images, write the concat list and launch FFmpeg (non-blocking)."""
    valid_files, metadata = validate_images_for_compilation(spec.image_dir)

    ffmpeg = find_ffmpeg_safe()
    if not ffmpeg:
        raise RuntimeError(
            "FFMPEG not found. Download from https://ffmpeg.org/download.html and add it "
            "to PATH or place it next to Blender's executable."
        )

    cleanup = CompilationCleanupManager()
    handles = []
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt',
                                         delete=False, encoding='utf-8') as f:
            filelist_path = f.name
            cleanup.register_temp_file(filelist_path)

            for filename in valid_files:
                filepath = os.path.join(spec.image_dir, filename)
                try:
                    filepath = _ffmpeg_validator.validate_input_file(filepath)
                except FFMPEGSecurityError as e:
                    logger.warning(f"Skipping {filename}: {e}")
                    continue
                filepath = filepath.replace('\\', '/').replace("'", "'\\''")
                f.write(f"file '{filepath}'\n")
                f.write(f"duration {1/spec.fps}\n")

            if valid_files:
                last = _ffmpeg_validator.validate_input_file(
                    os.path.join(spec.image_dir, valid_files[-1]))
                last = last.replace('\\', '/').replace("'", "'\\''")
                f.write(f"file '{last}'\n")

        filters = []
        if metadata.get('needs_scale'):
            filters.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")
        if metadata.get('has_alpha'):
            filters.append("format=yuv420p")

        cmd = _ffmpeg_validator.build_safe_command(
            ffmpeg=ffmpeg, input_file=filelist_path,
            output_file=bpy.path.abspath(spec.output_path),
            fps=spec.fps, codec=spec.codec, preset=spec.preset, crf=spec.crf,
            pixfmt='yuv420p',
            video_filter=','.join(filters) if filters else None,
        )

        fd, progress_path = tempfile.mkstemp(prefix='tlx_ffprogress_', suffix='.log')
        cleanup.register_temp_file(progress_path)
        progress_f = os.fdopen(fd, 'w')
        handles.append(progress_f)
        fd, stderr_path = tempfile.mkstemp(prefix='tlx_fferr_', suffix='.log')
        cleanup.register_temp_file(stderr_path)
        stderr_f = os.fdopen(fd, 'w')
        handles.append(stderr_f)

        # No console window on Windows; shell=False with an argument list.
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0
        proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=progress_f, stderr=stderr_f,
            shell=False, creationflags=flags,
        )
    except Exception:
        for h in handles:
            try:
                h.close()
            except OSError:
                pass
        cleanup.cleanup()
        raise

    logger.info(f"Encoding started: {spec.label} ({len(valid_files)} images)")
    return _Job(spec, proc, cleanup, len(valid_files), progress_path, stderr_path, handles)


def _kill(job: _Job) -> None:
    try:
        job.proc.kill()
        job.proc.wait(timeout=5)
    except Exception as e:
        logger.warning(f"Could not stop FFmpeg cleanly: {e}")


def _remove_partial_output(job: _Job) -> None:
    try:
        out = bpy.path.abspath(job.spec.output_path)
        if os.path.exists(out):
            os.remove(out)
    except OSError:
        pass


def _launch_next() -> None:
    """Start the next queued job. Jobs that fail to start count as failed."""
    global _current
    while _current is None and _queue:
        spec = _queue.popleft()
        try:
            _current = _start_job(spec)
        except Exception as e:
            _batch['failed'] += 1
            logger.error(f"Cannot start encoding {spec.label}: {e}")


def _complete_current(rc: int) -> None:
    global _current
    job = _current
    job.read_progress()
    job.close_handles()
    out = bpy.path.abspath(job.spec.output_path)
    if rc == 0 and os.path.exists(out):
        _batch['done'] += 1
        _batch['outputs'].append(out)
        logger.info(f"Video created: {out} "
                    f"({os.path.getsize(out) / (1024 * 1024):.2f} MB)")
    else:
        _batch['failed'] += 1
        logger.error(f"FFmpeg failed for {job.spec.label} (code {rc})\n{job.stderr_tail()}")
        _remove_partial_output(job)
    job.cleanup.cleanup()
    _current = None


def _stall_current() -> None:
    global _current
    job = _current
    logger.error(f"FFmpeg made no progress for {_STALL_TIMEOUT:.0f}s on "
                 f"{job.spec.label} — stopping it")
    _kill(job)
    job.close_handles()
    _remove_partial_output(job)
    job.cleanup.cleanup()
    _batch['failed'] += 1
    _current = None


def _overall_fraction() -> float:
    total = max(1, _batch['total'])
    running = _current.fraction if _current else 0.0
    return min(1.0, (_batch['done'] + _batch['failed'] + running) / total)


def _each_window_screen():
    wm = bpy.context.window_manager
    for window in (wm.windows if wm else []):
        yield window


def _set_status_text(text: Optional[str]) -> None:
    try:
        for window in _each_window_screen():
            window.workspace.status_text_set(text)
    except Exception as e:
        logger.debug(f"Status text failed: {e}")


def _tag_redraw() -> None:
    try:
        for window in _each_window_screen():
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
    except Exception:
        pass


def _update_ui() -> None:
    global _progress_active
    try:
        wm = bpy.context.window_manager
        if wm:
            if not _progress_active:
                wm.progress_begin(0, 1)
                _progress_active = True
            wm.progress_update(_overall_fraction())
        job = _current
        if job:
            index = _batch['done'] + _batch['failed'] + 1
            _set_status_text(
                f"Timelapse X: encoding {job.spec.label}  {job.fraction * 100:.0f}%"
                f"  ({index}/{_batch['total']})")
    except Exception as e:
        logger.debug(f"Progress UI update failed: {e}")
    _tag_redraw()


def _end_progress_ui() -> None:
    global _progress_active
    if _progress_active:
        try:
            bpy.context.window_manager.progress_end()
        except Exception:
            pass
        _progress_active = False


def _clear_status_text_once() -> None:
    if not (_current or _queue):
        _set_status_text(None)
    return None


def _finish_batch() -> None:
    """Called once the queue is empty and nothing is running."""
    done, failed = _batch['done'], _batch['failed']
    if failed == 0 and done:
        text = (f"Exported {done} MP4(s)" if done > 1
                else f"Exported {os.path.basename(_batch['outputs'][0])}")
        ok = True
    elif done:
        text, ok = f"Exported {done} MP4(s), {failed} failed — see console", False
    else:
        text, ok = "Video export failed — see console", False
    _last_result.update(text=text, ok=ok)
    logger.info(text)
    _end_progress_ui()
    _set_status_text(f"Timelapse X: {text}")
    if not bpy.app.timers.is_registered(_clear_status_text_once):
        bpy.app.timers.register(_clear_status_text_once,
                                first_interval=_STATUS_CLEAR_AFTER)
    _reset_batch()
    _tag_redraw()


def _tick() -> Optional[float]:
    """bpy.app.timers callback: poll FFmpeg, advance the queue. Never blocks."""
    try:
        if _current is None:
            _launch_next()
        if _current is None:
            _finish_batch()
            return None

        rc = _current.proc.poll()
        if rc is None:
            _current.read_progress()
            if time.monotonic() - _current.last_advance > _STALL_TIMEOUT:
                _stall_current()
                return 0.01
            _update_ui()
            return _POLL_INTERVAL

        _complete_current(rc)
        return 0.01   # look at the queue again right away
    except Exception as e:
        logger.error(f"Video encoding monitor failed: {e}", exc_info=True)
        cancel_all(announce=False)
        return None


def enqueue_jobs(specs) -> None:
    """Queue encoding jobs; they run one at a time in the background."""
    if not (_current or _queue):
        _reset_batch()
        _last_result.update(text='', ok=True)
    for spec in specs:
        _queue.append(spec)
        _batch['total'] += 1
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=0.05, persistent=True)


def cancel_all(announce: bool = True) -> bool:
    """Stop the running job and drop everything queued. Safe to call at any time."""
    global _current
    had_work = bool(_current or _queue)
    _queue.clear()
    if _current is not None:
        job = _current
        _kill(job)
        job.close_handles()
        _remove_partial_output(job)
        job.cleanup.cleanup()
        _current = None
    if bpy.app.timers.is_registered(_tick):
        try:
            bpy.app.timers.unregister(_tick)
        except ValueError:
            pass
    _end_progress_ui()
    _reset_batch()
    if had_work and announce:
        _last_result.update(text="Video export cancelled", ok=False)
        _set_status_text(None)
    _tag_redraw()
    return had_work


def get_status() -> dict:
    """Cheap snapshot for the UI panel (safe to call on every redraw)."""
    active = bool(_current or _queue)
    index  = _batch['done'] + _batch['failed'] + 1
    label  = _current.spec.label if _current else ''
    return {
        'active':      active,
        'label':       label,
        'fraction':    _overall_fraction() if active else 0.0,
        'index':       min(index, max(1, _batch['total'])),
        'total':       _batch['total'],
        'last_result': _last_result['text'],
        'last_ok':     _last_result['ok'],
    }


# ========================================================================================
# Operators
# ========================================================================================

class TLX_OT_compile_video(Operator):
    """Compile image sequence to MP4 video in the background (auto-fix RGBA + odd resolution)"""

    bl_idname  = 'tlx.compile_video'
    bl_label   = 'Make Video from Images'
    bl_options = {'REGISTER'}

    input_dir:   StringProperty(name='Images Folder',       subtype='DIR_PATH',  default='')
    output_path: StringProperty(name='Video Output (MP4)',   subtype='FILE_PATH', default='')
    filepath:    StringProperty(subtype='FILE_PATH',  default='', options={'HIDDEN'})
    directory:   StringProperty(subtype='DIR_PATH',   default='', options={'HIDDEN'})
    ask_output:  BoolProperty(name='Choose Output via Dialog', default=False)
    fps:         IntProperty(name='FPS', min=1, max=240, default=constants.DEFAULT_VIDEO_FPS)

    # SKIP_SAVE keeps 'stage' from being remembered between invocations;
    # invoke() also resets it, so a cancelled file picker cannot leave it stale.
    stage: StringProperty(default='', options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        prefs = utils.get_addon_preferences()

        # Always reset stage so a previous cancelled run cannot leave it stale
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
        ], key=utils.natural_sort_key)
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

        if not find_ffmpeg_safe():
            self.report({'ERROR'}, 'FFMPEG not found. Download: https://ffmpeg.org/download.html')
            return {'CANCELLED'}

        enqueue_jobs([_JobSpec(os.path.basename(output_path), images_folder,
                               output_path, self.fps)])
        self.report({'INFO'},
                    f'Encoding {len(files)} frames in the background '
                    f'(~{len(files) / self.fps:.1f}s video). Blender stays usable.')
        return {'FINISHED'}


class TLX_OT_compile_session_all(Operator):
    """Compile all cameras from the current session to MP4 in the background"""

    bl_idname  = 'tlx.compile_session_all'
    bl_label   = 'Compile Current Session'
    bl_options = {'REGISTER'}

    fps: IntProperty(name='FPS', min=1, max=240, default=constants.DEFAULT_VIDEO_FPS)

    @classmethod
    def poll(cls, context):
        session_dir = StateManager().latest_session_dir
        return bool(session_dir and os.path.isdir(session_dir))

    def execute(self, context):
        prefs       = utils.get_addon_preferences()
        session_dir = StateManager().latest_session_dir

        if not (session_dir and os.path.isdir(session_dir)):
            self.report({'ERROR'}, 'No session folder yet - record something first')
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

        if not find_ffmpeg_safe():
            self.report({'ERROR'}, 'FFMPEG not found. Download: https://ffmpeg.org/download.html')
            return {'CANCELLED'}

        def _get_output_dir(base):
            if prefs and prefs.mp4_output_mode == 'CUSTOM_DIR':
                return utils.ensure_directory(prefs.mp4_custom_dir)
            return utils.ensure_directory(base)

        date_dir   = os.path.basename(os.path.dirname(session_dir)) or 'session'
        time_dir   = os.path.basename(session_dir) or 'time'
        tag        = f"{date_dir}_{time_dir}"
        timestamp  = datetime.now().strftime('%Y%m%d_%H%M%S')

        specs = []
        for target_dir in sorted(targets):
            folder_name = os.path.basename(target_dir)
            try:
                output_path = os.path.join(
                    _get_output_dir(target_dir),
                    f"{tag}_{folder_name}_{timestamp}.mp4")
            except Exception as e:
                logger.error(f"Cannot prepare output folder for {folder_name}: {e}")
                continue
            specs.append(_JobSpec(os.path.basename(output_path), target_dir,
                                  output_path, self.fps))

        if not specs:
            self.report({'ERROR'}, 'No output folder could be prepared')
            return {'CANCELLED'}

        enqueue_jobs(specs)
        self.report({'INFO'},
                    f'Encoding {len(specs)} video(s) in the background. Blender stays usable.')
        return {'FINISHED'}


class TLX_OT_cancel_video_compile(Operator):
    """Cancel the running video export and clear its queue"""

    bl_idname  = 'tlx.cancel_video_compile'
    bl_label   = 'Cancel Video Export'
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return get_status()['active']

    def execute(self, context):
        cancel_all()
        self.report({'INFO'}, 'Video export cancelled')
        return {'FINISHED'}


class TLX_OT_test_ffmpeg_security(Operator):
    """Test FFMPEG availability"""

    bl_idname  = 'tlx.test_ffmpeg_security'
    bl_label   = 'Test FFMPEG'
    bl_options = {'REGISTER'}

    def execute(self, context):
        ffmpeg = find_ffmpeg_safe(refresh=True)
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
    TLX_OT_cancel_video_compile,
    TLX_OT_test_ffmpeg_security,
)


def register():
    logger.info("Registering video operators")
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except Exception as e:
            logger.error(f"Failed to register {cls.__name__}: {e}")


def unregister():
    logger.info("Unregistering video operators")
    cancel_all(announce=False)
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception as e:
            logger.warning(f"Failed to unregister {cls.__name__}: {e}")
