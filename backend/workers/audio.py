#!/usr/bin/env python3
"""
Audio Converter Worker

Converts DTS audio tracks to AC3/AAC for device compatibility.
Based on the existing DTS converter but integrated with the new modular architecture.
"""

import logging
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, List, Tuple, Callable

from ..utils.ffprobe import FFProbe, MediaInfo, AudioStream
from ..utils.config import AudioConfig

logger = logging.getLogger(__name__)


# ISO 639-2 language code to full name mapping
LANGUAGE_NAMES = {
    'eng': 'English', 'spa': 'Spanish', 'fre': 'French', 'fra': 'French',
    'ger': 'German', 'deu': 'German', 'ita': 'Italian', 'por': 'Portuguese',
    'jpn': 'Japanese', 'chi': 'Chinese', 'zho': 'Chinese', 'kor': 'Korean',
    'rus': 'Russian', 'ara': 'Arabic', 'hin': 'Hindi', 'tha': 'Thai',
    'vie': 'Vietnamese', 'pol': 'Polish', 'dut': 'Dutch', 'nld': 'Dutch',
    'swe': 'Swedish', 'nor': 'Norwegian', 'dan': 'Danish', 'fin': 'Finnish',
    'ces': 'Czech', 'hun': 'Hungarian', 'tur': 'Turkish', 'gre': 'Greek',
    'ell': 'Greek', 'heb': 'Hebrew',
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
    error: Optional[str] = None
    converted_streams: Optional[List[Dict]] = None


class AudioConverter:
    """
    Converts incompatible audio formats (DTS, TrueHD, etc.) to compatible formats.
    
    Features:
    - DTS -> AC3 (5.1) or AAC (stereo/7.1+)
    - TrueHD -> AC3/AAC
    - Optional dual-track mode (keep original + add converted)
    - Proper track title generation
    """
    
    def __init__(
        self,
        config: AudioConfig,
        ffprobe: Optional[FFProbe] = None,
        get_volume_root: Optional[Callable[[str], str]] = None
    ):
        """
        Initialize audio converter.
        
        Args:
            config: Audio conversion configuration
            ffprobe: FFProbe instance (created if not provided)
            get_volume_root: Function to get volume root for temp files (uses /tmp if not provided)
        """
        self.config = config
        self.ffprobe = ffprobe or FFProbe()
        self.get_volume_root = get_volume_root or (lambda _: '/tmp')
    
    def should_convert(self, file_path: str) -> bool:
        """Check if a file has audio that needs conversion."""
        if not self.config.enabled:
            return False
        
        info = self.ffprobe.get_file_info(file_path)
        if info is None:
            return False
        
        # Check for DTS streams
        if self.config.convert_dts and info.has_dts:
            return True
        
        # Check for TrueHD streams
        if self.config.convert_truehd and info.has_truehd:
            return True
        
        return False
    
    def get_dts_streams(self, info: MediaInfo) -> List[AudioStream]:
        """Get all DTS audio streams from media info."""
        if not info.audio_streams:
            return []
        
        dts_streams = []
        for stream in info.audio_streams:
            if stream.is_dts:
                dts_streams.append(stream)
        
        return dts_streams
    
    def get_truehd_streams(self, info: MediaInfo) -> List[AudioStream]:
        """Get all TrueHD audio streams from media info."""
        if not info.audio_streams:
            return []
        
        truehd_streams = []
        for stream in info.audio_streams:
            if stream.is_truehd:
                truehd_streams.append(stream)
        
        return truehd_streams
    
    def convert(
        self,
        input_file: str,
        output_file: Optional[str] = None,
        job_id: Optional[str] = None
    ) -> AudioConversionResult:
        """
        Convert incompatible audio in a media file.
        
        Args:
            input_file: Path to input file
            output_file: Path for output file (defaults to replacing input)
            job_id: Unique job ID for temp directory
        
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
                error=f"Input file not found: {input_file}"
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
                error="Failed to analyze input file"
            )
        
        # Find streams to convert
        streams_to_convert = []
        
        if self.config.convert_dts:
            streams_to_convert.extend(self.get_dts_streams(info))
        
        if self.config.convert_truehd:
            streams_to_convert.extend(self.get_truehd_streams(info))
        
        if not streams_to_convert:
            return AudioConversionResult(
                success=True,
                input_file=input_file,
                output_file=output_file or input_file,
                streams_converted=0,
                streams_total=len(info.audio_streams),
                original_size=info.size,
                new_size=info.size,
                error=None
            )
        
        logger.info(f"Converting {len(streams_to_convert)} audio stream(s) in: {input_path.name}")
        
        # Prepare paths
        replace_input = output_file is None
        if replace_input:
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
            # Build and run ffmpeg command
            cmd = self._build_ffmpeg_command(
                str(input_path),
                str(temp_output),
                info,
                streams_to_convert
            )
            logger.debug(f"Running: {' '.join(cmd)}")
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.config.job_timeout or None  # 0 = no timeout
            )
            
            if result.returncode != 0:
                return AudioConversionResult(
                    success=False,
                    input_file=input_file,
                    output_file=output_file,
                    streams_converted=0,
                    streams_total=len(info.audio_streams),
                    original_size=info.size,
                    new_size=0,
                    error=f"FFmpeg failed: {result.stderr[:500]}"
                )
            
            # Move temp file to output location
            if temp_output.exists():
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
                converted_info.append({
                    'index': stream.index,
                    'from_codec': stream.codec_name,
                    'to_codec': target_codec,
                    'channels': stream.channels,
                    'bitrate': target_bitrate,
                    'language': stream.language,
                })
            
            logger.info(f"Converted: {input_file} ({info.size / 1024 / 1024:.1f}MB → {new_size / 1024 / 1024:.1f}MB)")
            
            return AudioConversionResult(
                success=True,
                input_file=input_file,
                output_file=str(output_path),
                streams_converted=len(streams_to_convert),
                streams_total=len(info.audio_streams),
                original_size=info.size,
                new_size=new_size,
                converted_streams=converted_info
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
                error=f"FFmpeg timeout (exceeded {self.config.job_timeout}s)"
            )
            
        except Exception as e:
            logger.exception(f"Conversion failed: {e}")
            return AudioConversionResult(
                success=False,
                input_file=input_file,
                output_file=output_file,
                streams_converted=0,
                streams_total=len(info.audio_streams),
                original_size=info.size,
                new_size=0,
                error=str(e)
            )
            
        finally:
            # Clean up temp directory
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
    
    def _determine_target_format(
        self,
        channels: int,
        source_bitrate: int
    ) -> Tuple[str, int, Optional[str]]:
        """
        Determine optimal target format, bitrate, and channel layout.
        
        Returns:
            (codec, bitrate, channel_layout) tuple
        """
        target_layout = None
        
        # Handle >8 channels - downmix to 7.1
        if channels > 8:
            logger.warning(f"Source has {channels} channels - downmixing to 7.1")
            target_codec = 'aac'
            bitrate_cap = self.config.aac_surround_bitrate
            target_layout = '7.1'
        # 7.1 content - use AAC (E-AC3 encoder maxes at 5.1)
        elif channels > 6:
            target_codec = 'aac'
            bitrate_cap = self.config.aac_surround_bitrate
        # 5.1 content - use AC3 for best compatibility
        elif channels > 2:
            if self.config.prefer_ac3:
                target_codec = 'ac3'
                bitrate_cap = self.config.ac3_bitrate
            else:
                target_codec = 'eac3'
                bitrate_cap = self.config.eac3_bitrate
        # Stereo - use AAC
        else:
            target_codec = 'aac'
            bitrate_cap = self.config.aac_stereo_bitrate
        
        # Match source bitrate or use cap
        target_bitrate = min(source_bitrate if source_bitrate > 0 else bitrate_cap, bitrate_cap)
        
        # Ensure minimum quality
        if target_codec == 'ac3' and channels > 2:
            target_bitrate = max(target_bitrate, 448)
        elif target_codec == 'eac3' and channels > 6:
            target_bitrate = max(target_bitrate, 256)
        elif target_codec == 'aac':
            target_bitrate = max(target_bitrate, 128)
        
        return target_codec, target_bitrate, target_layout
    
    def _generate_track_title(self, language: str, original_title: str) -> str:
        """Generate a clean track title based on language."""
        title_lower = original_title.lower() if original_title else ''
        
        # Preserve commentary tracks
        if 'commentary' in title_lower:
            return original_title
        
        # Check for codec references (strip these)
        codec_keywords = [
            'dts', 'ac3', 'eac3', 'aac', 'dolby', 'truehd', 'atmos',
            'pcm', 'flac', 'opus', 'vorbis', 'mp3', 'lossless',
            '5.1', '7.1', '2.0', 'stereo', 'surround', 'ma', 'hr'
        ]
        has_codec_reference = any(kw in title_lower for kw in codec_keywords)
        
        # Language codes to ignore
        language_codes = [
            'und', 'eng', 'spa', 'fre', 'fra', 'ger', 'deu', 'ita', 'por',
            'jpn', 'chi', 'zho', 'kor', 'rus', 'english', 'spanish', 'french',
            'german', 'italian', 'japanese', 'korean', 'russian', 'undefined'
        ]
        is_just_language = title_lower in language_codes
        
        if has_codec_reference or is_just_language or not original_title:
            lang_code = language.lower() if language else 'und'
            return LANGUAGE_NAMES.get(lang_code, language.capitalize() if language else '')
        
        return original_title
    
    def _build_ffmpeg_command(
        self,
        input_file: str,
        output_file: str,
        info: MediaInfo,
        streams_to_convert: List[AudioStream]
    ) -> List[str]:
        """Build ffmpeg command for audio conversion."""
        
        cmd = ['ffmpeg', '-i', input_file, '-y']
        
        # Video: copy
        cmd.extend(['-c:v', 'copy'])
        
        # Subtitles: copy
        cmd.extend(['-c:s', 'copy'])
        
        # Build stream indices to convert
        convert_indices = {s.index for s in streams_to_convert}
        
        # Process each audio stream
        audio_output_index = 0
        for stream in info.audio_streams:
            if stream.index in convert_indices:
                # Convert this stream
                target_codec, target_bitrate, target_layout = self._determine_target_format(
                    stream.channels, stream.bitrate // 1000 if stream.bitrate else 0
                )
                
                if self.config.keep_original:
                    # Dual track mode: copy original first
                    cmd.extend([f'-c:a:{audio_output_index}', 'copy'])
                    audio_output_index += 1
                
                # Add converted stream
                cmd.extend([f'-c:a:{audio_output_index}', target_codec])
                cmd.extend([f'-b:a:{audio_output_index}', f'{target_bitrate}k'])
                
                if target_layout:
                    cmd.extend([f'-ac:a:{audio_output_index}', str(8), '-channel_layout:a', target_layout])
                
                # Set title
                title = self._generate_track_title(stream.language or '', stream.title or '')
                if title:
                    cmd.extend([f'-metadata:s:a:{audio_output_index}', f'title={title}'])
                
                audio_output_index += 1
            else:
                # Copy non-DTS streams
                cmd.extend([f'-c:a:{audio_output_index}', 'copy'])
                audio_output_index += 1
        
        # Map all streams
        cmd.extend(['-map', '0', '-map_chapters', '0'])
        
        cmd.append(output_file)
        return cmd
    
    def get_status(self, file_path: str) -> Dict:
        """Get conversion status/info for a file."""
        info = self.ffprobe.get_file_info(file_path)
        if info is None:
            return {'status': 'error', 'message': 'Failed to analyze file'}
        
        dts_streams = self.get_dts_streams(info)
        truehd_streams = self.get_truehd_streams(info)
        
        return {
            'status': 'ok',
            'file': file_path,
            'audio_streams': len(info.audio_streams),
            'dts_streams': len(dts_streams),
            'truehd_streams': len(truehd_streams),
            'needs_conversion': self.should_convert(file_path),
            'file_size': info.size,
            'duration': info.duration,
        }
