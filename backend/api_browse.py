"""Browse library API routes - movies, series, scan, analyze."""

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
import requests

from backend import core
from backend.core import translate_path
from backend.utils.anime_detect import ContentType

logger = logging.getLogger("remuxcode")

router = APIRouter(tags=["browse"])


def _needs_audio_conversion(info) -> bool:
    """Config-aware check whether this file needs audio conversion."""
    cfg = core.config.audio if core.config else None
    if not cfg:
        return False
    if cfg.convert_dts and info.has_dts:
        return True
    if cfg.convert_truehd and info.has_truehd:
        return True
    return False


def _needs_video_conversion(info, is_anime: bool) -> bool:
    """Config-aware check whether this file needs video conversion."""
    cfg = core.config.video if core.config else None
    if not cfg:
        return False
    video = info.primary_video
    if video is None:
        return False
    if cfg.anime_only and not is_anime:
        return False
    if video.is_10bit_h264 and cfg.convert_10bit_x264:
        return True
    if video.is_h264 and not video.is_10bit and cfg.convert_8bit_x264:
        return True
    return False


@router.get("/analyze")
def analyze_file(path: str = Query(..., description="Path to media file")) -> dict[str, Any]:
    """Analyze a single file without converting."""
    file_path = translate_path(path)
    if not Path(file_path).exists():
        raise HTTPException(status_code=404, detail="File not found")

    info = core.ffprobe.get_file_info(file_path) if core.ffprobe else None
    if not info:
        raise HTTPException(status_code=500, detail="Failed to analyze file")

    content_type = (
        core.anime_detector.detect(file_path, use_api=False)
        if core.anime_detector
        else ContentType.UNKNOWN
    )
    is_anime = content_type == ContentType.ANIME

    return {
        "file": file_path,
        "format": info.format_name,
        "duration": info.duration,
        "size": info.size,
        "video": {
            "codec": info.primary_video.codec_name if info.primary_video else None,
            "bit_depth": info.primary_video.bit_depth if info.primary_video else None,
            "resolution": (
                f"{info.primary_video.width}x{info.primary_video.height}"
                if info.primary_video
                else None
            ),
            "profile": info.primary_video.profile if info.primary_video else None,
            "is_hevc": info.is_hevc,
            "is_10bit_h264": info.primary_video.is_10bit_h264 if info.primary_video else False,
        },
        "audio_streams": len(info.audio_streams),
        "has_dts": info.has_dts,
        "has_truehd": info.has_truehd,
        "needs_audio_conversion": _needs_audio_conversion(info),
        "needs_video_conversion": _needs_video_conversion(info, is_anime),
        "is_anime": is_anime,
        "content_type": content_type.value,
    }


@router.get("/scan")
def scan_directory(
    path: str = Query(..., description="Directory to scan"),
    recursive: bool = Query(True),
    filter: str = Query("any", description="Filter: any, video, audio, anime"),
) -> dict[str, Any]:
    """Scan a directory for files needing conversion."""
    dir_path = translate_path(path)
    if not Path(dir_path).is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")

    extensions = {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".m4v"}
    results: list[dict[str, Any]] = []
    pattern = "**/*" if recursive else "*"

    for ext in extensions:
        for file_path in Path(dir_path).glob(pattern + ext):
            if not file_path.is_file():
                continue
            try:
                info = core.ffprobe.get_file_info(str(file_path)) if core.ffprobe else None
                if not info:
                    continue

                content_type = (
                    core.anime_detector.detect(str(file_path), use_api=False)
                    if core.anime_detector
                    else ContentType.UNKNOWN
                )
                is_anime = content_type == ContentType.ANIME

                needs_audio = _needs_audio_conversion(info)
                needs_video = _needs_video_conversion(info, is_anime)

                if filter == "video" and not needs_video:
                    continue
                if filter == "audio" and not needs_audio:
                    continue
                if filter == "anime" and not is_anime:
                    continue

                results.append(
                    {
                        "file": str(file_path),
                        "size": info.size,
                        "video": {
                            "codec": info.primary_video.codec_name if info.primary_video else None,
                            "bit_depth": info.primary_video.bit_depth
                            if info.primary_video
                            else None,
                            "is_10bit_h264": info.primary_video.is_10bit_h264
                            if info.primary_video
                            else False,
                            "is_hevc": info.is_hevc,
                        },
                        "has_dts": info.has_dts,
                        "has_truehd": info.has_truehd,
                        "needs_audio_conversion": needs_audio,
                        "needs_video_conversion": needs_video,
                        "is_anime": is_anime,
                    }
                )
            except Exception as e:
                logger.warning("Error scanning %s: %s", file_path, e)

    return {
        "directory": dir_path,
        "recursive": recursive,
        "filter": filter,
        "total_files": len(results),
        "summary": {
            "needs_video_conversion": sum(1 for r in results if r["needs_video_conversion"]),
            "needs_audio_conversion": sum(1 for r in results if r["needs_audio_conversion"]),
            "anime_files": sum(1 for r in results if r["is_anime"]),
        },
        "files": results,
    }


