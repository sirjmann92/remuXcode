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
    # Determine source and extract file paths
    if "movie" in data:
        files = [data.get("movieFile", {}).get("path")]
    elif "episodes" in data:
        files = [ep.get("episodeFile", {}).get("path") for ep in data.get("episodes", [])]
    else:
        return {"error": "Unknown webhook format"}

    # Filter and translate paths
    files = [translate_path(f) for f in files if f]

    if not files:
        return {"message": "No files to process"}

    # Queue jobs
    job_ids = []
    for file_path in files:
        if Path(file_path).exists():
            job = create_job(file_path, JobType.FULL, source="webhook")
            job_ids.append(job.id)

    return {
        "message": f"Queued {len(job_ids)} file(s)",
        "job_ids": job_ids,
    }
