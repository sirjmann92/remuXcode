"""Configuration API routes - view and update settings."""

import logging
import re
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from backend import core
from backend.auth import get_api_key, regenerate_api_key

logger = logging.getLogger("remuxcode")

router = APIRouter(tags=["config"])


@router.get("/config")
async def get_config_summary() -> dict[str, Any]:
    """Get current configuration summary."""
    if not core.config:
        raise HTTPException(status_code=503, detail="Service not ready")

    cfg = core.config
    return {
        "video": {
            "enabled": cfg.video.enabled,
            "codec": cfg.video.codec,
            "convert_10bit_x264": cfg.video.convert_10bit_x264,
            "convert_8bit_x264": cfg.video.convert_8bit_x264,
            "anime_only": cfg.video.anime_only,
            "anime_crf": cfg.video.anime_crf,
            "live_action_crf": cfg.video.live_action_crf,
            # Advanced
            "anime_auto_detect": cfg.video.anime_auto_detect,
            "anime_preset": cfg.video.anime_preset,
            "anime_tune": cfg.video.anime_tune or "",
            "anime_framerate": cfg.video.anime_framerate,
            "live_action_preset": cfg.video.live_action_preset,
            "live_action_tune": cfg.video.live_action_tune or "",
            "live_action_framerate": cfg.video.live_action_framerate,
            "av1_anime_crf": cfg.video.av1_anime_crf,
            "av1_anime_preset": cfg.video.av1_anime_preset,
            "av1_anime_framerate": cfg.video.av1_anime_framerate,
            "av1_live_action_crf": cfg.video.av1_live_action_crf,
            "av1_live_action_preset": cfg.video.av1_live_action_preset,
            "av1_live_action_framerate": cfg.video.av1_live_action_framerate,
            "vbv_maxrate": cfg.video.vbv_maxrate,
            "vbv_bufsize": cfg.video.vbv_bufsize,
            "level": cfg.video.level,
            "profile": cfg.video.profile,
            "pix_fmt": cfg.video.pix_fmt,
        },
        "audio": {
            "enabled": cfg.audio.enabled,
            "anime_only": cfg.audio.anime_only,
            "convert_dts": cfg.audio.convert_dts,
            "convert_dts_x": cfg.audio.convert_dts_x,
            "convert_truehd": cfg.audio.convert_truehd,
            "keep_original": cfg.audio.keep_original,
            "keep_original_dts_x": cfg.audio.keep_original_dts_x,
            "original_as_secondary": cfg.audio.original_as_secondary,
            "prefer_ac3": cfg.audio.prefer_ac3,
            # Advanced
            "ac3_bitrate": cfg.audio.ac3_bitrate,
            "eac3_bitrate": cfg.audio.eac3_bitrate,
            "aac_surround_bitrate": cfg.audio.aac_surround_bitrate,
            "aac_stereo_bitrate": cfg.audio.aac_stereo_bitrate,
        },
        "cleanup": {
            "enabled": cfg.cleanup.enabled,
            "anime_only": cfg.cleanup.anime_only,
            "clean_audio": cfg.cleanup.clean_audio,
            "clean_subtitles": cfg.cleanup.clean_subtitles,
            "keep_languages": cfg.cleanup.keep_languages,
            "keep_commentary": cfg.cleanup.keep_commentary,
            "anime_keep_original_audio": cfg.cleanup.anime_keep_original_audio,
            # Advanced
            "keep_undefined": cfg.cleanup.keep_undefined,
            "keep_audio_description": cfg.cleanup.keep_audio_description,
            "keep_sdh": cfg.cleanup.keep_sdh,
        },
        "sonarr": {
            "configured": bool(cfg.sonarr.url and cfg.sonarr.api_key),
            "url": cfg.sonarr.url,
            "api_key": cfg.sonarr.api_key,
        },
        "radarr": {
            "configured": bool(cfg.radarr.url and cfg.radarr.api_key),
            "url": cfg.radarr.url,
            "api_key": cfg.radarr.api_key,
        },
        "path_mappings": [{"container": c, "host": h} for c, h in core.PATH_MAPPINGS],
        "workers": cfg.workers,
        "job_history_days": cfg.job_history_days,
        "api_key": get_api_key(),
    }


@router.post("/config/api-key/regenerate")
def regenerate_key() -> dict[str, str]:
    """Generate a new API key, persist it, and return the new value."""
    new_key = regenerate_api_key()
    return {"api_key": new_key}


@router.post("/config/refresh/sonarr")
def refresh_sonarr_library() -> dict[str, str]:
    """Trigger a full Sonarr library refresh."""
    try:
        core.refresh_sonarr()
        return {"message": "Sonarr library refresh completed"}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Sonarr refresh failed: {e}") from e


@router.post("/config/refresh/radarr")
def refresh_radarr_library() -> dict[str, str]:
    """Trigger a full Radarr library refresh."""
    try:
        core.refresh_radarr()
        return {"message": "Radarr library refresh completed"}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Radarr refresh failed: {e}") from e