@router.get("/movies")
def list_movies(
    search: str | None = Query(None),
    analyze: bool = Query(True),
    filter: str = Query("any", description="Filter: any, needs_conversion, video, audio, anime"),
) -> dict[str, Any]:
    """List movies from Radarr with optional media analysis."""
    radarr_url = os.getenv("RADARR_URL", core.config.radarr.url if core.config else "")
    radarr_key = os.getenv("RADARR_API_KEY", core.config.radarr.api_key if core.config else "")
    if not radarr_url or not radarr_key:
        raise HTTPException(status_code=500, detail="Radarr not configured")

    try:
        response = requests.get(
            f"{radarr_url}/api/v3/movie",
            headers={"X-Api-Key": radarr_key},
            timeout=30,
        )
        response.raise_for_status()
        all_movies = response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to query Radarr: {e}") from e

    if search:
        search_lower = search.lower()
        all_movies = [m for m in all_movies if search_lower in m.get("title", "").lower()]

    results: list[dict[str, Any]] = []
    for movie in all_movies:
        if not movie.get("hasFile"):
            continue
        movie_file = movie.get("movieFile", {})
        file_path = movie_file.get("path")
        if not file_path:
            continue

        host_path = translate_path(file_path)
        item: dict[str, Any] = {
            "id": movie["id"],
            "title": movie.get("title"),
            "year": movie.get("year"),
            "path": host_path,
        }

        media_info = movie_file.get("mediaInfo", {})
        audio_codec = media_info.get("audioCodec", "").upper()
        video_codec = media_info.get("videoCodec", "").upper()
        item["has_dts"] = "DTS" in audio_codec
        item["video_codec"] = video_codec

        if analyze and Path(host_path).exists() and core.ffprobe:
            try:
                info = core.ffprobe.get_file_info(host_path)
                if info:
                    content_type = (
                        core.anime_detector.detect(host_path, use_api=False)
                        if core.anime_detector
                        else ContentType.UNKNOWN
                    )
                    is_anime = content_type == ContentType.ANIME
                    needs_audio = _needs_audio_conversion(info)
                    needs_video = _needs_video_conversion(info, is_anime)
                    item.update(
                        {
                            "video": {
                                "codec": info.primary_video.codec_name
                                if info.primary_video
                                else None,
                                "bit_depth": info.primary_video.bit_depth
                                if info.primary_video
                                else None,
                            },
                            "has_dts": info.has_dts,
                            "has_truehd": info.has_truehd,
                            "needs_audio_conversion": needs_audio,
                            "needs_video_conversion": needs_video,
                            "is_anime": is_anime,
                        }
                    )
            except Exception as e:
                logger.warning("Error analyzing %s: %s", host_path, e)

        if (
            filter == "needs_conversion"
            and not item.get("needs_audio_conversion", False)
            and not item.get("needs_video_conversion", False)
        ):
            continue
        if filter == "video" and not item.get("needs_video_conversion", False):
            continue
        if filter == "audio" and not item.get("needs_audio_conversion", False):
            continue
        if filter == "anime" and not item.get("is_anime", False):
            continue

        results.append(item)

    return {
        "total": len(results),
        "summary": {
            "needs_video_conversion": sum(
                1 for r in results if r.get("needs_video_conversion", False)
            ),
            "needs_audio_conversion": sum(
                1 for r in results if r.get("needs_audio_conversion", False)
            ),
            "anime": sum(1 for r in results if r.get("is_anime", False)),
        },
        "movies": results,
    }


