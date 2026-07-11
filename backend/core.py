"""Core processing logic for remuXcode.

Contains the job queue, file processing pipeline, path translation,
component initialization, and Sonarr/Radarr integration.
"""

from collections.abc import Callable
import contextlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import json as _json
import logging
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
from typing import Any
import uuid

import requests

from backend.utils.anime_detect import AnimeDetector
from backend.utils.config import Config, get_config
from backend.utils.ffprobe import FFProbe
from backend.utils.job_store import JobStore
from backend.utils.language import LanguageDetector
from backend.utils.media_store import MediaStore
from backend.workers.audio import AudioConverter
from backend.workers.cleanup import StreamCleanup
from backend.workers.video import VideoConverter

logger = logging.getLogger("remuxcode")

# ---------------------------------------------------------------------------
# Configuration loaded from environment
# ---------------------------------------------------------------------------

CONFIG_PATH = os.getenv(
    "REMUXCODE_CONFIG_PATH",
    os.getenv("MEDIA_CONFIG_PATH", str(Path(__file__).parent / "config.yaml")),
)

# API key (set during initialization, auto-generated if not configured)
api_key: str = ""

# Path mappings (container path → host path)
PATH_MAPPINGS: list[tuple[str, str]] = []


def _load_path_mappings() -> list[tuple[str, str]]:
    mappings: list[tuple[str, str]] = []
    for key in sorted(os.environ.keys()):
        if key.startswith("PATH_MAPPING_") and key.endswith("_CONTAINER"):
            index = key.replace("PATH_MAPPING_", "").replace("_CONTAINER", "")
            container_path = os.getenv(f"PATH_MAPPING_{index}_CONTAINER")
            host_path = os.getenv(f"PATH_MAPPING_{index}_HOST")
            if container_path and host_path:
                mappings.append((container_path, host_path))
    mappings.sort(key=lambda x: len(x[0]), reverse=True)
    return mappings


# ---------------------------------------------------------------------------
# Enums & Data Classes
# ---------------------------------------------------------------------------


class JobType(Enum):
    """Job conversion type."""

    AUDIO = "audio"
    VIDEO = "video"
    CLEANUP = "cleanup"
    FULL = "full"
    RETAG = "retag"


class JobStatus(Enum):
    """Job execution status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class ConversionJob:
    """Represents a single transcoding job."""

    id: str
    job_type: JobType
    file_path: str
    status: JobStatus
    created_at: float
    started_at: float | None = None
    completed_at: float | None = None
    progress: float = 0.0
    result: dict[str, Any] | None = None
    error: str | None = None
    source: str = "webhook"
    cancel_event: threading.Event | None = None
    last_progress_at: float | None = None
    current_phase: str | None = None
    status_detail: str | None = None
    completed_phases: list[str] | None = None
    planned_phases: list[str] | None = None
    poster_url: str | None = None
    media_type: str | None = None
    output_path: str | None = None
    encode_options: dict[str, Any] | None = None
    log_lines: list[dict[str, Any]] = field(default_factory=list)

    def log(self, source: str, level: str, message: str) -> None:
        """Append a timestamped log entry. Keeps the last 500 entries."""
        self.log_lines.append(
            {"ts": time.time(), "source": source, "level": level, "message": message}
        )
        if len(self.log_lines) > 600:
            # Preserve all non-stats entries; fill remaining slots with recent stats
            non_stats = [
                e for e in self.log_lines if not (e["source"] == "ffmpeg" and e["level"] == "stats")
            ]
            stats_only = [
                e for e in self.log_lines if e["source"] == "ffmpeg" and e["level"] == "stats"
            ]
            keep_stats = max(0, 500 - len(non_stats))
            self.log_lines = non_stats + stats_only[-keep_stats:] if keep_stats > 0 else non_stats

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for API responses."""
        return {
            "id": self.id,
            "job_type": self.job_type.value,
            "file_path": self.file_path,
            "status": self.status.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "progress": self.progress,
            "result": self.result,
            "error": self.error,
            "source": self.source,
            "current_phase": self.current_phase,
            "status_detail": self.status_detail,
            "completed_phases": self.completed_phases,
            "planned_phases": self.planned_phases,
            "poster_url": self.poster_url,
            "media_type": self.media_type,
            "output_path": self.output_path,
            "encode_options": self.encode_options,
        }


# ---------------------------------------------------------------------------
# Job Queue
# ---------------------------------------------------------------------------


