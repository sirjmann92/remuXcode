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
        # Sonarr: eventType "Download" for both single imports and batch
        # (On Import Complete fires once for a full batch with all episodes together)
        # Each episode in the array carries its own episodeFile
        raw_paths = [ep.get("episodeFile", {}).get("path") for ep in data.get("episodes", [])]
    else:
        logger.warning("Unrecognised webhook payload (eventType=%s): %s", event_type, data)
        return {"error": "Unknown webhook format"}

    # Filter nulls and translate paths
    files = [translate_path(f) for f in raw_paths if f]

    if not files:
        return {"message": "No files to process"}

    # Queue jobs
    job_ids = []
    for file_path in files:
        if Path(file_path).exists():
            job = create_job(file_path, JobType.FULL, source="webhook")
            job_ids.append(job.id)
            logger.info("Queued job %s for %s", job.id, Path(file_path).name)
        else:
            logger.warning("Webhook file not found on disk: %s", file_path)

    return {
        "message": f"Queued {len(job_ids)} file(s)",
        "job_ids": job_ids,
    }
