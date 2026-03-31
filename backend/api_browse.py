"""Browse library API routes - movies, series, scan, analyze."""

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
import requests

from backend import core
from backend.core import translate_path
from backend.utils.anime_detect import ContentType

logger = logging.getLogger("remuxcode")

router = APIRouter(tags=["browse"])


def _needs_audio_conversion(info: Any) -> bool:
    """Config-aware check whether this file needs audio conversion."""
    cfg = core.config.audio if core.config else None
    if not cfg:
        return False
    if cfg.convert_dts and info.has_dts:
        return True
    if cfg.convert_truehd and info.has_truehd:
        return True
    return False


def _needs_video_conversion(info: Any, is_anime: bool) -> bool:
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


def _get_poster_url(images: list[dict[str, Any]], cover_type: str = "poster") -> str | None:
    """Extract a poster/banner URL from Sonarr/Radarr images array."""
    for img in images:
        if img.get("coverType") == cover_type and img.get("remoteUrl"):
            return img["remoteUrl"]
    return None


def _split_slash_field(value: Any) -> list[str]:
    """Split a Sonarr/Radarr slash-separated field (e.g. 'eng/fre/spa') into a list."""
    if isinstance(value, str):
        return [s.strip() for s in value.split("/") if s.strip()]
    if isinstance(value, list):
        return value
    return []


def _needs_cleanup(media_info: dict[str, Any], *, is_anime: bool = False) -> bool:
    """Check if a file likely needs subtitle/audio cleanup based on Sonarr/Radarr mediaInfo."""
    cfg = core.config.cleanup if core.config else None
    if not cfg or (not cfg.clean_subtitles and not cfg.clean_audio):
        return False
    keep = {lang.lower() for lang in cfg.keep_languages}

    # For anime with anime_keep_original_audio, the original language will be kept
    # at runtime, so we can't flag those audio streams as needing removal.
    # We approximate: if anime, check only subtitles for cleanup needs.
    audio_langs = [a.lower() for a in _split_slash_field(media_info.get("audioLanguages", ""))]
    subs = [s.lower() for s in _split_slash_field(media_info.get("subtitles", ""))]

    if cfg.clean_subtitles and subs:
        extra_subs = [s for s in subs if s not in keep]
        if extra_subs:
            return True
    if cfg.clean_audio and audio_langs:
        if is_anime and cfg.anime_keep_original_audio:
            # For anime, all audio tracks are potentially kept (original + keep_languages),
            # so we can't reliably determine cleanup need from mediaInfo alone.
            # Skip audio-based cleanup flagging for anime — subtitles above still apply.
            pass
        else:
            extra_audio = [a for a in audio_langs if a not in keep]
            if extra_audio:
                return True
    return False


@router.get("/poster/{source}/{item_id}")
def proxy_poster(source: str, item_id: int) -> Response:
    """Proxy poster images from Sonarr/Radarr to avoid CORS issues."""
    if source == "radarr":
        base_url = os.getenv("RADARR_URL", core.config.radarr.url if core.config else "")
        api_key = os.getenv(
            "RADARR_API_KEY",
            core.config.radarr.api_key if core.config else "",
        )
        poster_path = f"/api/v3/mediacover/{item_id}/poster.jpg"
    elif source == "sonarr":
        base_url = os.getenv("SONARR_URL", core.config.sonarr.url if core.config else "")
        api_key = os.getenv(
            "SONARR_API_KEY",
            core.config.sonarr.api_key if core.config else "",
        )
        poster_path = f"/api/v3/mediacover/{item_id}/poster.jpg"
    else:
        raise HTTPException(status_code=400, detail="Source must be 'sonarr' or 'radarr'")

    if not base_url or not api_key:
        raise HTTPException(status_code=500, detail=f"{source} not configured")

    try:
        resp = requests.get(
            f"{base_url}{poster_path}",
            headers={"X-Api-Key": api_key},
            timeout=10,
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=404, detail="Poster not found")
        return Response(
            content=resp.content,
            media_type=resp.headers.get("content-type", "image/jpeg"),
            headers={"Cache-Control": "public, max-age=86400"},
        )
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch poster: {e}") from e


