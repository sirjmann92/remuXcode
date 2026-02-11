#!/usr/bin/env python3
"""
FFProbe Wrapper

Provides structured access to media file information via ffprobe.
Returns parsed JSON data about video, audio, and subtitle streams.
"""

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


@dataclass
class VideoStream:
    """Represents a video stream in a media file."""
    index: int
    codec_name: str
    codec_long_name: str
    profile: Optional[str]
    width: int
    height: int
    pix_fmt: str
    bit_depth: int
    frame_rate: str
    duration: Optional[float]
    bitrate: Optional[int]
    
    @property
    def is_hevc(self) -> bool:
        """Check if stream is HEVC/H.265."""
        return self.codec_name.lower() in ('hevc', 'h265')
    
    @property
    def is_h264(self) -> bool:
        """Check if stream is H.264/AVC."""
        return self.codec_name.lower() in ('h264', 'avc', 'avc1')
    
    @property
    def is_10bit(self) -> bool:
        """Check if stream is 10-bit."""
        return self.bit_depth >= 10 or '10' in (self.pix_fmt or '')
    
    @property
    def is_10bit_h264(self) -> bool:
        """Check if stream is 10-bit H.264 (needs conversion for compatibility)."""
        return self.is_h264 and self.is_10bit


@dataclass
class AudioStream:
    """Represents an audio stream in a media file."""
    index: int
    codec_name: str
    codec_long_name: str
    channels: int
    channel_layout: Optional[str]
    sample_rate: int
    bitrate: Optional[int]
    language: Optional[str]
    title: Optional[str]
    is_default: bool
    is_forced: bool
    
    @property
    def is_dts(self) -> bool:
        """Check if stream is DTS (any variant)."""
        return self.codec_name.lower().startswith('dts')
    
    @property
    def is_truehd(self) -> bool:
        """Check if stream is TrueHD."""
        return self.codec_name.lower() == 'truehd'
    
    @property
    def is_lossless(self) -> bool:
        """Check if stream is lossless (DTS-HD MA, TrueHD, FLAC, etc.)."""
        lossless_codecs = ('truehd', 'flac', 'alac', 'pcm', 'mlp')
        return (
            self.codec_name.lower() in lossless_codecs or
            'dts-hd ma' in (self.codec_long_name or '').lower() or
            'dts_ma' in self.codec_name.lower()
        )
    
    @property
    def needs_conversion(self) -> bool:
        """Check if stream needs conversion for compatibility."""
        # DTS variants that need conversion
        return self.is_dts or self.is_truehd


@dataclass
class SubtitleStream:
    """Represents a subtitle stream in a media file."""
    index: int
    codec_name: str
    language: Optional[str]
    title: Optional[str]
    is_default: bool
    is_forced: bool
    is_hearing_impaired: bool
    
    @property
    def is_sdh(self) -> bool:
        """Alias for is_hearing_impaired (SDH = Subtitles for Deaf/Hard of Hearing)."""
        return self.is_hearing_impaired
    
    @property
    def is_pgs(self) -> bool:
        """Check if stream is PGS (Blu-ray image-based)."""
        return self.codec_name.lower() in ('hdmv_pgs_subtitle', 'pgssub')
    
    @property
    def is_text_based(self) -> bool:
        """Check if stream is text-based (SRT, ASS, etc.)."""
        return self.codec_name.lower() in ('subrip', 'srt', 'ass', 'ssa', 'mov_text', 'webvtt')


@dataclass
class AttachmentStream:
    """Represents an attachment stream (fonts, images, etc.)."""
    index: int
    codec_name: str
    filename: Optional[str] = None
    mimetype: Optional[str] = None


