"""Job management API routes."""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from backend import core

logger = logging.getLogger("remuxcode")

router = APIRouter(tags=["jobs"])


@router.get("/jobs")
async def list_jobs(
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None),
    search: str | None = Query(default=None),
) -> dict[str, Any]:
    """List jobs with optional paging, filtering, and search.

    Returns `jobs` for backwards compatibility and includes metadata when
    paging/filter options are used.
    """
    if not core.job_queue:
        return {"jobs": []}

    all_jobs = core.job_queue.get_all_jobs()

    counts: dict[str, int] = {
        "all": len(all_jobs),
        "pending": 0,
        "running": 0,
        "completed": 0,
        "failed": 0,
        "cancelled": 0,
    }
    for job in all_jobs:
        if job.status.value in counts:
            counts[job.status.value] += 1

    filtered = all_jobs
    if status and status != "all":
        filtered = [j for j in filtered if j.status.value == status]

    if search:
        needle = search.lower()
        filtered = [j for j in filtered if needle in j.file_path.lower()]

    status_order = {
        "running": 0,
        "pending": 1,
        "completed": 2,
        "failed": 3,
        "cancelled": 4,
    }
    filtered.sort(
        key=lambda j: (
            status_order.get(j.status.value, 9),
            -(j.created_at or 0),
        )
    )

    total = len(filtered)
    if limit is not None:
        page = filtered[offset : offset + limit]
    else:
        page = filtered

    jobs = [j.to_dict() for j in page]
    has_more = limit is not None and (offset + len(page)) < total

    return {
        "jobs": jobs,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": has_more,
        "counts": counts,
    }


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

    # Try to cancel first (works for pending and running)
    if core.job_queue.cancel_job(job_id):
        return {"message": "Job cancelled", "job_id": job_id}

    # Try to delete completed/failed job
    if core.job_queue.delete_job(job_id):
        return {"message": "Job deleted", "job_id": job_id}

    job = core.job_queue.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    raise HTTPException(status_code=400, detail="Cannot modify job in current state")


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str) -> dict[str, Any]:
    """Cancel a pending or running job (kills ffmpeg if running)."""
    if not core.job_queue:
        raise HTTPException(status_code=503, detail="Service not ready")

    if core.job_queue.cancel_job(job_id):
        return {"message": "Job cancelled", "job_id": job_id}

    job = core.job_queue.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    raise HTTPException(
        status_code=400,
        detail=f"Cannot cancel job with status: {job.status.value}",
    )


@router.post("/jobs/cancel-pending")
async def cancel_all_pending() -> dict[str, Any]:
    """Cancel all pending jobs in the queue."""
    if not core.job_queue:
        raise HTTPException(status_code=503, detail="Service not ready")

    cancelled = 0
    for job in core.job_queue.get_all_jobs():
        if job.status.value == "pending":
            if core.job_queue.cancel_job(job.id):
                cancelled += 1

    return {"message": f"Cancelled {cancelled} pending job(s)", "cancelled": cancelled}


@router.post("/jobs/cancel-all")
async def cancel_all_jobs() -> dict[str, Any]:
    """Cancel all pending and running jobs (kills ffmpeg for running jobs)."""
    if not core.job_queue:
        raise HTTPException(status_code=503, detail="Service not ready")

    cancelled = 0
    for job in core.job_queue.get_all_jobs():
        if job.status.value in ("pending", "running"):
            if core.job_queue.cancel_job(job.id):
                cancelled += 1

    return {"message": f"Cancelled {cancelled} job(s)", "cancelled": cancelled}