@router.get("/analyze")
def analyze_file(path: str = Query(..., description="Path to media file")) -> dict[str, Any]:
    """Analyze a single file — returns full stream details for MediaInfo-style display."""
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

    video_streams = [
        {
            "index": v.index,
            "codec": v.codec_name,
            "codec_long": v.codec_long_name,
            "profile": v.profile,
            "width": v.width,
            "height": v.height,
            "resolution": f"{v.width}x{v.height}",
            "pix_fmt": v.pix_fmt,
            "bit_depth": v.bit_depth,
            "frame_rate": v.frame_rate,
            "bitrate": v.bitrate,
            "is_hevc": v.is_hevc,
            "is_h264": v.is_h264,
        }
        for v in info.video_streams
    ]

    audio_streams = [
        {
            "index": a.index,
            "codec": a.codec_name,
            "codec_long": a.codec_long_name,
            "channels": a.channels,
            "channel_layout": a.channel_layout,
            "sample_rate": a.sample_rate,
            "bitrate": a.bitrate,
            "language": a.language,
            "title": a.title,
            "is_default": a.is_default,
            "is_dts": a.is_dts,
            "is_truehd": a.is_truehd,
            "is_lossless": a.is_lossless,
        }
        for a in info.audio_streams
    ]

    subtitle_streams = [
        {
            "index": s.index,
            "codec": s.codec_name,
            "language": s.language,
            "title": s.title,
            "is_default": s.is_default,
            "is_forced": s.is_forced,
            "is_sdh": s.is_sdh,
        }
        for s in info.subtitle_streams
    ]

    return {
        "file": str(info.path),
        "format": info.format_name,
        "duration": info.duration,
        "size": info.size,
        "bitrate": info.bitrate,
        "chapters": len(info.chapters),
        "is_anime": is_anime,
        "content_type": content_type.value,
        "needs_audio_conversion": _needs_audio_conversion(info),
        "needs_video_conversion": _needs_video_conversion(info, is_anime),
        "video_streams": video_streams,
        "audio_streams": audio_streams,
        "subtitle_streams": subtitle_streams,
        "format_tags": info.format_tags,
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
        media_info = movie_file.get("mediaInfo", {})
        audio_codec = media_info.get("audioCodec", "").upper()
        video_codec = media_info.get("videoCodec", "").upper()

        # Detect anime early (needed for cleanup language decisions)
        genres_lower = [g.lower() for g in movie.get("genres", [])]
        orig_lang = movie.get("originalLanguage", {}).get("name", "").lower()
        movie_is_anime = "animation" in genres_lower and orig_lang == "japanese"
        if not movie_is_anime and core.anime_detector:
            movie_is_anime = (
                core.anime_detector.detect(host_path, use_api=False) == ContentType.ANIME
            )

        item: dict[str, Any] = {
            "id": movie["id"],
            "title": movie.get("title"),
            "year": movie.get("year"),
            "path": host_path,
            "size": movie_file.get("size"),
            "genres": movie.get("genres", []),
            "poster": f"/api/poster/radarr/{movie['id']}",
            "has_dts": "DTS" in audio_codec,
            "has_truehd": "TRUEHD" in audio_codec,
            "video_codec": video_codec,
            "audio_codec": media_info.get("audioCodec", ""),
            "audio_channels": media_info.get("audioChannels"),
            "audio_languages": _split_slash_field(media_info.get("audioLanguages", "")),
            "subtitles": _split_slash_field(media_info.get("subtitles", "")),
            "resolution": media_info.get("resolution", ""),
            "needs_cleanup": _needs_cleanup(media_info, is_anime=movie_is_anime),
        }

        # Config-aware audio detection from mediaInfo (overridden by ffprobe if analyzed)
        cfg_audio = core.config.audio if core.config else None
        if cfg_audio:
            if cfg_audio.convert_dts and item["has_dts"]:
                item["needs_audio_conversion"] = True
            if cfg_audio.convert_truehd and item["has_truehd"]:
                item["needs_audio_conversion"] = True
        item.setdefault("needs_audio_conversion", False)

        item["is_anime"] = movie_is_anime

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
            and not item.get("needs_cleanup", False)
        ):
            continue
        if filter == "video" and not item.get("needs_video_conversion", False):
            continue
        if filter == "audio" and not item.get("needs_audio_conversion", False):
            continue
        if filter == "anime" and not item.get("is_anime", False):
            continue
        if filter == "cleanup" and not item.get("needs_cleanup", False):
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
            "needs_cleanup": sum(1 for r in results if r.get("needs_cleanup", False)),
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

    logger.info("Fetching series list (%d series, analyze=%s)", len(all_series), analyze)

    results: list[dict[str, Any]] = []
    for series in all_series:
        series_id = series["id"]
        host_path = translate_path(series.get("path", ""))

        # Extract season info from Sonarr statistics
        seasons_data = series.get("seasons", [])
        season_count = sum(
            1
            for s in seasons_data
            if s.get("seasonNumber", 0) > 0
            and s.get("statistics", {}).get("episodeFileCount", 0) > 0
        )

        stats = series.get("statistics", {})
        item: dict[str, Any] = {
            "id": series_id,
            "title": series.get("title"),
            "year": series.get("year"),
            "path": host_path,
            "genres": series.get("genres", []),
            "poster": f"/api/poster/sonarr/{series_id}",
            "season_count": season_count,
            "episode_file_count": stats.get("episodeFileCount", 0),
            "size_on_disk": stats.get("sizeOnDisk", 0),
            "status": series.get("status", ""),
            "series_type": series.get("seriesType", ""),
        }

        # Detect anime from genres (Sonarr/TVDB provides "Anime" genre)
        genres_lower = [g.lower() for g in series.get("genres", [])]
        item["is_anime"] = "anime" in genres_lower

        # Check episode files for audio/cleanup needs via Sonarr mediaInfo
        try:
            ep_response = requests.get(
                f"{sonarr_url}/api/v3/episodefile",
                params={"seriesId": series_id},
                headers={"X-Api-Key": sonarr_key},
                timeout=30,
            )
            if ep_response.status_code == 200:
                episode_files = ep_response.json()
                audio_count = 0
                cleanup_count = 0
                cfg_audio = core.config.audio if core.config else None
                for ef in episode_files:
                    mi = ef.get("mediaInfo", {})
                    ac = mi.get("audioCodec", "").upper()
                    has_audio_issue = False
                    if cfg_audio:
                        if cfg_audio.convert_dts and "DTS" in ac:
                            has_audio_issue = True
                        if cfg_audio.convert_truehd and "TRUEHD" in ac:
                            has_audio_issue = True
                    if has_audio_issue:
                        audio_count += 1
                    if _needs_cleanup(mi, is_anime=item.get("is_anime", False)):
                        cleanup_count += 1
                item["audio_convert_count"] = audio_count
                item["cleanup_count"] = cleanup_count
        except Exception as e:
            logger.warning("Error getting episodes for series %s: %s", series_id, e)

        if (
            filter == "needs_conversion"
            and item.get("audio_convert_count", 0) == 0
            and item.get("video_convert_count", 0) == 0
            and item.get("cleanup_count", 0) == 0
        ):
            continue
        if filter == "video" and item.get("video_convert_count", 0) == 0:
            continue
        if filter == "audio" and item.get("audio_convert_count", 0) == 0:
            continue
        if filter == "anime" and not item["is_anime"]:
            continue
        if filter == "cleanup" and item.get("cleanup_count", 0) == 0:
            continue

        results.append(item)

    return {
        "total": len(results),
        "summary": {
            "needs_audio_conversion": sum(r.get("audio_convert_count", 0) for r in results),
            "needs_video_conversion": sum(r.get("video_convert_count", 0) for r in results),
            "needs_cleanup": sum(r.get("cleanup_count", 0) for r in results),
            "anime_series": sum(1 for r in results if r["is_anime"]),
        },
        "series": results,
    }


