#!/usr/bin/env python3
"""Configuration manager.

Loads configuration from YAML file with environment variable substitution.
Provides typed access to configuration values with defaults.
"""

from dataclasses import dataclass, field
import logging
import os
from pathlib import Path
import re
from typing import Any

import yaml

logger = logging.getLogger(__name__)


def get_available_cpus() -> int:
    """Detect available CPUs, respecting Docker/cgroup limits.

    Checks cgroup v2 then v1 CPU quotas before falling back to os.cpu_count().
    When Docker 'cpus: 16' is set, os.cpu_count() still reports host CPUs,
    but the cgroup quota correctly reflects the container limit.
    """
    # cgroup v2: /sys/fs/cgroup/cpu.max contains "quota period" (e.g. "1600000 100000" = 16 CPUs)
    try:
        with Path("/sys/fs/cgroup/cpu.max").open() as f:
            parts = f.read().strip().split()
            if parts[0] != "max":
                return max(1, int(int(parts[0]) / int(parts[1])))
    except (OSError, ValueError, IndexError):
        pass

    # cgroup v1: separate quota and period files
    try:
        with Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").open() as f:
            quota = int(f.read().strip())
        if quota > 0:
            with Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").open() as f:
                period = int(f.read().strip())
            return max(1, int(quota / period))
    except (OSError, ValueError):
        pass

    return os.cpu_count() or 4


# Default configuration file locations (in order of priority)
DEFAULT_CONFIG_PATHS = [
    Path("/etc/remuxcode/config.yaml"),
    Path.home() / ".config/remuxcode/config.yaml",
    Path(__file__).parent.parent.parent / "config.yaml",
]


@dataclass
class AudioConfig:
    """Audio conversion settings."""

    enabled: bool = True
    anime_only: bool = False  # When True, only convert audio in anime content
    convert_dts: bool = True
    convert_dts_x: bool = False  # DTS:X is object-based — skip by default
    convert_truehd: bool = False  # TrueHD is lossless Dolby — default is to leave it alone
    keep_original: bool = False
    keep_original_dts_x: bool = False
    original_as_secondary: bool = True  # Converted track first (plays by default)
    prefer_ac3: bool = True
    ac3_bitrate: int = 640  # kbps for AC3 5.1
    eac3_bitrate: int = 1536  # kbps for E-AC3
    aac_surround_bitrate: int = 512  # kbps for AAC 5.1+
    aac_stereo_bitrate: int = 320  # kbps for AAC stereo
    job_timeout: int = 7200  # seconds (0 = no timeout)


@dataclass
class CleanupConfig:
    """Stream cleanup settings."""

    enabled: bool = True
    anime_only: bool = False  # When True, only clean streams in anime content
    clean_audio: bool = True
    clean_subtitles: bool = True
    keep_languages: list[str] = field(default_factory=lambda: ["eng"])
    keep_undefined: bool = False
    keep_commentary: bool = True
    keep_audio_description: bool = True
    keep_sdh: bool = True
    anime_keep_original_audio: bool = True


@dataclass
class VideoConfig:
    """Video conversion settings."""

    enabled: bool = True
    codec: str = "hevc"  # hevc or av1
    convert_10bit_x264: bool = True
    convert_8bit_x264: bool = False

    # Only convert anime content (skip live action)
    anime_only: bool = True

    # Anime-specific settings
    anime_auto_detect: bool = True
    anime_paths: list[str] = field(default_factory=lambda: ["/Anime/", "/アニメ/"])

    # HEVC settings - Anime
    anime_crf: int = 19
    anime_preset: str = "slow"
    anime_tune: str = "animation"
    anime_framerate: str = "24000/1001"  # 23.976 fps

    # HEVC settings - Live action
    live_action_crf: int = 22
    live_action_preset: str = "medium"
    live_action_tune: str | None = None  # or 'grain'
    live_action_framerate: str = ""  # Empty = auto-detect from source

    # AV1 settings - Anime (SVT-AV1 encoder)
    av1_anime_crf: int = 28  # Equivalent to ~HEVC CRF 19
    av1_anime_preset: int = 6  # 0-13, lower = slower/better
    av1_anime_framerate: str = "24000/1001"

    # AV1 settings - Live action
    av1_live_action_crf: int = 30  # Equivalent to ~HEVC CRF 22
    av1_live_action_preset: int = 8  # Faster preset
    av1_live_action_framerate: str = ""

    # Common encoding settings (HEVC-specific, not used for AV1)
    vbv_maxrate: int = 5000
    vbv_bufsize: int = 10000
    level: str = "4.1"
    profile: str = "main10"
    pix_fmt: str = "yuv420p10le"
    hw_accel: str = "none"  # none, auto, qsv, vaapi, nvenc
    job_timeout: int = 7200  # seconds (0 = no timeout)


