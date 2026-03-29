"""Configuration API routes - view and update settings."""

import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException

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
        },
        "audio": {
            "enabled": cfg.audio.enabled,
            "convert_dts": cfg.audio.convert_dts,
            "convert_truehd": cfg.audio.convert_truehd,
            "keep_original": cfg.audio.keep_original,
            "prefer_ac3": cfg.audio.prefer_ac3,
        },
        "cleanup": {
            "enabled": cfg.cleanup.enabled,
            "clean_audio": cfg.cleanup.clean_audio,
            "clean_subtitles": cfg.cleanup.clean_subtitles,
            "keep_languages": cfg.cleanup.keep_languages,
            "keep_commentary": cfg.cleanup.keep_commentary,
        },
        "sonarr": {
            "configured": bool(
                os.getenv("SONARR_URL", cfg.sonarr.url)
                and os.getenv("SONARR_API_KEY", cfg.sonarr.api_key)
            ),
            "url": os.getenv("SONARR_URL", cfg.sonarr.url),
        },
        "radarr": {
            "configured": bool(
                os.getenv("RADARR_URL", cfg.radarr.url)
                and os.getenv("RADARR_API_KEY", cfg.radarr.api_key)
            ),
            "url": os.getenv("RADARR_URL", cfg.radarr.url),
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