@router.get("/series/{series_id}")
def get_series_detail(
    series_id: int,
    analyze: bool = Query(False),
) -> dict[str, Any]:
    """Get detailed series info with seasons and episodes from Sonarr."""
    sonarr_url = os.getenv("SONARR_URL", core.config.sonarr.url if core.config else "")
    sonarr_key = os.getenv("SONARR_API_KEY", core.config.sonarr.api_key if core.config else "")
    if not sonarr_url or not sonarr_key:
        raise HTTPException(status_code=500, detail="Sonarr not configured")

    # Fetch series metadata
    try:
        resp = requests.get(
            f"{sonarr_url}/api/v3/series/{series_id}",
            headers={"X-Api-Key": sonarr_key},
            timeout=15,
        )
        resp.raise_for_status()
        series = resp.json()
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to query Sonarr: {e}",
        ) from e

    # Fetch episode files
    try:
        ef_resp = requests.get(
            f"{sonarr_url}/api/v3/episodefile",
            params={"seriesId": series_id},
            headers={"X-Api-Key": sonarr_key},
            timeout=30,
        )
        ef_resp.raise_for_status()
        episode_files = ef_resp.json()
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to query episode files: {e}",
        ) from e

    # Fetch episodes for metadata (titles, episode numbers)
    try:
        ep_resp = requests.get(
            f"{sonarr_url}/api/v3/episode",
            params={"seriesId": series_id},
            headers={"X-Api-Key": sonarr_key},
            timeout=30,
        )
        ep_resp.raise_for_status()
        episodes = ep_resp.json()
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to query episodes: {e}",
        ) from e

    # Index episode files by ID for lookup
    ef_by_id: dict[int, dict[str, Any]] = {ef["id"]: ef for ef in episode_files}

    # Detect anime from series metadata (Sonarr/TVDB genre or seriesType)
    series_genres = [g.lower() for g in series.get("genres", [])]
    series_is_anime = "anime" in series_genres or series.get("seriesType", "").lower() == "anime"

    # Build season → episode structure
    seasons: dict[int, list[dict[str, Any]]] = {}
    for ep in episodes:
        season_num = ep.get("seasonNumber", 0)
        ep_file_id = ep.get("episodeFileId", 0)
        ef = ef_by_id.get(ep_file_id)
        if not ef:
            continue  # No file on disk

        mi = ef.get("mediaInfo", {})
        host_path = translate_path(ef.get("path", ""))

        ep_item: dict[str, Any] = {
            "episode_number": ep.get("episodeNumber"),
            "title": ep.get("title", ""),
            "path": host_path,
            "size": ef.get("size"),
            "video_codec": mi.get("videoCodec", ""),
            "audio_codec": mi.get("audioCodec", ""),
            "audio_channels": mi.get("audioChannels"),
            "audio_languages": _split_slash_field(mi.get("audioLanguages", "")),
            "subtitles": _split_slash_field(mi.get("subtitles", "")),
            "resolution": mi.get("resolution", ""),
            "needs_cleanup": _needs_cleanup(mi, is_anime=series_is_anime),
        }

        ac = mi.get("audioCodec", "").upper()
        ep_item["has_dts"] = "DTS" in ac
        ep_item["has_truehd"] = "TRUEHD" in ac

        # Config-aware audio detection from mediaInfo
        cfg_audio = core.config.audio if core.config else None
        ep_item["needs_audio_conversion"] = False
        if cfg_audio:
            if cfg_audio.convert_dts and ep_item["has_dts"]:
                ep_item["needs_audio_conversion"] = True
            if cfg_audio.convert_truehd and ep_item["has_truehd"]:
                ep_item["needs_audio_conversion"] = True

        # Deep ffprobe analysis if requested
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
                    ep_item.update(
                        {
                            "needs_audio_conversion": _needs_audio_conversion(info),
                            "needs_video_conversion": _needs_video_conversion(info, is_anime),
                            "is_anime": is_anime,
                        }
                    )
            except Exception:
                pass

        seasons.setdefault(season_num, []).append(ep_item)

    # Sort episodes within each season
    for eps in seasons.values():
        eps.sort(key=lambda e: e.get("episode_number", 0))

    # Build season summaries
    season_list = []
    for season_num in sorted(seasons.keys()):
        eps = seasons[season_num]
        season_list.append(
            {
                "season_number": season_num,
                "episode_count": len(eps),
                "needs_audio": sum(1 for e in eps if e.get("needs_audio_conversion")),
                "needs_cleanup": sum(1 for e in eps if e.get("needs_cleanup")),
                "needs_work": sum(
                    1 for e in eps if e.get("needs_audio_conversion") or e.get("needs_cleanup")
                ),
                "size": sum(e.get("size", 0) or 0 for e in eps),
                "episodes": eps,
            }
        )

    host_path = translate_path(series.get("path", ""))
    genres_lower = [g.lower() for g in series.get("genres", [])]
    is_anime = "anime" in genres_lower

    return {
        "id": series_id,
        "title": series.get("title"),
        "year": series.get("year"),
        "path": host_path,
        "genres": series.get("genres", []),
        "poster": f"/api/poster/sonarr/{series_id}",
        "status": series.get("status", ""),
        "is_anime": is_anime,
        "seasons": season_list,
    }
