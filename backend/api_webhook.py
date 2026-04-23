"""Webhook endpoint for Sonarr/Radarr integration."""

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends

from backend.auth import require_auth
from backend.core import JobType, create_job, translate_path

logger = logging.getLogger("remuxcode")

router = APIRouter(tags=["webhook"], dependencies=[Depends(require_auth)])


@router.post("/webhook")
async def handle_webhook(data: dict[str, Any]) -> dict[str, Any]:
    """Handle Sonarr/Radarr webhook."""
    event_type = data.get("eventType", "")
    logger.info("Webhook received: eventType=%s", event_type)
    logger.debug("Webhook payload: %s", data)

    # Test event fired when clicking "Test" in Sonarr/Radarr webhook settings
    if event_type == "Test":
        return {"status": "ok"}

    # Extract file paths based on payload shape
    raw_paths: list[str | None]
    if "movie" in data:
        # Radarr: single movie file per event
        raw_paths = [data.get("movieFile", {}).get("path")]
    elif "episodes" in data:
        # Sonarr: episodes[] carries only metadata (episode numbers, titles, air dates).
        # File paths live in episodeFiles[] — a plural array at the top level — for all
        # event types (On File Import, On Import Complete, On File Upgrade).
        # See docs/sonarr-webhook-reference.md for confirmed payload structure.
        if data.get("episodeFiles"):
            raw_paths = [f.get("path") for f in data["episodeFiles"]]
        else:
            # Fallback: older Sonarr versions may use singular episodeFile
            raw_paths = [data.get("episodeFile", {}).get("path")]
    else:
        logger.warning("Unrecognised webhook payload (eventType=%s): %s", event_type, data)
        return {"error": "Unknown webhook format"}

    # Filter nulls and translate paths
    files = [translate_path(f) for f in raw_paths if f]

    if not files:
        logger.warning(
            "Webhook eventType=%s matched but no file paths extracted; raw_paths=%s",
            event_type,
            raw_paths,
        )
        return {"message": "No files to process"}

    # Extract poster URL from payload for job display
    poster_url: str | None = None
    if "movie" in data:
        movie_id = data.get("movie", {}).get("id")
        if movie_id:
            poster_url = f"/api/poster/radarr/{movie_id}"
    elif "series" in data:
        series_id = data.get("series", {}).get("id")
        if series_id:
            poster_url = f"/api/poster/sonarr/{series_id}"

    # Determine media type from payload shape
    media_type = "movie" if "movie" in data else "episode" if "episodes" in data else None

    # Queue jobs
    job_ids = []
    for file_path in files:
        if Path(file_path).exists():
            job = create_job(
                file_path,
                JobType.FULL,
                source="webhook",
                poster_url=poster_url,
                media_type=media_type,
            )
            job_ids.append(job.id)
        else:
            logger.warning("Webhook file not found on disk: %s", file_path)

    return {
        "message": f"Queued {len(job_ids)} file(s)",
        "job_ids": job_ids,
    }