@dataclass 
class MediaInfo:
    """Complete information about a media file."""
    path: Path
    format_name: str
    duration: float
    size: int
    bitrate: int
    video_streams: List[VideoStream]
    audio_streams: List[AudioStream]
    subtitle_streams: List[SubtitleStream]
    attachment_streams: List[AttachmentStream]
    chapters: List[Dict]
    
    @property
    def primary_video(self) -> Optional[VideoStream]:
        """Get the primary (first) video stream."""
        return self.video_streams[0] if self.video_streams else None
    
    @property
    def has_dts(self) -> bool:
        """Check if file has any DTS audio streams."""
        return any(s.is_dts for s in self.audio_streams)
    
    @property
    def has_truehd(self) -> bool:
        """Check if file has any TrueHD audio streams."""
        return any(s.is_truehd for s in self.audio_streams)
    
    @property
    def needs_audio_conversion(self) -> bool:
        """Check if file needs audio conversion."""
        return any(s.needs_conversion for s in self.audio_streams)
    
    @property
    def needs_video_conversion(self) -> bool:
        """Check if file needs video conversion (10-bit H.264)."""
        video = self.primary_video
        return video is not None and video.is_10bit_h264
    
    @property
    def is_hevc(self) -> bool:
        """Check if primary video is already HEVC."""
        video = self.primary_video
        return video is not None and video.is_hevc
    
    def get_audio_by_language(self, lang_codes: List[str]) -> List[AudioStream]:
        """Get audio streams matching language codes."""
        return [s for s in self.audio_streams if s.language in lang_codes]
    
    def get_subs_by_language(self, lang_codes: List[str]) -> List[SubtitleStream]:
        """Get subtitle streams matching language codes."""
        return [s for s in self.subtitle_streams if s.language in lang_codes]