class AudioUpdate(BaseModel):
    """Partial audio config update."""

    enabled: bool | None = None
    anime_only: bool | None = None
    convert_dts: bool | None = None
    convert_dts_x: bool | None = None
    convert_truehd: bool | None = None
    keep_original: bool | None = None
    keep_original_dts_x: bool | None = None
    original_as_secondary: bool | None = None
    prefer_ac3: bool | None = None
    ac3_bitrate: int | None = Field(None, ge=64, le=6144)
    eac3_bitrate: int | None = Field(None, ge=64, le=6144)
    aac_surround_bitrate: int | None = Field(None, ge=64, le=6144)
    aac_stereo_bitrate: int | None = Field(None, ge=64, le=6144)


class VideoUpdate(BaseModel):
    """Partial video config update."""

    enabled: bool | None = None
    codec: Literal["hevc", "av1"] | None = None
    convert_10bit_x264: bool | None = None
    convert_8bit_x264: bool | None = None
    anime_only: bool | None = None
    anime_auto_detect: bool | None = None
    anime_crf: int | None = Field(None, ge=0, le=51)
    anime_preset: str | None = None
    anime_tune: str | None = None
    anime_framerate: str | None = None
    live_action_crf: int | None = Field(None, ge=0, le=51)
    live_action_preset: str | None = None
    live_action_tune: str | None = None
    live_action_framerate: str | None = None
    av1_anime_crf: int | None = Field(None, ge=0, le=63)
    av1_anime_preset: int | None = Field(None, ge=0, le=13)
    av1_anime_framerate: str | None = None
    av1_live_action_crf: int | None = Field(None, ge=0, le=63)
    av1_live_action_preset: int | None = Field(None, ge=0, le=13)
    av1_live_action_framerate: str | None = None
    vbv_maxrate: int | None = Field(None, ge=0, le=100000)
    vbv_bufsize: int | None = Field(None, ge=0, le=200000)
    level: str | None = None
    profile: str | None = None
    pix_fmt: str | None = None


class CleanupUpdate(BaseModel):
    """Partial cleanup config update."""

    enabled: bool | None = None
    anime_only: bool | None = None
    clean_audio: bool | None = None
    clean_subtitles: bool | None = None
    keep_languages: list[str] | None = None
    keep_undefined: bool | None = None
    keep_commentary: bool | None = None
    keep_audio_description: bool | None = None
    keep_sdh: bool | None = None
    anime_keep_original_audio: bool | None = None

    @field_validator("keep_languages")
    @classmethod
    def validate_languages(cls, v: list[str] | None) -> list[str] | None:
        """Ensure language codes are valid ISO 639 format."""
        if v is None:
            return v
        validated = []
        for lang in v:
            lang = lang.strip().lower()
            if not re.match(r"^[a-z]{2,3}$", lang):
                msg = f"Invalid language code: {lang!r} (expected 2-3 letter ISO 639 code)"
                raise ValueError(msg)
            validated.append(lang)
        if not validated:
            msg = "At least one language code is required"
            raise ValueError(msg)
        return validated


class SonarrUpdate(BaseModel):
    """Partial sonarr config update."""

    url: str | None = None
    api_key: str | None = None


class RadarrUpdate(BaseModel):
    """Partial radarr config update."""

    url: str | None = None
    api_key: str | None = None


class ConfigUpdate(BaseModel):
    """Partial config update payload."""

    audio: AudioUpdate | None = None
    video: VideoUpdate | None = None
    cleanup: CleanupUpdate | None = None
    sonarr: SonarrUpdate | None = None
    radarr: RadarrUpdate | None = None
    workers: int | None = Field(None, ge=1, le=16)
    job_history_days: int | None = Field(None, ge=1, le=365)


@router.patch("/config")
async def update_config(body: ConfigUpdate) -> dict[str, str]:
    """Update configuration values in memory and persist to YAML."""
    if not core.config:
        raise HTTPException(status_code=503, detail="Service not ready")

    cfg = core.config

    if body.audio:
        for field, val in body.audio.model_dump(exclude_none=True).items():
            setattr(cfg.audio, field, val)

    if body.video:
        for field, val in body.video.model_dump(exclude_none=True).items():
            setattr(cfg.video, field, val)

    if body.sonarr:
        for field, val in body.sonarr.model_dump(exclude_none=True).items():
            setattr(cfg.sonarr, field, val)

    if body.radarr:
        for field, val in body.radarr.model_dump(exclude_none=True).items():
            setattr(cfg.radarr, field, val)

    if body.workers is not None:
        cfg.workers = body.workers

    if body.job_history_days is not None:
        cfg.job_history_days = body.job_history_days

    if body.cleanup:
        for field, val in body.cleanup.model_dump(exclude_none=True).items():
            setattr(cfg.cleanup, field, val)

    try:
        cfg.save()
    except Exception as e:
        logger.error("Failed to save config: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to persist config: {e}") from e

    # Update detector instances with new Sonarr/Radarr config
    if body.sonarr or body.radarr:
        core.update_integration_config()

    return {"message": "Configuration updated"}
