"""Library analysis API — background scan and progress tracking."""

import logging
from pathlib import Path
import threading
from typing import Any

from fastapi import APIRouter, HTTPException
import requests

from backend import core

logger = logging.getLogger("remuxcode")

router = APIRouter(prefix="/analyze", tags=["analyze"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def build_analysis_dict(info: Any) -> dict[str, Any]:
    """Build a compact analysis dict from a MediaInfo for DB storage."""
    v = info.primary_video
    return {
        "video_codec": v.codec_name if v else None,
        "video_profile": v.profile if v else None,
        "width": v.width if v else None,
        "height": v.height if v else None,
        "bit_depth": v.bit_depth if v else None,
        "is_hevc": v.is_hevc if v else False,
        "is_h264": v.is_h264 if v else False,
        "is_av1": v.is_av1 if v else False,
        "audio_streams": [
            {
                "codec": a.codec_name,
                "profile": a.profile,
                "channels": a.channels,
                "language": a.language,
                "title": a.title,
                "is_dts": a.is_dts,
                "is_dts_x": a.is_dts_x,
                "is_truehd": a.is_truehd,
            }
            for a in info.audio_streams
        ],
        "has_dts": info.has_dts,
        "has_dts_x": info.has_dts_x,
        "has_truehd": info.has_truehd,
        "subtitle_count": len(info.subtitle_streams),
    }


def analyze_and_store(
    file_path: str,
    *,
    radarr_movie_file_id: int | None = None,
    sonarr_episode_file_id: int | None = None,
) -> bool:
    """FFprobe a file and store analysis in the media store. Returns True on success."""
    if not core.media_store or not core.ffprobe:
        return False
    p = Path(file_path)
    if not p.exists():
        return False
    try:
        stat = p.stat()
        info = core.ffprobe.get_file_info(file_path)
        if not info:
            return False
        analysis = build_analysis_dict(info)
        core.media_store.upsert(
            file_path,
            analysis,
            stat.st_mtime,
            stat.st_size,
            radarr_movie_file_id=radarr_movie_file_id,
            sonarr_episode_file_id=sonarr_episode_file_id,
        )
        return True
    except Exception as e:
        logger.warning("Failed to analyze %s: %s", file_path, e)
        return False


# ---------------------------------------------------------------------------
# Background scan state
# ---------------------------------------------------------------------------

_scan_lock = threading.Lock()
_scan_thread: threading.Thread | None = None
_scan_cancel = threading.Event()
_scan_progress: dict[str, Any] = {
    "running": False,
    "type": None,
    "total": 0,
    "analyzed": 0,
    "skipped": 0,
    "failed": 0,
    "current_file": None,
}


def _update_progress(**kwargs: Any) -> None:
    with _scan_lock:
        _scan_progress.update(kwargs)


def _get_progress() -> dict[str, Any]:
    with _scan_lock:
        return dict(_scan_progress)


# ---------------------------------------------------------------------------
# Scan workers
# ---------------------------------------------------------------------------


def _scan_radarr_library() -> None:
    """Background: ffprobe all Radarr movies missing from the analysis cache."""
    radarr_url = core.config.radarr.url if core.config else ""
    radarr_key = core.config.radarr.api_key if core.config else ""
    if not radarr_url or not radarr_key:
        _update_progress(running=False, current_file=None)
        return

    try:
        resp = requests.get(
            f"{radarr_url}/api/v3/movie",
            headers={"X-Api-Key": radarr_key},
            timeout=60,
        )
        resp.raise_for_status()
        all_movies = resp.json()
    except Exception as e:
        logger.error("Library scan: failed to fetch movies from Radarr: %s", e)
        _update_progress(running=False, current_file=None)
        return

    # Collect files to scan
    files: list[tuple[str, int | None]] = []
    for movie in all_movies:
        if not movie.get("hasFile"):
            continue
        mf = movie.get("movieFile", {})
        path = mf.get("path")
        if not path:
            continue
        host_path = core.translate_path(path)
        files.append((host_path, mf.get("id")))

    _update_progress(total=len(files))
    logger.info("Movie scan started: %d files to process", len(files))

    # Bulk lookup existing analysis
    radarr_ids = [fid for _, fid in files if fid is not None]
    existing = core.media_store.bulk_lookup_radarr(radarr_ids) if core.media_store else {}

    analyzed = 0
    skipped = 0
    failed = 0
    for file_path, movie_file_id in files:
        if _scan_cancel.is_set():
            break

        # Skip if already analyzed and fresh
        entry = existing.get(movie_file_id) if movie_file_id else None
        if entry and core.media_store and core.media_store.is_fresh(entry):
            skipped += 1
            _update_progress(analyzed=analyzed, skipped=skipped)
            continue

        _update_progress(current_file=Path(file_path).name)
        ok = analyze_and_store(file_path, radarr_movie_file_id=movie_file_id)
        if ok:
            analyzed += 1
        else:
            failed += 1
        _update_progress(analyzed=analyzed, skipped=skipped, failed=failed)

    _update_progress(running=False, current_file=None)
    logger.info(
        "Movie scan complete: %d analyzed, %d skipped, %d failed",
        analyzed,
        skipped,
        failed,
    )


def _scan_sonarr_library() -> None:
    """Background: ffprobe all Sonarr episode files missing from the analysis cache."""
    sonarr_url = core.config.sonarr.url if core.config else ""
    sonarr_key = core.config.sonarr.api_key if core.config else ""
    if not sonarr_url or not sonarr_key:
        _update_progress(running=False, current_file=None)
        return

    try:
        resp = requests.get(
            f"{sonarr_url}/api/v3/series",
            headers={"X-Api-Key": sonarr_key},
            timeout=60,
        )
        resp.raise_for_status()
        all_series = resp.json()
    except Exception as e:
        logger.error("Library scan: failed to fetch series from Sonarr: %s", e)
        _update_progress(running=False, current_file=None)
        return

    # Collect all episode files across all series
    files: list[tuple[str, int | None]] = []
    for series in all_series:
        series_id = series["id"]
        try:
            ef_resp = requests.get(
                f"{sonarr_url}/api/v3/episodefile",
                params={"seriesId": series_id},
                headers={"X-Api-Key": sonarr_key},
                timeout=30,
            )
            if ef_resp.status_code == 200:
                for ef in ef_resp.json():
                    path = ef.get("path")
                    if path:
                        host_path = core.translate_path(path)
                        files.append((host_path, ef.get("id")))
        except Exception as e:
            logger.warning("Library scan: error fetching episodes for series %d: %s", series_id, e)

    _update_progress(total=len(files))
    logger.info("Series scan started: %d files to process", len(files))

    # Bulk lookup
    sonarr_ids = [fid for _, fid in files if fid is not None]
    existing = core.media_store.bulk_lookup_sonarr(sonarr_ids) if core.media_store else {}

    analyzed = 0
    skipped = 0
    failed = 0
    for file_path, episode_file_id in files:
        if _scan_cancel.is_set():
            break

        entry = existing.get(episode_file_id) if episode_file_id else None
        if entry and core.media_store and core.media_store.is_fresh(entry):
            skipped += 1
            _update_progress(analyzed=analyzed, skipped=skipped)
            continue

        _update_progress(current_file=Path(file_path).name)
        ok = analyze_and_store(file_path, sonarr_episode_file_id=episode_file_id)
        if ok:
            analyzed += 1
        else:
            failed += 1
        _update_progress(analyzed=analyzed, skipped=skipped, failed=failed)

    _update_progress(running=False, current_file=None)
    logger.info(
        "Series scan complete: %d analyzed, %d skipped, %d failed",
        analyzed,
        skipped,
        failed,
    )


def _start_scan(scan_type: str, target_fn: Any) -> dict[str, Any]:
    global _scan_thread
    with _scan_lock:
        if _scan_progress["running"]:
            raise HTTPException(
                status_code=409,
                detail=f"A scan is already running ({_scan_progress['type']})",
            )
        _scan_cancel.clear()
        _scan_progress.update(
            running=True,
            type=scan_type,
            total=0,
            analyzed=0,
            skipped=0,
            failed=0,
            current_file=None,
        )

    _scan_thread = threading.Thread(target=target_fn, name=f"scan-{scan_type}", daemon=True)
    _scan_thread.start()
    return {"message": f"Started {scan_type} library scan", "type": scan_type}


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


@router.post("/scan/movies")
def start_movie_scan() -> dict[str, Any]:
    """Start a background scan of all Radarr movies."""
    return _start_scan("movies", _scan_radarr_library)


@router.post("/scan/series")
def start_series_scan() -> dict[str, Any]:
    """Start a background scan of all Sonarr series."""
    return _start_scan("series", _scan_sonarr_library)


@router.get("/scan/progress")
def scan_progress() -> dict[str, Any]:
    """Get current scan progress."""
    return _get_progress()


@router.post("/scan/stop")
def stop_scan() -> dict[str, Any]:
    """Cancel a running library scan."""
    _scan_cancel.set()
    return {"message": "Scan stop requested"}


@router.get("/stats")
def analysis_stats() -> dict[str, Any]:
    """Return analysis DB coverage stats."""
    if not core.media_store:
        return {"total_analyzed": 0, "radarr_files": 0, "sonarr_files": 0}
    return core.media_store.get_stats()