@dataclass
class SonarrConfig:
    """Sonarr integration settings."""

    enabled: bool = True
    url: str = "http://localhost:8989"
    api_key: str = ""


@dataclass
class RadarrConfig:
    """Radarr integration settings."""

    enabled: bool = True
    url: str = "http://localhost:7878"
    api_key: str = ""


class Config:
    """Configuration manager with YAML loading and environment variable substitution.

    Environment variables can be referenced in YAML using ${VAR_NAME} syntax.
    """

    def __init__(self, config_path: str | None = None):
        """Initialize configuration manager from YAML file path."""
        self.config_path = self._find_config_path(config_path)
        self._raw_config: dict[str, Any] = {}
        self._load_config()

        # Typed configuration sections
        self.audio = self._parse_audio_config()
        self.video = self._parse_video_config()
        self.cleanup = self._parse_cleanup_config()
        self.sonarr = self._parse_sonarr_config()
        self.radarr = self._parse_radarr_config()

        # General settings
        # Worker count with env override: REMUXCODE_WORKERS > processing.max_concurrent_jobs > default (1)
        self.workers = int(
            os.getenv("REMUXCODE_WORKERS", self._get("processing.max_concurrent_jobs", 1))
        )
        self.job_history_days = int(
            os.getenv("JOB_HISTORY_DAYS", self._get("general.job_history_days", 30))
        )
        # FFmpeg thread limit: 0 = auto (~80% of available CPUs)
        self.ffmpeg_threads = int(
            os.getenv("FFMPEG_THREADS", self._get("processing.ffmpeg_threads", 0))
        )

    @property
    def effective_ffmpeg_threads(self) -> int:
        """Resolve ffmpeg_threads: 0 → ~80% of available CPUs, else the explicit value."""
        if self.ffmpeg_threads > 0:
            return self.ffmpeg_threads
        return max(1, int(get_available_cpus() * 0.8))

    def _find_config_path(self, provided_path: str | None) -> Path | None:
        """Find configuration file from provided path or defaults."""
        if provided_path:
            path = Path(provided_path)
            if path.exists():
                return path
            # File doesn't exist yet — keep the path so save() can create it
            logger.info(
                "Config not found at %s — using defaults (created on first save)", provided_path
            )
            return path

        for path in DEFAULT_CONFIG_PATHS:
            if path.exists():
                logger.info("Using configuration file: %s", path)
                return path

        logger.warning("No configuration file found, using defaults")
        return None

    def _load_config(self) -> None:
        """Load configuration from YAML file."""
        if self.config_path is None or not self.config_path.exists():
            self._raw_config = {}
            return

        try:
            with self.config_path.open() as f:
                content = f.read()

            # Substitute environment variables
            content = self._substitute_env_vars(content)

            self._raw_config = yaml.safe_load(content) or {}
            logger.info("Loaded configuration from %s", self.config_path)

        except Exception as e:
            logger.error("Failed to load config: %s", e)
            self._raw_config = {}

    def _substitute_env_vars(self, content: str) -> str:
        """Replace ${VAR_NAME} or ${VAR_NAME:-default} with environment variable values."""
        pattern = r"\$\{([^}]+)\}"

        def replacer(match: re.Match[str]) -> str:
            expr = match.group(1)

            # Handle ${VAR:-default} syntax
            if ":-" in expr:
                var_name, default = expr.split(":-", 1)
                value = os.environ.get(var_name, default)
            else:
                var_name = expr
                value = os.environ.get(var_name, "")
                if not value:
                    logger.debug("Environment variable %s not set", var_name)

            return value

        return re.sub(pattern, replacer, content)

    def _get(self, key: str, default: Any = None) -> Any:
        """Get configuration value using dot notation.

        Args:
            key: Dot-separated path (e.g., 'audio.enabled')
            default: Default value if not found

        Returns:
            Configuration value or default
        """
        keys = key.split(".")
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
            enabled=self._get("audio.enabled", True),
            anime_only=self._get("audio.anime_only", False),
            convert_dts=self._get("audio.convert_dts", True),
            convert_dts_x=self._get("audio.convert_dts_x", False),
            convert_truehd=self._get("audio.convert_truehd", False),
            keep_original=self._get("audio.keep_original", False),
            keep_original_dts_x=self._get("audio.keep_original_dts_x", False),
            original_as_secondary=self._get("audio.original_as_secondary", True),
            prefer_ac3=self._get("audio.prefer_ac3", True),
            ac3_bitrate=self._get("audio.ac3_bitrate", 640),
            eac3_bitrate=self._get("audio.eac3_bitrate", 1536),
            aac_surround_bitrate=self._get("audio.aac_surround_bitrate", 512),
            aac_stereo_bitrate=self._get("audio.aac_stereo_bitrate", 320),
            job_timeout=self._get("processing.job_timeout", 7200),
        )

    def _parse_cleanup_config(self) -> CleanupConfig:
        """Parse stream cleanup configuration."""
        return CleanupConfig(
            enabled=self._get("cleanup.enabled", True),
            anime_only=self._get("cleanup.anime_only", False),
            clean_audio=self._get("cleanup.clean_audio", True),
            clean_subtitles=self._get("cleanup.clean_subtitles", True),
            keep_languages=self._get("cleanup.keep_languages", ["eng"]),
            keep_undefined=self._get("cleanup.keep_undefined", False),
            keep_commentary=self._get("cleanup.keep_commentary", True),
            keep_audio_description=self._get("cleanup.keep_audio_description", True),
            keep_sdh=self._get("cleanup.keep_sdh", True),
            anime_keep_original_audio=self._get("cleanup.anime_keep_original_audio", True),
        )

    def _parse_video_config(self) -> VideoConfig:
        """Parse video configuration section."""
        return VideoConfig(
            enabled=self._get("video.enabled", True),
            codec=self._get("video.codec", "hevc"),
            convert_10bit_x264=self._get("video.convert_10bit_x264", True),
            convert_8bit_x264=self._get("video.convert_8bit_x264", False),
            anime_only=self._get("video.anime_only", True),
            anime_auto_detect=self._get("video.anime_auto_detect", True),
            anime_paths=self._get("video.anime_paths", ["/Anime/", "/アニメ/"]),
            anime_crf=self._get("video.anime_crf", 19),
            anime_preset=self._get("video.anime_preset", "slow"),
            anime_tune=self._get("video.anime_tune", "animation"),
            anime_framerate=self._get("video.anime_framerate", "24000/1001"),
            live_action_crf=self._get("video.live_action_crf", 22),
            live_action_preset=self._get("video.live_action_preset", "medium"),
            live_action_tune=self._get("video.live_action_tune"),
            live_action_framerate=self._get("video.live_action_framerate", ""),
            av1_anime_crf=self._get("video.av1_anime_crf", 28),
            av1_anime_preset=self._get("video.av1_anime_preset", 6),
            av1_anime_framerate=self._get("video.av1_anime_framerate", "24000/1001"),
            av1_live_action_crf=self._get("video.av1_live_action_crf", 30),
            av1_live_action_preset=self._get("video.av1_live_action_preset", 8),
            av1_live_action_framerate=self._get("video.av1_live_action_framerate", ""),
            vbv_maxrate=self._get("video.vbv_maxrate", 5000),
            vbv_bufsize=self._get("video.vbv_bufsize", 10000),
            level=self._get("video.level", "4.1"),
            profile=self._get("video.profile", "main10"),
            pix_fmt=self._get("video.pix_fmt", "yuv420p10le"),
            hw_accel=self._get("video.hw_accel", "none"),
            job_timeout=self._get("processing.job_timeout", 7200),
        )

    def _parse_sonarr_config(self) -> SonarrConfig:
        """Parse Sonarr configuration."""
        return SonarrConfig(
            enabled=self._get("sonarr.enabled", True),
            url=self._get("sonarr.url", "http://localhost:8989"),
            api_key=self._get("sonarr.api_key", ""),
        )

    def _parse_radarr_config(self) -> RadarrConfig:
        """Parse Radarr configuration."""
        return RadarrConfig(
            enabled=self._get("radarr.enabled", True),
            url=self._get("radarr.url", "http://localhost:7878"),
            api_key=self._get("radarr.api_key", ""),
        )

    def reload(self) -> None:
        """Reload configuration from file."""
        self._load_config()
        self.audio = self._parse_audio_config()
        self.video = self._parse_video_config()
        self.cleanup = self._parse_cleanup_config()
        self.sonarr = self._parse_sonarr_config()
        self.radarr = self._parse_radarr_config()
        logger.info("Configuration reloaded")

    def save(self) -> None:
        """Persist current in-memory config back to the YAML file."""
        if not self.config_path:
            raise RuntimeError("No config file path available")

        self.config_path.parent.mkdir(parents=True, exist_ok=True)

        # Merge updated sections into raw config
        self._raw_config.setdefault("audio", {})
        for key in (
            "enabled",
            "anime_only",
            "convert_dts",
            "convert_dts_x",
            "convert_truehd",
            "keep_original",
            "keep_original_dts_x",
            "original_as_secondary",
            "prefer_ac3",
            "ac3_bitrate",
            "eac3_bitrate",
            "aac_surround_bitrate",
            "aac_stereo_bitrate",
        ):
            self._raw_config["audio"][key] = getattr(self.audio, key)

        self._raw_config.setdefault("video", {})
        for key in (
            "enabled",
            "codec",
            "convert_10bit_x264",
            "convert_8bit_x264",
            "anime_only",
            "anime_auto_detect",
            "anime_crf",
            "anime_preset",
            "anime_tune",
            "anime_framerate",
            "live_action_crf",
            "live_action_preset",
            "live_action_tune",
            "live_action_framerate",
            "av1_anime_crf",
            "av1_anime_preset",
            "av1_anime_framerate",
            "av1_live_action_crf",
            "av1_live_action_preset",
            "av1_live_action_framerate",
            "vbv_maxrate",
            "vbv_bufsize",
            "level",
            "profile",
            "pix_fmt",
            "hw_accel",
        ):
            self._raw_config["video"][key] = getattr(self.video, key)

        self._raw_config.setdefault("cleanup", {})
        for key in (
            "enabled",
            "anime_only",
            "clean_audio",
            "clean_subtitles",
            "keep_languages",
            "keep_undefined",
            "keep_commentary",
            "keep_audio_description",
            "keep_sdh",
            "anime_keep_original_audio",
        ):
            self._raw_config["cleanup"][key] = getattr(self.cleanup, key)

        self._raw_config.setdefault("sonarr", {})
        for key in ("enabled", "url", "api_key"):
            self._raw_config["sonarr"][key] = getattr(self.sonarr, key)

        self._raw_config.setdefault("radarr", {})
        for key in ("enabled", "url", "api_key"):
            self._raw_config["radarr"][key] = getattr(self.radarr, key)

        self._raw_config.setdefault("processing", {})
        self._raw_config["processing"]["max_concurrent_jobs"] = self.workers
        self._raw_config["processing"]["ffmpeg_threads"] = self.ffmpeg_threads

        self._raw_config.setdefault("general", {})
        self._raw_config["general"]["job_history_days"] = self.job_history_days

        with self.config_path.open("w") as f:
            yaml.dump(self._raw_config, f, default_flow_style=False, sort_keys=False)
        logger.info("Configuration saved to %s", self.config_path)


# Global configuration instance
_config: Config | None = None


def get_config(config_path: str | None = None) -> Config:
    """Get or create global configuration instance."""
    global _config
    if _config is None:
        _config = Config(config_path)
    return _config
