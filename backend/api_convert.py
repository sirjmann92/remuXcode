"""Convert API routes - single file and batch conversion."""

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
import requests

from backend import core
from backend.core import JobType, create_job, translate_path

logger = logging.getLogger("remuxcode")

router = APIRouter(tags=["convert"])


@router.post("/convert")
async def convert_file(data: dict[str, Any]) -> dict[str, Any]:
    """Queue conversion of a single file."""
    file_path = data.get("path")
    if not file_path:
        raise HTTPException(status_code=400, detail="Missing path")

    file_path = translate_path(file_path)
    job_type_str = data.get("type", "full")
    try:
        job_type = JobType(job_type_str)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid type: {job_type_str}") from None

    job = create_job(file_path, job_type, source="api", poster_url=data.get("poster_url"), media_type=data.get("media_type"))
    return {
        "message": "Job queued",
        "job_id": job.id,
        "status_url": f"/api/jobs/{job.id}",
    }


@router.post("/convert/movies")
def batch_convert_movies(data: dict[str, Any]) -> dict[str, Any]:
    """Batch convert movies by Radarr movie IDs."""
    movie_ids = data.get("movie_ids", [])
    if not movie_ids:
        raise HTTPException(status_code=400, detail="Missing movie_ids")

    radarr_url = core.config.radarr.url if core.config else ""
    radarr_key = core.config.radarr.api_key if core.config else ""
    if not radarr_url or not radarr_key:
        raise HTTPException(status_code=500, detail="Radarr not configured")

    job_type_str = data.get("type", "full")
    try:
        job_type = JobType(job_type_str)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid type: {job_type_str}") from None

    job_ids: list[str] = []
    for movie_id in movie_ids:
        try:
            resp = requests.get(
                f"{radarr_url}/api/v3/movie/{movie_id}",
                headers={"X-Api-Key": radarr_key},
                timeout=30,
            )
            if resp.status_code != 200:
                continue
            movie = resp.json()
            file_path = movie.get("movieFile", {}).get("path")
            if file_path:
                file_path = translate_path(file_path)
                if Path(file_path).exists():
                    job = create_job(file_path, job_type, source="batch", media_type="movie")
                    job_ids.append(job.id)
        except Exception as e:
            logger.error("Error getting movie %s: %s", movie_id, e)

    return {
        "message": f"Queued {len(job_ids)} movie(s)",
        "job_ids": job_ids,
    }


@router.post("/convert/series")
def batch_convert_series(data: dict[str, Any]) -> dict[str, Any]:
    """Batch convert series episodes by Sonarr series IDs."""
    series_ids = data.get("series_ids", [])
    if not series_ids:
        raise HTTPException(status_code=400, detail="Missing series_ids")

    sonarr_url = core.config.sonarr.url if core.config else ""
    sonarr_key = core.config.sonarr.api_key if core.config else ""
    if not sonarr_url or not sonarr_key:
        raise HTTPException(status_code=500, detail="Sonarr not configured")

    job_type_str = data.get("type", "full")
    try:
        job_type = JobType(job_type_str)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid type: {job_type_str}") from None

    season_number = data.get("season_number")

    job_ids: list[str] = []
    for series_id in series_ids:
        try:
            resp = requests.get(
                f"{sonarr_url}/api/v3/episodefile",
                params={"seriesId": series_id},
                headers={"X-Api-Key": sonarr_key},
                timeout=30,
            )
            if resp.status_code != 200:
                continue
            for ep_file in resp.json():
                if season_number is not None:
                    ep_season = ep_file.get("seasonNumber")
                    if ep_season != season_number:
                        continue
                file_path = ep_file.get("path")
                if file_path:
                    file_path = translate_path(file_path)
                    if Path(file_path).exists():
                        job = create_job(file_path, job_type, source="batch", media_type="episode")
                        job_ids.append(job.id)
        except Exception as e:
            logger.error("Error getting series %s: %s", series_id, e)

    return {
        "message": f"Queued {len(job_ids)} episode(s)",
        "job_ids": job_ids,
    }
