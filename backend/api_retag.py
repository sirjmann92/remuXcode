"""Retag API route — fix track language/title metadata without transcoding."""

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from backend.core import JobType, create_job, translate_path

logger = logging.getLogger("remuxcode")

router = APIRouter(tags=["retag"])


@router.post("/retag")
async def retag_file(data: dict[str, Any]) -> dict[str, Any]:
    """Queue a retag job to correct track language/title metadata.

    Expected body::

        {
            "path": "/media/Shows/Show/S01/E01.mkv",
            "overrides": [
                {"track_type": "audio", "track_index": 0, "language": "eng", "title": "English"},
                {
                    "track_type": "subtitle",
                    "track_index": 0,
                    "language": "eng",
                    "title": "English (SDH)",
                },
            ],
        }

    ``track_index`` is 0-based within each track type.
    """
    file_path = data.get("path")
    if not file_path:
        raise HTTPException(status_code=400, detail="Missing path")

    overrides = data.get("overrides")
    if not overrides or not isinstance(overrides, list):
        raise HTTPException(status_code=400, detail="Missing or invalid overrides list")

    # Validate each override entry
    for i, ov in enumerate(overrides):
        if not isinstance(ov, dict):
            raise HTTPException(status_code=400, detail=f"Override {i} must be an object")
        if ov.get("track_type") not in ("audio", "subtitle"):
            raise HTTPException(
                status_code=400,
                detail=f"Override {i}: track_type must be 'audio' or 'subtitle'",
            )
        if not isinstance(ov.get("track_index"), int):
            raise HTTPException(
                status_code=400,
                detail=f"Override {i}: track_index must be an integer",
            )
        if ov.get("language") is None and ov.get("title") is None:
            raise HTTPException(
                status_code=400,
                detail=f"Override {i}: must set at least one of language or title",
            )

    file_path = translate_path(file_path)
    if not Path(file_path).exists():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

    job = create_job(
        file_path,
        JobType.RETAG,
        source="api",
        encode_options={"overrides": overrides},
    )
    return {
        "message": "Retag job queued",
        "job_id": job.id,
        "status_url": f"/api/jobs/{job.id}",
    }
