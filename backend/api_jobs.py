"""Job management API routes."""

import calendar
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from backend import core

logger = logging.getLogger("remuxcode")

router = APIRouter(tags=["jobs"])


def _parse_date_param(date_str: str) -> float:
    """Parse a YYYY-MM-DD date string to a UNIX timestamp (start of day UTC)."""
    import time

    return float(calendar.timegm(time.strptime(date_str, "%Y-%m-%d")))


@router.get("/jobs")
async def list_jobs(
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None),
    search: str | None = Query(default=None),
    job_type: str | None = Query(default=None),
    phase: str | None = Query(default=None),
    media_type: str | None = Query(default=None),
    source: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
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

    if job_type and job_type != "all":
        filtered = [j for j in filtered if j.job_type.value == job_type]

    if phase and phase != "all":
        filtered = [
            j
            for j in filtered
            if (j.completed_phases and phase in j.completed_phases)
            or (j.result and j.result.get(phase))
        ]

    if media_type and media_type != "all":
        filtered = [j for j in filtered if j.media_type == media_type]

    if source and source != "all":
        filtered = [j for j in filtered if j.source == source]

    if date_from:
        try:
            from_ts = _parse_date_param(date_from)
            filtered = [j for j in filtered if j.created_at >= from_ts]
        except ValueError:
            pass

    if date_to:
        try:
            # End of the specified day
            to_ts = _parse_date_param(date_to) + 86400
            filtered = [j for j in filtered if j.created_at < to_ts]
        except ValueError:
            pass

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


@router.post("/jobs/cancel-running")
async def cancel_running_jobs() -> dict[str, Any]:
    """Cancel only currently running jobs (kills ffmpeg)."""
    if not core.job_queue:
        raise HTTPException(status_code=503, detail="Service not ready")

    cancelled = 0
    for job in core.job_queue.get_all_jobs():
        if job.status.value == "running":
            if core.job_queue.cancel_job(job.id):
                cancelled += 1

    return {"message": f"Cancelled {cancelled} running job(s)", "cancelled": cancelled}


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


@router.delete("/jobs/finished")
async def delete_finished_jobs() -> dict[str, Any]:
    """Permanently delete all completed, failed, and cancelled jobs from the database."""
    if not core.job_queue:
        raise HTTPException(status_code=503, detail="Service not ready")

    deleted = 0
    for job in core.job_queue.get_all_jobs():
        if job.status.value in ("completed", "failed", "cancelled"):
            if core.job_queue.delete_job(job.id):
                deleted += 1

    return {"message": f"Deleted {deleted} finished job(s)", "deleted": deleted}
