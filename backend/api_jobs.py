"""Job management API routes."""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from backend import core

logger = logging.getLogger("remuxcode")

router = APIRouter(tags=["jobs"])


@router.get("/jobs")
async def list_jobs() -> dict[str, Any]:
    """List all jobs."""
    if not core.job_queue:
        return {"jobs": []}
    jobs = [j.to_dict() for j in core.job_queue.get_all_jobs()]
    return {"jobs": jobs}


@router.get("/jobs/active")
async def active_jobs() -> dict[str, Any]:
    """Return a mapping of file_path → {status, progress, job_id} for pending/running jobs."""
    if not core.job_queue:
        return {"active": {}}
    active: dict[str, dict[str, Any]] = {}
    for job in core.job_queue.get_all_jobs():
        if job.status.value in ("pending", "running"):
            active[job.file_path] = {
                "job_id": job.id,
                "status": job.status.value,
                "progress": job.progress,
            }
    return {"active": active}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    """Get job by ID."""
    if not core.job_queue:
        raise HTTPException(status_code=503, detail="Service not ready")
    job = core.job_queue.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_dict()


@router.delete("/jobs/{job_id}")
async def cancel_or_delete_job(job_id: str) -> dict[str, Any]:
    """Cancel a pending/running job or delete a completed/failed job."""
    if not core.job_queue:
        raise HTTPException(status_code=503, detail="Service not ready")

    # Try to cancel first
    if core.job_queue.cancel_job(job_id):
        return {"message": "Job cancelled", "job_id": job_id}

    # Try to delete completed/failed job
    if core.job_queue.delete_job(job_id):
        return {"message": "Job deleted", "job_id": job_id}

    job = core.job_queue.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    raise HTTPException(status_code=400, detail="Cannot delete running job")
