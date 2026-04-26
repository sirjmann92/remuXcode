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
import uuid

from backend.utils.anime_detect import AnimeDetector, ContentType
from backend.utils.config import VideoConfig
from backend.utils.ffprobe import FFProbe, VideoStream
from backend.utils.hwaccel import HWAccelCaps, resolve_encoder
from backend.workers._progress import run_ffmpeg_with_progress
from backend.workers._safe_move import safe_replace

logger = logging.getLogger(__name__)


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
        """
        self.config = config
        self.ffprobe = ffprobe or FFProbe()
        self.ffmpeg_threads = ffmpeg_threads
        self.hw_accel = hw_accel
        self.hw_caps = hw_caps
        self.anime_detector = anime_detector or AnimeDetector()
        self.get_volume_root = get_volume_root or (lambda _: tempfile.gettempdir())

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
        - If anime_only is True, only converts anime content
        """
        if not self.config.enabled:
            return False

        info = self.ffprobe.get_file_info(file_path)
        if info is None:
            return False

        video = info.primary_video
        if video is None:
            return False

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

        # Check if anime-only mode and skip non-anime
        if self.config.anime_only:
            content_type = self.anime_detector.detect(file_path, use_api=False)
            if content_type != ContentType.ANIME:
                logger.debug("Skipping non-anime file (anime_only=True): %s", file_path)
                return False

        # Check if live-action-only mode and skip anime
        if self.config.live_action_only:
            content_type = self.anime_detector.detect(file_path, use_api=False)
            if content_type == ContentType.ANIME:
                logger.debug("Skipping anime file (live_action_only=True): %s", file_path)
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

        # Prepare output path
        replace_input = output_file is None
        if output_file is None:
            output_file = input_file

        output_path = Path(output_file)

        # Create temp file in same volume as source (for instant rename)
        if job_id is None:
            job_id = uuid.uuid4().hex[:12]

        volume_root = self.get_volume_root(input_file)
        temp_dir = Path(volume_root) / f".remuxcode-temp-{job_id}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_suffix = "av1" if codec_to == "av1" else "hevc"
        temp_file = temp_dir / f"{input_path.name}.{temp_suffix}-tmp.mkv"

        try:
            # Clean up any leftover temp files
            if temp_file.exists():
                temp_file.unlink()

            # Build and run ffmpeg command
            # Snapshot source file identity before encoding
            try:
                _src_stat = input_path.stat()
                _src_mtime = _src_stat.st_mtime
                _src_size = _src_stat.st_size
            except OSError:
                _src_mtime = None
                _src_size = None

            cmd = self._build_ffmpeg_command(str(input_path), str(temp_file), content_type, video)
            logger.debug("Running: %s", " ".join(cmd))

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
            )

            if returncode != 0:
                return VideoConversionResult(
                    success=False,
                    input_file=input_file,
                    output_file=output_file,
                    original_size=info.size,
                    new_size=0,
                    codec_from=video.codec_name,
                    codec_to=codec_to,
                    content_type=content_type.value,
                    error=f"FFmpeg failed: {stderr_text[-2000:]}",
                )

            # Move temp file to output location
            if temp_file.exists():
                # Check if original still exists (could be deleted during conversion)
                original_exists = output_path.exists()

                # Detect mid-job file replacement
                if replace_input and original_exists and _src_mtime is not None:
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

                if not original_exists and replace_input:
                    # Original was deleted during conversion (e.g., Radarr upgrade)
                    # Ensure parent directory exists
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    logger.warning(
                        "Original file deleted during conversion, placing converted file at: %s",
                        output_path,
                    )
                elif replace_input:
                    pass  # safe_replace handles backup + move atomically

                if detail_callback:
                    detail_callback("Replacing file safely...")
                safe_replace(temp_file, output_path)

                # Clean up temp directory after successful move
                try:
                    if temp_dir.exists():
                        shutil.rmtree(temp_dir, ignore_errors=True)
                except Exception:
                    pass

            new_size = output_path.stat().st_size

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
    ) -> list[str]:
        """Build ffmpeg command with content-appropriate settings."""
        encoder = resolve_encoder(self.target_codec, self.hw_accel, self.hw_caps)

        # HW-accelerated encoders
        if encoder == "hevc_qsv":
            return self._build_qsv_command(
                input_file, output_file, content_type, codec="hevc", video=video
            )
        if encoder == "av1_qsv":
            return self._build_qsv_command(
                input_file, output_file, content_type, codec="av1", video=video
            )
        if encoder == "hevc_vaapi":
            return self._build_vaapi_command(
                input_file, output_file, content_type, codec="hevc", video=video
            )
        if encoder == "av1_vaapi":
            return self._build_vaapi_command(
                input_file, output_file, content_type, codec="av1", video=video
            )
        if encoder == "hevc_nvenc":
            return self._build_nvenc_command(
                input_file, output_file, content_type, codec="hevc", video=video
            )
        if encoder == "av1_nvenc":
            return self._build_nvenc_command(
                input_file, output_file, content_type, codec="av1", video=video
            )

        # Software encoders
        if self.target_codec == "av1":
            return self._build_av1_command(input_file, output_file, content_type, video=video)
        return self._build_hevc_command(input_file, output_file, content_type, video=video)

    @staticmethod
    def _build_color_args(video: VideoStream | None) -> list[str]:
        """Return ffmpeg color metadata flags matching the source stream.

        Falls back to BT.709 when the source has no color metadata (safe for
        all standard SDR content).  HDR sources carry bt2020/smpte2084 etc.
        and those values are passed through unchanged.
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

    def _build_hevc_command(
        self,
        input_file: str,
        output_file: str,
        content_type: ContentType,
        video: VideoStream | None = None,
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

        # Pass through HDR10 mastering display metadata if present
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

        cmd.extend(
            [
                "-analyzeduration",
                "200M",
                "-probesize",
                "200M",
                "-i",
                input_file,
                "-map",
                "0:V",
                "-map",
                "0:a?",
                "-map",
                "0:s?",
                "-map",
                "0:t?",
                "-map_chapters",
                "0",
                "-c",
                "copy",
                "-c:v:0",
                "libx265",
                "-pix_fmt",
                self.config.pix_fmt,
                "-profile:v",
                self.config.profile,
                "-level:v",
                self.config.level,
                "-preset",
                preset,
            ]
        )

        # Add tune if specified
        if tune:
            cmd.extend(["-tune", tune])

        # Add x265 params
        cmd.extend(["-x265-params", ":".join(x265_params)])

        # Add framerate and color settings
        cmd.extend(["-fps_mode", "cfr"])

        # Add specific framerate if configured
        if framerate:
            cmd.extend(["-r", framerate])

        cmd.extend(self._build_color_args(video))

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

        # Output file
        cmd.append(output_file)

        return cmd

    def _build_av1_command(
        self,
        input_file: str,
        output_file: str,
        content_type: ContentType,
        video: VideoStream | None = None,
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

        # Add animation-specific tuning for anime
        if content_type == ContentType.ANIME:
            svtav1_params.append("enable-overlay=0")
            svtav1_params.append("film-grain=0")

        # Pass through HDR10 metadata if present
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

        cmd.extend(
            [
                "-analyzeduration",
                "200M",
                "-probesize",
                "200M",
                "-i",
                input_file,
                "-map",
                "0:V",
                "-map",
                "0:a?",
                "-map",
                "0:s?",
                "-map",
                "0:t?",
                "-map_chapters",
                "0",
                "-c",
                "copy",
                "-c:v:0",
                "libsvtav1",
                "-pix_fmt",
                "yuv420p10le",  # AV1 10-bit
                "-crf",
                str(crf),
                "-preset",
                str(preset),
            ]
        )

        # Add SVT-AV1 params
        cmd.extend(["-svtav1-params", ":".join(svtav1_params)])

        # Add framerate settings
        cmd.extend(["-fps_mode", "cfr"])

        # Add specific framerate if configured
        if framerate:
            cmd.extend(["-r", framerate])

        cmd.extend(self._build_color_args(video))

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

    def _build_qsv_command(
        self,
        input_file: str,
        output_file: str,
        content_type: ContentType,
        *,
        codec: str = "hevc",
        video: VideoStream | None = None,
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

        # Pick upload pixel format:  p010le for 10-bit output, nv12 for 8-bit.
        is_10bit = "10" in (self.config.pix_fmt or "") or "10" in (self.config.profile or "")
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

        cmd.extend(
            [
                "-analyzeduration",
                "200M",
                "-probesize",
                "200M",
                "-i",
                input_file,
                "-map",
                "0:V",
                "-map",
                "0:a?",
                "-map",
                "0:s?",
                "-map",
                "0:t?",
                "-map_chapters",
                "0",
                "-c",
                "copy",
                # Upload SW-decoded frames to QSV surface for HW encoding
                "-filter:v:0",
                f"format={upload_fmt},hwupload=extra_hw_frames=64",
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
            cmd.extend(["-look_ahead", "1", "-look_ahead_depth", "40"])

        cmd.extend(["-fps_mode", "cfr"])
        if framerate:
            cmd.extend(["-r", framerate])

        cmd.extend(self._build_color_args(video))
        self._append_copy_streams(cmd)
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

        # Pick upload pixel format:  p010 for 10-bit output, nv12 for 8-bit.
        is_10bit = "10" in (self.config.pix_fmt or "") or "10" in (self.config.profile or "")
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

        cmd.extend(
            [
                "-analyzeduration",
                "200M",
                "-probesize",
                "200M",
                "-i",
                input_file,
                "-map",
                "0:V",
                "-map",
                "0:a?",
                "-map",
                "0:s?",
                "-map",
                "0:t?",
                "-map_chapters",
                "0",
                "-c",
                "copy",
                # Upload SW-decoded frames to VAAPI surface for HW encoding
                "-filter:v:0",
                f"format={upload_fmt},hwupload",
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

        cmd.extend(self._build_color_args(video))
        self._append_copy_streams(cmd)
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
    ) -> list[str]:
        """Build ffmpeg command for NVENC encoding (HEVC or AV1).

        Uses software decoding + NVENC hardware encoding.  Full HW-decode
        via ``-hwaccel cuda`` fails when CUVID can't decode the input format
        (e.g. some 10-bit H.264 profiles).  SW decode + HW encode is safe
        for every input.
        """
        encoder = "hevc_nvenc" if codec == "hevc" else "av1_nvenc"
        quality, framerate = self._get_quality_params(content_type, encoder)

        # Pick upload pixel format:  p010le for 10-bit output, nv12 for 8-bit.
        is_10bit = "10" in (self.config.pix_fmt or "") or "10" in (self.config.profile or "")
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

        cmd.extend(
            [
                "-analyzeduration",
                "200M",
                "-probesize",
                "200M",
                "-i",
                input_file,
                "-map",
                "0:V",
                "-map",
                "0:a?",
                "-map",
                "0:s?",
                "-map",
                "0:t?",
                "-map_chapters",
                "0",
                "-c",
                "copy",
                # Upload SW-decoded frames to CUDA surface for HW encoding
                "-filter:v:0",
                f"format={upload_fmt},hwupload_cuda",
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

        cmd.extend(self._build_color_args(video))
        self._append_copy_streams(cmd)
        cmd.append(output_file)
        return cmd