@router.get("/series")
def list_series(
    search: str | None = Query(None),
    analyze: bool = Query(True),
    filter: str = Query("any", description="Filter: any, needs_conversion, video, audio, anime"),
) -> dict[str, Any]:
    """List series from Sonarr with optional media analysis."""
    sonarr_url = os.getenv("SONARR_URL", core.config.sonarr.url if core.config else "")
    sonarr_key = os.getenv("SONARR_API_KEY", core.config.sonarr.api_key if core.config else "")
    if not sonarr_url or not sonarr_key:
        raise HTTPException(status_code=500, detail="Sonarr not configured")

    try:
        response = requests.get(
            f"{sonarr_url}/api/v3/series",
            headers={"X-Api-Key": sonarr_key},
            timeout=30,
        )
        response.raise_for_status()
        all_series = response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to query Sonarr: {e}") from e

    if search:
        search_lower = search.lower()
        all_series = [s for s in all_series if search_lower in s.get("title", "").lower()]

    logger.info("Analyzing %d series (analyze=%s)", len(all_series), analyze)

    results: list[dict[str, Any]] = []
    for idx, series in enumerate(all_series, 1):
        series_id = series["id"]
        host_path = translate_path(series.get("path", ""))

        item: dict[str, Any] = {
            "id": series_id,
            "title": series.get("title"),
            "year": series.get("year"),
            "path": host_path,
            "total_episodes": 0,
            "audio_convert_count": 0,
            "video_convert_count": 0,
            "anime_count": 0,
        }

        try:
            ep_response = requests.get(
                f"{sonarr_url}/api/v3/episodefile",
                params={"seriesId": series_id},
                headers={"X-Api-Key": sonarr_key},
                timeout=30,
            )
            if ep_response.status_code == 200:
                episode_files = ep_response.json()
                item["total_episodes"] = len(episode_files)

                if analyze and len(all_series) > 1:
                    logger.info(
                        "  [%d/%d] %s - %d episodes",
                        idx,
                        len(all_series),
                        series.get("title"),
                        len(episode_files),
                    )

                for ep_file in episode_files:
                    ep_path = translate_path(ep_file.get("path", ""))

                    if analyze and Path(ep_path).exists() and core.ffprobe:
                        try:
                            info = core.ffprobe.get_file_info(ep_path)
                            if info:
                                content_type = (
                                    core.anime_detector.detect(ep_path, use_api=False)
                                    if core.anime_detector
                                    else ContentType.UNKNOWN
                                )
                                is_anime = content_type == ContentType.ANIME
                                if is_anime:
                                    item["anime_count"] += 1
                                if _needs_audio_conversion(info):
                                    item["audio_convert_count"] += 1
                                if _needs_video_conversion(info, is_anime):
                                    item["video_convert_count"] += 1
                        except Exception:
                            pass
        except Exception as e:
            logger.warning("Error getting episodes for series %s: %s", series_id, e)

        item["is_anime"] = (
            core.anime_detector.detect(host_path, use_api=False) == ContentType.ANIME
            if core.anime_detector
            else False
        )

        if (
            filter == "needs_conversion"
            and item["audio_convert_count"] == 0
            and item["video_convert_count"] == 0
        ):
            continue
        if filter == "video" and item["video_convert_count"] == 0:
            continue
        if filter == "audio" and item["audio_convert_count"] == 0:
            continue
        if filter == "anime" and not item["is_anime"]:
            continue

        results.append(item)

    return {
        "total": len(results),
        "summary": {
            "needs_audio_conversion": sum(r["audio_convert_count"] for r in results),
            "needs_video_conversion": sum(r["video_convert_count"] for r in results),
            "anime_series": sum(1 for r in results if r["is_anime"]),
        },
        "series": results,
    }
