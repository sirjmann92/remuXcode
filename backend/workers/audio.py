#!/usr/bin/env python3
"""Audio converter worker.

Converts DTS audio tracks to AC3/AAC for device compatibility.
Based on the existing DTS converter but integrated with the new modular architecture.
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

from backend.utils.config import AudioConfig
from backend.utils.ffprobe import AudioStream, FFProbe, MediaInfo
from backend.workers._progress import run_ffmpeg_with_progress

logger = logging.getLogger(__name__)


# ISO 639-2 language code to full name mapping
LANGUAGE_NAMES = {
    "eng": "English",
    "spa": "Spanish",
    "fre": "French",
    "fra": "French",
    "ger": "German",
    "deu": "German",
    "ita": "Italian",
    "por": "Portuguese",
    "jpn": "Japanese",
    "chi": "Chinese",
    "zho": "Chinese",
    "kor": "Korean",
    "rus": "Russian",
    "ara": "Arabic",
    "hin": "Hindi",
    "tha": "Thai",
    "vie": "Vietnamese",
    "pol": "Polish",
    "dut": "Dutch",
    "nld": "Dutch",
    "swe": "Swedish",
    "nor": "Norwegian",
    "dan": "Danish",
    "fin": "Finnish",
    "ces": "Czech",
    "hun": "Hungarian",
    "tur": "Turkish",
    "gre": "Greek",
    "ell": "Greek",
    "heb": "Hebrew",
}


@dataclass
class AudioConversionResult:
    """Result of an audio conversion operation."""

    success: bool
    input_file: str
    output_file: str
    streams_converted: int
    streams_total: int
    original_size: int
    new_size: int
    error: str | None = None
    converted_streams: list[dict] | None = None


class AudioConverter:
    """Converts incompatible audio formats (DTS, TrueHD, etc.) to compatible formats.

    Features:
    - DTS -> AC3 (5.1) or AAC (stereo/7.1+)
    - TrueHD -> AC3/AAC
    - Optional dual-track mode (keep original + add converted)
    - Proper track title generation
    """

    def __init__(
        self,
        config: AudioConfig,
        ffprobe: FFProbe | None = None,
        get_volume_root: Callable[[str], str] | None = None,
    ):
        """Initialize audio converter.

        Args:
            config: Audio conversion configuration
            ffprobe: FFProbe instance (created if not provided)
            get_volume_root: Function to get volume root for temp files (uses /tmp if not provided)
        """
        self.config = config
        self.ffprobe = ffprobe or FFProbe()
        self.get_volume_root = get_volume_root or (lambda _: tempfile.gettempdir())

    @staticmethod
    def _has_compatible_companion(lang: str, compatible_langs: set[str]) -> bool:
        """Check if a stream's language already has a compatible (non-DTS/TrueHD) track.

        When keep_original* is enabled, the converted companion persists alongside
        the original.  Detecting that avoids re-processing already-converted files.
        """
        if lang == "und":
            return bool(compatible_langs)
        return lang in compatible_langs

    def _compatible_languages(self, info: MediaInfo) -> set[str]:
        """Build set of languages that have a compatible (non-DTS, non-TrueHD) track."""
        return {
            (s.language or "und").lower()
            for s in info.audio_streams
            if not s.is_dts and not s.is_truehd
        }

    def should_convert(self, file_path: str, *, is_anime: bool = False) -> bool:
        """Check if a file has audio that needs conversion."""
        if not self.config.enabled:
            return False

        # Skip non-anime content when anime_only is enabled
        if self.config.anime_only and not is_anime:
            return False

        info = self.ffprobe.get_file_info(file_path)
        if info is None:
            return False

        # When keep_original* is enabled, a converted companion track persists
        # alongside the original.  Detect same-language compatible tracks so we
        # don't re-flag files that were already processed.
        compat = (
            self._compatible_languages(info)
            if (self.config.keep_original or self.config.keep_original_dts_x)
            else set()
        )

        for s in info.audio_streams:
            lang = (s.language or "und").lower()
            if self.config.convert_dts and s.is_dts and not s.is_dts_x:
                if self.config.keep_original and self._has_compatible_companion(lang, compat):
                    continue
                return True
            if self.config.convert_dts_x and s.is_dts_x:
                if self.config.keep_original_dts_x and self._has_compatible_companion(lang, compat):
                    continue
                return True
            if self.config.convert_truehd and s.is_truehd:
                if self.config.keep_original and self._has_compatible_companion(lang, compat):
                    continue
                return True

        return False

    def get_dts_streams(self, info: MediaInfo) -> list[AudioStream]:
        """Get regular DTS audio streams (excluding DTS:X)."""
        if not info.audio_streams:
            return []

        return [stream for stream in info.audio_streams if stream.is_dts and not stream.is_dts_x]

    def get_dts_x_streams(self, info: MediaInfo) -> list[AudioStream]:
        """Get DTS:X (object-based) audio streams."""
        if not info.audio_streams:
            return []

        return [stream for stream in info.audio_streams if stream.is_dts_x]

    def get_truehd_streams(self, info: MediaInfo) -> list[AudioStream]:
        """Get all TrueHD audio streams from media info."""
        if not info.audio_streams:
            return []

        return [stream for stream in info.audio_streams if stream.is_truehd]

    def convert(
        self,
        input_file: str,
        output_file: str | None = None,
        job_id: str | None = None,
        progress_callback: Callable[[float], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> AudioConversionResult:
        """Convert incompatible audio in a media file.

        Args:
            input_file: Path to input file
            output_file: Path for output file (defaults to replacing input)
            job_id: Unique job ID for temp directory
            progress_callback: Optional callback receiving progress 0-100.
            cancel_event: Event to signal cancellation (kills ffmpeg).

        Returns:
            AudioConversionResult with conversion details
        """
        input_path = Path(input_file)

        if not input_path.exists():
            return AudioConversionResult(
                success=False,
                input_file=input_file,
                output_file=output_file or input_file,
                streams_converted=0,
                streams_total=0,
                original_size=0,
                new_size=0,
                error=f"Input file not found: {input_file}",
            )

        # Get media info
        info = self.ffprobe.get_file_info(input_file)
        if info is None:
            return AudioConversionResult(
                success=False,
                input_file=input_file,
                output_file=output_file or input_file,
                streams_converted=0,
                streams_total=0,
                original_size=0,
                new_size=0,
                error="Failed to analyze input file",
            )

        # Find streams to convert (skip streams with existing companions
        # when keep_original* is enabled to avoid re-processing)
        streams_to_convert: list[AudioStream] = []
        compat = (
            self._compatible_languages(info)
            if (self.config.keep_original or self.config.keep_original_dts_x)
            else set()
        )

        if self.config.convert_dts:
            for s in self.get_dts_streams(info):
                lang = (s.language or "und").lower()
                if self.config.keep_original and self._has_compatible_companion(lang, compat):
                    continue
                streams_to_convert.append(s)

        if self.config.convert_dts_x:
            for s in self.get_dts_x_streams(info):
                lang = (s.language or "und").lower()
                if self.config.keep_original_dts_x and self._has_compatible_companion(lang, compat):
                    continue
                streams_to_convert.append(s)

        if self.config.convert_truehd:
            for s in self.get_truehd_streams(info):
                lang = (s.language or "und").lower()
                if self.config.keep_original and self._has_compatible_companion(lang, compat):
                    continue
                streams_to_convert.append(s)

        if not streams_to_convert:
            return AudioConversionResult(
                success=True,
                input_file=input_file,
                output_file=output_file or input_file,
                streams_converted=0,
                streams_total=len(info.audio_streams),
                original_size=info.size,
                new_size=info.size,
                error=None,
            )

        logger.info(
            "Converting %d audio stream(s) in: %s", len(streams_to_convert), input_path.name
        )

        # Prepare paths
        replace_input = output_file is None
        if output_file is None:
            output_file = input_file

        output_path = Path(output_file)

        # Create temp directory in same volume as source (for instant rename)
        if job_id is None:
            job_id = uuid.uuid4().hex[:12]

        volume_root = self.get_volume_root(input_file)
        temp_dir = Path(volume_root) / f".remuxcode-temp-{job_id}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_output = temp_dir / input_path.name

        try:
            # Snapshot source file identity before encoding so we can detect
            # mid-job replacements (e.g. Sonarr upgrades) before we overwrite.
            try:
                _src_stat = input_path.stat()
                _src_mtime = _src_stat.st_mtime
                _src_size = _src_stat.st_size
            except OSError:
                _src_mtime = None
                _src_size = None

            # Build and run ffmpeg command
            cmd = self._build_ffmpeg_command(
                str(input_path), str(temp_output), info, streams_to_convert
            )
            logger.debug("Running: %s", " ".join(cmd))

            returncode, stderr_text = run_ffmpeg_with_progress(
                cmd,
                duration_secs=info.duration,
                progress_cb=progress_callback,
                timeout=self.config.job_timeout or None,  # 0 = no timeout
                cancel_event=cancel_event,
            )

            if returncode != 0:
                return AudioConversionResult(
                    success=False,
                    input_file=input_file,
                    output_file=output_file,
                    streams_converted=0,
                    streams_total=len(info.audio_streams),
                    original_size=info.size,
                    new_size=0,
                    error=f"FFmpeg failed: {stderr_text[:500]}",
                )

            # Move temp file to output location
            if temp_output.exists():
                # Check if original still exists (could be deleted during conversion)
                original_exists = output_path.exists()

                # Detect mid-job file replacement: if the source file still exists
                # but its mtime or size changed, a newer version was placed there
                # while we were encoding.  Discarding our output is safer than
                # overwriting the replacement.
                if replace_input and original_exists and _src_mtime is not None:
                    try:
                        cur_stat = output_path.stat()
                        if cur_stat.st_mtime != _src_mtime or cur_stat.st_size != _src_size:
                            logger.warning(
                                "Source file was replaced during audio conversion "
                                "(mtime/size changed) — discarding converted output "
                                "to avoid overwriting the new file: %s",
                                output_path,
                            )
                            temp_output.unlink(missing_ok=True)
                            shutil.rmtree(temp_dir, ignore_errors=True)
                            return AudioConversionResult(
                                success=False,
                                input_file=input_file,
                                output_file=output_file,
                                streams_converted=0,
                                streams_total=len(info.audio_streams),
                                original_size=info.size,
                                new_size=0,
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
                    pass  # Don't unlink — shutil.move overwrites in-place,
                    # preserving the file's location on mergerfs setups.

                shutil.move(str(temp_output), str(output_path))

                # Clean up temp directory after successful move
                try:
                    if temp_dir.exists():
                        shutil.rmtree(temp_dir, ignore_errors=True)
                except Exception:
                    pass

            new_size = output_path.stat().st_size

            converted_info = []
            for stream in streams_to_convert:
                target_codec, target_bitrate, _ = self._determine_target_format(
                    stream.channels, stream.bitrate // 1000 if stream.bitrate else 0
                )
                converted_info.append(
                    {
                        "index": stream.index,
                        "from_codec": stream.codec_name,
                        "to_codec": target_codec,
                        "channels": stream.channels,
                        "bitrate": target_bitrate,
                        "language": stream.language,
                    }
                )

            logger.info(
                "Converted: %s (%.1fMB \u2192 %.1fMB)",
                input_file,
                info.size / 1024 / 1024,
                new_size / 1024 / 1024,
            )

            return AudioConversionResult(
                success=True,
                input_file=input_file,
                output_file=str(output_path),
                streams_converted=len(streams_to_convert),
                streams_total=len(info.audio_streams),
                original_size=info.size,
                new_size=new_size,
                converted_streams=converted_info,
            )

        except subprocess.TimeoutExpired:
            return AudioConversionResult(
                success=False,
                input_file=input_file,
                output_file=output_file,
                streams_converted=0,
                streams_total=len(info.audio_streams),
                original_size=info.size,
                new_size=0,
                error=f"FFmpeg timeout (exceeded {self.config.job_timeout}s)",
            )

        except Exception as e:
            logger.exception("Conversion failed")
            return AudioConversionResult(
                success=False,
                input_file=input_file,
                output_file=output_file,
                streams_converted=0,
                streams_total=len(info.audio_streams),
                original_size=info.size,
                new_size=0,
                error=str(e),
            )

        finally:
            # Clean up temp directory
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)

    def _determine_target_format(
        self, channels: int, source_bitrate: int
    ) -> tuple[str, int, str | None]:
        """Determine optimal target format, bitrate, and channel layout.

        Returns:
            (codec, bitrate, channel_layout) tuple
        """
        target_layout = None

        # Handle >8 channels - downmix to 7.1
        if channels > 8:
            logger.warning("Source has %d channels - downmixing to 7.1", channels)
            target_codec = "aac"
            bitrate_cap = self.config.aac_surround_bitrate
            target_layout = "7.1"
        # 7.1 content - use AAC (E-AC3 encoder maxes at 5.1)
        elif channels > 6:
            target_codec = "aac"
            bitrate_cap = self.config.aac_surround_bitrate
        # 5.1 content - use AC3 for best compatibility
        elif channels > 2:
            if self.config.prefer_ac3:
                target_codec = "ac3"
                bitrate_cap = self.config.ac3_bitrate
            else:
                target_codec = "eac3"
                bitrate_cap = self.config.eac3_bitrate
        # Stereo - use AAC
        else:
            target_codec = "aac"
            bitrate_cap = self.config.aac_stereo_bitrate

        # Match source bitrate or use cap
        target_bitrate = min(source_bitrate if source_bitrate > 0 else bitrate_cap, bitrate_cap)

        # Ensure minimum quality
        if target_codec == "ac3" and channels > 2:
            target_bitrate = max(target_bitrate, 448)
        elif target_codec == "eac3" and channels > 6:
            target_bitrate = max(target_bitrate, 256)
        elif target_codec == "aac":
            target_bitrate = max(target_bitrate, 128)

        return target_codec, target_bitrate, target_layout

    def _generate_track_title(self, language: str, original_title: str) -> str:
        """Generate a clean track title based on language."""
        title_lower = original_title.lower() if original_title else ""

        # Preserve commentary tracks
        if "commentary" in title_lower:
            return original_title

        # Check for codec references (strip these)
        codec_keywords = [
            "dts",
            "ac3",
            "eac3",
            "aac",
            "dolby",
            "truehd",
            "atmos",
            "pcm",
            "flac",
            "opus",
            "vorbis",
            "mp3",
            "lossless",
            "5.1",
            "7.1",
            "2.0",
            "stereo",
            "surround",
            "ma",
            "hr",
        ]
        has_codec_reference = any(kw in title_lower for kw in codec_keywords)

        # Language codes to ignore
        language_codes = [
            "und",
            "eng",
            "spa",
            "fre",
            "fra",
            "ger",
            "deu",
            "ita",
            "por",
            "jpn",
            "chi",
            "zho",
            "kor",
            "rus",
            "english",
            "spanish",
            "french",
            "german",
            "italian",
            "japanese",
            "korean",
            "russian",
            "undefined",
        ]
        is_just_language = title_lower in language_codes

        if has_codec_reference or is_just_language or not original_title:
            lang_code = language.lower() if language else "und"
            return LANGUAGE_NAMES.get(lang_code, language.capitalize() if language else "")

        return original_title

    def _build_ffmpeg_command(
        self,
        input_file: str,
        output_file: str,
        info: MediaInfo,
        streams_to_convert: list[AudioStream],
    ) -> list[str]:
        """Build ffmpeg command for audio conversion.

        Uses explicit stream mapping (instead of -map 0) so dual-track mode
        can map the same input stream twice and control track ordering.
        """

        cmd = ["ffmpeg", "-i", input_file, "-y"]

        convert_indices = {s.index for s in streams_to_convert}

        # Build explicit stream maps and per-stream codec args
        map_args: list[str] = []
        codec_args: list[str] = []

        # Map video streams
        for vs in info.video_streams:
            map_args.extend(["-map", f"0:{vs.index}"])

        # Process audio streams
        audio_output_index = 0
        for stream in info.audio_streams:
            if stream.index in convert_indices:
                # Determine keep_original per stream type
                keep_orig = (
                    self.config.keep_original_dts_x
                    if stream.is_dts_x
                    else self.config.keep_original
                )

                target_codec, target_bitrate, target_layout = self._determine_target_format(
                    stream.channels, stream.bitrate // 1000 if stream.bitrate else 0
                )

                title = self._generate_track_title(stream.language or "", stream.title or "")

                if keep_orig and self.config.original_as_secondary:
                    # Converted first (players use it by default), original second
                    map_args.extend(["-map", f"0:{stream.index}"])
                    codec_args.extend([f"-c:a:{audio_output_index}", target_codec])
                    codec_args.extend([f"-b:a:{audio_output_index}", f"{target_bitrate}k"])
                    if target_layout:
                        codec_args.extend(
                            [
                                f"-ac:a:{audio_output_index}",
                                str(8),
                                "-channel_layout:a",
                                target_layout,
                            ]
                        )
                    if title:
                        codec_args.extend([f"-metadata:s:a:{audio_output_index}", f"title={title}"])
                    audio_output_index += 1

                    map_args.extend(["-map", f"0:{stream.index}"])
                    codec_args.extend([f"-c:a:{audio_output_index}", "copy"])
                    audio_output_index += 1

                elif keep_orig:
                    # Original first, converted second
                    map_args.extend(["-map", f"0:{stream.index}"])
                    codec_args.extend([f"-c:a:{audio_output_index}", "copy"])
                    audio_output_index += 1

                    map_args.extend(["-map", f"0:{stream.index}"])
                    codec_args.extend([f"-c:a:{audio_output_index}", target_codec])
                    codec_args.extend([f"-b:a:{audio_output_index}", f"{target_bitrate}k"])
                    if target_layout:
                        codec_args.extend(
                            [
                                f"-ac:a:{audio_output_index}",
                                str(8),
                                "-channel_layout:a",
                                target_layout,
                            ]
                        )
                    if title:
                        codec_args.extend([f"-metadata:s:a:{audio_output_index}", f"title={title}"])
                    audio_output_index += 1

                else:
                    # Convert only, drop original
                    map_args.extend(["-map", f"0:{stream.index}"])
                    codec_args.extend([f"-c:a:{audio_output_index}", target_codec])
                    codec_args.extend([f"-b:a:{audio_output_index}", f"{target_bitrate}k"])
                    if target_layout:
                        codec_args.extend(
                            [
                                f"-ac:a:{audio_output_index}",
                                str(8),
                                "-channel_layout:a",
                                target_layout,
                            ]
                        )
                    if title:
                        codec_args.extend([f"-metadata:s:a:{audio_output_index}", f"title={title}"])
                    audio_output_index += 1
            else:
                # Copy non-converted streams as-is
                map_args.extend(["-map", f"0:{stream.index}"])
                codec_args.extend([f"-c:a:{audio_output_index}", "copy"])
                audio_output_index += 1

        # Map subtitle and attachment streams
        for ss in info.subtitle_streams:
            map_args.extend(["-map", f"0:{ss.index}"])
        for att in info.attachment_streams:
            map_args.extend(["-map", f"0:{att.index}"])

        # Assemble final command
        cmd.extend(map_args)
        cmd.extend(["-c:v", "copy"])
        if info.subtitle_streams:
            cmd.extend(["-c:s", "copy"])
        cmd.extend(codec_args)
        cmd.extend(["-map_chapters", "0"])

        # Tag the container so Sonarr detects a size change and re-reads MediaInfo
        cmd.extend(["-metadata:g", "ENCODED_BY=remuxcode"])

        cmd.append(output_file)
        return cmd