class FFProbe:
    """Wrapper around ffprobe for media file analysis."""
    
    def __init__(self, ffprobe_path: str = 'ffprobe'):
        self.ffprobe_path = ffprobe_path
    
    def get_file_info(self, file_path: str) -> Optional[MediaInfo]:
        """
        Get complete information about a media file.
        
        Args:
            file_path: Path to the media file
            
        Returns:
            MediaInfo object or None if analysis fails
        """
        path = Path(file_path)
        if not path.exists():
            logger.error(f"File not found: {file_path}")
            return None
        
        try:
            result = subprocess.run(
                [
                    self.ffprobe_path,
                    '-v', 'quiet',
                    '-print_format', 'json',
                    '-show_format',
                    '-show_streams',
                    '-show_chapters',
                    str(path)
                ],
                capture_output=True,
                text=True,
                timeout=60
            )
            
            if result.returncode != 0:
                logger.error(f"ffprobe failed for {file_path}: {result.stderr}")
                return None
            
            data = json.loads(result.stdout)
            return self._parse_media_info(path, data)
            
        except subprocess.TimeoutExpired:
            logger.error(f"ffprobe timeout for {file_path}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse ffprobe output for {file_path}: {e}")
            return None
        except Exception as e:
            logger.error(f"Error analyzing {file_path}: {e}")
            return None
    
    def _parse_media_info(self, path: Path, data: Dict) -> MediaInfo:
        """Parse ffprobe JSON output into MediaInfo object."""
        format_info = data.get('format', {})
        streams = data.get('streams', [])
        chapters = data.get('chapters', [])
        
        video_streams = []
        audio_streams = []
        subtitle_streams = []
        attachment_streams = []
        
        for stream in streams:
            codec_type = stream.get('codec_type')
            
            if codec_type == 'video':
                video_streams.append(self._parse_video_stream(stream))
            elif codec_type == 'audio':
                audio_streams.append(self._parse_audio_stream(stream))
            elif codec_type == 'subtitle':
                subtitle_streams.append(self._parse_subtitle_stream(stream))
            elif codec_type == 'attachment':
                attachment_streams.append(self._parse_attachment_stream(stream))
        
        return MediaInfo(
            path=path,
            format_name=format_info.get('format_name', ''),
            duration=float(format_info.get('duration', 0)),
            size=int(format_info.get('size', 0)),
            bitrate=int(format_info.get('bit_rate', 0)),
            video_streams=video_streams,
            audio_streams=audio_streams,
            subtitle_streams=subtitle_streams,
            attachment_streams=attachment_streams,
            chapters=chapters
        )
    
    def _parse_video_stream(self, stream: Dict) -> VideoStream:
        """Parse video stream information."""
        # Determine bit depth
        pix_fmt = stream.get('pix_fmt', '')
        bit_depth = 8
        if 'p10' in pix_fmt or '10le' in pix_fmt or '10be' in pix_fmt:
            bit_depth = 10
        elif 'p12' in pix_fmt or '12le' in pix_fmt or '12be' in pix_fmt:
            bit_depth = 12
        elif stream.get('bits_per_raw_sample'):
            bit_depth = int(stream.get('bits_per_raw_sample', 8))
        
        # Parse frame rate
        r_frame_rate = stream.get('r_frame_rate', '0/1')
        
        return VideoStream(
            index=stream.get('index', 0),
            codec_name=stream.get('codec_name', ''),
            codec_long_name=stream.get('codec_long_name', ''),
            profile=stream.get('profile'),
            width=stream.get('width', 0),
            height=stream.get('height', 0),
            pix_fmt=pix_fmt,
            bit_depth=bit_depth,
            frame_rate=r_frame_rate,
            duration=float(stream.get('duration', 0)) if stream.get('duration') else None,
            bitrate=int(stream.get('bit_rate', 0)) if stream.get('bit_rate') else None
        )
    
    def _parse_audio_stream(self, stream: Dict) -> AudioStream:
        """Parse audio stream information."""
        tags = stream.get('tags', {})
        disposition = stream.get('disposition', {})
        
        return AudioStream(
            index=stream.get('index', 0),
            codec_name=stream.get('codec_name', ''),
            codec_long_name=stream.get('codec_long_name', ''),
            channels=stream.get('channels', 0),
            channel_layout=stream.get('channel_layout'),
            sample_rate=int(stream.get('sample_rate', 0)),
            bitrate=int(stream.get('bit_rate', 0)) if stream.get('bit_rate') else None,
            language=tags.get('language'),
            title=tags.get('title'),
            is_default=disposition.get('default', 0) == 1,
            is_forced=disposition.get('forced', 0) == 1
        )
    
    def _parse_subtitle_stream(self, stream: Dict) -> SubtitleStream:
        """Parse subtitle stream information."""
        tags = stream.get('tags', {})
        disposition = stream.get('disposition', {})
        title = tags.get('title', '').lower()
        
        # Detect hearing impaired from title or disposition
        is_hi = (
            disposition.get('hearing_impaired', 0) == 1 or
            'sdh' in title or
            'hearing' in title or
            'impaired' in title or
            'cc' in title
        )
        
        return SubtitleStream(
            index=stream.get('index', 0),
            codec_name=stream.get('codec_name', ''),
            language=tags.get('language'),
            title=tags.get('title'),
            is_default=disposition.get('default', 0) == 1,
            is_forced=disposition.get('forced', 0) == 1,
            is_hearing_impaired=is_hi
        )
    
    def _parse_attachment_stream(self, stream: Dict) -> AttachmentStream:
        """Parse attachment stream information."""
        tags = stream.get('tags', {})
        
        return AttachmentStream(
            index=stream.get('index', 0),
            codec_name=stream.get('codec_name', ''),
            filename=tags.get('filename'),
            mimetype=tags.get('mimetype')
        )
    
    def is_10bit_h264(self, file_path: str) -> bool:
        """Quick check if file is 10-bit H.264."""
        info = self.get_file_info(file_path)
        return info is not None and info.needs_video_conversion
    
    def has_dts(self, file_path: str) -> bool:
        """Quick check if file has DTS audio."""
        info = self.get_file_info(file_path)
        return info is not None and info.has_dts

