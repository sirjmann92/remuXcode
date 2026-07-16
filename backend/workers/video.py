#!/usr/bin/env python3
"""Video converter worker.

Converts 10-bit H.264 video to HEVC for better device compatibility.
Applies different encoding settings for anime vs live action content.
"""

from collections.abc import Callable
from dataclasses import dataclass
import logging
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

from backend.utils.anime_detect import AnimeDetector, ContentType
from backend.utils.config import VideoConfig
from backend.utils.ffprobe import AttachmentStream, FFProbe, VideoStream
from backend.utils.hwaccel import HWAccelCaps, resolve_encoder
from backend.workers._progress import ffmpeg_error_summary, run_ffmpeg_with_progress
from backend.workers._safe_move import safe_replace, wait_for_output_file

logger = logging.getLogger(__name__)

# Fallback MIME types for attachment streams that carry no mimetype tag.
# The MKV muxer rejects output when an attachment is missing its mimetype or
# filename tag.  We infer mimetype from the filename extension and fall back to
# application/octet-stream; for filename we reverse-map the mimetype to an
# extension and synthesise "attachment_N.<ext>".
_ATTACHMENT_MIME_FALLBACK: dict[str, str] = {
    ".ttf": "application/x-truetype-font",
    ".otf": "application/vnd.ms-opentype",
    ".woff": "application/font-woff",
    ".woff2": "font/woff2",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}

# Reverse map: MIME type → a reasonable file extension (first match wins)
_ATTACHMENT_MIME_TO_EXT: dict[str, str] = {
    "application/x-truetype-font": ".ttf",
    "application/vnd.ms-opentype": ".otf",
    "application/font-woff": ".woff",
    "font/woff2": ".woff2",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
    "application/octet-stream": ".bin",
}


@dataclass
class VideoConversionResult:
    """Result of a video conversion operation."""

    success: bool
    input_file: str
    output_file: str
    original_size: int
    new_size: int
    codec_from: str
    codec_to: str
    content_type: str
    error: str | None = None

    @property
    def size_change(self) -> int:
        """Size change in bytes (negative = smaller)."""
        return self.new_size - self.original_size

    @property
    def size_change_percent(self) -> float:
        """Size change as percentage."""
        if self.original_size == 0:
            return 0.0
        return (self.size_change / self.original_size) * 100


class DVPreparationError(Exception):
    """Raised when the Dolby Vision base-layer preparation step fails.

    preserve_temp: when True, the caller must not delete the job temp dir —
    the prep output likely exists (network-fs stat lag) and is kept for
    inspection/recovery.
    """

    preserve_temp: bool = False


