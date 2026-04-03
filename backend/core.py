"""Core processing logic for remuXcode.

Contains the job queue, file processing pipeline, path translation,
component initialization, and Sonarr/Radarr integration.
"""

from collections.abc import Callable
import contextlib
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import json as _json
import logging
import os
from pathlib import Path
import queue
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
        }


# ---------------------------------------------------------------------------
# Job Queue
# ---------------------------------------------------------------------------


class JobQueue:
    """Thread-safe job queue with status tracking and persistent storage."""

    def __init__(self, max_workers: int = 1, job_store: JobStore | None = None) -> None:
        """Initialize the job queue with max_workers worker threads."""
        self.jobs: dict[str, ConversionJob] = {}
        self.pending_queue: queue.Queue[str] = queue.Queue()
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
        if self.job_store:
            self._save_job_to_store(job)
        self.pending_queue.put(job.id)
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
            job.status = JobStatus.PENDING
            job.progress = 0.0
            with self.lock:
                self.jobs[job.id] = job
            self.pending_queue.put(job.id)
            count += 1
        if count > 0:
            logger.info("Loaded %d pending job(s) from database", count)
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
        )

    def _watchdog_loop(self) -> None:
        """Periodically check for stale running jobs and fail them."""
        while self.running:
            try:
                time.sleep(30)  # Check every 30 seconds
                now = time.time()
                with self.lock:
                    running_jobs = [
                        j for j in self.jobs.values() if j.status == JobStatus.RUNNING
                    ]
                for job in running_jobs:
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

                    # If all worker threads are stuck, spawn a replacement
                    alive_workers = [w for w in self.workers if w.is_alive()]
                    # A stuck thread is alive but not making progress — spawn
                    # a replacement so the queue keeps draining.
                    if len(alive_workers) >= self.max_workers:
                        idx = len(self.workers)
                        replacement = threading.Thread(
                            target=self._worker_loop,
                            name=f"Worker-{idx}",
                            daemon=True,
                        )
                        replacement.start()
                        self.workers.append(replacement)
                        logger.warning(
                            "Watchdog: spawned replacement worker thread Worker-%d", idx
                        )
            except Exception as exc:
                logger.error("Watchdog error: %s", exc)

    def _worker_loop(self) -> None:
        while self.running:
            try:
                job_id = self.pending_queue.get(timeout=1)
                self._process_job(job_id)
            except queue.Empty:
                continue
            except Exception as e:
                logger.error("Worker error: %s", e)

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
        if self.job_store:
            self._save_job_to_store(job)

        def _update_progress(pct: float) -> None:
            job.progress = pct
            job.last_progress_at = time.time()

        try:
            result = process_file(
                job.file_path,
                job.job_type,
                job.id,
                progress_callback=_update_progress,
                cancel_event=job.cancel_event,
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
                try:
                    trigger_rename(job.file_path)
                except Exception as rename_err:
                    logger.warning("Rename trigger failed (job still completed): %s", rename_err)

                # Auto-analyze the (potentially renamed) output file
                try:
                    from backend.api_analyze import analyze_and_store

                    analyze_and_store(job.file_path)
                except Exception as analyze_err:
                    logger.debug("Post-job analysis skipped: %s", analyze_err)

            job.status = JobStatus.COMPLETED
            logger.info("Job %s completed successfully", job_id)
        except Exception as e:
            # CancelledError from workers is expected when user cancels
            if job.status == JobStatus.CANCELLED:
                logger.info("Job %s was cancelled during processing", job_id)
                return
            job.error = str(e)
            job.status = JobStatus.FAILED
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
    """Get volume root for temp directory."""
    temp_dir_override = os.getenv("TEMP_DIR", "").strip()
    if temp_dir_override:
        return temp_dir_override
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
    will_video = (
        do_video and video_converter is not None and video_converter.should_convert(file_path)
    )
    will_cleanup = (
        do_cleanup
        and stream_cleanup is not None
        and stream_cleanup.should_cleanup(file_path, is_anime=file_is_anime)
    )

    active_phases = [p for p in (will_audio, will_video, will_cleanup) if p]
    n_phases = len(active_phases) or 1
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

    if will_audio:
        logger.info("Converting audio: %s", Path(file_path).name)
        assert audio_converter is not None
        audio_result = audio_converter.convert(
            file_path,
            job_id=job_id,
            progress_callback=make_phase_cb(phase_idx),
            cancel_event=cancel_event,
        )
        phase_idx += 1
        results["audio"] = {
            "success": audio_result.success,
            "streams_converted": audio_result.streams_converted,
            "converted_streams": audio_result.converted_streams,
            "error": audio_result.error,
        }
        if not audio_result.success:
            logger.error("Audio conversion failed: %s", audio_result.error)

    if will_video:
        logger.info("Converting video: %s", Path(file_path).name)
        assert video_converter is not None
        video_result = video_converter.convert(
            file_path,
            job_id=job_id,
            progress_callback=make_phase_cb(phase_idx),
            cancel_event=cancel_event,
        )
        phase_idx += 1
        results["video"] = {
            "success": video_result.success,
            "codec_from": video_result.codec_from,
            "codec_to": video_result.codec_to,
            "content_type": video_result.content_type,
            "size_change_percent": video_result.size_change_percent,
            "error": video_result.error,
        }
        if not video_result.success:
            logger.error("Video conversion failed: %s", video_result.error)

    if will_cleanup:
        logger.info("Cleaning streams: %s", Path(file_path).name)
        assert stream_cleanup is not None
        cleanup_result = stream_cleanup.cleanup(
            file_path,
            job_id=job_id,
            progress_callback=make_phase_cb(phase_idx),
            is_anime=file_is_anime,
            cancel_event=cancel_event,
        )
        results["cleanup"] = {
            "success": cleanup_result.success,
            "audio_removed": cleanup_result.audio_removed,
            "audio_kept": cleanup_result.audio_kept,
            "subtitle_removed": cleanup_result.subtitle_removed,
            "subtitle_kept": cleanup_result.subtitle_kept,
            "original_language": cleanup_result.original_language,
            "error": cleanup_result.error,
        }
        if not cleanup_result.success:
            logger.error("Stream cleanup failed: %s", cleanup_result.error)

    return results


# ---------------------------------------------------------------------------
# Sonarr / Radarr integration
# ---------------------------------------------------------------------------


def trigger_rename(file_path: str, media_type: str = "auto") -> None:
    """Trigger Sonarr/Radarr refresh and rename after conversion."""
    if media_type == "auto":
        path_lower = file_path.lower()
        media_type = "movie" if ("/movies/" in path_lower or "/films/" in path_lower) else "tv"

    try:
        if media_type == "movie":
            _trigger_radarr_rename(file_path)
        else:
            _trigger_sonarr_rename(file_path)
    except Exception as e:
        logger.error("Failed to trigger rename: %s", e)


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


def _trigger_radarr_rename(file_path: str) -> None:
    radarr_url, radarr_key = _get_radarr_config()

    if not radarr_url or not radarr_key:
        logger.warning("Radarr not configured, skipping rename")
        return

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
        return

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

    # Get movie file ID
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


def _trigger_sonarr_rename(file_path: str) -> None:
    sonarr_url, sonarr_key = _get_sonarr_config()

    if not sonarr_url or not sonarr_key:
        logger.warning("Sonarr not configured, skipping rename")
        return

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
        return

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


def create_job(file_path: str, job_type: JobType, source: str = "api") -> ConversionJob:
    """Create and queue a new conversion job."""
    job = ConversionJob(
        id=uuid.uuid4().hex[:12],
        job_type=job_type,
        file_path=file_path,
        status=JobStatus.PENDING,
        created_at=time.time(),
        source=source,
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

    ffprobe = FFProbe()
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
    )
    video_converter = VideoConverter(
        config=config.video,
        ffprobe=ffprobe,
        anime_detector=anime_detector,
        get_volume_root=get_volume_root,
        ffmpeg_threads=config.effective_ffmpeg_threads,
    )
    stream_cleanup = StreamCleanup(
        config=config.cleanup,
        ffprobe=ffprobe,
        language_detector=language_detector,
        get_volume_root=get_volume_root,
        ffmpeg_threads=config.effective_ffmpeg_threads,
    )

    db_path = os.getenv("REMUXCODE_DB_PATH", str(Path(__file__).parent / "jobs.db"))
    job_store = JobStore(db_path=db_path)

    # Media analysis cache (persistent ffprobe results) — same directory as jobs.db
    media_db_default = str(Path(db_path).parent / "media.db")
    media_db_path = os.getenv("REMUXCODE_MEDIA_DB_PATH", media_db_default)
    media_store = MediaStore(db_path=media_db_path)

    # Purge orphaned rows in background (stat-checking thousands of NAS files is slow)
    threading.Thread(target=media_store.purge_missing, name="purge-missing", daemon=True).start()

    # Clean up old finished jobs on startup
    job_store.cleanup_old_jobs(days=config.job_history_days)

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


def cleanup_temp_dirs() -> None:
    """Clean up orphaned temp directories on startup."""
    logger.info("Cleaning up orphaned temp directories...")
    patterns = [
        ".dts-temp-*",
        ".audio-temp-*",
        ".cleanup-temp-*",
        ".hevc-tmp*",
        ".remuxcode-temp-*",
    ]
    volumes = {host for _, host in PATH_MAPPINGS}
    volumes.add(os.getenv("TEMP_DIR", tempfile.gettempdir()))

    count = 0
    for volume in volumes:
        if not Path(volume).exists():
            continue
        for pattern in patterns:
            for path in Path(volume).glob(pattern):
                try:
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
                    count += 1
                except Exception as e:
                    logger.warning("Failed to remove %s: %s", path, e)

    if count:
        logger.info("Cleaned up %d orphaned temp file(s)/directory(ies)", count)
