#!/usr/bin/env python3
"""
Stream Cleanup Worker

Removes unwanted audio and subtitle streams based on language preferences.
Keeps original language + English (or configured languages).
"""

import logging
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, List, Set, Callable

from ..utils.ffprobe import FFProbe, MediaInfo, AudioStream, SubtitleStream
from ..utils.language import LanguageDetector
from ..utils.config import CleanupConfig

logger = logging.getLogger(__name__)


@dataclass
class CleanupResult:
    """Result of a stream cleanup operation."""
    success: bool
    input_file: str
    output_file: str
    audio_removed: int
    audio_kept: int
    subtitle_removed: int
    subtitle_kept: int
    original_size: int
    new_size: int
    original_language: Optional[str] = None
    error: Optional[str] = None


class StreamCleanup:
    """
    Removes unwanted audio and subtitle streams.
    
    Features:
    - Detect original content language (NFO, Sonarr/Radarr API, path)
    - Keep original language + English audio
    - Keep original language + English + configurable subtitles
    - Preserve forced/SDH subtitles
    """
    
    def __init__(
        self,
        config: CleanupConfig,
        ffprobe: Optional[FFProbe] = None,
        language_detector: Optional[LanguageDetector] = None,
        get_volume_root: Optional[Callable[[str], str]] = None
    ):
        """
        Initialize stream cleanup worker.
        
        Args:
            config: Cleanup configuration
            ffprobe: FFProbe instance
            language_detector: LanguageDetector instance
            get_volume_root: Function to get volume root for temp files (uses /tmp if not provided)
        """
        self.config = config
        self.ffprobe = ffprobe or FFProbe()
        self.language_detector = language_detector or LanguageDetector()
        self.get_volume_root = get_volume_root or (lambda _: '/tmp')
    
    def should_cleanup(self, file_path: str) -> bool:
        """Check if file has streams that should be removed."""
        if not self.config.enabled:
            return False
        
        info = self.ffprobe.get_file_info(file_path)
        if info is None:
            return False
        
        # Detect original language
        original_lang = self._detect_original_language(file_path)
        
        # Build set of languages to keep
        keep_languages = self._get_languages_to_keep(original_lang)
        
        # Check if any audio streams should be removed
        if self.config.clean_audio:
            for stream in info.audio_streams:
                if not self._should_keep_audio(stream, keep_languages):
                    return True
        
        # Check if any subtitle streams should be removed
        if self.config.clean_subtitles:
            for stream in info.subtitle_streams:
                if not self._should_keep_subtitle(stream, keep_languages):
                    return True
        
        return False
    
    def cleanup(
        self,
        input_file: str,
        output_file: Optional[str] = None,
        job_id: Optional[str] = None,
        force_original_language: Optional[str] = None
    ) -> CleanupResult:
        """
        Remove unwanted streams from a media file.
        
        Args:
            input_file: Path to input file
            output_file: Path for output file (defaults to replacing input)
            job_id: Unique job ID for temp directory
            force_original_language: Override language detection
        
        Returns:
            CleanupResult with details of streams removed
        """
        input_path = Path(input_file)
        
        if not input_path.exists():
            return CleanupResult(
                success=False,
                input_file=input_file,
                output_file=output_file or input_file,
                audio_removed=0,
                audio_kept=0,
                subtitle_removed=0,
                subtitle_kept=0,
                original_size=0,
                new_size=0,
                error=f"Input file not found: {input_file}"
            )
        
        # Get media info
        info = self.ffprobe.get_file_info(input_file)
        if info is None:
            return CleanupResult(
                success=False,
                input_file=input_file,
                output_file=output_file or input_file,
                audio_removed=0,
                audio_kept=0,
                subtitle_removed=0,
                subtitle_kept=0,
                original_size=0,
                new_size=0,
                error="Failed to analyze input file"
            )
        
        # Detect original language
        if force_original_language:
            original_lang = force_original_language
        else:
            original_lang = self._detect_original_language(input_file)
        
        # Build set of languages to keep
        keep_languages = self._get_languages_to_keep(original_lang)
        
        # Determine which streams to keep
        audio_keep = []
        audio_remove = []
        for stream in info.audio_streams:
            if self._should_keep_audio(stream, keep_languages):
                audio_keep.append(stream)
            else:
                audio_remove.append(stream)
        
        subtitle_keep = []
        subtitle_remove = []
        for stream in info.subtitle_streams:
            if self._should_keep_subtitle(stream, keep_languages):
                subtitle_keep.append(stream)
            else:
                subtitle_remove.append(stream)
        
        # If nothing to remove, skip processing
        audio_to_remove = len(audio_remove) if self.config.clean_audio else 0
        subs_to_remove = len(subtitle_remove) if self.config.clean_subtitles else 0
        
        if audio_to_remove == 0 and subs_to_remove == 0:
            logger.info(f"No streams to remove from: {input_path.name}")
            return CleanupResult(
                success=True,
                input_file=input_file,
                output_file=output_file or input_file,
                audio_removed=0,
                audio_kept=len(info.audio_streams),
                subtitle_removed=0,
                subtitle_kept=len(info.subtitle_streams),
                original_size=info.size,
                new_size=info.size,
                original_language=original_lang
            )
        
        logger.info(
            f"Cleaning {input_path.name}: keeping {len(audio_keep)} audio, "
            f"{len(subtitle_keep)} subs (original: {original_lang})"
        )
        
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
                audio_keep if self.config.clean_audio else info.audio_streams,
                subtitle_keep if self.config.clean_subtitles else info.subtitle_streams
            )
            logger.debug(f"Running: {' '.join(cmd)}")
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=3600
            )
            
            if result.returncode != 0:
                return CleanupResult(
                    success=False,
                    input_file=input_file,
                    output_file=output_file,
                    audio_removed=0,
                    audio_kept=len(info.audio_streams),
                    subtitle_removed=0,
                    subtitle_kept=len(info.subtitle_streams),
                    original_size=info.size,
                    new_size=0,
                    original_language=original_lang,
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
            
            logger.info(
                f"Cleaned: {input_file} "
                f"(removed {audio_to_remove} audio, {subs_to_remove} subs, "
                f"{info.size / 1024 / 1024:.1f}MB → {new_size / 1024 / 1024:.1f}MB)"
            )
            
            return CleanupResult(
                success=True,
                input_file=input_file,
                output_file=str(output_path),
                audio_removed=audio_to_remove,
                audio_kept=len(audio_keep),
                subtitle_removed=subs_to_remove,
                subtitle_kept=len(subtitle_keep),
                original_size=info.size,
                new_size=new_size,
                original_language=original_lang
            )
            
        except subprocess.TimeoutExpired:
            return CleanupResult(
                success=False,
                input_file=input_file,
                output_file=output_file,
                audio_removed=0,
                audio_kept=len(info.audio_streams),
                subtitle_removed=0,
                subtitle_kept=len(info.subtitle_streams),
                original_size=info.size,
                new_size=0,
                original_language=original_lang,
                error="FFmpeg timeout"
            )
            
        except Exception as e:
            logger.exception(f"Cleanup failed: {e}")
            return CleanupResult(
                success=False,
                input_file=input_file,
                output_file=output_file,
                audio_removed=0,
                audio_kept=len(info.audio_streams),
                subtitle_removed=0,
                subtitle_kept=len(info.subtitle_streams),
                original_size=info.size,
                new_size=0,
                original_language=original_lang,
                error=str(e)
            )
            
        finally:
            # Clean up temp directory
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
    
    def _detect_original_language(self, file_path: str) -> str:
        """Detect the original content language."""
        return self.language_detector.detect_original_language(file_path)
    
    def _get_languages_to_keep(self, original_lang: str) -> Set[str]:
        """Build set of language codes to keep."""
        # Start with configured languages
        keep = set(self.config.keep_languages)
        
        # Always keep original language
        if original_lang and original_lang != 'und':
            keep.add(original_lang)
            # Add alternate codes for the same language
            alternates = {
                'fre': 'fra', 'fra': 'fre',
                'ger': 'deu', 'deu': 'ger',
                'chi': 'zho', 'zho': 'chi',
                'dut': 'nld', 'nld': 'dut',
                'gre': 'ell', 'ell': 'gre',
            }
            if original_lang in alternates:
                keep.add(alternates[original_lang])
        
        # Always keep undefined if configured
        if self.config.keep_undefined:
            keep.add('und')
            keep.add('')
        
        return keep
    
    def _should_keep_audio(self, stream: AudioStream, keep_languages: Set[str]) -> bool:
        """Determine if an audio stream should be kept."""
        lang = stream.language.lower() if stream.language else ''
        
        # Keep if language matches
        if lang in keep_languages:
            return True
        
        # Keep commentary tracks if configured
        title_lower = (stream.title or '').lower()
        if self.config.keep_commentary and 'commentary' in title_lower:
            return True
        
        # Keep audio description if configured
        if self.config.keep_audio_description:
            if 'description' in title_lower or 'descriptive' in title_lower:
                return True
        
        return False
    
    def _should_keep_subtitle(self, stream: SubtitleStream, keep_languages: Set[str]) -> bool:
        """Determine if a subtitle stream should be kept."""
        lang = stream.language.lower() if stream.language else ''
        
        # Always keep forced subtitles
        if stream.is_forced:
            return True
        
        # Keep SDH/CC if configured
        if self.config.keep_sdh and stream.is_sdh:
            return True
        
        # Keep if language matches
        if lang in keep_languages:
            return True
        
        return False
    
    def _build_ffmpeg_command(
        self,
        input_file: str,
        output_file: str,
        info: MediaInfo,
        audio_keep: List[AudioStream],
        subtitle_keep: List[SubtitleStream]
    ) -> List[str]:
        """Build ffmpeg command for stream removal."""
        
        cmd = ['ffmpeg', '-i', input_file, '-y']
        
        # Map video streams
        if info.video_streams:
            for stream in info.video_streams:
                cmd.extend(['-map', f'0:{stream.index}'])
        
        # Map kept audio streams
        for stream in audio_keep:
            cmd.extend(['-map', f'0:{stream.index}'])
        
        # Map kept subtitle streams
        for stream in subtitle_keep:
            cmd.extend(['-map', f'0:{stream.index}'])
        
        # Map attachments (fonts, etc.)
        if info.attachment_streams:
            for stream in info.attachment_streams:
                cmd.extend(['-map', f'0:{stream.index}'])
        
        # Map chapters
        cmd.extend(['-map_chapters', '0'])
        
        # Copy all streams (no re-encoding)
        cmd.extend(['-c', 'copy'])
        
        cmd.append(output_file)
        return cmd
    
    def get_status(self, file_path: str) -> Dict:
        """Get cleanup status/info for a file."""
        info = self.ffprobe.get_file_info(file_path)
        if info is None:
            return {'status': 'error', 'message': 'Failed to analyze file'}
        
        original_lang = self._detect_original_language(file_path)
        keep_languages = self._get_languages_to_keep(original_lang)
        
        audio_status = []
        for stream in info.audio_streams:
            keep = self._should_keep_audio(stream, keep_languages)
            audio_status.append({
                'index': stream.index,
                'codec': stream.codec_name,
                'language': stream.language,
                'title': stream.title,
                'channels': stream.channels,
                'keep': keep,
            })
        
        subtitle_status = []
        for stream in info.subtitle_streams:
            keep = self._should_keep_subtitle(stream, keep_languages)
            subtitle_status.append({
                'index': stream.index,
                'codec': stream.codec_name,
                'language': stream.language,
                'title': stream.title,
                'forced': stream.is_forced,
                'sdh': stream.is_sdh,
                'keep': keep,
            })
        
        return {
            'status': 'ok',
            'file': file_path,
            'original_language': original_lang,
            'keep_languages': list(keep_languages),
            'audio_streams': audio_status,
            'subtitle_streams': subtitle_status,
            'needs_cleanup': self.should_cleanup(file_path),
            'file_size': info.size,
        }
