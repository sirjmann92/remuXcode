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
from backend.utils.ffprobe import FFProbe
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
    ):
        """Initialize video converter.

        Args:
            config: Video conversion configuration
            ffprobe: FFProbe instance (created if not provided)
            anime_detector: AnimeDetector instance (created if not provided)
            get_volume_root: Function to get volume root for temp files (uses /tmp if not provided)
            ffmpeg_threads: Thread limit for ffmpeg (0 = unlimited)
        """
        self.config = config
        self.ffprobe = ffprobe or FFProbe()
        self.ffmpeg_threads = ffmpeg_threads
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

        # Check if anime-only mode and skip non-anime
        if self.config.anime_only:
            content_type = self.anime_detector.detect(file_path, use_api=False)
            if content_type != ContentType.ANIME:
                logger.debug("Skipping non-anime file (anime_only=True): %s", file_path)
                return False

        # Check conversion criteria
        if video.is_10bit_h264 and self.config.convert_10bit_x264:
            return True

        if video.is_h264 and not video.is_10bit and self.config.convert_8bit_x264:
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
    ) -> VideoConversionResult:
        """Convert video to HEVC or AV1.

        Args:
            input_file: Path to input file
            output_file: Path for output file (defaults to replacing input)
            force_content_type: Override content type detection
            job_id: Unique job identifier (used for temp directory naming)
            progress_callback: Optional callback receiving progress 0-100.
            cancel_event: Event to signal cancellation (kills ffmpeg).

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
        logger.info(
            "Converting %s (%s %s-bit \u2192 %s, %s)",
            input_file,
            video.codec_name,
            video.bit_depth,
            codec_label,
            content_type.value,
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

            cmd = self._build_ffmpeg_command(str(input_path), str(temp_file), content_type)
            logger.debug("Running: %s", " ".join(cmd))

            returncode, stderr_text = run_ffmpeg_with_progress(
                cmd,
                duration_secs=info.duration,
                progress_cb=progress_callback,
                timeout=self.config.job_timeout or None,  # 0 = no timeout
                cancel_event=cancel_event,
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
                    error=f"FFmpeg failed: {stderr_text[:500]}",
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
        self, input_file: str, output_file: str, content_type: ContentType
    ) -> list[str]:
        """Build ffmpeg command with content-appropriate settings."""

        if self.target_codec == "av1":
            return self._build_av1_command(input_file, output_file, content_type)
        return self._build_hevc_command(input_file, output_file, content_type)

    def _build_hevc_command(
        self, input_file: str, output_file: str, content_type: ContentType
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

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-stats",
        ]

        if self.ffmpeg_threads > 0:
            cmd.extend(["-threads", str(self.ffmpeg_threads)])

        cmd.extend([
            "-analyzeduration",
            "10M",
            "-probesize",
            "10M",
            "-i",
            input_file,
            "-map",
            "0",
            "-map_chapters",
            "0",
            "-c:v",
            "libx265",
            "-pix_fmt",
            self.config.pix_fmt,
            "-profile:v",
            self.config.profile,
            "-level:v",
            self.config.level,
            "-preset",
            preset,
        ])

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

        cmd.extend(
            [
                "-color_primaries",
                "bt709",
                "-color_trc",
                "bt709",
                "-colorspace",
                "bt709",
            ]
        )

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
        self, input_file: str, output_file: str, content_type: ContentType
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

        # SVT-AV1 params
        svtav1_params = [
            "tune=0",  # 0=VQ (visual quality), 1=PSNR
        ]

        # Add animation-specific tuning for anime
        if content_type == ContentType.ANIME:
            svtav1_params.append("enable-overlay=0")
            svtav1_params.append("film-grain=0")

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-stats",
        ]

        if self.ffmpeg_threads > 0:
            cmd.extend(["-threads", str(self.ffmpeg_threads)])

        cmd.extend([
            "-analyzeduration",
            "10M",
            "-probesize",
            "10M",
            "-i",
            input_file,
            "-map",
            "0",
            "-map_chapters",
            "0",
            "-c:v",
            "libsvtav1",
            "-pix_fmt",
            "yuv420p10le",  # AV1 10-bit
            "-crf",
            str(crf),
            "-preset",
            str(preset),
        ])

        # Add SVT-AV1 params
        cmd.extend(["-svtav1-params", ":".join(svtav1_params)])

        # Add framerate settings
        cmd.extend(["-fps_mode", "cfr"])

        # Add specific framerate if configured
        if framerate:
            cmd.extend(["-r", framerate])

        cmd.extend(
            [
                "-color_primaries",
                "bt709",
                "-color_trc",
                "bt709",
                "-colorspace",
                "bt709",
            ]
        )

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
