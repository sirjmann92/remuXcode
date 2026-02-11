#!/usr/bin/env python3
"""
Video Converter Worker

Converts 10-bit H.264 video to HEVC for better device compatibility.
Applies different encoding settings for anime vs live action content.
"""

import logging
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, List, Callable

from ..utils.ffprobe import FFProbe, MediaInfo
from ..utils.anime_detect import AnimeDetector, ContentType
from ..utils.config import VideoConfig

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
    error: Optional[str] = None
    
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
    """
    Converts video to HEVC with content-aware encoding settings.
    
    Features:
    - 10-bit H.264 detection (main conversion target)
    - Anime vs live action detection for optimal settings
    - Backup original files
    - Interrupt-safe with cleanup
    """
    
    def __init__(
        self,
        config: VideoConfig,
        ffprobe: Optional[FFProbe] = None,
        anime_detector: Optional[AnimeDetector] = None,
        get_volume_root: Optional[Callable[[str], str]] = None
    ):
        """
        Initialize video converter.
        
        Args:
            config: Video conversion configuration
            ffprobe: FFProbe instance (created if not provided)
            anime_detector: AnimeDetector instance (created if not provided)
            get_volume_root: Function to get volume root for temp files (uses /tmp if not provided)
        """
        self.config = config
        self.ffprobe = ffprobe or FFProbe()
        self.anime_detector = anime_detector or AnimeDetector()
        self.get_volume_root = get_volume_root or (lambda _: '/tmp')
    
    def should_convert(self, file_path: str) -> bool:
        """
        Check if a file should be converted.
        
        Returns True if:
        - Video is 10-bit H.264 and config.convert_10bit_x264 is True
        - Video is 8-bit H.264 and config.convert_8bit_x264 is True
        - Video is not already HEVC
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
        
        # Already HEVC - skip
        if video.is_hevc:
            logger.debug(f"Skipping HEVC file: {file_path}")
            return False
        
        # Check if anime-only mode and skip non-anime
        if self.config.anime_only:
            content_type = self.anime_detector.detect(file_path, use_api=False)
            if content_type != ContentType.ANIME:
                logger.debug(f"Skipping non-anime file (anime_only=True): {file_path}")
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
        output_file: Optional[str] = None,
        force_content_type: Optional[ContentType] = None
    ) -> VideoConversionResult:
        """
        Convert video to HEVC.
        
        Args:
            input_file: Path to input file
            output_file: Path for output file (defaults to replacing input)
            force_content_type: Override content type detection
        
        Returns:
            VideoConversionResult with conversion details
        """
        input_path = Path(input_file)
        
        if not input_path.exists():
            return VideoConversionResult(
                success=False,
                input_file=input_file,
                output_file=output_file or input_file,
                original_size=0,
                new_size=0,
                codec_from='unknown',
                codec_to='hevc',
                content_type='unknown',
                error=f"Input file not found: {input_file}"
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
                codec_from='unknown',
                codec_to='hevc',
                content_type='unknown',
                error="Failed to analyze input file"
            )
        
        video = info.primary_video
        if video is None:
            return VideoConversionResult(
                success=False,
                input_file=input_file,
                output_file=output_file or input_file,
                original_size=info.size,
                new_size=0,
                codec_from='unknown',
                codec_to='hevc',
                content_type='unknown',
                error="No video stream found"
            )
        
        # Detect content type
        if force_content_type:
            content_type = force_content_type
        elif self.config.anime_auto_detect:
            content_type = self.anime_detector.detect(input_file)
        else:
            content_type = ContentType.LIVE_ACTION
        
        logger.info(f"Converting {input_file} ({video.codec_name} {video.bit_depth}-bit → HEVC, {content_type.value})")
        
        # Prepare output path
        replace_input = output_file is None
        if replace_input:
            output_file = input_file
        
        output_path = Path(output_file)
        
        # Create temp file in same volume as source (for instant rename)
        volume_root = self.get_volume_root(input_file)
        temp_dir = Path(volume_root) / f".remuxcode-temp-{uuid.uuid4().hex[:8]}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_file = temp_dir / f"{input_path.name}.hevc-tmp.mkv"
        
        try:
            # Clean up any leftover temp files
            if temp_file.exists():
                temp_file.unlink()
            
            # Build and run ffmpeg command
            cmd = self._build_ffmpeg_command(str(input_path), str(temp_file), content_type)
            logger.debug(f"Running: {' '.join(cmd)}")
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.config.job_timeout or None  # 0 = no timeout
            )
            
            if result.returncode != 0:
                return VideoConversionResult(
                    success=False,
                    input_file=input_file,
                    output_file=output_file,
                    original_size=info.size,
                    new_size=0,
                    codec_from=video.codec_name,
                    codec_to='hevc',
                    content_type=content_type.value,
                    error=f"FFmpeg failed: {result.stderr[:500]}"
                )
            
            # Move temp file to output location
            if temp_file.exists():
                # Check if original still exists (could be deleted during conversion)
                original_exists = output_path.exists()
                
                if not original_exists and replace_input:
                    # Original was deleted during conversion (e.g., Radarr upgrade)
                    # Ensure parent directory exists
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    logger.warning(f"Original file deleted during conversion, placing converted file at: {output_path}")
                elif replace_input:
                    # Normal case: remove original before move
                    if output_path.exists():
                        output_path.unlink()
                
                shutil.move(str(temp_file), str(output_path))
                
                # Clean up temp directory after successful move
                try:
                    if temp_dir.exists():
                        shutil.rmtree(temp_dir, ignore_errors=True)
                except Exception:
                    pass
            
            new_size = output_path.stat().st_size
            
            logger.info(f"Converted: {input_file} ({info.size / 1024 / 1024:.1f}MB → {new_size / 1024 / 1024:.1f}MB)")
            
            return VideoConversionResult(
                success=True,
                input_file=input_file,
                output_file=str(output_path),
                original_size=info.size,
                new_size=new_size,
                codec_from=video.codec_name,
                codec_to='hevc',
                content_type=content_type.value
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
                codec_to='hevc',
                content_type=content_type.value,
                error=f"FFmpeg timeout (exceeded {self.config.job_timeout}s)"
            )
            
        except Exception as e:
            # Clean up
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
            
            logger.exception(f"Conversion failed: {e}")
            return VideoConversionResult(
                success=False,
                input_file=input_file,
                output_file=output_file,
                original_size=info.size,
                new_size=0,
                codec_from=video.codec_name,
                codec_to='hevc',
                content_type=content_type.value,
                error=str(e)
            )
    
    def _build_ffmpeg_command(
        self,
        input_file: str,
        output_file: str,
        content_type: ContentType
    ) -> List[str]:
        """Build ffmpeg command with content-appropriate settings."""
        
        # Select encoding parameters based on content type
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
            'ffmpeg',
            '-hide_banner',
            '-loglevel', 'warning',
            '-stats',
            '-analyzeduration', '10M',
            '-probesize', '10M',
            '-i', input_file,
            '-map', '0',
            '-map_chapters', '0',
            '-c:v', 'libx265',
            '-pix_fmt', self.config.pix_fmt,
            '-profile:v', self.config.profile,
            '-level:v', self.config.level,
            '-preset', preset,
        ]
        
        # Add tune if specified
        if tune:
            cmd.extend(['-tune', tune])
        
        # Add x265 params
        cmd.extend(['-x265-params', ':'.join(x265_params)])
        
        # Add framerate and color settings
        cmd.extend(['-fps_mode', 'cfr'])
        
        # Add specific framerate if configured
        if framerate:
            cmd.extend(['-r', framerate])
        
        cmd.extend([
            '-color_primaries', 'bt709',
            '-color_trc', 'bt709',
            '-colorspace', 'bt709',
        ])
        
        # Copy audio, subtitles, and attachments
        cmd.extend([
            '-c:a', 'copy',
            '-c:s', 'copy',
            '-c:t', 'copy',
        ])
        
        # Output file
        cmd.append(output_file)
        
        return cmd
    
    def get_status(self, file_path: str) -> Dict:
        """Get conversion status/info for a file."""
        info = self.ffprobe.get_file_info(file_path)
        if info is None:
            return {'status': 'error', 'message': 'Failed to analyze file'}
        
        video = info.primary_video
        if video is None:
            return {'status': 'error', 'message': 'No video stream found'}
        
        content_type = self.anime_detector.detect(file_path)
        
        return {
            'status': 'ok',
            'file': file_path,
            'codec': video.codec_name,
            'bit_depth': video.bit_depth,
            'resolution': f"{video.width}x{video.height}",
            'is_hevc': video.is_hevc,
            'is_10bit_h264': video.is_10bit_h264,
            'needs_conversion': self.should_convert(file_path),
            'content_type': content_type.value,
            'file_size': info.size,
            'duration': info.duration,
        }