class VideoConverter:
    """Converts video to HEVC or AV1 with content-aware encoding settings.

    Features:
    - 10-bit H.264 detection (main conversion target)
    - Anime vs live action detection for optimal settings
    - HEVC (libx265) and AV1 (libsvtav1) codec support
    - Interrupt-safe with cleanup
    """

    def __init__(
        self,
        config: VideoConfig,
        ffprobe: FFProbe | None = None,
        anime_detector: AnimeDetector | None = None,
        get_volume_root: Callable[[str], str] | None = None,
        ffmpeg_threads: int = 0,
        hw_accel: str = "none",
        hw_caps: HWAccelCaps | None = None,
        affinity_fn: Callable[[], None] | None = None,
    ):
        """Initialize video converter.

        Args:
            config: Video conversion configuration
            ffprobe: FFProbe instance (created if not provided)
            anime_detector: AnimeDetector instance (created if not provided)
            get_volume_root: Function to get volume root for temp files (uses /tmp if not provided)
            ffmpeg_threads: Thread limit for ffmpeg (0 = unlimited)
            hw_accel: Hardware acceleration mode (none, auto, qsv, vaapi, nvenc)
            hw_caps: Pre-detected HW capabilities (auto-detected if None)
            affinity_fn: Optional preexec_fn to pin ffmpeg to P-cores
        """
        self.config = config
        self.ffprobe = ffprobe or FFProbe()
        self.ffmpeg_threads = ffmpeg_threads
        self.hw_accel = hw_accel
        self.hw_caps = hw_caps
        self.anime_detector = anime_detector or AnimeDetector()
        self.get_volume_root = get_volume_root or (lambda _: tempfile.gettempdir())
        self.affinity_fn = affinity_fn

    @property
    def target_codec(self) -> str:
        """Target codec name based on config."""
        return self.config.codec.lower()

    def should_convert(self, file_path: str) -> bool:
        """Check if a file should be converted.

        Returns True if:
        - Video is 10-bit H.264 and config.convert_10bit_x264 is True
        - Video is 8-bit H.264 and config.convert_8bit_x264 is True
        - Video is not already the target codec (HEVC or AV1)
        - If process_anime is False, skips anime content
        - If process_live_action is False, skips live action content
        """
        if not self.config.enabled:
            return False

        info = self.ffprobe.get_file_info(file_path)
        if info is None:
            return False

        video = info.primary_video
        if video is None:
            return False

        # Interlaced check before codec short-circuit: an interlaced file at
        # the target codec still needs re-encoding to become progressive.
        if self.config.deinterlace and video.is_interlaced:
            return True

        # Already target codec - skip
        if self.target_codec == "av1" and video.is_av1:
            logger.debug("Skipping AV1 file: %s", file_path)
            return False
        if self.target_codec == "hevc" and video.is_hevc:
            logger.debug("Skipping HEVC file: %s", file_path)
            return False

        # Skip Dolby Vision files unless dv_to_hdr10 is explicitly enabled.
        # The DV RPU layer cannot survive a re-encode; only the HDR10 base is kept.
        if video.is_dolby_vision and not self.config.dv_to_hdr10:
            logger.info(
                "Skipping Dolby Vision file (enable 'dv_to_hdr10' to encode, DV RPU will be stripped): %s",
                file_path,
            )
            return False

        # Skip HDR10+ files unless hdr10plus_to_hdr10 is explicitly enabled.
        # Dynamic SMPTE 2094-40 metadata cannot be re-injected during re-encode.
        if video.is_hdr10_plus and not self.config.hdr10plus_to_hdr10:
            logger.info(
                "Skipping HDR10+ file (enable 'hdr10plus_to_hdr10' to encode, dynamic metadata will be stripped): %s",
                file_path,
            )
            return False

        # Content-type filter: only call the detector if at least one type is disabled
        if not self.config.process_anime or not self.config.process_live_action:
            content_type = self.anime_detector.detect(file_path, use_api=False)
            _is_anime = content_type == ContentType.ANIME
            if _is_anime and not self.config.process_anime:
                logger.debug("Skipping anime content (process_anime=False): %s", file_path)
                return False
            if not _is_anime and not self.config.process_live_action:
                logger.debug(
                    "Skipping live action content (process_live_action=False): %s", file_path
                )
                return False

        # Check conversion criteria
        if video.is_10bit_h264 and self.config.convert_10bit_x264:
            return True

        if video.is_h264 and not video.is_10bit and self.config.convert_8bit_x264:
            return True

        if video.is_legacy_codec and self.config.convert_legacy_codecs:
            return True

        return False

    def convert(
        self,
        input_file: str,
        output_file: str | None = None,
        force_content_type: ContentType | None = None,
        job_id: str | None = None,
        progress_callback: Callable[[float], None] | None = None,
        cancel_event: threading.Event | None = None,
        detail_callback: Callable[[str], None] | None = None,
        log_cb: Callable[[str, str, str], None] | None = None,
        encode_options: dict | None = None,
        title: str | None = None,
        is_final_write: bool | None = None,
        source_snapshot: tuple[float, int] | None = None,
    ) -> VideoConversionResult:
        """Convert video to HEVC or AV1.

        Args:
            input_file: Path to input file
            output_file: Path for output file (defaults to replacing input)
            force_content_type: Override content type detection
            job_id: Unique job identifier (used for temp directory naming)
            progress_callback: Optional callback receiving progress 0-100.
            cancel_event: Event to signal cancellation (kills ffmpeg).
            detail_callback: Optional callback receiving status detail strings.
            log_cb: Optional callback receiving (source, level, message) log entries.
            encode_options: Optional custom encode overrides (resolution, HDR strip, force).
            title: Clean container title to write into output MKV global metadata.
            is_final_write: Whether this call's output is the real tracked media
                file (vs. an intermediate chain-temp handoff to the next phase
                in a multi-phase job). Defaults to matching ``output_file is
                None`` (in-place replacement) when not given explicitly.
            source_snapshot: Optional (mtime, size) of the tracked media file,
                captured by the orchestrator before any phase ran. Used by the
                final-write replacement guard so it covers the whole job, not
                just this phase.

        Returns:
            VideoConversionResult with conversion details
        """
        codec_to = self.target_codec
        input_path = Path(input_file)

        if not input_path.exists():
            return VideoConversionResult(
                success=False,
                input_file=input_file,
                output_file=output_file or input_file,
                original_size=0,
                new_size=0,
                codec_from="unknown",
                codec_to=codec_to,
                content_type="unknown",
                error=f"Input file not found: {input_file}",
            )

        # Get media info
        info = self.ffprobe.get_file_info(input_file)
        if info is None:
            return VideoConversionResult(
                success=False,
                input_file=input_file,
                output_file=output_file or input_file,
                original_size=0,
                new_size=0,
                codec_from="unknown",
                codec_to=codec_to,
                content_type="unknown",
                error="Failed to analyze input file",
            )

        video = info.primary_video
        if video is None:
            return VideoConversionResult(
                success=False,
                input_file=input_file,
                output_file=output_file or input_file,
                original_size=info.size,
                new_size=0,
                codec_from="unknown",
                codec_to=codec_to,
                content_type="unknown",
                error="No video stream found",
            )

        # Detect content type
        if force_content_type:
            content_type = force_content_type
        elif self.config.anime_auto_detect:
            content_type = self.anime_detector.detect(input_file)
        else:
            content_type = ContentType.LIVE_ACTION

        codec_label = "AV1" if codec_to == "av1" else "HEVC"
        encoder = resolve_encoder(codec_to, self.hw_accel, self.hw_caps)
        hw_label = ""
        if encoder not in ("libx265", "libsvtav1"):
            method = encoder.split("_")[-1].upper()
            hw_label = f" [{method}]"
        logger.info(
            "Converting %s (%s %s-bit → %s%s, %s)",
            input_file,
            video.codec_name,
            video.bit_depth,
            codec_label,
            hw_label,
            content_type.value,
        )

        if detail_callback:
            detail_callback(
                f"Encoding {video.codec_name} {video.bit_depth}-bit → {codec_label}{hw_label} ({content_type.value})"
            )

        # --- Dolby Vision retention (custom encode option) ----------------
        # When requested, carry the DV dynamic metadata through the re-encode
        # as Profile 8.1 instead of stripping to plain HDR10.  Hard
        # requirements: DV source, HEVC target via software x265 (hardware
        # encoders cannot attach per-frame RPUs), no SDR tone-mapping, and no
        # rescale (RPU L5 active-area offsets are relative to the source
        # raster).
        retain_dv = bool(encode_options and encode_options.get("retain_dv"))
        dv_retain_active = False
        if retain_dv:
            if not video.is_dolby_vision:
                logger.info(
                    "retain_dv requested but source has no Dolby Vision, encoding normally: %s",
                    input_file,
                )
                if log_cb:
                    log_cb("app", "info", "Source has no Dolby Vision — retain option ignored")
            else:
                _dv_error: str | None = None
                _target_res = (encode_options or {}).get("target_resolution")
                _would_downscale = bool(
                    _target_res
                    and _target_res != "original"
                    and video.height
                    and video.height > int(str(_target_res).rstrip("p"))
                )
                if codec_to != "hevc" or encoder != "libx265":
                    _dv_error = (
                        "Dolby Vision retention requires software HEVC encoding (libx265); "
                        f"current target is {encoder}"
                    )
                elif encode_options and encode_options.get("strip_hdr"):
                    _dv_error = "Dolby Vision retention cannot be combined with SDR tone-mapping"
                elif _would_downscale:
                    _dv_error = "Dolby Vision retention requires original resolution (no downscale)"
                elif self.config.vbv_maxrate <= 0 or self.config.vbv_bufsize <= 0:
                    # Checked up front: x265 refuses DV RPU encoding without
                    # VBV, and hitting that only after the base-layer prep
                    # would waste 20+ minutes of I/O.
                    _dv_error = (
                        "Dolby Vision retention requires VBV rate control — set non-zero "
                        "VBV Max Rate and Buffer Size in video settings (e.g. 40000/80000)"
                    )
                if _dv_error:
                    return VideoConversionResult(
                        success=False,
                        input_file=input_file,
                        output_file=output_file or input_file,
                        original_size=info.size,
                        new_size=0,
                        codec_from=video.codec_name,
                        codec_to=codec_to,
                        content_type=content_type.value,
                        error=_dv_error,
                    )
                dv_retain_active = True

        # Prepare output path
        replace_input = output_file is None
        if is_final_write is None:
            is_final_write = replace_input
        if output_file is None:
            output_file = input_file

        output_path = Path(output_file)

        # Create temp file in same volume as OUTPUT (for instant rename).
        # For in-place replacement the output IS the input, so get_volume_root
        # applies normally.  For chain phases the output path (chain file or
        # original file) may be on a different mergerfs branch than the input;
        # placing the temp dir next to the output guarantees a same-device
        # rename and avoids EXDEV errors.
        if job_id is None:
            job_id = uuid.uuid4().hex[:12]

        if replace_input:
            volume_root = self.get_volume_root(input_file)
        else:
            volume_root = str(Path(output_file).parent)
        temp_dir = Path(volume_root) / f".remuxcode-temp-{job_id}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_suffix = "av1" if codec_to == "av1" else "hevc"
        temp_file = temp_dir / f"{input_path.name}.{temp_suffix}-tmp.mkv"

        try:
            # Clean up any leftover temp files
            if temp_file.exists():
                temp_file.unlink()

            # --- Dolby Vision base-layer preparation ----------------------
            # Profile 7 (dual-layer, from UHD BD remuxes) RPUs reference an
            # enhancement layer that cannot survive a re-encode; dovi_tool
            # mode 2 rewrites them for single-layer BL+RPU (Profile 8.1)
            # playback.  Profile 5/8 sources need no conversion — ffmpeg's
            # decoder→encoder RPU passthrough handles them directly.
            dv_bl_input: str | None = None
            if dv_retain_active and video.dv_profile in (None, 7):
                if detail_callback:
                    detail_callback("Preparing Dolby Vision base layer (Profile 7 → 8.1)...")
                try:
                    dv_bl_input = self._prepare_dv_base_layer(
                        str(input_path),
                        temp_dir,
                        cancel_event=cancel_event,
                        log_cb=log_cb,
                        progress_callback=progress_callback,
                        detail_callback=detail_callback,
                    )
                except DVPreparationError as exc:
                    if not exc.preserve_temp:
                        shutil.rmtree(temp_dir, ignore_errors=True)
                    return VideoConversionResult(
                        success=False,
                        input_file=input_file,
                        output_file=output_file,
                        original_size=info.size,
                        new_size=0,
                        codec_from=video.codec_name,
                        codec_to=codec_to,
                        content_type=content_type.value,
                        error=f"Dolby Vision preparation failed: {exc}",
                    )
                if detail_callback:
                    detail_callback(
                        f"Encoding {video.codec_name} {video.bit_depth}-bit → HEVC + DV 8.1 ({content_type.value})"
                    )

            # Build and run ffmpeg command
            # Snapshot the identity of the tracked file we may overwrite
            # (output_path — for chained phases this is NOT the input) so the
            # pre-replace guard can detect Sonarr/Radarr swapping it mid-job.
            # For multi-phase jobs core supplies a snapshot taken at job start.
            _src_mtime: float | None = None
            _src_size: int | None = None
            if source_snapshot is not None:
                _src_mtime, _src_size = source_snapshot
            elif is_final_write:
                try:
                    _src_stat = output_path.stat()
                    _src_mtime = _src_stat.st_mtime
                    _src_size = _src_stat.st_size
                except OSError:
                    pass

            cmd = self._build_ffmpeg_command(
                str(input_path),
                str(temp_file),
                content_type,
                video,
                encode_options=encode_options,
                title=title,
                attachments=info.attachment_streams or None,
                dv_passthrough=dv_retain_active,
                dv_bl_input=dv_bl_input,
            )
            logger.debug("Running: %s", " ".join(cmd))
            if log_cb:
                log_cb("app", "info", f"$ {' '.join(cmd)}")

            # Estimate total frames for progress when out_time_us is N/A
            # (common with HW encoders like QSV).
            _total_frames: float | None = None
            if info.duration and video.frame_rate:
                try:
                    num, den = video.frame_rate.split("/")
                    _total_frames = info.duration * int(num) / int(den)
                except (ValueError, ZeroDivisionError):
                    pass

            returncode, stderr_text = run_ffmpeg_with_progress(
                cmd,
                duration_secs=info.duration,
                progress_cb=progress_callback,
                timeout=self.config.job_timeout or None,  # 0 = no timeout
                cancel_event=cancel_event,
                total_frames=_total_frames,
                log_cb=log_cb,
                affinity_fn=self.affinity_fn,
            )

            # The converted base layer is nearly source-sized; free it as soon
            # as the encode is done rather than waiting for temp-dir cleanup.
            if dv_bl_input:
                Path(dv_bl_input).unlink(missing_ok=True)

            if returncode != 0:
                temp_file.unlink(missing_ok=True)
                shutil.rmtree(temp_dir, ignore_errors=True)
                return VideoConversionResult(
                    success=False,
                    input_file=input_file,
                    output_file=output_file,
                    original_size=info.size,
                    new_size=0,
                    codec_from=video.codec_name,
                    codec_to=codec_to,
                    content_type=content_type.value,
                    error=ffmpeg_error_summary(returncode, stderr_text),
                )

            # Sanity-check: FFmpeg exited 0 but the output file isn't visible.
            # NFS/SMB attribute caching can hide a freshly-written file from
            # stat() for a while; wait_for_output_file retries with backoff and
            # forces dentry-cache revalidation between attempts.
            if not wait_for_output_file(temp_file, log_cb=log_cb):
                # FFmpeg reported a clean finish, so the file very likely
                # exists server-side even though stat() can't see it.  Keep
                # the temp dir for inspection/recovery instead of discarding
                # a completed encode.
                return VideoConversionResult(
                    success=False,
                    input_file=input_file,
                    output_file=output_file,
                    original_size=info.size,
                    new_size=0,
                    codec_from=video.codec_name,
                    codec_to=codec_to,
                    content_type=content_type.value,
                    error=(
                        "FFmpeg exited normally but output file is still missing "
                        f"after retries — temp dir preserved for inspection: {temp_file}"
                    ),
                )

            # Confirm the DV metadata actually survived the encode. A missing
            # record still leaves a valid HDR10 file, so warn rather than fail.
            if dv_retain_active:
                _out_info = self.ffprobe.get_file_info(str(temp_file))
                _out_video = _out_info.primary_video if _out_info else None
                if _out_video and _out_video.is_dolby_vision:
                    _msg = (
                        "Dolby Vision retained: Profile "
                        f"{_out_video.dv_profile if _out_video.dv_profile is not None else '?'}"
                    )
                    logger.info("%s: %s", _msg, temp_file.name)
                    if log_cb:
                        log_cb("app", "info", _msg)
                else:
                    logger.warning(
                        "Dolby Vision retention was requested but the encoded output "
                        "has no DOVI configuration record (HDR10 fallback still intact): %s",
                        temp_file.name,
                    )
                    if log_cb:
                        log_cb(
                            "app",
                            "warning",
                            "Dolby Vision metadata missing from encoded output — file is HDR10 only",
                        )

            # Move temp file to output location
            if temp_file.exists():
                # Check if original still exists (could be deleted during conversion)
                original_exists = output_path.exists()

                # Detect mid-job file replacement
                if is_final_write and original_exists and _src_mtime is not None:
                    try:
                        cur_stat = output_path.stat()
                        if cur_stat.st_mtime != _src_mtime or cur_stat.st_size != _src_size:
                            logger.warning(
                                "Source file was replaced during video conversion "
                                "(mtime/size changed) — discarding converted output "
                                "to avoid overwriting the new file: %s",
                                output_path,
                            )
                            temp_file.unlink(missing_ok=True)
                            shutil.rmtree(temp_dir, ignore_errors=True)
                            return VideoConversionResult(
                                success=False,
                                input_file=input_file,
                                output_file=output_file,
                                original_size=info.size,
                                new_size=0,
                                codec_from=video.codec_name,
                                codec_to=codec_to,
                                content_type=content_type.value,
                                error="Source file replaced during conversion — output discarded",
                            )
                    except OSError:
                        pass

                if not original_exists and is_final_write:
                    # Original was deleted during conversion (e.g., Radarr upgrade)
                    # Ensure parent directory exists
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    logger.warning(
                        "Original file deleted during conversion, placing converted file at: %s",
                        output_path,
                    )
                elif is_final_write:
                    pass  # safe_replace handles backup + move atomically

                if detail_callback:
                    detail_callback(
                        "Replacing file safely..."
                        if is_final_write
                        else "Saving intermediate output..."
                    )
                safe_replace(temp_file, output_path)

                # Clean up temp directory after successful move
                try:
                    if temp_dir.exists():
                        shutil.rmtree(temp_dir, ignore_errors=True)
                except Exception:
                    pass

            new_size = output_path.stat().st_size

            # Write accurate BPS/DURATION/NUMBER_OF_FRAMES track statistics tags
            # so MediaInfo and media servers report the correct AV1 bitrate.
            if output_path.suffix.lower() == ".mkv":
                try:
                    _stats_cmd = ["mkvpropedit", "--add-track-statistics-tags", str(output_path)]
                    if log_cb:
                        log_cb("app", "info", f"$ {' '.join(_stats_cmd)}")
                    proc_stats = subprocess.run(
                        _stats_cmd,
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=120,
                    )
                    if proc_stats.returncode == 0:
                        logger.debug("Updated track statistics tags: %s", output_path.name)
                    else:
                        logger.warning(
                            "mkvpropedit track-statistics failed for %s: %s",
                            output_path.name,
                            proc_stats.stderr.strip(),
                        )
                except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
                    logger.warning("mkvpropedit track-statistics error: %s", exc)

            logger.info(
                "Converted: %s (%.1fMB \u2192 %.1fMB)",
                input_file,
                info.size / 1024 / 1024,
                new_size / 1024 / 1024,
            )

            return VideoConversionResult(
                success=True,
                input_file=input_file,
                output_file=str(output_path),
                original_size=info.size,
                new_size=new_size,
                codec_from=video.codec_name,
                codec_to=codec_to,
                content_type=content_type.value,
            )

        except subprocess.TimeoutExpired:
            # Clean up
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)

            return VideoConversionResult(
                success=False,
                input_file=input_file,
                output_file=output_file,
                original_size=info.size,
                new_size=0,
                codec_from=video.codec_name,
                codec_to=codec_to,
                content_type=content_type.value,
                error=f"FFmpeg timeout (exceeded {self.config.job_timeout}s)",
            )

        except Exception as e:
            # Clean up
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)

            logger.exception("Conversion failed")
            return VideoConversionResult(
                success=False,
                input_file=input_file,
                output_file=output_file,
                original_size=info.size,
                new_size=0,
                codec_from=video.codec_name,
                codec_to=codec_to,
                content_type=content_type.value,
                error=str(e),
            )

    def _build_ffmpeg_command(
        self,
        input_file: str,
        output_file: str,
        content_type: ContentType,
        video: VideoStream | None = None,
        encode_options: dict | None = None,
        title: str | None = None,
        attachments: list[AttachmentStream] | None = None,
        dv_passthrough: bool = False,
        dv_bl_input: str | None = None,
    ) -> list[str]:
        """Build ffmpeg command with content-appropriate settings.

        dv_passthrough enables Dolby Vision RPU coding on the encoder
        (software HEVC only — enforced by the caller).  dv_bl_input, when
        set, is a raw HEVC base layer whose RPUs were pre-converted to
        Profile 8.1; video is read from it while audio/subs/chapters come
        from the original input_file.
        """
        encoder = resolve_encoder(self.target_codec, self.hw_accel, self.hw_caps)

        # HW-accelerated encoders
        if encoder == "hevc_qsv":
            cmd = self._build_qsv_command(
                input_file,
                output_file,
                content_type,
                codec="hevc",
                video=video,
                encode_options=encode_options,
                title=title,
            )
        elif encoder == "av1_qsv":
            cmd = self._build_qsv_command(
                input_file,
                output_file,
                content_type,
                codec="av1",
                video=video,
                encode_options=encode_options,
                title=title,
            )
        elif encoder == "hevc_vaapi":
            cmd = self._build_vaapi_command(
                input_file,
                output_file,
                content_type,
                codec="hevc",
                video=video,
                encode_options=encode_options,
                title=title,
            )
        elif encoder == "av1_vaapi":
            cmd = self._build_vaapi_command(
                input_file,
                output_file,
                content_type,
                codec="av1",
                video=video,
                encode_options=encode_options,
                title=title,
            )
        elif encoder == "hevc_nvenc":
            cmd = self._build_nvenc_command(
                input_file,
                output_file,
                content_type,
                codec="hevc",
                video=video,
                encode_options=encode_options,
                title=title,
            )
        elif encoder == "av1_nvenc":
            cmd = self._build_nvenc_command(
                input_file,
                output_file,
                content_type,
                codec="av1",
                video=video,
                encode_options=encode_options,
                title=title,
            )
        elif self.target_codec == "av1":
            # Software encoders
            cmd = self._build_av1_command(
                input_file,
                output_file,
                content_type,
                video=video,
                encode_options=encode_options,
                title=title,
            )
        else:
            cmd = self._build_hevc_command(
                input_file,
                output_file,
                content_type,
                video=video,
                encode_options=encode_options,
                title=title,
                dv_passthrough=dv_passthrough,
                dv_bl_input=dv_bl_input,
            )

        return self._patch_attachment_mimetypes(cmd, attachments or [])

    @staticmethod
    def _build_color_args(video: VideoStream | None) -> list[str]:
        """Return ffmpeg color metadata flags matching the source stream.

        Used by hardware-accelerated builders where the filter chain already
        handles pixel format conversion.  Falls back to BT.709 when the source
        has no color metadata (safe for all standard SDR content).
        """
        primaries = (video.color_primaries if video else None) or "bt709"
        trc = (video.color_trc if video else None) or "bt709"
        space = (video.color_space if video else None) or "bt709"
        return [
            "-color_primaries",
            primaries,
            "-color_trc",
            trc,
            "-colorspace",
            space,
        ]

    @staticmethod
    def _build_sw_vf_filter(
        pix_fmt: str,
        framerate: str,
        encode_options: dict | None = None,
        video: VideoStream | None = None,
        reset_pts: bool = False,
        deinterlace: bool = False,
    ) -> list[str]:
        """Build a single -vf filter chain for software encoders (HEVC, AV1).

        Handles optional scale (target_resolution) and HDR→SDR tone-mapping
        (strip_hdr).  When both are requested they are chained in the correct
        order: scale → tone-map → format.

        Only pixel-format conversion and optional fps normalisation are done
        here when no encode_options are provided.  Color metadata tags are
        intentionally omitted: declaring a colorspace that differs from the
        source causes FFmpeg 7.x to insert an ``auto_scale`` filter for the
        implied conversion, which then conflicts with the explicit filter graph
        and raises "Impossible to convert … auto_scale_0".
        """
        parts: list[str] = []

        # --- Deinterlace (must be first — before scale/fps/format) ---
        if deinterlace:
            parts.append("yadif=mode=0")

        # --- Optional scale (downscale only) ---
        if encode_options:
            target_res = encode_options.get("target_resolution")
            if target_res and target_res != "original":
                target_height = int(target_res.rstrip("p"))
                src_height = video.height if video else None
                if src_height is None or src_height > target_height:
                    parts.append(f"scale=-2:{target_height}:flags=lanczos")

        # --- FPS normalisation ---
        if framerate:
            parts.append(f"fps=fps={framerate}")

        # --- PTS reset for DV sources ---
        # For DV sources, regenerate monotonically-increasing PTS from the
        # frame counter to smooth out any minor timestamp irregularities
        # from DV RPU SEI data in the HEVC bitstream.  With valid container
        # timestamps (no -igndts), FRAME_RATE is correctly detected from
        # the source stream.
        if reset_pts:
            parts.append("setpts=N/FRAME_RATE/TB")

        # --- HDR → SDR tone-mapping ---
        if encode_options and encode_options.get("strip_hdr"):
            # zscale-based tone-map: linear light → hable → BT.709 SDR
            # Outputs yuv420p regardless of what pix_fmt says (SDR = 8-bit)
            parts.extend(
                [
                    "zscale=t=linear:npl=100",
                    "format=gbrpf32le",
                    "zscale=p=bt709",
                    "tonemap=tonemap=hable:desat=0",
                    "zscale=t=bt709:m=bt709:r=tv",
                    "format=yuv420p",
                ]
            )
        else:
            parts.append(f"format={pix_fmt}")

        return ["-vf", ",".join(parts)]

    def _prepare_dv_base_layer(
        self,
        input_file: str,
        temp_dir: Path,
        cancel_event: threading.Event | None = None,
        log_cb: Callable[[str, str, str], None] | None = None,
        progress_callback: Callable[[float], None] | None = None,
        detail_callback: Callable[[str], None] | None = None,
    ) -> str:
        """Extract the HEVC base layer with its RPU converted to DV Profile 8.1.

        Pipes the source video stream through ``dovi_tool -m 2 convert
        --discard``, which rewrites Profile 7 RPUs for single-layer BL+RPU
        playback and drops the enhancement layer.  The resulting raw Annex-B
        bitstream (written into the job temp dir, roughly source-video-sized)
        is the video input for the subsequent encode.

        Returns the path of the converted bitstream.

        Raises:
            DVPreparationError: On cancellation or if either pipeline stage
                fails or produces no output.
        """
        bl_path = temp_dir / f"{Path(input_file).name}.dv81-bl.hevc"
        bl_path.unlink(missing_ok=True)

        extract_cmd = [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-err_detect",
            "ignore_err",
            "-analyzeduration",
            "200M",
            "-probesize",
            "200M",
            "-i",
            input_file,
            "-map",
            "0:v:0",
            "-c",
            "copy",
            "-f",
            "hevc",
            "-",
        ]
        convert_cmd = ["dovi_tool", "-m", "2", "convert", "--discard", "-", "-o", str(bl_path)]

        logger.info("Preparing DV base layer: %s", bl_path.name)
        if log_cb:
            log_cb("app", "info", f"$ {' '.join(extract_cmd)} | {' '.join(convert_cmd)}")

        extract_proc = subprocess.Popen(  # noqa: S603
            extract_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        convert_proc = subprocess.Popen(  # noqa: S603
            convert_cmd,
            stdin=extract_proc.stdout,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        # Let dovi_tool own the read end so a converter exit propagates
        # SIGPIPE to ffmpeg instead of deadlocking the pipe.
        if extract_proc.stdout:
            extract_proc.stdout.close()

        def _kill_both() -> None:
            for proc in (extract_proc, convert_proc):
                if proc.poll() is None:
                    proc.kill()
            extract_proc.wait()
            convert_proc.wait()

        deadline = time.monotonic() + 4 * 3600  # I/O bound; generous cap
        last_heartbeat = 0.0
        while True:
            try:
                convert_proc.wait(timeout=2)
                break
            except subprocess.TimeoutExpired:
                if cancel_event is not None and cancel_event.is_set():
                    _kill_both()
                    raise DVPreparationError("cancelled") from None
                if time.monotonic() > deadline:
                    _kill_both()
                    raise DVPreparationError("timed out extracting base layer") from None
                # Heartbeat: large remuxes take well over the 15-minute
                # stale-job watchdog window to stream through dovi_tool, and
                # only progress callbacks refresh job.last_progress_at — so
                # pulse it (value unchanged) and surface bytes written.
                if time.monotonic() - last_heartbeat >= 10:
                    last_heartbeat = time.monotonic()
                    if progress_callback:
                        progress_callback(0.0)
                    if detail_callback:
                        try:
                            _written = bl_path.stat().st_size
                        except OSError:
                            _written = 0
                        detail_callback(
                            "Preparing Dolby Vision base layer (Profile 7 → 8.1)... "
                            f"{_written / 1e9:.1f} GB written"
                        )

        extract_rc = extract_proc.wait()
        convert_stderr = (convert_proc.stderr.read() if convert_proc.stderr else b"").decode(
            errors="replace"
        )
        extract_stderr = (extract_proc.stderr.read() if extract_proc.stderr else b"").decode(
            errors="replace"
        )

        if convert_proc.returncode != 0:
            bl_path.unlink(missing_ok=True)
            raise DVPreparationError(
                f"dovi_tool failed: {convert_stderr.strip()[-500:] or 'unknown error'}"
            )
        if extract_rc != 0:
            bl_path.unlink(missing_ok=True)
            raise DVPreparationError(
                f"base layer extraction failed: {extract_stderr.strip()[-500:] or 'unknown error'}"
            )
        # A freshly-closed multi-GB file can transiently fail stat() on
        # CIFS/NFS (attribute-cache lag) even though it exists server-side —
        # retry with dentry-cache revalidation before declaring it missing.
        if not wait_for_output_file(bl_path, log_cb=log_cb):
            exc = DVPreparationError(
                "converted base layer is still not visible after retries — "
                f"temp dir preserved for inspection: {bl_path}"
            )
            exc.preserve_temp = True
            raise exc

        bl_size = bl_path.stat().st_size
        if bl_size == 0:
            bl_path.unlink(missing_ok=True)
            raise DVPreparationError("converted base layer is empty")

        logger.info("DV base layer ready: %s (%d bytes)", bl_path.name, bl_size)
        return str(bl_path)

    def _build_hevc_command(
        self,
        input_file: str,
        output_file: str,
        content_type: ContentType,
        video: VideoStream | None = None,
        encode_options: dict | None = None,
        title: str | None = None,
        dv_passthrough: bool = False,
        dv_bl_input: str | None = None,
    ) -> list[str]:
        """Build ffmpeg command for HEVC (libx265) encoding."""

        # Select encoding parameters based on content type
        tune: str | None
        if content_type == ContentType.ANIME:
            crf = self.config.anime_crf
            preset = self.config.anime_preset
            tune = self.config.anime_tune
            framerate = self.config.anime_framerate
        else:
            crf = self.config.live_action_crf
            preset = self.config.live_action_preset
            tune = self.config.live_action_tune
            framerate = self.config.live_action_framerate

        # Base x265 params
        x265_params = [
            f"crf={crf}",
            f"vbv-maxrate={self.config.vbv_maxrate}",
            f"vbv-bufsize={self.config.vbv_bufsize}",
            "ref=4",
            "bframes=6",
            "open-gop=0",
            "keyint=240",
            "min-keyint=24",
            "scenecut=40",
        ]

        # Limit x265 thread pool so concurrent workers don't over-subscribe CPUs.
        # When ffmpeg_threads is 0 (auto), let x265 self-tune.
        if self.ffmpeg_threads > 0:
            x265_params.append(f"pools={self.ffmpeg_threads}")

        # Pass through HDR10 mastering display metadata if present (skip when stripping to SDR)
        strip_hdr = bool(encode_options and encode_options.get("strip_hdr"))
        if not strip_hdr:
            if video and video.hdr_master_display:
                x265_params.append(f"master-display={video.hdr_master_display}")
            if video and video.hdr_max_cll:
                x265_params.append(f"max-cll={video.hdr_max_cll}")

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-stats",
        ]

        if self.ffmpeg_threads > 0:
            cmd.extend(["-threads", str(self.ffmpeg_threads)])

        # DV HEVC streams embed RPU SEI NAL units that may be malformed;
        # ignore_err skips bad data without aborting the decode.
        # -fflags+igndts is intentionally omitted: it destroys container
        # timestamp info, causing FRAME_RATE in setpts to fall back to 25 fps
        # and -fps_mode cfr to fill timing gaps with duplicate frames,
        # eventually deadlocking the SVT-AV1 pipeline.
        is_dv_source = bool(video and video.is_dolby_vision)
        cmd.extend(["-err_detect", "ignore_err"])

        if dv_bl_input:
            # Video comes from the pre-converted raw HEVC base layer (Annex-B
            # bitstreams carry no container timing, so declare the source
            # frame rate); audio/subs/attachments/chapters come from the
            # original file.
            if video and video.frame_rate and video.frame_rate != "0/1":
                cmd.extend(["-framerate", video.frame_rate])
            cmd.extend(
                [
                    "-i",
                    dv_bl_input,
                    "-analyzeduration",
                    "200M",
                    "-probesize",
                    "200M",
                    "-i",
                    input_file,
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a?",
                    "-map",
                    "1:s?",
                    "-map",
                    "1:t?",
                    "-map_chapters",
                    "1",
                    "-map_metadata",
                    "1",
                ]
            )
        else:
            cmd.extend(
                [
                    "-analyzeduration",
                    "200M",
                    "-probesize",
                    "200M",
                    "-i",
                    input_file,
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a?",
                    "-map",
                    "0:s?",
                    "-map",
                    "0:t?",
                    "-map_chapters",
                    "0",
                ]
            )

        cmd.extend(
            [
                "-c:v:0",
                "libx265",
                "-profile:v",
                self.config.profile,
                "-preset",
                preset,
            ]
        )

        # Re-attach decoded DV RPUs to the encoded frames (Profile 8.1).
        if dv_passthrough:
            cmd.extend(["-dolbyvision", "1"])

        # Add tune if specified
        if tune:
            cmd.extend(["-tune", tune])

        # Add x265 params
        cmd.extend(["-x265-params", ":".join(x265_params)])

        # Pixel format, framerate, and optional scale/tone-map filters.
        # This prevents FFmpeg 7.x from auto-inserting ``auto_scale`` for
        # colour-space or fps-mode reasons, which conflicts with an explicit
        # filter graph and causes "Impossible to convert" errors on some files.
        vf_args = self._build_sw_vf_filter(
            self.config.pix_fmt,
            framerate,
            encode_options=encode_options,
            video=video,
            deinterlace=self.config.deinterlace and bool(video and video.is_interlaced),
        )
        cmd.extend(vf_args)

        # Copy audio, subtitles, and attachments
        cmd.extend(
            [
                "-c:a",
                "copy",
                "-c:s",
                "copy",
                "-c:t",
                "copy",
            ]
        )

        cmd.extend(["-fps_mode", "cfr"])
        if framerate:
            cmd.extend(["-r", framerate])
        elif is_dv_source and video and video.frame_rate and video.frame_rate != "0/1":
            # DV sources often lack a declared frame rate in stream headers;
            # without an explicit -r, -fps_mode cfr's implicit fps filter falls
            # back to 25 fps, causing duplicate frames and pipeline deadlock.
            cmd.extend(["-r", video.frame_rate])

        # Clear stale video stream tags from source MKV
        cmd.extend(self._clear_video_stream_tags())

        # Override container title with clean version (strips scene-group garbage)
        if title:
            cmd.extend(["-metadata", f"title={title}"])

        # Output file
        cmd.append(output_file)

        return cmd

    def _build_av1_command(
        self,
        input_file: str,
        output_file: str,
        content_type: ContentType,
        video: VideoStream | None = None,
        encode_options: dict | None = None,
        title: str | None = None,
    ) -> list[str]:
        """Build ffmpeg command for AV1 (libsvtav1) encoding."""

        # Select encoding parameters based on content type
        if content_type == ContentType.ANIME:
            crf = self.config.av1_anime_crf
            preset = self.config.av1_anime_preset
            framerate = self.config.av1_anime_framerate
        else:
            crf = self.config.av1_live_action_crf
            preset = self.config.av1_live_action_preset
            framerate = self.config.av1_live_action_framerate

        # Compute keyframe interval: 5 seconds × fps.
        # SVT-AV1 defaults to 2–3 s which wastes bitrate on I-frames.
        # Prefer the configured output framerate; fall back to the source
        # frame_rate from ffprobe; fall back to 120 (5 s @ 24 fps).
        _fps_str = framerate or (video.frame_rate if video else "") or ""
        try:
            if "/" in _fps_str:
                _n, _d = _fps_str.split("/")
                _fps = float(_n) / float(_d)
            else:
                _fps = float(_fps_str)
            keyint = max(1, round(_fps * 5))
        except (ValueError, ZeroDivisionError):
            keyint = 120  # safe fallback: 5 s @ 24 fps

        # SVT-AV1 params
        svtav1_params = [
            "tune=0",  # 0=VQ (visual quality), 1=PSNR
            f"keyint={keyint}",  # 5-second keyframe interval
        ]

        # Limit SVT-AV1 logical processors so concurrent workers don't over-subscribe CPUs.
        # SVT-AV1 ignores ffmpeg's global -threads flag; lp must be set explicitly.
        # When ffmpeg_threads is 0 (auto), let SVT-AV1 self-tune.
        if self.ffmpeg_threads > 0:
            svtav1_params.append(f"lp={self.ffmpeg_threads}")

        # Film grain synthesis: 0=disabled, 1–50 adds synthetic grain at decode time.
        # Anime gets 0 (clean cel-shaded content needs no grain).
        # Live action can benefit from subtle values (e.g. 4) to mask compression
        # artifacts on cinematic or noisy sources without visible over-application.
        grain = (
            self.config.av1_anime_film_grain
            if content_type == ContentType.ANIME
            else self.config.av1_live_action_film_grain
        )
        svtav1_params.append(f"film-grain={grain}")

        # Pass through HDR10 metadata if present (skip when stripping to SDR)
        strip_hdr = bool(encode_options and encode_options.get("strip_hdr"))
        if not strip_hdr:
            if video and video.hdr_master_display:
                svtav1_params.append(f"mastering-display={video.hdr_master_display}")
            if video and video.hdr_max_cll:
                svtav1_params.append(f"content-light={video.hdr_max_cll}")

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-stats",
        ]

        if self.ffmpeg_threads > 0:
            cmd.extend(["-threads", str(self.ffmpeg_threads)])

        # DV HEVC streams embed RPU SEI NAL units that may be malformed;
        # ignore_err skips bad data without aborting the decode.
        # -fflags+igndts is intentionally omitted: it destroys container
        # timestamp info, causing FRAME_RATE in setpts to fall back to 25 fps
        # and -fps_mode cfr to fill timing gaps with duplicate frames,
        # eventually deadlocking the SVT-AV1 pipeline.
        is_dv_source = bool(video and video.is_dolby_vision)
        cmd.extend(["-err_detect", "ignore_err"])

        cmd.extend(
            [
                "-analyzeduration",
                "200M",
                "-probesize",
                "200M",
                "-i",
                input_file,
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-map",
                "0:s?",
                "-map",
                "0:t?",
                "-map_chapters",
                "0",
                "-c:v:0",
                "libsvtav1",
                "-crf",
                str(crf),
                "-preset",
                str(preset),
            ]
        )

        # Add SVT-AV1 params
        cmd.extend(["-svtav1-params", ":".join(svtav1_params)])

        # Pixel format, framerate, and optional scale/tone-map filters.
        # Prevents FFmpeg 7.x auto_scale insertion (see _build_hevc_command).
        vf_args = self._build_sw_vf_filter(
            "yuv420p10le",
            framerate,
            encode_options=encode_options,
            video=video,
            deinterlace=self.config.deinterlace and bool(video and video.is_interlaced),
        )
        cmd.extend(vf_args)

        # Copy audio, subtitles, and attachments
        cmd.extend(
            [
                "-c:a",
                "copy",
                "-c:s",
                "copy",
                "-c:t",
                "copy",
            ]
        )

        cmd.extend(["-fps_mode", "cfr"])
        if framerate:
            cmd.extend(["-r", framerate])
        elif is_dv_source and video and video.frame_rate and video.frame_rate != "0/1":
            # DV sources often lack a declared frame rate in stream headers;
            # without an explicit -r, -fps_mode cfr's implicit fps filter falls
            # back to 25 fps, causing duplicate frames and pipeline deadlock.
            cmd.extend(["-r", video.frame_rate])

        # Clear stale video stream tags from source MKV
        cmd.extend(self._clear_video_stream_tags())

        # Override container title with clean version (strips scene-group garbage)
        if title:
            cmd.extend(["-metadata", f"title={title}"])

        # Output file
        cmd.append(output_file)

        return cmd

    # ------------------------------------------------------------------
    # Hardware-accelerated encoder builders
    # ------------------------------------------------------------------

    def _get_quality_params(self, content_type: ContentType, encoder: str = "") -> tuple[int, str]:
        """Return (quality_value, framerate) for given content type and encoder.

        Framerate depends on the target codec (HEVC vs AV1), not the encoder.
        Quality depends on the encoder method — HW encoders use their own
        config fields while SW encoders use the CRF fields.
        """
        is_anime = content_type == ContentType.ANIME

        # Framerate: codec-dependent, not encoder-dependent
        if self.target_codec == "av1":
            framerate = (
                self.config.av1_anime_framerate
                if is_anime
                else self.config.av1_live_action_framerate
            )
        else:
            framerate = (
                self.config.anime_framerate if is_anime else self.config.live_action_framerate
            )

        # Quality: encoder-dependent
        if "_qsv" in encoder:
            quality = (
                self.config.qsv_anime_quality if is_anime else self.config.qsv_live_action_quality
            )
        elif "_vaapi" in encoder:
            quality = (
                self.config.vaapi_anime_quality
                if is_anime
                else self.config.vaapi_live_action_quality
            )
        elif "_nvenc" in encoder:
            quality = (
                self.config.nvenc_anime_quality
                if is_anime
                else self.config.nvenc_live_action_quality
            )
        elif self.target_codec == "av1":
            quality = self.config.av1_anime_crf if is_anime else self.config.av1_live_action_crf
        else:
            quality = self.config.anime_crf if is_anime else self.config.live_action_crf

        return quality, framerate

    @staticmethod
    def _append_copy_streams(cmd: list[str]) -> None:
        """Append flags to copy audio, subtitles, and attachments."""
        cmd.extend(["-c:a", "copy", "-c:s", "copy", "-c:t", "copy"])

    @staticmethod
    def _patch_attachment_mimetypes(
        cmd: list[str],
        attachments: list[AttachmentStream],
    ) -> list[str]:
        """Insert -metadata:s:t:N tags for attachments missing mimetype or filename.

        The MKV muxer refuses to write the output header when any attachment
        stream is missing its mimetype or filename tag.  This method patches the
        completed ffmpeg command by inserting metadata overrides immediately
        before the output filename (the last element).
        """
        if not attachments:
            return cmd

        fixes: list[str] = []
        for i, att in enumerate(attachments):
            mime = att.mimetype
            if not mime:
                ext = Path(att.filename or "").suffix.lower()
                mime = _ATTACHMENT_MIME_FALLBACK.get(ext, "application/octet-stream")
                fixes += [f"-metadata:s:t:{i}", f"mimetype={mime}"]

            if not att.filename:
                ext = _ATTACHMENT_MIME_TO_EXT.get(mime, ".bin")
                fixes += [f"-metadata:s:t:{i}", f"filename=attachment_{i}{ext}"]

        if not fixes:
            return cmd

        # cmd[-1] is the output file path — insert before it
        return cmd[:-1] + fixes + [cmd[-1]]

    @staticmethod
    def _clear_video_stream_tags() -> list[str]:
        """Clear stale per-stream stats from the re-encoded video stream.

        Default ``-map_metadata 0`` copies BPS, NUMBER_OF_BYTES, SOURCE_ID, title,
        and other per-stream tags from the source MKV into the output video stream.
        After re-encoding these values are stale (e.g. the original 4K HEVC bitrate
        instead of the new AV1 bitrate).

        NOTE: ``-map_metadata:s:v:0 -1`` was previously used but FFmpeg 7.x treats
        it as ``-map_metadata:s -1``, clearing ALL per-stream metadata including
        attachment filename/mimetype — causing the MKV muxer to reject the output.
        Instead, explicitly unset individual stale stats tags; FFmpeg's MKV muxer
        then auto-generates correct values from the actual encoded output.
        """
        # "title" is included so source stream titles like "Dolby Vision P7 FEL"
        # are not carried into a re-encoded output that no longer contains DV data.
        stale = ("BPS", "NUMBER_OF_BYTES", "NUMBER_OF_FRAMES", "DURATION", "SOURCE_ID", "title")
        flags: list[str] = []
        for tag in stale:
            flags += ["-metadata:s:v:0", f"{tag}="]
        return flags

    @staticmethod
    def _build_preupload_sw_filter(
        upload_fmt: str,
        encode_options: dict | None,
        video: VideoStream | None,
        reset_pts: bool = False,
        deinterlace: bool = False,
    ) -> str:
        """Build the SW filter chain run *before* hwupload for HW encoders.

        Handles optional scale (target_resolution) and HDR→SDR tone-mapping
        (strip_hdr).  Returns the complete comma-joined filter string ready to
        be concatenated with the hwupload command (e.g. ``…,hwupload``).
        """
        parts: list[str] = []

        # Deinterlace (must be first — before scale/PTS/format/hwupload)
        if deinterlace:
            parts.append("yadif=mode=0")

        if encode_options:
            target_res = encode_options.get("target_resolution")
            if target_res and target_res != "original":
                target_height = int(target_res.rstrip("p"))
                src_height = video.height if video else None
                if src_height is None or src_height > target_height:
                    parts.append(f"scale=-2:{target_height}:flags=lanczos")

        # PTS reset for DV sources — regenerate monotonic timestamps from the
        # decoded frame counter before the HW upload.  DV HEVC streams produce
        # frames with broken or frozen PTS; setpts guarantees the HW encoder
        # always receives valid, increasing timestamps.  Placed after scale so
        # N counts the frames that will actually be encoded.
        if reset_pts:
            parts.append("setpts=N/FRAME_RATE/TB")

        if encode_options and encode_options.get("strip_hdr"):
            parts.extend(
                [
                    "zscale=t=linear:npl=100",
                    "format=gbrpf32le",
                    "zscale=p=bt709",
                    "tonemap=tonemap=hable:desat=0",
                    "zscale=t=bt709:m=bt709:r=tv",
                    "format=nv12",  # SDR output → 8-bit nv12 before HW upload
                ]
            )
            return ",".join(parts)

        parts.append(f"format={upload_fmt}")
        return ",".join(parts)

    @staticmethod
    def _sdr_color_args() -> list[str]:
        """Return BT.709 SDR color flags for use when stripping HDR."""
        return [
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-colorspace",
            "bt709",
        ]

    def _build_qsv_command(
        self,
        input_file: str,
        output_file: str,
        content_type: ContentType,
        *,
        codec: str = "hevc",
        video: VideoStream | None = None,
        encode_options: dict | None = None,
        title: str | None = None,
    ) -> list[str]:
        """Build ffmpeg command for Intel QSV encoding (HEVC or AV1).

        Uses software decoding + QSV hardware encoding.  Full HW-decode
        via ``-hwaccel qsv`` fails on many Intel GPUs for inputs the QSV
        decoder doesn't support (e.g. H.264 High 10 profile).  Letting the
        CPU decode and the GPU encode avoids those errors with negligible
        speed impact (encoding is the bottleneck, not decoding).
        """
        encoder = "hevc_qsv" if codec == "hevc" else "av1_qsv"
        quality, framerate = self._get_quality_params(content_type, encoder)

        # When stripping HDR → SDR the output is 8-bit regardless of config.
        strip_hdr = bool(encode_options and encode_options.get("strip_hdr"))
        is_10bit = (not strip_hdr) and (
            "10" in (self.config.pix_fmt or "") or "10" in (self.config.profile or "")
        )
        upload_fmt = "p010le" if is_10bit else "nv12"

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-stats",
            # Initialise QSV device for encoding — decoding stays in software
            # so every input codec/profile is supported.
            "-init_hw_device",
            "qsv=hw",
            "-filter_hw_device",
            "hw",
        ]

        if self.ffmpeg_threads > 0:
            cmd.extend(["-threads", str(self.ffmpeg_threads)])

        is_dv_source = bool(video and video.is_dolby_vision)
        cmd.extend(["-err_detect", "ignore_err"])

        cmd.extend(
            [
                "-analyzeduration",
                "200M",
                "-probesize",
                "200M",
                "-i",
                input_file,
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-map",
                "0:s?",
                "-map",
                "0:t?",
                "-map_chapters",
                "0",
                # Upload SW-decoded frames to QSV surface for HW encoding
                "-filter:v:0",
                self._build_preupload_sw_filter(
                    upload_fmt,
                    encode_options,
                    video,
                    deinterlace=self.config.deinterlace and bool(video and video.is_interlaced),
                )
                + ",hwupload=extra_hw_frames=64",
                "-c:v:0",
                encoder,
                "-global_quality",
                str(quality),
                "-preset",
                self.config.qsv_preset,
            ]
        )

        if codec == "hevc":
            # QSV auto-selects the correct profile (main/main10) based on the
            # input pixel format.  Explicitly setting -profile:v can conflict
            # when the format doesn't match, so only set it for 8-bit main.
            if not is_10bit:
                cmd.extend(["-profile:v", "main"])
            # look_ahead (ICQ mode) is supported by hevc_qsv but not av1_qsv.
            cmd.extend(["-look_ahead", "1", "-look_ahead_depth", "40"])

        cmd.extend(["-fps_mode", "cfr"])
        if framerate:
            cmd.extend(["-r", framerate])
        elif is_dv_source and video and video.frame_rate and video.frame_rate != "0/1":
            cmd.extend(["-r", video.frame_rate])

        cmd.extend(self._sdr_color_args() if strip_hdr else self._build_color_args(video))
        self._append_copy_streams(cmd)
        cmd.extend(self._clear_video_stream_tags())
        if title:
            cmd.extend(["-metadata", f"title={title}"])
        cmd.append(output_file)
        return cmd

    def _build_vaapi_command(
        self,
        input_file: str,
        output_file: str,
        content_type: ContentType,
        *,
        codec: str = "hevc",
        video: VideoStream | None = None,
        encode_options: dict | None = None,
        title: str | None = None,
    ) -> list[str]:
        """Build ffmpeg command for VAAPI encoding (HEVC or AV1).

        Uses software decoding + VAAPI hardware encoding.  Full HW-decode
        via ``-hwaccel vaapi`` fails when the VAAPI driver can't decode the
        input format (e.g. H.264 High 10 on many Intel/AMD GPUs).  SW decode
        + HW encode avoids those errors reliably.
        """
        encoder = "hevc_vaapi" if codec == "hevc" else "av1_vaapi"
        quality, framerate = self._get_quality_params(content_type, encoder)
        device = "/dev/dri/renderD128"
        if self.hw_caps and self.hw_caps.render_devices:
            device = self.hw_caps.render_devices[0]

        # When stripping HDR → SDR the output is 8-bit regardless of config.
        strip_hdr = bool(encode_options and encode_options.get("strip_hdr"))
        is_10bit = (not strip_hdr) and (
            "10" in (self.config.pix_fmt or "") or "10" in (self.config.profile or "")
        )
        upload_fmt = "p010" if is_10bit else "nv12"

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-stats",
            # Initialise VAAPI device for encoding — decoding stays in software
            # so every input codec/profile is supported.
            "-init_hw_device",
            f"vaapi=hw:{device}",
            "-filter_hw_device",
            "hw",
        ]

        if self.ffmpeg_threads > 0:
            cmd.extend(["-threads", str(self.ffmpeg_threads)])

        is_dv_source = bool(video and video.is_dolby_vision)
        cmd.extend(["-err_detect", "ignore_err"])

        cmd.extend(
            [
                "-analyzeduration",
                "200M",
                "-probesize",
                "200M",
                "-i",
                input_file,
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-map",
                "0:s?",
                "-map",
                "0:t?",
                "-map_chapters",
                "0",
                # Upload SW-decoded frames to VAAPI surface for HW encoding
                "-filter:v:0",
                self._build_preupload_sw_filter(
                    upload_fmt,
                    encode_options,
                    video,
                    deinterlace=self.config.deinterlace and bool(video and video.is_interlaced),
                )
                + ",hwupload",
                "-c:v:0",
                encoder,
                "-rc_mode",
                "CQP",
                "-qp",
                str(quality),
            ]
        )

        if codec == "hevc":
            # VAAPI auto-selects main/main10 from the surface format.
            # Only set profile explicitly for 8-bit to avoid mismatches.
            if not is_10bit:
                cmd.extend(["-profile:v", "main"])

        cmd.extend(["-fps_mode", "cfr"])
        if framerate:
            cmd.extend(["-r", framerate])
        elif is_dv_source and video and video.frame_rate and video.frame_rate != "0/1":
            cmd.extend(["-r", video.frame_rate])

        cmd.extend(self._sdr_color_args() if strip_hdr else self._build_color_args(video))
        self._append_copy_streams(cmd)
        cmd.extend(self._clear_video_stream_tags())
        if title:
            cmd.extend(["-metadata", f"title={title}"])
        cmd.append(output_file)
        return cmd

    def _build_nvenc_command(
        self,
        input_file: str,
        output_file: str,
        content_type: ContentType,
        *,
        codec: str = "hevc",
        video: VideoStream | None = None,
        encode_options: dict | None = None,
        title: str | None = None,
    ) -> list[str]:
        """Build ffmpeg command for NVENC encoding (HEVC or AV1).

        Uses software decoding + NVENC hardware encoding.  Full HW-decode
        via ``-hwaccel cuda`` fails when CUVID can't decode the input format
        (e.g. some 10-bit H.264 profiles).  SW decode + HW encode is safe
        for every input.
        """
        encoder = "hevc_nvenc" if codec == "hevc" else "av1_nvenc"
        quality, framerate = self._get_quality_params(content_type, encoder)

        # When stripping HDR → SDR the output is 8-bit regardless of config.
        strip_hdr = bool(encode_options and encode_options.get("strip_hdr"))
        is_10bit = (not strip_hdr) and (
            "10" in (self.config.pix_fmt or "") or "10" in (self.config.profile or "")
        )
        upload_fmt = "p010le" if is_10bit else "nv12"

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-stats",
            # Initialise CUDA device for encoding — decoding stays in software
            # so every input codec/profile is supported.
            "-init_hw_device",
            "cuda=hw",
            "-filter_hw_device",
            "hw",
        ]

        if self.ffmpeg_threads > 0:
            cmd.extend(["-threads", str(self.ffmpeg_threads)])

        is_dv_source = bool(video and video.is_dolby_vision)
        cmd.extend(["-err_detect", "ignore_err"])

        cmd.extend(
            [
                "-analyzeduration",
                "200M",
                "-probesize",
                "200M",
                "-i",
                input_file,
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-map",
                "0:s?",
                "-map",
                "0:t?",
                "-map_chapters",
                "0",
                # Upload SW-decoded frames to CUDA surface for HW encoding
                "-filter:v:0",
                self._build_preupload_sw_filter(
                    upload_fmt,
                    encode_options,
                    video,
                    deinterlace=self.config.deinterlace and bool(video and video.is_interlaced),
                )
                + ",hwupload_cuda",
                "-c:v:0",
                encoder,
                "-cq",
                str(quality),
                "-preset",
                self.config.nvenc_preset,
            ]
        )

        if codec == "hevc":
            # NVENC handles main10 correctly when receiving P010 surfaces.
            if is_10bit:
                cmd.extend(["-profile:v", "main10"])
            else:
                cmd.extend(["-profile:v", "main"])

        cmd.extend(["-fps_mode", "cfr"])
        if framerate:
            cmd.extend(["-r", framerate])
        elif is_dv_source and video and video.frame_rate and video.frame_rate != "0/1":
            cmd.extend(["-r", video.frame_rate])

        cmd.extend(self._sdr_color_args() if strip_hdr else self._build_color_args(video))
        self._append_copy_streams(cmd)
        cmd.extend(self._clear_video_stream_tags())
        if title:
            cmd.extend(["-metadata", f"title={title}"])
        cmd.append(output_file)
        return cmd
