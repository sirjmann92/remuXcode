#!/usr/bin/env python3
"""
Configuration Manager

Loads configuration from YAML file with environment variable substitution.
Provides typed access to configuration values with defaults.
"""

import os
import re
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

# Default configuration file locations (in order of priority)
DEFAULT_CONFIG_PATHS = [
    Path('/etc/remuxcode/config.yaml'),
    Path.home() / '.config/remuxcode/config.yaml',
    Path(__file__).parent.parent.parent / 'config.yaml',
]


@dataclass
class AudioConfig:
    """Audio conversion settings."""
    enabled: bool = True
    convert_dts: bool = True
    convert_truehd: bool = True
    keep_original: bool = False
    prefer_ac3: bool = True
    ac3_bitrate: int = 640       # kbps for AC3 5.1
    eac3_bitrate: int = 1536     # kbps for E-AC3
    aac_surround_bitrate: int = 512  # kbps for AAC 5.1+
    aac_stereo_bitrate: int = 320    # kbps for AAC stereo
    job_timeout: int = 7200      # seconds (0 = no timeout)


@dataclass
class CleanupConfig:
    """Stream cleanup settings."""
    enabled: bool = True
    clean_audio: bool = True
    clean_subtitles: bool = True
    keep_languages: List[str] = field(default_factory=lambda: ['eng'])
    keep_undefined: bool = False
    keep_commentary: bool = True
    keep_audio_description: bool = True
    keep_sdh: bool = True


@dataclass
class VideoConfig:
    """Video conversion settings."""
    enabled: bool = True
    codec: str = 'hevc'  # hevc or av1
    convert_10bit_x264: bool = True
    convert_8bit_x264: bool = False
    
    # Only convert anime content (skip live action)
    anime_only: bool = True
    
    # Anime-specific settings
    anime_auto_detect: bool = True
    anime_paths: List[str] = field(default_factory=lambda: ['/Anime/', '/アニメ/'])
    
    # HEVC settings - Anime
    anime_crf: int = 19
    anime_preset: str = 'slow'
    anime_tune: str = 'animation'
    anime_framerate: str = '24000/1001'  # 23.976 fps
    
    # HEVC settings - Live action
    live_action_crf: int = 22
    live_action_preset: str = 'medium'
    live_action_tune: Optional[str] = None  # or 'grain'
    live_action_framerate: str = ''  # Empty = auto-detect from source
    
    # AV1 settings - Anime (SVT-AV1 encoder)
    av1_anime_crf: int = 28  # Equivalent to ~HEVC CRF 19
    av1_anime_preset: int = 6  # 0-13, lower = slower/better
    av1_anime_framerate: str = '24000/1001'
    
    # AV1 settings - Live action
    av1_live_action_crf: int = 30  # Equivalent to ~HEVC CRF 22
    av1_live_action_preset: int = 8  # Faster preset
    av1_live_action_framerate: str = ''
    
    # Common encoding settings (HEVC-specific, not used for AV1)
    vbv_maxrate: int = 5000
    vbv_bufsize: int = 10000
    level: str = '4.1'
    profile: str = 'main10'
    pix_fmt: str = 'yuv420p10le'
    job_timeout: int = 7200      # seconds (0 = no timeout)


@dataclass
class LanguageConfig:
    """Language filtering settings."""
    enabled: bool = True
    always_keep: List[str] = field(default_factory=lambda: ['eng'])
    keep_original: bool = True
    keep_forced_subs: bool = True
    keep_sdh: bool = True
    remove_commentary: bool = False


@dataclass
class SonarrConfig:
    """Sonarr integration settings."""
    enabled: bool = True
    url: str = 'http://localhost:8989'
    api_key: str = ''
    trigger_rename: bool = True


@dataclass
class RadarrConfig:
    """Radarr integration settings."""
    enabled: bool = True
    url: str = 'http://localhost:7878'
    api_key: str = ''
    trigger_rename: bool = True


@dataclass
class WebhookConfig:
    """Webhook server settings."""
    enabled: bool = True
    host: str = '0.0.0.0'
    port: int = 7889
    api_key: str = ''


@dataclass
class PathMapping:
    """Container to host path mapping."""
    container: str
    host: str


@dataclass
class NotificationConfig:
    """Notification settings."""
    discord_enabled: bool = False
    discord_webhook: str = ''
    slack_enabled: bool = False
    slack_webhook: str = ''