class JobQueue:
    """Thread-safe job queue with status tracking and persistent storage."""

    def __init__(self, max_workers: int = 1, job_store: JobStore | None = None) -> None:
        """Initialize the job queue with max_workers worker threads."""
        self.jobs: dict[str, ConversionJob] = {}
        self.pending_queue: list[str] = []
        self.lock = threading.Lock()
        self.workers: list[threading.Thread] = []
        self.running = False
        self.max_workers = max_workers
        self.job_store = job_store
        self._watchdog_thread: threading.Thread | None = None
        # Stale job timeout in seconds (no progress update for this long → fail)
        self.stale_timeout: int = 15 * 60  # 15 minutes

    def start(self) -> None:
        """Start worker threads."""
        self.running = True
        for i in range(self.max_workers):
            worker = threading.Thread(target=self._worker_loop, name=f"Worker-{i}", daemon=True)
            worker.start()
            self.workers.append(worker)
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop, name="Watchdog", daemon=True
        )
        self._watchdog_thread.start()
        logger.info("Started %d job worker(s) + watchdog", self.max_workers)

    def scale_workers(self, new_count: int) -> None:
        """Dynamically adjust the number of worker threads.

        Scale up: spawn additional threads immediately.
        Scale down: excess threads self-exit after finishing their current job.
        """
        with self.lock:
            current_alive = sum(1 for w in self.workers if w.is_alive())
            self.max_workers = new_count
            if new_count > current_alive:
                for i in range(new_count - current_alive):
                    worker = threading.Thread(
                        target=self._worker_loop,
                        name=f"Worker-{current_alive + i}",
                        daemon=True,
                    )
                    worker.start()
                    self.workers.append(worker)
                logger.info(
                    "Scaled workers: %d → %d (spawned %d)",
                    current_alive,
                    new_count,
                    new_count - current_alive,
                )
            elif new_count < current_alive:
                logger.info(
                    "Scaled workers: %d → %d (excess will exit when idle)",
                    current_alive,
                    new_count,
                )
            # Scale-down is handled in _worker_loop via max_workers check

    def stop(self) -> None:
        """Stop all worker threads."""
        self.running = False
        for worker in self.workers:
            worker.join(timeout=5)
        if self._watchdog_thread:
            self._watchdog_thread.join(timeout=5)

    def add_job(self, job: ConversionJob) -> str:
        """Add a job to the queue and return its ID."""
        with self.lock:
            self.jobs[job.id] = job
            self.pending_queue.append(job.id)
            queue_pos = len(self.pending_queue) - 1
        if self.job_store:
            self._save_job_to_store(job)
            self.job_store.update_queue_position(job.id, queue_pos)
        logger.info(
            "Queued job %s: %s for %s", job.id, job.job_type.value, Path(job.file_path).name
        )
        return job.id

    def get_job(self, job_id: str) -> ConversionJob | None:
        """Return a job by ID, or None if not found."""
        return self.jobs.get(job_id)

    def get_all_jobs(self) -> list[ConversionJob]:
        """Return all jobs."""
        with self.lock:
            return list(self.jobs.values())

    def cancel_job(self, job_id: str) -> bool:
        """Cancel a pending or running job."""
        job = self.jobs.get(job_id)
        if not job:
            return False
        if job.status not in (JobStatus.PENDING, JobStatus.RUNNING):
            return False
        job.status = JobStatus.CANCELLED
        job.completed_at = time.time()
        job.error = "Cancelled by user"
        # Signal the cancel event to kill any running ffmpeg subprocess
        if job.cancel_event:
            job.cancel_event.set()
        if self.job_store:
            self._save_job_to_store(job)
        logger.info("Cancelled job %s", job_id)
        return True

    def reorder_queue(self, ordered_ids: list[str]) -> bool:
        """Reorder pending jobs.

        ``ordered_ids`` may be a subset of the current pending queue (e.g. the
        UI only loaded the first 25 jobs, or a job transitioned to running
        between the last poll and the drop).  IDs that are present in the
        queue but absent from ``ordered_ids`` are appended at the end in their
        current relative order.  IDs in ``ordered_ids`` that are no longer
        pending are silently ignored.
        """
        with self.lock:
            current_pending = list(self.pending_queue)
            current_pending_set = set(current_pending)
            # Keep only IDs that are still pending, preserving submitted order
            head = [jid for jid in ordered_ids if jid in current_pending_set]
            head_set = set(head)
            # Append any pending jobs the client didn't know about
            tail = [jid for jid in current_pending if jid not in head_set]
            self.pending_queue = head + tail
            final_order = list(self.pending_queue)
        if self.job_store:
            for pos, job_id in enumerate(final_order):
                self.job_store.update_queue_position(job_id, pos)
        logger.info("Reordered queue: %s", final_order)
        return True

    def get_pending_order(self) -> list[str]:
        """Return current ordered list of pending job IDs."""
        with self.lock:
            return list(self.pending_queue)

    def delete_job(self, job_id: str) -> bool:
        """Delete a finished job."""
        job = self.jobs.get(job_id)
        if not job:
            return False
        if job.status not in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
            return False
        if self.job_store:
            self.job_store.delete_job(job_id)
        with self.lock:
            del self.jobs[job_id]
        return True

    def _save_job_to_store(self, job: ConversionJob) -> None:
        if not self.job_store:
            return
        job_data: dict[str, Any] = {
            "id": job.id,
            "file_path": job.file_path,
            "status": job.status.value,
            "progress": job.progress,
            "error": job.error,
            "job_type": job.job_type.value,
            "source": job.source,
            "result": job.result,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(job.created_at)),
            "started_at": (
                time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(job.started_at))
                if job.started_at
                else None
            ),
            "completed_at": (
                time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(job.completed_at))
                if job.completed_at
                else None
            ),
            "poster_url": job.poster_url,
            "media_type": job.media_type,
            "encode_options": job.encode_options,
            "log_lines": job.log_lines,
        }
        if job.result:
            job_data["video_converted"] = (
                1 if (job.result.get("video") and job.result["video"].get("success")) else 0
            )
            job_data["audio_converted"] = (
                1 if (job.result.get("audio") and job.result["audio"].get("success")) else 0
            )
            job_data["streams_cleaned"] = (
                1 if (job.result.get("cleanup") and job.result["cleanup"].get("success")) else 0
            )
        self.job_store.save_job(job_data)

    def load_pending_jobs(self) -> int:
        """Load pending/running jobs from DB and re-queue them."""
        if not self.job_store:
            return 0
        pending = self.job_store.get_pending_jobs()
        count = 0
        for job_data in pending:
            job = self._row_to_job(job_data)
            # If this is a custom encode job (has encode_options) that was saved
            # with the old 'video'-only type, upgrade it to 'full' so audio and
            # cleanup also run on resume.
            if job.encode_options and job.job_type == JobType.VIDEO:
                job.job_type = JobType.FULL
            job.status = JobStatus.PENDING
            job.progress = 0.0
            with self.lock:
                self.jobs[job.id] = job
                self.pending_queue.append(job.id)
            count += 1
        if count > 0:
            logger.info("Loaded %d pending job(s) from database", count)
            # Normalize queue_positions to 0,1,2,... so sparse/stale positions
            # from previous runs don't cause newly-added jobs (which land at the
            # tail of the in-memory queue) to sort ahead of older pending jobs.
            with self.lock:
                ordered = list(self.pending_queue)
            for pos, job_id in enumerate(ordered):
                self.job_store.update_queue_position(job_id, pos)
        return count

    def load_finished_jobs(self) -> int:
        """Load completed/failed/cancelled jobs from DB into memory for display."""
        if not self.job_store:
            return 0
        all_rows = self.job_store.get_all_jobs()
        count = 0
        for row in all_rows:
            if row["id"] in self.jobs:
                continue
            status = row.get("status", "")
            if status not in ("completed", "failed", "cancelled"):
                continue
            job = self._row_to_job(row)
            with self.lock:
                self.jobs[job.id] = job
            count += 1
        if count > 0:
            logger.info("Loaded %d finished job(s) from database", count)
        return count

    @staticmethod
    def _parse_iso_ts(iso_str: str | None) -> float | None:
        """Parse ISO timestamp string to epoch float."""
        if not iso_str:
            return None
        try:
            dt = datetime.fromisoformat(iso_str)
            return dt.timestamp()
        except (ValueError, TypeError):
            return None

    def _row_to_job(self, row: dict[str, Any]) -> ConversionJob:
        """Convert a DB row dict to a ConversionJob."""
        result = None
        result_json = row.get("result_json")
        if result_json:
            with contextlib.suppress(ValueError, TypeError):
                result = _json.loads(result_json)
        # Fall back to boolean flags if no JSON result
        if result is None and any(
            row.get(k) for k in ("video_converted", "audio_converted", "streams_cleaned")
        ):
            result = {}
            if row.get("video_converted"):
                result["video"] = {"success": True}
            if row.get("audio_converted"):
                result["audio"] = {"success": True}
            if row.get("streams_cleaned"):
                result["cleanup"] = {"success": True}

        job_type_str = row.get("job_type", "full")
        try:
            job_type = JobType(job_type_str)
        except ValueError:
            job_type = JobType.FULL

        status_str = row.get("status", "pending")
        try:
            status = JobStatus(status_str)
        except ValueError:
            status = JobStatus.PENDING

        created = self._parse_iso_ts(row.get("created_at")) or time.time()

        # Reconstruct planned/completed phases from result data
        phases: list[str] | None = None
        if result:
            phases = [p for p in ("audio", "video", "cleanup") if result.get(p)]

        encode_options: dict[str, Any] | None = None
        encode_options_json = row.get("encode_options_json")
        if encode_options_json:
            with contextlib.suppress(ValueError, TypeError):
                encode_options = _json.loads(encode_options_json)

        log_lines: list[dict[str, Any]] = []
        log_lines_json = row.get("log_lines_json")
        if log_lines_json:
            with contextlib.suppress(ValueError, TypeError):
                log_lines = _json.loads(log_lines_json)

        return ConversionJob(
            id=row["id"],
            job_type=job_type,
            file_path=row["file_path"],
            status=status,
            created_at=created,
            started_at=self._parse_iso_ts(row.get("started_at")),
            completed_at=self._parse_iso_ts(row.get("completed_at")),
            progress=row.get("progress", 0.0) or 0.0,
            result=result,
            error=row.get("error"),
            source=row.get("source", "webhook"),
            planned_phases=phases,
            completed_phases=phases,
            poster_url=row.get("poster_url"),
            media_type=row.get("media_type"),
            encode_options=encode_options,
            log_lines=log_lines,
        )

    def _watchdog_loop(self) -> None:
        """Periodically check for stale running jobs and fail them.

        Also periodically flushes running jobs' in-memory logs to the job
        store, so a container restart mid-encode doesn't lose the log output
        accumulated so far (previously only persisted at job start/finish).
        """
        while self.running:
            try:
                time.sleep(30)  # Check every 30 seconds
                now = time.time()
                with self.lock:
                    running_jobs = [j for j in self.jobs.values() if j.status == JobStatus.RUNNING]
                for job in running_jobs:
                    if self.job_store:
                        self._save_job_to_store(job)

                    last_update = job.last_progress_at or job.started_at or job.created_at
                    stale_seconds = now - last_update
                    if stale_seconds < self.stale_timeout:
                        continue

                    stale_min = stale_seconds / 60
                    logger.error(
                        "Watchdog: job %s has had no progress for %.0f minutes "
                        "(last progress: %.1f%%) — marking as failed. "
                        "The worker thread may be stuck in a blocking I/O call.",
                        job.id,
                        stale_min,
                        job.progress,
                    )

                    job.status = JobStatus.FAILED
                    job.completed_at = now
                    job.error = (
                        f"Stale job detected: no progress for {stale_min:.0f} minutes "
                        f"(stuck at {job.progress:.1f}%). "
                        "The worker may have been blocked by a network I/O issue."
                    )
                    if job.cancel_event:
                        job.cancel_event.set()
                    if self.job_store:
                        self._save_job_to_store(job)
            except Exception as exc:
                logger.error("Watchdog error: %s", exc)

    def _worker_loop(self) -> None:
        while self.running:
            job_id = None
            with self.lock:
                # Scale-down: excess workers exit after finishing their current
                # job, without picking up new work.
                alive = sum(1 for w in self.workers if w.is_alive())
                if alive > self.max_workers:
                    with contextlib.suppress(ValueError):
                        self.workers.remove(threading.current_thread())
                    return
                if self.pending_queue:
                    job_id = self.pending_queue.pop(0)
            if job_id:
                self._process_job(job_id)
            else:
                time.sleep(0.1)

    def _process_job(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if not job:
            return
        if job.status == JobStatus.CANCELLED:
            logger.info("Job %s was cancelled, skipping", job_id)
            return

        # Create cancel event for this job so cancel_job() can kill ffmpeg
        job.cancel_event = threading.Event()

        job.status = JobStatus.RUNNING
        job.started_at = time.time()
        job.last_progress_at = time.time()
        job.log("app", "info", f"Job started: {Path(job.file_path).name}")
        if self.job_store:
            self._save_job_to_store(job)

        def _update_progress(pct: float) -> None:
            job.progress = pct
            job.last_progress_at = time.time()

        def _update_detail(phase: str, detail: str) -> None:
            job.current_phase = phase
            job.status_detail = detail

        try:
            if job.job_type == JobType.RETAG:
                from backend.workers.retag import Retagger, TrackOverride

                raw_overrides = (job.encode_options or {}).get("overrides", [])
                overrides = [
                    TrackOverride(
                        track_type=o["track_type"],
                        track_index=o["track_index"],
                        language=o.get("language"),
                        title=o.get("title"),
                    )
                    for o in raw_overrides
                ]
                job.log("app", "info", f"Retagging {len(overrides)} track(s)")
                retag_result = Retagger().retag(
                    job.file_path,
                    overrides,
                    log_cb=lambda src, lvl, msg: job.log(src, lvl, msg),
                )
                job.result = {"retag": retag_result.to_dict()}
                if not retag_result.success:
                    job.status = JobStatus.FAILED
                    job.error = retag_result.error or "Retag failed"
                    job.log("app", "error", f"Retag failed: {job.error}")
                else:
                    job.status = JobStatus.COMPLETED
                    job.log("app", "info", "Retag completed successfully")
                    try:
                        from backend.api_analyze import analyze_and_store

                        analyze_and_store(job.file_path)
                    except Exception as analyze_err:
                        logger.warning("Post-retag analysis failed: %s", analyze_err)
                return

            result = process_file(
                job.file_path,
                job.job_type,
                job.id,
                progress_callback=_update_progress,
                cancel_event=job.cancel_event,
                detail_callback=_update_detail,
                job=job,
            )
            if job.status == JobStatus.CANCELLED:
                logger.info("Job %s was cancelled during processing", job_id)
                return
            job.result = result

            any_success = (
                (result.get("audio") and result["audio"].get("success"))
                or (result.get("video") and result["video"].get("success"))
                or (result.get("cleanup") and result["cleanup"].get("success"))
            )
            if any_success:
                rename_result = RenameResult()
                try:
                    rename_result = trigger_rename(job.file_path)
                except Exception as rename_err:
                    logger.warning("Rename trigger failed (job still completed): %s", rename_err)

                # Analyze the (possibly renamed) output file and store in
                # media DB with the Sonarr/Radarr file ID so browse lookups
                # find the updated analysis immediately.
                analyze_path = rename_result.new_path or job.file_path
                if rename_result.new_path and rename_result.new_path != job.file_path:
                    job.output_path = rename_result.new_path
                    job.log("app", "info", f"Renamed to: {Path(rename_result.new_path).name}")
                try:
                    from backend.api_analyze import analyze_and_store

                    analyze_and_store(
                        analyze_path,
                        radarr_movie_file_id=rename_result.radarr_movie_file_id,
                        sonarr_episode_file_id=rename_result.sonarr_episode_file_id,
                    )
                except Exception as analyze_err:
                    logger.warning("Post-job analysis failed for %s: %s", analyze_path, analyze_err)

            any_phase_failed = (
                (result.get("video") and not result["video"].get("success"))
                or (result.get("audio") and not result["audio"].get("success"))
                or (result.get("cleanup") and not result["cleanup"].get("success"))
            )
            if any_phase_failed:
                job.status = JobStatus.FAILED
                job.error = next(
                    (
                        r["error"]
                        for r in [result.get("video"), result.get("audio"), result.get("cleanup")]
                        if r and not r.get("success") and r.get("error")
                    ),
                    "A phase failed without an error message",
                )
                job.log("app", "error", f"Job failed: {job.error}")
                logger.error("Job %s failed: one or more phases failed", job_id)
            else:
                job.status = JobStatus.COMPLETED
                job.log("app", "info", "Job completed successfully")
                logger.info("Job %s completed successfully", job_id)
        except Exception as e:
            # CancelledError from workers is expected when user cancels
            if job.status == JobStatus.CANCELLED:
                job.log("app", "info", "Job cancelled by user")
                logger.info("Job %s was cancelled during processing", job_id)
                return
            job.error = str(e)
            job.status = JobStatus.FAILED
            job.log("app", "error", f"Job failed: {job.error}")
            logger.error("Job %s failed: %s", job_id, e)
        finally:
            job.completed_at = time.time()
            if self.job_store:
                self._save_job_to_store(job)
            # Invalidate browse cache so next request fetches fresh data
            from backend.api_browse import invalidate_cache

            invalidate_cache()


# ---------------------------------------------------------------------------
# Global singletons (initialised during startup)
# ---------------------------------------------------------------------------

config: Config | None = None
job_queue: JobQueue | None = None
job_store: JobStore | None = None
media_store: MediaStore | None = None
ffprobe: FFProbe | None = None
anime_detector: AnimeDetector | None = None
language_detector: LanguageDetector | None = None
audio_converter: AudioConverter | None = None
video_converter: VideoConverter | None = None
stream_cleanup: StreamCleanup | None = None


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def translate_path(container_path: str) -> str:
    """Translate container path to host path using PATH_MAPPING env vars."""
    for container_prefix, host_prefix in PATH_MAPPINGS:
        if container_path.startswith(container_prefix):
            return container_path.replace(container_prefix, host_prefix, 1)
    return container_path


def get_volume_root(file_path: str) -> str:
    """Return the directory to create temp files in.

    By default this is the **parent directory** of *file_path* so the temp
    dir lives on the same underlying device / mergerfs branch as the source
    file.  This avoids cross-device copies (network round-trips on NFS/CIFS,
    wrong branch selection on mergerfs) and enables atomic same-device rename
    at the end of the job.

    Set the ``TEMP_DIR`` env-var to override (e.g. to a fast local NVMe path).
    """
    temp_dir_override = os.getenv("TEMP_DIR", "").strip()
    if temp_dir_override:
        return temp_dir_override
    parent = str(Path(file_path).parent)
    if parent and os.access(parent, os.W_OK):
        return parent
    # Fallback: volume root from PATH_MAPPINGS, then system temp
    for _, host_prefix in PATH_MAPPINGS:
        if file_path.startswith(host_prefix):
            if os.access(host_prefix, os.W_OK):
                return host_prefix
            logger.warning("Volume root %r is not writable, falling back to temp dir", host_prefix)
            return tempfile.gettempdir()
    return tempfile.gettempdir()


# ---------------------------------------------------------------------------
# Processing pipeline
# ---------------------------------------------------------------------------


def process_file(
    file_path: str,
    job_type: JobType,
    job_id: str | None = None,
    progress_callback: Callable[[float], None] | None = None,
    cancel_event: threading.Event | None = None,
    detail_callback: Callable[[str, str], None] | None = None,
    job: ConversionJob | None = None,
) -> dict[str, Any]:
    """Process a single file with the specified conversion type."""
    results: dict[str, Any] = {"file": file_path, "audio": None, "video": None, "cleanup": None}

    if not Path(file_path).exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    do_audio = job_type in (JobType.AUDIO, JobType.FULL)
    do_video = job_type in (JobType.VIDEO, JobType.FULL)
    do_cleanup = job_type in (JobType.CLEANUP, JobType.FULL)

    # Detect content type once for use by cleanup (anime dual-audio logic)
    file_is_anime = bool(anime_detector and anime_detector.is_anime(file_path))

    # Pre-determine which phases will actually run so we can divide progress equally.
    will_audio = (
        do_audio
        and audio_converter is not None
        and audio_converter.should_convert(file_path, is_anime=file_is_anime)
    )
    _force_encode = bool(job and job.encode_options and job.encode_options.get("force_encode"))
    will_video = (
        do_video
        and video_converter is not None
        and (_force_encode or video_converter.should_convert(file_path))
    )
    will_cleanup = (
        do_cleanup
        and stream_cleanup is not None
        and stream_cleanup.should_cleanup(file_path, is_anime=file_is_anime)
    )

    # Build planned phases list for the UI and for phase-chaining.
    phases_to_run: list[str] = []
    if will_audio:
        phases_to_run.append("audio")
    if will_video:
        phases_to_run.append("video")
    if will_cleanup:
        phases_to_run.append("cleanup")
    if job:
        job.planned_phases = phases_to_run[:]
        job.completed_phases = []

    n_phases = len(phases_to_run) or 1
    phase_size = 100.0 / n_phases
    phase_idx = 0

    def make_phase_cb(idx: int) -> Callable[[float], None] | None:
        if progress_callback is None:
            return None
        _cb = progress_callback  # capture non-None reference so Pyright can narrow it in closure
        offset = idx * phase_size

        def cb(raw_pct: float) -> None:
            _cb(offset + raw_pct * phase_size / 100.0)

        return cb

    def _set_phase(phase: str, detail: str) -> None:
        if detail_callback:
            detail_callback(phase, detail)

    def _complete_phase(phase: str) -> None:
        if job and job.completed_phases is not None:
            job.completed_phases.append(phase)
        if detail_callback:
            detail_callback(phase, "")

    # ------------------------------------------------------------------
    # Phase chaining
    # ------------------------------------------------------------------
    # When multiple phases will run on the same file, chain them through
    # intermediate temp files rather than writing back to the original
    # between each phase.  This means:
    #   - one fewer full-file read/write cycle per intermediate phase
    #   - atomicity: if any phase fails the original is never touched
    #
    # Each intermediate phase writes to a .chain-N.mkv temp; the last
    # phase writes directly to the original path (safe_replace handles
    # the atomic rename internally).  On any failure all chain temps are
    # cleaned up and results are returned as-is (original untouched).
    # ------------------------------------------------------------------

    chain_temps: list[Path] = []
    chain_current = file_path  # input for the next phase; advances each iteration

    # Snapshot the original file's identity before any phase runs. The worker
    # performing the final write compares the file at file_path against this
    # to detect Sonarr/Radarr replacing it mid-job, so the guard covers the
    # whole job even when phases chain through temp files.
    try:
        _orig_stat = Path(file_path).stat()
        source_snapshot: tuple[float, int] | None = (_orig_stat.st_mtime, _orig_stat.st_size)
    except OSError:
        source_snapshot = None

    if len(phases_to_run) > 1:
        _vol = get_volume_root(file_path)
        # Use a distinct prefix so individual worker temp-dir cleanup
        # (which uses ".remuxcode-temp-{job_id}") never deletes chain files.
        _chain_dir = Path(_vol) / f".remuxcode-chain-{job_id}"
        _chain_dir.mkdir(parents=True, exist_ok=True)
        _orig_name = Path(file_path).name
        # One temp per intermediate phase (last phase writes straight to original)
        chain_temps.extend(
            _chain_dir / f"{_orig_name}.chain-{_i}.mkv" for _i in range(len(phases_to_run) - 1)
        )

    def _phase_input() -> str:
        """Current input file for the next phase."""
        return chain_current

    def _phase_output(phase_name: str) -> str | None:
        """Explicit output path for this phase.

        Returns None for single-phase jobs so the converter replaces
        the input in-place (existing behaviour).  For multi-phase jobs
        returns a chain-temp path for all but the last phase, and the
        original file path for the last phase.
        """
        if len(phases_to_run) <= 1:
            return None
        idx = phases_to_run.index(phase_name)
        if idx == len(phases_to_run) - 1:
            return file_path  # last phase: overwrite original
        return str(chain_temps[idx])

    def _is_final_write(out: str | None) -> bool:
        """Whether a phase's output target is the real source file.

        True for single-phase jobs (out is None, in-place replacement) and for
        the last phase of a multi-phase job (out == file_path). False for an
        intermediate chain-temp handoff to the next phase.
        """
        return out is None or out == file_path

    def _cleanup_chain_temps() -> None:
        for _t in chain_temps:
            _t.unlink(missing_ok=True)
        if chain_temps:
            # rmdir (not rmtree): only removes the chain dir when empty. A
            # worker may have deliberately preserved its temp dir inside it
            # (output missing after a clean ffmpeg exit — likely network-fs
            # cache lag) and that evidence must survive the job teardown.
            with contextlib.suppress(OSError):
                chain_temps[0].parent.rmdir()

    def _log_cb(source: str, level: str, message: str) -> None:
        if job:
            job.log(source, level, message)

    if will_audio:
        _set_phase("audio", "Analyzing audio streams...")
        if job:
            job.log("app", "info", "Starting audio conversion")
        logger.info("Converting audio: %s", Path(file_path).name)
        assert audio_converter is not None
        _in = _phase_input()
        _out = _phase_output("audio")
        audio_result = audio_converter.convert(
            _in,
            output_file=_out,
            job_id=job_id,
            progress_callback=make_phase_cb(phase_idx),
            cancel_event=cancel_event,
            detail_callback=lambda detail: _set_phase("audio", detail),
            log_cb=_log_cb,
            is_final_write=_is_final_write(_out),
            source_snapshot=source_snapshot,
        )
        _complete_phase("audio")
        phase_idx += 1
        results["audio"] = {
            "success": audio_result.success,
            "streams_converted": audio_result.streams_converted,
            "streams_dropped": audio_result.streams_dropped,
            "converted_streams": audio_result.converted_streams,
            "original_size": audio_result.original_size,
            "new_size": audio_result.new_size,
            "error": audio_result.error,
        }
        if not audio_result.success:
            if job:
                job.log("app", "error", f"Audio failed: {audio_result.error}")
            logger.error("Audio conversion failed: %s", audio_result.error)
            _cleanup_chain_temps()
            return results
        if job:
            streams_msg = f"{audio_result.streams_converted} stream(s) converted"
            if audio_result.streams_dropped:
                streams_msg += f", {audio_result.streams_dropped} dropped"
            job.log("app", "info", f"Audio complete: {streams_msg}")
        # Advance chain: next phase reads from the output we just produced
        if _out is not None:
            chain_current = _out

    if will_video:
        _set_phase("video", "Analyzing video streams...")
        if job:
            job.log("app", "info", "Starting video encode")
        logger.info("Converting video: %s", Path(file_path).name)
        assert video_converter is not None
        _in = _phase_input()
        _out = _phase_output("video")
        _encode_opts = job.encode_options if job else None
        video_result = video_converter.convert(
            _in,
            output_file=_out,
            job_id=job_id,
            progress_callback=make_phase_cb(phase_idx),
            cancel_event=cancel_event,
            detail_callback=lambda detail: _set_phase("video", detail),
            log_cb=_log_cb,
            encode_options=_encode_opts,
            title=Path(file_path).parent.name,
            is_final_write=_is_final_write(_out),
            source_snapshot=source_snapshot,
        )
        _complete_phase("video")
        phase_idx += 1
        results["video"] = {
            "success": video_result.success,
            "codec_from": video_result.codec_from,
            "codec_to": video_result.codec_to,
            "content_type": video_result.content_type,
            "original_size": video_result.original_size,
            "new_size": video_result.new_size,
            "size_change_percent": video_result.size_change_percent,
            "error": video_result.error,
        }
        if not video_result.success:
            if job:
                job.log("app", "error", f"Video encode failed: {video_result.error}")
            logger.error("Video conversion failed: %s", video_result.error)
            _cleanup_chain_temps()
            return results
        if job:
            codec_msg = f"{video_result.codec_from} → {video_result.codec_to}"
            if video_result.content_type:
                codec_msg += f" ({video_result.content_type})"
            job.log("app", "info", f"Video encode complete: {codec_msg}")
        if _out is not None:
            chain_current = _out

    if will_cleanup:
        _set_phase("cleanup", "Analyzing streams...")
        if job:
            job.log("app", "info", "Starting stream cleanup")
        logger.info("Cleaning streams: %s", Path(file_path).name)
        assert stream_cleanup is not None
        _in = _phase_input()
        _out = _phase_output("cleanup")
        cleanup_result = stream_cleanup.cleanup(
            _in,
            output_file=_out,
            job_id=job_id,
            progress_callback=make_phase_cb(phase_idx),
            is_anime=file_is_anime,
            cancel_event=cancel_event,
            detail_callback=lambda detail: _set_phase("cleanup", detail),
            log_cb=_log_cb,
            is_final_write=_is_final_write(_out),
            source_snapshot=source_snapshot,
        )
        _complete_phase("cleanup")
        results["cleanup"] = {
            "success": cleanup_result.success,
            "audio_removed": cleanup_result.audio_removed,
            "audio_kept": cleanup_result.audio_kept,
            "subtitle_removed": cleanup_result.subtitle_removed,
            "subtitle_kept": cleanup_result.subtitle_kept,
            "original_size": cleanup_result.original_size,
            "new_size": cleanup_result.new_size,
            "original_language": cleanup_result.original_language,
            "error": cleanup_result.error,
        }
        if not cleanup_result.success:
            if job:
                job.log("app", "error", f"Cleanup failed: {cleanup_result.error}")
            logger.error("Stream cleanup failed: %s", cleanup_result.error)
            _cleanup_chain_temps()
            return results
        if job:
            removed = cleanup_result.audio_removed + cleanup_result.subtitle_removed
            job.log("app", "info", f"Cleanup complete: {removed} stream(s) removed")

    # All phases completed — clean up any leftover intermediate chain temps
    # (the last phase wrote directly to file_path so they are now orphaned).
    _cleanup_chain_temps()

    return results


# ---------------------------------------------------------------------------
# Sonarr / Radarr integration
# ---------------------------------------------------------------------------


@dataclass
class RenameResult:
    """Result of a Sonarr/Radarr rename operation."""

    new_path: str | None = None
    radarr_movie_file_id: int | None = None
    sonarr_episode_file_id: int | None = None


def trigger_rename(file_path: str, media_type: str = "auto") -> RenameResult:
    """Trigger Sonarr/Radarr refresh and rename after conversion.

    Returns a RenameResult containing the (possibly new) file path
    and the Sonarr/Radarr file ID for DB linkage.
    """
    if media_type == "auto":
        path_lower = file_path.lower()
        media_type = "movie" if ("/movies/" in path_lower or "/films/" in path_lower) else "tv"

    try:
        if media_type == "movie":
            return _trigger_radarr_rename(file_path)
        return _trigger_sonarr_rename(file_path)
    except Exception as e:
        logger.error("Failed to trigger rename: %s", e)
        return RenameResult()


def _get_radarr_config() -> tuple[str, str]:
    """Return (url, api_key) for Radarr."""
    url = (config.radarr.url if config else "").rstrip("/")
    key = config.radarr.api_key if config else ""
    return url, key


def _get_sonarr_config() -> tuple[str, str]:
    """Return (url, api_key) for Sonarr."""
    url = (config.sonarr.url if config else "").rstrip("/")
    key = config.sonarr.api_key if config else ""
    return url, key


def refresh_radarr() -> None:
    """Trigger a full Radarr library refresh (all movies)."""
    radarr_url, radarr_key = _get_radarr_config()
    if not radarr_url or not radarr_key:
        raise RuntimeError("Radarr not configured")
    resp = requests.post(
        f"{radarr_url}/api/v3/command",
        headers={"X-Api-Key": radarr_key},
        json={"name": "RefreshMovie"},
        timeout=30,
    )
    resp.raise_for_status()
    cmd_id = resp.json().get("id")
    logger.info("Triggered full Radarr library refresh")
    _poll_command(radarr_url, radarr_key, cmd_id, "Radarr library refresh", max_wait=120)


def refresh_sonarr() -> None:
    """Trigger a full Sonarr library refresh (all series)."""
    sonarr_url, sonarr_key = _get_sonarr_config()
    if not sonarr_url or not sonarr_key:
        raise RuntimeError("Sonarr not configured")
    resp = requests.post(
        f"{sonarr_url}/api/v3/command",
        headers={"X-Api-Key": sonarr_key},
        json={"name": "RefreshSeries"},
        timeout=30,
    )
    resp.raise_for_status()
    cmd_id = resp.json().get("id")
    logger.info("Triggered full Sonarr library refresh")
    _poll_command(sonarr_url, sonarr_key, cmd_id, "Sonarr library refresh", max_wait=120)


def _find_webhook_notification(base_url: str, api_key: str) -> dict[str, Any] | None:
    """Return the 'Custom Converter' webhook notification resource, if configured."""
    resp = requests.get(
        f"{base_url}/api/v3/notification",
        headers={"X-Api-Key": api_key},
        timeout=15,
    )
    resp.raise_for_status()
    for notification in resp.json():
        if (
            notification.get("implementation") == "Webhook"
            and notification.get("name") == "Custom Converter"
        ):
            return notification
    return None


def _test_webhook_notification(base_url: str, api_key: str, service: str) -> str:
    """Ask Sonarr/Radarr to send a real test webhook to remuXcode.

    This exercises both directions: the remuXcode -> Sonarr/Radarr API call
    (auth/reachability) and the Sonarr/Radarr -> remuXcode webhook delivery
    that the API call triggers, so failures can be attributed to whichever
    side actually broke.
    """
    try:
        notification = _find_webhook_notification(base_url, api_key)
    except requests.RequestException as e:
        raise RuntimeError(f"Could not reach {service} API: {e}") from e

    if notification is None:
        raise RuntimeError(
            f"No 'Custom Converter' webhook notification found in {service}. "
            "Add it under Settings > Connect first."
        )

    resp = requests.post(
        f"{base_url}/api/v3/notification/test",
        headers={"X-Api-Key": api_key},
        json=notification,
        timeout=30,
    )
    if resp.status_code == 200:
        return f"{service} successfully sent a test webhook to remuXcode"

    try:
        errors = resp.json()
        detail = (
            "; ".join(e.get("errorMessage", str(e)) for e in errors)
            if isinstance(errors, list)
            else str(errors)
        )
    except ValueError:
        detail = resp.text
    raise RuntimeError(f"{service} could not deliver the test webhook: {detail}")


def test_sonarr_webhook() -> str:
    """Trigger Sonarr's own notification test for the 'Custom Converter' webhook."""
    sonarr_url, sonarr_key = _get_sonarr_config()
    if not sonarr_url or not sonarr_key:
        raise RuntimeError("Sonarr not configured")
    return _test_webhook_notification(sonarr_url, sonarr_key, "Sonarr")


def test_radarr_webhook() -> str:
    """Trigger Radarr's own notification test for the 'Custom Converter' webhook."""
    radarr_url, radarr_key = _get_radarr_config()
    if not radarr_url or not radarr_key:
        raise RuntimeError("Radarr not configured")
    return _test_webhook_notification(radarr_url, radarr_key, "Radarr")


def _trigger_radarr_rename(file_path: str) -> RenameResult:
    radarr_url, radarr_key = _get_radarr_config()

    if not radarr_url or not radarr_key:
        logger.warning("Radarr not configured, skipping rename")
        return RenameResult()

    response = requests.get(
        f"{radarr_url}/api/v3/movie",
        headers={"X-Api-Key": radarr_key},
        timeout=30,
    )
    response.raise_for_status()

    movie_id = None
    file_path_obj = Path(file_path)
    for movie in response.json():
        movie_folder = Path(movie.get("path", "")).name
        if movie_folder and movie_folder in file_path_obj.parts:
            movie_id = movie["id"]
            logger.info("Triggering Radarr refresh/rename for: %s", movie.get("title"))
            break

    if not movie_id:
        logger.warning("Movie not found in Radarr for path: %s", file_path)
        return RenameResult()

    # Refresh — forces Radarr to re-read mediainfo from disk
    resp = requests.post(
        f"{radarr_url}/api/v3/command",
        headers={"X-Api-Key": radarr_key},
        json={"name": "RefreshMovie", "movieIds": [movie_id]},
        timeout=30,
    )
    resp.raise_for_status()
    cmd_id = resp.json().get("id")
    logger.info("Triggered Radarr refresh for movie ID %s", movie_id)
    _poll_command(radarr_url, radarr_key, cmd_id, "Radarr refresh")

    # Get movie file ID (pre-rename)
    movie_file_id = None
    resp = requests.get(
        f"{radarr_url}/api/v3/moviefile",
        params={"movieId": movie_id},
        headers={"X-Api-Key": radarr_key},
        timeout=30,
    )
    if resp.status_code == 200:
        for mf in resp.json():
            if Path(mf.get("path", "")).name == file_path_obj.name:
                movie_file_id = mf.get("id")
                break

    # Rename — poll to completion before returning
    rename_payload: dict[str, Any] = {"name": "RenameMovie", "movieIds": [movie_id]}
    if movie_file_id:
        rename_payload["movieFileIds"] = [movie_file_id]
    resp = requests.post(
        f"{radarr_url}/api/v3/command",
        headers={"X-Api-Key": radarr_key},
        json=rename_payload,
        timeout=30,
    )
    resp.raise_for_status()
    rename_cmd_id = resp.json().get("id")
    _poll_command(radarr_url, radarr_key, rename_cmd_id, "Radarr rename")
    logger.info("Radarr rename completed for movie ID %s", movie_id)

    # Re-fetch file info to get the (possibly new) path after rename
    new_path = file_path
    if movie_file_id:
        resp = requests.get(
            f"{radarr_url}/api/v3/moviefile/{movie_file_id}",
            headers={"X-Api-Key": radarr_key},
            timeout=30,
        )
        if resp.status_code == 200:
            new_path = resp.json().get("path", file_path)
            if new_path != file_path:
                logger.info("Radarr renamed file: %s", Path(new_path).name)

    return RenameResult(new_path=new_path, radarr_movie_file_id=movie_file_id)


def _trigger_sonarr_rename(file_path: str) -> RenameResult:
    sonarr_url, sonarr_key = _get_sonarr_config()

    if not sonarr_url or not sonarr_key:
        logger.warning("Sonarr not configured, skipping rename")
        return RenameResult()

    response = requests.get(
        f"{sonarr_url}/api/v3/series",
        headers={"X-Api-Key": sonarr_key},
        timeout=30,
    )
    response.raise_for_status()

    series_id = None
    file_path_obj = Path(file_path)
    for series in response.json():
        series_folder = Path(series.get("path", "")).name
        if series_folder and series_folder in file_path_obj.parts:
            series_id = series["id"]
            logger.info("Triggering Sonarr refresh/rename for: %s", series.get("title"))
            break

    if not series_id:
        logger.warning("Series not found in Sonarr for path: %s", file_path)
        return RenameResult()

    # RefreshSeries — forces full metadata re-read including mediainfo
    # (RescanSeries only detects new/removed files, not content changes)
    resp = requests.post(
        f"{sonarr_url}/api/v3/command",
        headers={"X-Api-Key": sonarr_key},
        json={"name": "RefreshSeries", "seriesId": series_id},
        timeout=30,
    )
    resp.raise_for_status()
    cmd_id = resp.json().get("id")
    logger.info("Triggered Sonarr refresh for series ID %s", series_id)
    _poll_command(sonarr_url, sonarr_key, cmd_id, "Sonarr refresh")

    # RescanSeries — picks up file changes on disk after refresh
    resp = requests.post(
        f"{sonarr_url}/api/v3/command",
        headers={"X-Api-Key": sonarr_key},
        json={"name": "RescanSeries", "seriesId": series_id},
        timeout=30,
    )
    resp.raise_for_status()
    cmd_id = resp.json().get("id")
    logger.info("Triggered Sonarr rescan for series ID %s", series_id)
    _poll_command(sonarr_url, sonarr_key, cmd_id, "Sonarr rescan")

    # Get episode file ID
    episode_file_id = None
    resp = requests.get(
        f"{sonarr_url}/api/v3/episodefile",
        params={"seriesId": series_id},
        headers={"X-Api-Key": sonarr_key},
        timeout=30,
    )
    if resp.status_code == 200:
        for ef in resp.json():
            if Path(ef.get("path", "")).name == file_path_obj.name:
                episode_file_id = ef.get("id")
                break

    # Rename — poll to completion before returning
    rename_payload: dict[str, Any] = {"name": "RenameFiles", "seriesId": series_id}
    if episode_file_id:
        rename_payload["files"] = [episode_file_id]
    resp = requests.post(
        f"{sonarr_url}/api/v3/command",
        headers={"X-Api-Key": sonarr_key},
        json=rename_payload,
        timeout=30,
    )
    resp.raise_for_status()
    rename_cmd_id = resp.json().get("id")
    _poll_command(sonarr_url, sonarr_key, rename_cmd_id, "Sonarr rename")
    logger.info("Sonarr rename completed for series ID %s", series_id)

    # Re-fetch file info to get the (possibly new) path after rename
    new_path = file_path
    if episode_file_id:
        resp = requests.get(
            f"{sonarr_url}/api/v3/episodefile/{episode_file_id}",
            headers={"X-Api-Key": sonarr_key},
            timeout=30,
        )
        if resp.status_code == 200:
            new_path = resp.json().get("path", file_path)
            if new_path != file_path:
                logger.info("Sonarr renamed file: %s", Path(new_path).name)

    return RenameResult(new_path=new_path, sonarr_episode_file_id=episode_file_id)


def _poll_command(
    base_url: str, api_key: str, command_id: int, label: str, max_wait: int = 60
) -> None:
    """Poll a Sonarr/Radarr command until completion."""
    waited = 0
    while waited < max_wait:
        time.sleep(2)
        waited += 2
        resp = requests.get(
            f"{base_url}/api/v3/command/{command_id}",
            headers={"X-Api-Key": api_key},
            timeout=10,
        )
        resp.raise_for_status()
        status = resp.json().get("status")
        if status == "completed":
            logger.info("%s completed", label)
            return
        if status == "failed":
            logger.error("%s failed", label)
            return
    logger.warning("%s timed out after %ds", label, max_wait)


# ---------------------------------------------------------------------------
# Job helpers (used by API routes)
# ---------------------------------------------------------------------------


def create_job(
    file_path: str,
    job_type: JobType,
    source: str = "api",
    poster_url: str | None = None,
    media_type: str | None = None,
    encode_options: dict[str, Any] | None = None,
) -> ConversionJob:
    """Create and queue a new conversion job."""
    # Deduplicate: if a pending/running job already exists for this file+type,
    # return the existing job instead of queueing a duplicate.
    if job_queue:
        with job_queue.lock:
            for existing in job_queue.jobs.values():
                if (
                    existing.file_path == file_path
                    and existing.job_type == job_type
                    and existing.status in (JobStatus.PENDING, JobStatus.RUNNING)
                ):
                    logger.info(
                        "Skipping duplicate job for %s (existing: %s)",
                        Path(file_path).name,
                        existing.id,
                    )
                    return existing

    job = ConversionJob(
        id=uuid.uuid4().hex[:12],
        job_type=job_type,
        file_path=file_path,
        status=JobStatus.PENDING,
        created_at=time.time(),
        source=source,
        poster_url=poster_url,
        media_type=media_type,
        encode_options=encode_options,
    )
    if job_queue:
        job_queue.add_job(job)
    return job


# ---------------------------------------------------------------------------
# Initialization / Shutdown
# ---------------------------------------------------------------------------


def _ensure_api_key() -> str:
    """Return the configured API key, generating one if needed."""
    config_dir = Path(CONFIG_PATH).parent
    key_file = config_dir / ".api_key"

    # Read existing key from file
    if key_file.is_file():
        key = key_file.read_text().strip()
        if key:
            return key

    # Auto-generate a key and persist to config directory
    config_dir.mkdir(parents=True, exist_ok=True)
    key = uuid.uuid4().hex + uuid.uuid4().hex  # 64-char hex key
    try:
        key_file.write_text(key)
        key_file.chmod(0o600)
    except OSError as exc:
        logger.warning("Could not persist auto-generated API key: %s", exc)

    logger.info("=" * 60)
    logger.info("  No API key configured — one has been generated for you.")
    logger.info("  API Key: %s", key)
    logger.info("  Paste this into the Config page of the web UI.")
    logger.info("  For webhooks, set X-API-Key header to this value.")
    logger.info("=" * 60)
    return key


def initialize_components() -> None:
    """Initialize all converter components."""
    global config, job_queue, job_store, media_store, ffprobe, anime_detector, language_detector
    global audio_converter, video_converter, stream_cleanup, PATH_MAPPINGS, api_key

    logger.info("Initializing remuXcode components...")

    api_key = _ensure_api_key()
    PATH_MAPPINGS = _load_path_mappings()

    config = get_config(CONFIG_PATH)

    from backend.utils.cpu_affinity import get_cpu_info, make_affinity_fn

    _p_core_ids, _is_hybrid = get_cpu_info()
    _affinity_fn = make_affinity_fn(_p_core_ids) if config.ffmpeg_pin_to_p_cores else None

    ffprobe = FFProbe(strip_cover_art=config.strip_cover_art)
    anime_detector = AnimeDetector(
        sonarr_url=config.sonarr.url,
        sonarr_api_key=config.sonarr.api_key,
        radarr_url=config.radarr.url,
        radarr_api_key=config.radarr.api_key,
    )
    language_detector = LanguageDetector(
        sonarr_url=config.sonarr.url,
        sonarr_api_key=config.sonarr.api_key,
        radarr_url=config.radarr.url,
        radarr_api_key=config.radarr.api_key,
    )

    audio_converter = AudioConverter(
        config=config.audio,
        ffprobe=ffprobe,
        get_volume_root=get_volume_root,
        ffmpeg_threads=config.effective_ffmpeg_threads,
        affinity_fn=_affinity_fn,
    )
    video_converter = VideoConverter(
        config=config.video,
        ffprobe=ffprobe,
        anime_detector=anime_detector,
        get_volume_root=get_volume_root,
        ffmpeg_threads=config.effective_ffmpeg_threads,
        hw_accel=config.video.hw_accel,
        affinity_fn=_affinity_fn,
    )
    stream_cleanup = StreamCleanup(
        config=config.cleanup,
        ffprobe=ffprobe,
        language_detector=language_detector,
        get_volume_root=get_volume_root,
        ffmpeg_threads=config.effective_ffmpeg_threads,
        affinity_fn=_affinity_fn,
    )

    db_path = os.getenv("REMUXCODE_DB_PATH", str(Path(CONFIG_PATH).parent / "jobs.db"))
    job_store = JobStore(db_path=db_path)

    # Media analysis cache (persistent ffprobe results) — same directory as jobs.db
    media_db_default = str(Path(db_path).parent / "media.db")
    media_db_path = os.getenv("REMUXCODE_MEDIA_DB_PATH", media_db_default)
    media_store = MediaStore(db_path=media_db_path)

    # Purge orphaned rows in background (stat-checking thousands of NAS files is slow)
    threading.Thread(target=media_store.purge_missing, name="purge-missing", daemon=True).start()

    # Clean up old finished jobs on startup
    job_store.cleanup_old_jobs(days=config.job_history_days)

    # Clean up orphaned temp/chain dirs from previously failed or interrupted jobs.
    # Runs in a background thread: scanning file-parent directories on a NAS can
    # be slow and we don't want to block the startup sequence.
    threading.Thread(
        target=cleanup_temp_dirs,
        args=(job_store, media_store),
        name="startup-cleanup-temp",
        daemon=True,
    ).start()

    job_queue = JobQueue(max_workers=config.workers, job_store=job_store)
    job_queue.start()

    # Load finished jobs first (so they show in the UI), then re-queue pending
    finished_count = job_queue.load_finished_jobs()
    if finished_count > 0:
        logger.info("Restored %d finished job(s) from database", finished_count)

    pending_count = job_queue.load_pending_jobs()
    if pending_count > 0:
        logger.info("Resumed %d pending job(s) from database", pending_count)

    logger.info(
        "Host: %s:%s",
        os.getenv("REMUXCODE_HOST", "0.0.0.0"),
        os.getenv("REMUXCODE_PORT", os.getenv("PORT", "7889")),
    )
    logger.info("Config: %s", CONFIG_PATH)
    logger.info("Path mappings: %d", len(PATH_MAPPINGS))
    for container, host in PATH_MAPPINGS:
        logger.info("  %s -> %s", container, host)
    logger.info("All components initialized successfully")


def shutdown_components() -> None:
    """Gracefully stop workers."""
    if job_queue:
        job_queue.stop()


def update_integration_config() -> None:
    """Update detector instances with current Sonarr/Radarr config after settings change."""
    if not config:
        return
    if anime_detector:
        anime_detector.sonarr_url = config.sonarr.url.rstrip("/")
        anime_detector.sonarr_api_key = config.sonarr.api_key
        anime_detector.radarr_url = config.radarr.url.rstrip("/")
        anime_detector.radarr_api_key = config.radarr.api_key
    if language_detector:
        language_detector.sonarr_url = config.sonarr.url.rstrip("/")
        language_detector.sonarr_api_key = config.sonarr.api_key
        language_detector.radarr_url = config.radarr.url.rstrip("/")
        language_detector.radarr_api_key = config.radarr.api_key


def cleanup_temp_dirs(
    job_store: Any | None = None,
    media_store: Any | None = None,
) -> int:
    """Clean up orphaned temp directories from failed/interrupted jobs.

    Primary strategy: scan each known job's file parent directory for its
    specific temp/chain dirs by job ID.  This is precise and fast because
    get_volume_root() returns the file's parent directory (not the volume
    root), so temp dirs live next to the source file.

    Targeted fallback: glob every known media-file parent directory (from
    both job_store and media_store) for any remaining pattern matches.
    This catches orphaned dirs whose jobs were pruned from the DB but whose
    parent directory is still known via another job or a cached analysis.

    Last-resort fallback: glob the volume roots for any leftover dirs using
    the standard name patterns (catches legacy dirs with unknown job IDs).

    Safety rules:
    - Never removes temp/chain dirs belonging to currently RUNNING jobs.
    - Only removes .remuxcode-backup files when the corresponding real file
      also exists (i.e. the backup is a leftover from a successful encode).
      If only the backup exists the original was never restored; logs a
      warning and leaves it in place.

    Returns the number of paths removed.
    """
    logger.info("Cleaning up orphaned temp directories...")
    count = 0

    # Collect IDs of jobs that are currently running so we can protect them.
    running_ids: set[str] = set()
    seen_dirs: set[Path] = set()
    all_jobs: list[dict[str, Any]] = []

    if job_store is not None:
        try:
            all_jobs = job_store.get_all_jobs()
        except Exception:
            all_jobs = []
        for job_data in all_jobs:
            if job_data.get("status") == "running":
                running_ids.add(job_data.get("id", ""))
            fp = job_data.get("file_path", "")
            if fp:
                seen_dirs.add(Path(fp).parent)

    # Also collect parent dirs from the media analysis cache.  These cover
    # files whose encoding jobs were pruned from the job DB (by the
    # cleanup_old_jobs TTL) but whose directories still exist on disk and
    # may contain leftover .remuxcode-temp-* / .remuxcode-chain-* dirs.
    if media_store is not None:
        try:
            for fp in media_store.get_all_file_paths():
                if fp:
                    seen_dirs.add(Path(fp).parent)
        except Exception:
            pass

    # Primary: job-ID-based scan (precise, O(n jobs), no NAS traversal)
    for job_data in all_jobs:
        job_id = job_data.get("id", "")
        file_path = job_data.get("file_path", "")
        if not file_path or not job_id:
            continue
        if job_id in running_ids:
            logger.debug("Skipping temp dirs for running job %s", job_id)
            continue
        parent = Path(file_path).parent
        for candidate in (
            parent / f".remuxcode-temp-{job_id}",
            parent / f".remuxcode-chain-{job_id}",
        ):
            if candidate.exists():
                try:
                    shutil.rmtree(candidate)
                    count += 1
                    logger.debug("Removed orphaned dir: %s", candidate)
                except Exception as e:
                    logger.warning("Failed to remove %s: %s", candidate, e)

    # Backup files: only delete when the real file exists alongside it.
    # If the backup is the only copy the original was never restored — warn.
    for parent in seen_dirs:
        if not parent.exists():
            continue
        try:
            backups = list(parent.glob("*.remuxcode-backup"))
        except OSError:
            continue
        for backup in backups:
            # Strip .remuxcode-backup to get the expected real filename.
            # Some older backups were created without preserving the .mkv
            # extension in their name (e.g. "file.remuxcode-backup" instead of
            # "file.mkv.remuxcode-backup"), so fall back to checking common
            # media extensions when the bare stem has no recognised extension.
            real_file = backup.with_suffix("")  # strips .remuxcode-backup
            if not real_file.exists() and real_file.suffix not in {
                ".mkv",
                ".mp4",
                ".avi",
                ".mov",
                ".ts",
                ".m2ts",
            }:
                for _ext in (".mkv", ".mp4", ".avi", ".mov", ".ts", ".m2ts"):
                    _candidate = backup.parent / (real_file.name + _ext)
                    if _candidate.exists():
                        real_file = _candidate
                        break
            if real_file.exists():
                try:
                    backup.unlink()
                    count += 1
                    logger.debug("Removed orphaned backup: %s", backup.name)
                except Exception as e:
                    logger.warning("Failed to remove backup %s: %s", backup, e)
            else:
                logger.warning(
                    "Orphaned backup with no matching real file — leaving in place: %s",
                    backup,
                )

    # Fallback: glob volume roots for any remaining dirs by pattern.
    # Extract job ID from dir name to skip running jobs.
    patterns = [
        ".remuxcode-temp-*",
        ".remuxcode-chain-*",
        ".dts-temp-*",
        ".audio-temp-*",
        ".cleanup-temp-*",
        ".hevc-tmp*",
    ]

    # Targeted fallback: glob every known media-file parent directory for
    # orphaned temp/chain dirs by pattern.  This catches dirs whose job IDs
    # are no longer in the database (pruned by cleanup_old_jobs) but whose
    # parent directory is still referenced by the media analysis cache or
    # another job entry.  Far cheaper than a recursive volume-root scan.
    removed_paths: set[Path] = set()
    for parent in seen_dirs:
        if not parent.exists():
            continue
        for pattern in patterns:
            try:
                for path in parent.glob(pattern):
                    if path in removed_paths:
                        continue
                    dir_job_id = path.name.rsplit("-", 1)[-1]
                    if dir_job_id in running_ids:
                        logger.debug("Skipping temp dir for running job %s: %s", dir_job_id, path)
                        continue
                    try:
                        if path.is_dir():
                            shutil.rmtree(path)
                        else:
                            path.unlink()
                        removed_paths.add(path)
                        count += 1
                        logger.debug("Removed orphaned path: %s", path)
                    except Exception as e:
                        logger.warning("Failed to remove %s: %s", path, e)
            except OSError:
                continue

    # Last-resort fallback: glob volume roots for any remaining dirs by pattern.
    # Extract job ID from dir name to skip running jobs.
    volumes: set[str] = {host for _, host in PATH_MAPPINGS}
    temp_override = os.getenv("TEMP_DIR", "").strip()
    volumes.add(temp_override or tempfile.gettempdir())

    for volume in volumes:
        vol_path = Path(volume)
        if not vol_path.exists():
            continue
        for pattern in patterns:
            for path in vol_path.glob(pattern):
                if path in removed_paths:
                    continue
                # Extract trailing job ID and skip if job is running.
                # Dir names end with "-{job_id}" (12-char hex).
                dir_job_id = path.name.rsplit("-", 1)[-1]
                if dir_job_id in running_ids:
                    logger.debug("Skipping temp dir for running job %s: %s", dir_job_id, path)
                    continue
                try:
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
                    count += 1
                    logger.debug("Removed orphaned path: %s", path)
                except Exception as e:
                    logger.warning("Failed to remove %s: %s", path, e)

    if count:
        logger.info("Cleaned up %d orphaned temp file(s)/directory(ies)", count)
    else:
        logger.info("No orphaned temp directories found")
    return count