class Config:
    """
    Configuration manager with YAML loading and environment variable substitution.
    
    Environment variables can be referenced in YAML using ${VAR_NAME} syntax.
    """
    
    def __init__(self, config_path: Optional[str] = None):
        self.config_path = self._find_config_path(config_path)
        self._raw_config: Dict[str, Any] = {}
        self._load_config()
        
        # Typed configuration sections
        self.audio = self._parse_audio_config()
        self.video = self._parse_video_config()
        self.language = self._parse_language_config()
        self.cleanup = self._parse_cleanup_config()
        self.sonarr = self._parse_sonarr_config()
        self.radarr = self._parse_radarr_config()
        self.webhook = self._parse_webhook_config()
        self.path_mappings = self._parse_path_mappings()
        self.notifications = self._parse_notification_config()
        
        # General settings
        self.log_level = self._get('general.log_level', 'INFO')
        # Worker count with env override: REMUXCODE_WORKERS > processing.max_concurrent_jobs > default (1)
        self.workers = int(os.getenv('REMUXCODE_WORKERS', self._get('processing.max_concurrent_jobs', 1)))
        self.temp_dir = self._get('general.temp_dir', '/tmp')
    
    def _find_config_path(self, provided_path: Optional[str]) -> Optional[Path]:
        """Find configuration file from provided path or defaults."""
        if provided_path:
            path = Path(provided_path)
            if path.exists():
                return path
            logger.warning(f"Provided config path not found: {provided_path}")
        
        for path in DEFAULT_CONFIG_PATHS:
            if path.exists():
                logger.info(f"Using configuration file: {path}")
                return path
        
        logger.warning("No configuration file found, using defaults")
        return None
    
    def _load_config(self):
        """Load configuration from YAML file."""
        if self.config_path is None:
            self._raw_config = {}
            return
        
        try:
            with open(self.config_path, 'r') as f:
                content = f.read()
            
            # Substitute environment variables
            content = self._substitute_env_vars(content)
            
            self._raw_config = yaml.safe_load(content) or {}
            logger.info(f"Loaded configuration from {self.config_path}")
            
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            self._raw_config = {}
    
    def _substitute_env_vars(self, content: str) -> str:
        """Replace ${VAR_NAME} or ${VAR_NAME:-default} with environment variable values."""
        pattern = r'\$\{([^}]+)\}'
        
        def replacer(match):
            expr = match.group(1)
            
            # Handle ${VAR:-default} syntax
            if ':-' in expr:
                var_name, default = expr.split(':-', 1)
                value = os.environ.get(var_name, default)
            else:
                var_name = expr
                value = os.environ.get(var_name, '')
                if not value:
                    logger.warning(f"Environment variable {var_name} not set")
            
            return value
        
        return re.sub(pattern, replacer, content)
    
    def _get(self, key: str, default: Any = None) -> Any:
        """
        Get configuration value using dot notation.
        
        Args:
            key: Dot-separated path (e.g., 'audio.enabled')
            default: Default value if not found
            
        Returns:
            Configuration value or default
        """
        keys = key.split('.')
        value = self._raw_config
        
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        
        return value if value is not None else default
    
    def _parse_audio_config(self) -> AudioConfig:
        """Parse audio configuration section."""
        return AudioConfig(
            enabled=self._get('audio.enabled', True),
            convert_dts=self._get('audio.convert_dts', True),
            convert_truehd=self._get('audio.convert_truehd', True),
            keep_original=self._get('audio.keep_original', False),
            prefer_ac3=self._get('audio.prefer_ac3', True),
            ac3_bitrate=self._get('audio.ac3_bitrate', 640),
            eac3_bitrate=self._get('audio.eac3_bitrate', 1536),
            aac_surround_bitrate=self._get('audio.aac_surround_bitrate', 512),
            aac_stereo_bitrate=self._get('audio.aac_stereo_bitrate', 320),
            job_timeout=self._get('processing.job_timeout', 7200),
        )
    
    def _parse_cleanup_config(self) -> CleanupConfig:
        """Parse stream cleanup configuration."""
        return CleanupConfig(
            enabled=self._get('cleanup.enabled', True),
            clean_audio=self._get('cleanup.clean_audio', True),
            clean_subtitles=self._get('cleanup.clean_subtitles', True),
            keep_languages=self._get('cleanup.keep_languages', ['eng']),
            keep_undefined=self._get('cleanup.keep_undefined', False),
            keep_commentary=self._get('cleanup.keep_commentary', True),
            keep_audio_description=self._get('cleanup.keep_audio_description', True),
            keep_sdh=self._get('cleanup.keep_sdh', True),
        )
    
    def _parse_video_config(self) -> VideoConfig:
        """Parse video configuration section."""
        return VideoConfig(
            enabled=self._get('video.enabled', True),
            codec=self._get('video.codec', 'hevc'),
            convert_10bit_x264=self._get('video.convert_10bit_x264', True),
            convert_8bit_x264=self._get('video.convert_8bit_x264', False),
            anime_only=self._get('video.anime_only', True),
            anime_auto_detect=self._get('video.anime_auto_detect', True),
            anime_paths=self._get('video.anime_paths', ['/Anime/', '/アニメ/']),
            anime_crf=self._get('video.anime_crf', 19),
            anime_preset=self._get('video.anime_preset', 'slow'),
            anime_tune=self._get('video.anime_tune', 'animation'),
            anime_framerate=self._get('video.anime_framerate', '24000/1001'),
            live_action_crf=self._get('video.live_action_crf', 22),
            live_action_preset=self._get('video.live_action_preset', 'medium'),
            live_action_tune=self._get('video.live_action_tune'),
            live_action_framerate=self._get('video.live_action_framerate', ''),
            av1_anime_crf=self._get('video.av1_anime_crf', 28),
            av1_anime_preset=self._get('video.av1_anime_preset', 6),
            av1_anime_framerate=self._get('video.av1_anime_framerate', '24000/1001'),
            av1_live_action_crf=self._get('video.av1_live_action_crf', 30),
            av1_live_action_preset=self._get('video.av1_live_action_preset', 8),
            av1_live_action_framerate=self._get('video.av1_live_action_framerate', ''),
            vbv_maxrate=self._get('video.vbv_maxrate', 5000),
            vbv_bufsize=self._get('video.vbv_bufsize', 10000),
            level=self._get('video.level', '4.1'),
            profile=self._get('video.profile', 'main10'),
            pix_fmt=self._get('video.pix_fmt', 'yuv420p10le'),
            job_timeout=self._get('processing.job_timeout', 7200),
        )
    
    def _parse_language_config(self) -> LanguageConfig:
        """Parse language filtering configuration."""
        return LanguageConfig(
            enabled=self._get('language.enabled', True),
            always_keep=self._get('language.always_keep', ['eng']),
            keep_original=self._get('language.keep_original', True),
            keep_forced_subs=self._get('language.keep_forced_subs', True),
            keep_sdh=self._get('language.keep_sdh', True),
            remove_commentary=self._get('language.remove_commentary', False),
        )
    
    def _parse_sonarr_config(self) -> SonarrConfig:
        """Parse Sonarr configuration."""
        return SonarrConfig(
            enabled=self._get('sonarr.enabled', True),
            url=self._get('sonarr.url', 'http://localhost:8989'),
            api_key=self._get('sonarr.api_key', os.getenv('SONARR_API_KEY', '')),
            trigger_rename=self._get('sonarr.trigger_rename', True),
        )
    
    def _parse_radarr_config(self) -> RadarrConfig:
        """Parse Radarr configuration."""
        return RadarrConfig(
            enabled=self._get('radarr.enabled', True),
            url=self._get('radarr.url', 'http://localhost:7878'),
            api_key=self._get('radarr.api_key', os.getenv('RADARR_API_KEY', '')),
            trigger_rename=self._get('radarr.trigger_rename', True),
        )
    
    def _parse_webhook_config(self) -> WebhookConfig:
        """Parse webhook server configuration."""
        return WebhookConfig(
            enabled=self._get('webhook.enabled', True),
            host=self._get('webhook.host', '0.0.0.0'),
            port=self._get('webhook.port', 7889),
            api_key=self._get('webhook.api_key', os.getenv('WEBHOOK_API_KEY', '')),
        )
    
    def _parse_path_mappings(self) -> List[PathMapping]:
        """Parse path mappings configuration."""
        mappings_raw = self._get('path_mappings', [])
        mappings = []
        
        for m in mappings_raw:
            if isinstance(m, dict) and 'container' in m and 'host' in m:
                mappings.append(PathMapping(
                    container=m['container'],
                    host=m['host']
                ))
        
        return mappings
    
    def _parse_notification_config(self) -> NotificationConfig:
        """Parse notification configuration."""
        return NotificationConfig(
            discord_enabled=self._get('notifications.discord.enabled', False),
            discord_webhook=self._get('notifications.discord.webhook_url', ''),
            slack_enabled=self._get('notifications.slack.enabled', False),
            slack_webhook=self._get('notifications.slack.webhook_url', ''),
        )
    
    def reload(self):
        """Reload configuration from file."""
        self._load_config()
        self.audio = self._parse_audio_config()
        self.video = self._parse_video_config()
        self.language = self._parse_language_config()
        self.cleanup = self._parse_cleanup_config()
        self.sonarr = self._parse_sonarr_config()
        self.radarr = self._parse_radarr_config()
        self.webhook = self._parse_webhook_config()
        self.path_mappings = self._parse_path_mappings()
        self.notifications = self._parse_notification_config()
        logger.info("Configuration reloaded")


# Global configuration instance
_config: Optional[Config] = None


def get_config(config_path: Optional[str] = None) -> Config:
    """Get or create global configuration instance."""
    global _config
    if _config is None:
        _config = Config(config_path)
    return _config
