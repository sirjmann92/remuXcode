"""Browse library API routes - movies, series, scan, analyze."""

import logging
from pathlib import Path
import threading
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
import requests

from backend import core
from backend.core import translate_path
from backend.utils.anime_detect import ContentType

logger = logging.getLogger("remuxcode")


# ---------------------------------------------------------------------------
# Server-side response cache for Sonarr/Radarr API calls
# ---------------------------------------------------------------------------

_cache_lock = threading.Lock()
_cache: dict[str, dict[str, Any]] = {}
# Each entry: {"data": <response dict>, "timestamp": <float>}

CACHE_TTL = 300  # 5 minutes — fresh data served within this window


def _cache_get(key: str) -> dict[str, Any] | None:
    """Get cached data. Returns None if no cache exists."""
    with _cache_lock:
        return _cache.get(key)


def _cache_set(key: str, data: dict[str, Any]) -> None:
    """Store data in cache with current timestamp."""
    with _cache_lock:
        _cache[key] = {"data": data, "timestamp": time.monotonic()}


def invalidate_cache(key: str | None = None) -> None:
    """Soft-invalidate cache entries (mark stale but keep for fallback).

    Stale entries are still served if the upstream API is unreachable,
    preventing timeouts during heavy processing.
    """
    with _cache_lock:
        if key is None:
            for entry in _cache.values():
                entry["stale"] = True
        elif key in _cache:
            _cache[key]["stale"] = True


router = APIRouter(tags=["browse"])


def _has_compatible_companion(lang: str, compatible_langs: set[str]) -> bool:
    """Check if a stream's language already has a compatible (non-DTS/TrueHD) track.

    When keep_original* is enabled, the converted companion track persists
    alongside the original.  This detects that situation to avoid re-flagging
    already-processed files.
    """
    if lang == "und":
        return bool(compatible_langs)
    return lang in compatible_langs


def _compatible_track_languages(streams: Any, *, use_dicts: bool = False) -> set[str]:
    """Build set of languages that have a compatible (non-DTS, non-TrueHD) track."""
    langs: set[str] = set()
    for s in streams:
        if use_dicts:
            if not s.get("is_dts", False) and not s.get("is_truehd", False):
                langs.add((s.get("language") or "und").lower())
        elif not s.is_dts and not s.is_truehd:
            langs.add((s.language or "und").lower())
    return langs


def _needs_audio_conversion(info: Any) -> bool:
    """Config-aware check whether this file needs audio work.

    Returns True when any incompatible track needs conversion OR removal
    (i.e. a companion already exists but keep_original is now off).
    """
    cfg = core.config.audio if core.config else None
    if not cfg:
        return False
    # Always build compat so we detect tracks that should be dropped
    compat = _compatible_track_languages(info.audio_streams)
    for s in info.audio_streams:
        lang = (s.language or "und").lower()
        if cfg.convert_dts and s.is_dts and not s.is_dts_x:
            if cfg.keep_original and _has_compatible_companion(lang, compat):
                continue
            return True
        if cfg.convert_dts_x and s.is_dts_x:
            if cfg.keep_original_dts_x and _has_compatible_companion(lang, compat):
                continue
            return True
        if cfg.convert_truehd and s.is_truehd:
            if cfg.keep_original and _has_compatible_companion(lang, compat):
                continue
            return True
    return False


def _needs_audio_conversion_from_streams(streams: list[dict[str, Any]]) -> bool:
    """Stream-level check using cached analysis data (from media.db)."""
    cfg = core.config.audio if core.config else None
    if not cfg:
        return False
    # Always build compat so we detect tracks that should be dropped
    compat = _compatible_track_languages(streams, use_dicts=True)
    for s in streams:
        is_dts = s.get("is_dts", False)
        is_dts_x = s.get("is_dts_x", False)
        is_truehd = s.get("is_truehd", False)
        lang = (s.get("language") or "und").lower()
        if cfg.convert_dts and is_dts and not is_dts_x:
            if cfg.keep_original and _has_compatible_companion(lang, compat):
                continue
            return True
        if cfg.convert_dts_x and is_dts_x:
            if cfg.keep_original_dts_x and _has_compatible_companion(lang, compat):
                continue
            return True
        if cfg.convert_truehd and is_truehd:
            if cfg.keep_original and _has_compatible_companion(lang, compat):
                continue
            return True
    return False


def _audio_codecs_to_convert(streams: list[dict[str, Any]]) -> list[str]:
    """Return list of codec names that need conversion (for detail display).

    Only includes tracks that will actually be transcoded (no companion exists).
    Tracks with a companion are either already processed (keep_original on)
    or will be dropped (keep_original off) — neither needs transcoding.
    """
    cfg = core.config.audio if core.config else None
    if not cfg:
        return []
    compat = _compatible_track_languages(streams, use_dicts=True)
    codecs: list[str] = []
    for s in streams:
        is_dts = s.get("is_dts", False)
        is_dts_x = s.get("is_dts_x", False)
        is_truehd = s.get("is_truehd", False)
        codec = s.get("codec", "")
        lang = (s.get("language") or "und").lower()
        if cfg.convert_dts and is_dts and not is_dts_x:
            if _has_compatible_companion(lang, compat):
                continue
            codecs.append(codec.upper() or "DTS")
        elif cfg.convert_dts_x and is_dts_x:
            if _has_compatible_companion(lang, compat):
                continue
            codecs.append("DTS:X")
        elif cfg.convert_truehd and is_truehd:
            if _has_compatible_companion(lang, compat):
                continue
            codecs.append(codec.upper() or "TrueHD")
    return codecs


def _audio_codecs_to_drop(streams: list[dict[str, Any]]) -> list[str]:
    """Return list of codec names that will be dropped (companion already exists)."""
    cfg = core.config.audio if core.config else None
    if not cfg:
        return []
    compat = _compatible_track_languages(streams, use_dicts=True)
    codecs: list[str] = []
    for s in streams:
        is_dts = s.get("is_dts", False)
        is_dts_x = s.get("is_dts_x", False)
        is_truehd = s.get("is_truehd", False)
        codec = s.get("codec", "")
        lang = (s.get("language") or "und").lower()
        if cfg.convert_dts and is_dts and not is_dts_x:
            if not cfg.keep_original and _has_compatible_companion(lang, compat):
                codecs.append(codec.upper() or "DTS")
        elif cfg.convert_dts_x and is_dts_x:
            if not cfg.keep_original_dts_x and _has_compatible_companion(lang, compat):
                codecs.append("DTS:X")
        elif cfg.convert_truehd and is_truehd:
            if not cfg.keep_original and _has_compatible_companion(lang, compat):
                codecs.append(codec.upper() or "TrueHD")
    return codecs


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
    """Check if a file likely needs subtitle/audio cleanup based on Sonarr/Radarr mediaInfo.

    This is an imprecise check — Radarr's mediaInfo lacks track titles,
    so commentary/audio-description exemptions cannot be applied here.
    Use _needs_cleanup_from_streams() when ffprobe-level data is available.
    """
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
            extra_audio = [a for a in audio_langs if a and a != "und" and a not in keep]
            if extra_audio:
                return True
    return False


def _needs_cleanup_from_streams(
    audio_streams: list[dict[str, Any]],
    subtitle_langs: list[str],
    *,
    is_anime: bool = False,
) -> bool:
    """Precise cleanup check using stream-level data (applies commentary/AD exemptions)."""
    cfg = core.config.cleanup if core.config else None
    if not cfg or (not cfg.clean_subtitles and not cfg.clean_audio):
        return False
    keep = {lang.lower() for lang in cfg.keep_languages}

    if cfg.clean_subtitles and subtitle_langs:
        extra_subs = [s for s in subtitle_langs if s.lower() not in keep]
        if extra_subs:
            return True

    if cfg.clean_audio and audio_streams:
        if is_anime and cfg.anime_keep_original_audio:
            pass
        else:
            for stream in audio_streams:
                lang = (stream.get("language") or "").lower()

                # Untagged streams are always kept at runtime
                if not lang or lang == "und":
                    continue
                if lang in keep:
                    continue
                # This stream would be removed → needs cleanup
                return True

    return False


@router.get("/poster/{source}/{item_id}")
def proxy_poster(source: str, item_id: int) -> Response:
    """Proxy poster images from Sonarr/Radarr to avoid CORS issues."""
    if source == "radarr":
        base_url = core.config.radarr.url if core.config else ""
        api_key = core.config.radarr.api_key if core.config else ""
        poster_path = f"/api/v3/mediacover/{item_id}/poster.jpg"
    elif source == "sonarr":
        base_url = core.config.sonarr.url if core.config else ""
        api_key = core.config.sonarr.api_key if core.config else ""
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

    # Store analysis in media.db for filter/badge use
    if core.media_store:
        try:
            from backend.api_analyze import build_analysis_dict

            stat = Path(file_path).stat()
            core.media_store.upsert(
                file_path, build_analysis_dict(info), stat.st_mtime, stat.st_size
            )
            logger.info("Stored analysis for %s", Path(file_path).name)
        except Exception:
            logger.warning("Failed to store analysis in media.db", exc_info=True)

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

    # Build stream dicts usable by the shared codec helpers
    audio_stream_dicts = [
        {
            "codec": a.codec_name,
            "language": a.language,
            "is_dts": a.is_dts,
            "is_dts_x": a.is_dts_x,
            "is_truehd": a.is_truehd,
        }
        for a in info.audio_streams
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
        "audio_codecs_to_convert": _audio_codecs_to_convert(audio_stream_dicts),
        "audio_codecs_to_drop": _audio_codecs_to_drop(audio_stream_dicts),
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
    cache_bust: bool = Query(False, description="Force bypass server cache"),
) -> dict[str, Any]:
    """List movies from Radarr with optional media analysis."""
    radarr_url = core.config.radarr.url if core.config else ""
    radarr_key = core.config.radarr.api_key if core.config else ""
    if not radarr_url or not radarr_key:
        raise HTTPException(status_code=500, detail="Radarr not configured")

    # Try to serve from cache when not analyzing
    cache_key = "movies"
    cached = _cache_get(cache_key)
    now = time.monotonic()

    if not analyze and not cache_bust and cached and not cached.get("stale"):
        age = now - cached["timestamp"]
        if age < CACHE_TTL:
            return _apply_movie_filters(cached["data"], search, filter)

    # Fetch fresh data from Radarr
    try:
        response = requests.get(
            f"{radarr_url}/api/v3/movie",
            headers={"X-Api-Key": radarr_key},
            timeout=60,
        )
        response.raise_for_status()
        all_movies = response.json()
    except Exception as e:
        # Serve stale/cached data on upstream failure
        if cached:
            logger.warning("Radarr unavailable, serving stale cache: %s", e)
            return _apply_movie_filters(cached["data"], search, filter)
        raise HTTPException(status_code=500, detail=f"Failed to query Radarr: {e}") from e

    full_results = _build_movie_results(all_movies, analyze)
    full_response = _build_movie_response(full_results)

    # Cache the full unfiltered result
    _cache_set(cache_key, full_response)

    return _apply_movie_filters(full_response, search, filter)


def _build_movie_results(all_movies: list[dict[str, Any]], analyze: bool) -> list[dict[str, Any]]:
    """Build processed movie results from raw Radarr data."""
    results: list[dict[str, Any]] = []
    movie_file_ids: list[int] = []

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
        if not movie_is_anime and analyze and core.anime_detector:
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
            "has_dts_x": False,
            "video_codec": video_codec,
            "audio_codec": media_info.get("audioCodec", ""),
            "audio_channels": media_info.get("audioChannels"),
            "audio_languages": _split_slash_field(media_info.get("audioLanguages", "")),
            "subtitles": _split_slash_field(media_info.get("subtitles", "")),
            "resolution": media_info.get("resolution", ""),
            "needs_cleanup": _needs_cleanup(media_info, is_anime=movie_is_anime),
            "analyzed": False,
        }

        # Track movie_file_id for bulk analysis lookup
        mf_id = movie_file.get("id")
        item["_movie_file_id"] = mf_id
        if mf_id is not None:
            movie_file_ids.append(mf_id)

        # Config-aware audio detection from mediaInfo (overridden by ffprobe/cache if available)
        # Radarr mediaInfo only reports the primary track codec, so this is approximate.
        cfg_audio = core.config.audio if core.config else None
        if cfg_audio:
            if cfg_audio.convert_dts and item["has_dts"]:
                item["needs_audio_conversion"] = True
            if cfg_audio.convert_truehd and item["has_truehd"]:
                item["needs_audio_conversion"] = True
        item.setdefault("needs_audio_conversion", False)

        # Config-aware video detection from Radarr mediaInfo (overridden by ffprobe if analyzed)
        cfg_video_m = core.config.video if core.config else None
        if cfg_video_m:
            vc_m = media_info.get("videoCodec", "").lower()
            vbd_m = media_info.get("videoBitDepth", 8) or 8
            is_h264_m = any(x in vc_m for x in ("x264", "avc", "h264"))
            if not cfg_video_m.anime_only or movie_is_anime:
                if (cfg_video_m.convert_10bit_x264 and is_h264_m and vbd_m >= 10) or (
                    cfg_video_m.convert_8bit_x264 and is_h264_m and vbd_m < 10
                ):
                    item["needs_video_conversion"] = True
        item.setdefault("needs_video_conversion", False)

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
                    # Precise cleanup check using full stream data with titles
                    stream_dicts = [
                        {"language": a.language, "title": a.title} for a in info.audio_streams
                    ]
                    sub_langs = [s.language or "" for s in info.subtitle_streams]
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
                            "has_dts_x": info.has_dts_x,
                            "needs_audio_conversion": needs_audio,
                            "needs_video_conversion": needs_video,
                            "needs_cleanup": _needs_cleanup_from_streams(
                                stream_dicts,
                                sub_langs,
                                is_anime=is_anime,
                            ),
                            "is_anime": is_anime,
                            "analyzed": True,
                        }
                    )
            except Exception as e:
                logger.warning("Error analyzing %s: %s", host_path, e)

        results.append(item)

    # Enrich results from media analysis cache (bulk lookup)
    if core.media_store and movie_file_ids:
        analysis_map = core.media_store.bulk_lookup_radarr(movie_file_ids)
        if analysis_map:
            for item in results:
                mf_id = item.pop("_movie_file_id", None)
                entry = analysis_map.get(mf_id) if mf_id else None
                if entry and not item.get("analyzed"):
                    a = entry.get("analysis", {})
                    item["has_dts_x"] = a.get("has_dts_x", False)
                    item["has_dts"] = a.get("has_dts", item["has_dts"])
                    item["has_truehd"] = a.get("has_truehd", item["has_truehd"])
                    item["analyzed"] = True
                    # Re-evaluate audio conversion with stream-level data from cache
                    cached_streams = a.get("audio_streams", [])
                    if cached_streams:
                        item["needs_audio_conversion"] = _needs_audio_conversion_from_streams(
                            cached_streams
                        )
                        item["audio_codecs_to_convert"] = _audio_codecs_to_convert(cached_streams)
                        item["audio_codecs_to_drop"] = _audio_codecs_to_drop(cached_streams)
                    else:
                        # Fallback: re-evaluate with updated DTS/TrueHD flags
                        # Use has_dts_x to avoid false positive when only DTS:X present
                        cfg_audio_cache = core.config.audio if core.config else None
                        if cfg_audio_cache:
                            audio_needs = False
                            has_regular_dts = item["has_dts"] and not item.get("has_dts_x")
                            if cfg_audio_cache.convert_dts and has_regular_dts:
                                audio_needs = True
                            if cfg_audio_cache.convert_dts_x and item.get("has_dts_x"):
                                audio_needs = True
                            if cfg_audio_cache.convert_truehd and item["has_truehd"]:
                                audio_needs = True
                            item["needs_audio_conversion"] = audio_needs
                    # Use cached ffprobe result for video conversion if available
                    if "needs_video_conversion" in a:
                        item["needs_video_conversion"] = a["needs_video_conversion"]
                    # Re-evaluate cleanup using stream-level data if titles are available
                    if cached_streams:
                        item["needs_cleanup"] = _needs_cleanup_from_streams(
                            cached_streams,
                            item.get("subtitles", []),
                            is_anime=item.get("is_anime", False),
                        )
                    # Update path if renamed since last analysis
                    if entry.get("file_path") != item["path"]:
                        core.media_store.upsert(
                            item["path"],
                            a,
                            entry.get("file_mtime", 0),
                            entry.get("file_size", 0),
                            radarr_movie_file_id=mf_id,
                        )
            # Clean up _movie_file_id from items that weren't enriched
            for item in results:
                item.pop("_movie_file_id", None)
    else:
        for item in results:
            item.pop("_movie_file_id", None)

    return results


def _build_movie_response(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the full response dict from a list of movie results."""
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


def _apply_movie_filters(
    response: dict[str, Any],
    search: str | None,
    filter_val: str,
) -> dict[str, Any]:
    """Apply search and filter to a cached movie response."""
    movies = response["movies"]

    if search:
        search_lower = search.lower()
        movies = [m for m in movies if search_lower in (m.get("title") or "").lower()]

    if filter_val and filter_val != "any":
        movies = [m for m in movies if _movie_matches_filter(m, filter_val)]

    return _build_movie_response(movies)


def _movie_matches_filter(item: dict[str, Any], filter_val: str) -> bool:
    """Check if a movie matches a given filter."""
    if filter_val == "needs_conversion":
        return bool(
            item.get("needs_audio_conversion")
            or item.get("needs_video_conversion")
            or item.get("needs_cleanup")
        )
    if filter_val == "video":
        return bool(item.get("needs_video_conversion"))
    if filter_val == "audio":
        return bool(item.get("needs_audio_conversion"))
    if filter_val == "anime":
        return bool(item.get("is_anime"))
    if filter_val == "cleanup":
        return bool(item.get("needs_cleanup"))
    if filter_val == "dts":
        return bool(item.get("has_dts"))
    if filter_val == "dts_x":
        return bool(item.get("has_dts_x"))
    if filter_val == "truehd":
        return bool(item.get("has_truehd"))
    if filter_val == "hevc":
        return item.get("video_codec", "").upper() in ("HEVC", "H265", "X265")
    if filter_val == "h264":
        return item.get("video_codec", "").upper() in ("H264", "H.264", "X264", "AVC")
    return True


@router.get("/series")
def list_series(
    search: str | None = Query(None),
    analyze: bool = Query(True),
    filter: str = Query("any", description="Filter: any, needs_conversion, video, audio, anime"),
    cache_bust: bool = Query(False, description="Force bypass server cache"),
) -> dict[str, Any]:
    """List series from Sonarr with optional media analysis."""
    sonarr_url = core.config.sonarr.url if core.config else ""
    sonarr_key = core.config.sonarr.api_key if core.config else ""
    if not sonarr_url or not sonarr_key:
        raise HTTPException(status_code=500, detail="Sonarr not configured")

    # Try to serve from cache when not analyzing
    cache_key = "series"
    cached = _cache_get(cache_key)
    now = time.monotonic()

    if not analyze and not cache_bust and cached and not cached.get("stale"):
        age = now - cached["timestamp"]
        if age < CACHE_TTL:
            return _apply_series_filters(cached["data"], search, filter)

    # Fetch fresh data from Sonarr
    try:
        response = requests.get(
            f"{sonarr_url}/api/v3/series",
            headers={"X-Api-Key": sonarr_key},
            timeout=60,
        )
        response.raise_for_status()
        all_series = response.json()
    except Exception as e:
        # Serve stale/cached data on upstream failure
        if cached:
            logger.warning("Sonarr unavailable, serving stale cache: %s", e)
            return _apply_series_filters(cached["data"], search, filter)
        raise HTTPException(status_code=500, detail=f"Failed to query Sonarr: {e}") from e

    logger.info("Fetching series list (%d series, analyze=%s)", len(all_series), analyze)

    full_results = _build_series_results(all_series, sonarr_url, sonarr_key, analyze)
    full_response = _build_series_response(full_results)

    # Cache the full unfiltered result
    _cache_set(cache_key, full_response)

    return _apply_series_filters(full_response, search, filter)


def _build_series_results(
    all_series: list[dict[str, Any]],
    sonarr_url: str,
    sonarr_key: str,
    analyze: bool,
) -> list[dict[str, Any]]:
    """Build processed series results from raw Sonarr data."""
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
                video_count = 0
                cleanup_count = 0
                work_count = 0
                dts_x_count = 0
                cfg_audio = core.config.audio if core.config else None
                cfg_video = core.config.video if core.config else None
                audio_codecs_set: set[str] = set()
                video_codecs_set: set[str] = set()

                # Bulk lookup analysis for this series' episode files
                ep_file_ids = [ef.get("id") for ef in episode_files if ef.get("id")]
                analysis_map: dict[int, dict[str, Any]] = {}
                if core.media_store and ep_file_ids:
                    analysis_map = core.media_store.bulk_lookup_sonarr(ep_file_ids)

                for ef in episode_files:
                    mi = ef.get("mediaInfo", {})
                    ac = mi.get("audioCodec", "").upper()
                    # Check analysis cache for has_dts/has_truehd — Sonarr mediaInfo only
                    # reports the primary audio track codec, so a secondary DTS track would
                    # be missed. The ffprobe cache sees all tracks.
                    ep_entry = analysis_map.get(ef.get("id")) if ef.get("id") else None
                    ep_analysis = ep_entry.get("analysis", {}) if ep_entry else {}
                    ep_cached_streams = ep_analysis.get("audio_streams", [])
                    # Use stream-level cache for accurate audio detection when available
                    if ep_cached_streams:
                        has_audio_issue = _needs_audio_conversion_from_streams(ep_cached_streams)
                    else:
                        # Fallback: use Sonarr mediaInfo primary codec
                        has_dts_ep = ep_analysis.get("has_dts", "DTS" in ac)
                        has_truehd_ep = ep_analysis.get("has_truehd", "TRUEHD" in ac)
                        has_dts_x_ep = ep_analysis.get("has_dts_x", False)
                        has_audio_issue = False
                        if cfg_audio:
                            has_regular_dts = has_dts_ep and not has_dts_x_ep
                            if cfg_audio.convert_dts and has_regular_dts:
                                has_audio_issue = True
                            if cfg_audio.convert_dts_x and has_dts_x_ep:
                                has_audio_issue = True
                            if cfg_audio.convert_truehd and has_truehd_ep:
                                has_audio_issue = True
                    if has_audio_issue:
                        audio_count += 1
                    # Video conversion check from Sonarr mediaInfo
                    has_video_issue = False
                    if cfg_video:
                        vc = mi.get("videoCodec", "").lower()
                        vbd = mi.get("videoBitDepth", 8) or 8
                        is_h264_ep = any(x in vc for x in ("x264", "avc", "h264"))
                        is_anime_ep = item.get("is_anime", False)
                        if not cfg_video.anime_only or is_anime_ep:
                            if (cfg_video.convert_10bit_x264 and is_h264_ep and vbd >= 10) or (
                                cfg_video.convert_8bit_x264 and is_h264_ep and vbd < 10
                            ):
                                has_video_issue = True
                                video_count += 1
                    # Use stream-level data for precise cleanup check when available
                    has_cleanup_issue = False
                    cached_streams = ep_analysis.get("audio_streams", [])
                    if cached_streams:
                        ep_sub_langs = item.get("subtitles", []) or [
                            s.lower() for s in _split_slash_field(mi.get("subtitles", ""))
                        ]
                        if _needs_cleanup_from_streams(
                            cached_streams,
                            ep_sub_langs,
                            is_anime=item.get("is_anime", False),
                        ):
                            has_cleanup_issue = True
                            cleanup_count += 1
                    elif _needs_cleanup(mi, is_anime=item.get("is_anime", False)):
                        has_cleanup_issue = True
                        cleanup_count += 1
                    if has_audio_issue or has_video_issue or has_cleanup_issue:
                        work_count += 1
                    # Collect unique codec strings for contextual filtering
                    ac_raw = mi.get("audioCodec", "")
                    vc_raw = mi.get("videoCodec", "")
                    if ac_raw:
                        audio_codecs_set.add(ac_raw)
                    if vc_raw:
                        video_codecs_set.add(vc_raw)
                    # Check for DTS:X from analysis cache
                    if ep_analysis.get("has_dts_x"):
                        dts_x_count += 1
                        audio_codecs_set.add("DTS:X")
                item["audio_convert_count"] = audio_count
                item["video_convert_count"] = video_count
                item["cleanup_count"] = cleanup_count
                item["needs_work_count"] = work_count
                item["dts_x_count"] = dts_x_count
                item["audio_codecs"] = sorted(audio_codecs_set)
                item["video_codecs"] = sorted(video_codecs_set)
        except Exception as e:
            logger.warning("Error getting episodes for series %s: %s", series_id, e)

        results.append(item)

    return results


def _build_series_response(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the full response dict from a list of series results."""
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


def _apply_series_filters(
    response: dict[str, Any],
    search: str | None,
    filter_val: str,
) -> dict[str, Any]:
    """Apply search and filter to a cached series response."""
    series = response["series"]

    if search:
        search_lower = search.lower()
        series = [s for s in series if search_lower in (s.get("title") or "").lower()]

    if filter_val and filter_val != "any":
        series = [s for s in series if _series_matches_filter(s, filter_val)]

    return _build_series_response(series)


def _series_matches_filter(item: dict[str, Any], filter_val: str) -> bool:
    """Check if a series matches a given filter."""
    if filter_val == "needs_conversion":
        return (
            item.get("audio_convert_count", 0) > 0
            or item.get("video_convert_count", 0) > 0
            or item.get("cleanup_count", 0) > 0
        )
    if filter_val == "video":
        return item.get("video_convert_count", 0) > 0
    if filter_val == "audio":
        return item.get("audio_convert_count", 0) > 0
    if filter_val == "anime":
        return bool(item.get("is_anime"))
    if filter_val == "cleanup":
        return item.get("cleanup_count", 0) > 0
    if filter_val == "dts_x":
        return item.get("dts_x_count", 0) > 0
    return True


@router.get("/series/{series_id}")
def get_series_detail(
    series_id: int,
    analyze: bool = Query(False),
) -> dict[str, Any]:
    """Get detailed series info with seasons and episodes from Sonarr."""
    sonarr_url = core.config.sonarr.url if core.config else ""
    sonarr_key = core.config.sonarr.api_key if core.config else ""
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

    # Bulk lookup analysis from media cache
    ep_analysis_map: dict[int, dict[str, Any]] = {}
    if core.media_store:
        ep_ids = list(ef_by_id.keys())
        if ep_ids:
            ep_analysis_map = core.media_store.bulk_lookup_sonarr(ep_ids)

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
        ep_item["has_dts_x"] = False
        ep_item["analyzed"] = False

        # Merge analysis cache data
        entry = ep_analysis_map.get(ep_file_id)
        if entry:
            a = entry.get("analysis", {})
            ep_item["has_dts_x"] = a.get("has_dts_x", False)
            ep_item["has_dts"] = a.get("has_dts", ep_item["has_dts"])
            ep_item["has_truehd"] = a.get("has_truehd", ep_item["has_truehd"])
            ep_item["analyzed"] = True
            # Re-evaluate cleanup with stream-level data if titles are available
            cached_streams = a.get("audio_streams", [])
            if cached_streams:
                ep_sub_langs = ep_item.get("subtitles", [])
                ep_item["needs_cleanup"] = _needs_cleanup_from_streams(
                    cached_streams,
                    ep_sub_langs,
                    is_anime=series_is_anime,
                )

        # Config-aware audio detection — use stream-level cache when available
        cached_ep_streams = []
        if entry:
            a_cache = entry.get("analysis", {})
            cached_ep_streams = a_cache.get("audio_streams", [])
        if cached_ep_streams:
            ep_item["needs_audio_conversion"] = _needs_audio_conversion_from_streams(
                cached_ep_streams
            )
            ep_item["audio_codecs_to_convert"] = _audio_codecs_to_convert(cached_ep_streams)
            ep_item["audio_codecs_to_drop"] = _audio_codecs_to_drop(cached_ep_streams)
        else:
            # Fallback: use Sonarr mediaInfo primary codec
            cfg_audio = core.config.audio if core.config else None
            ep_item["needs_audio_conversion"] = False
            if cfg_audio:
                has_regular_dts = ep_item["has_dts"] and not ep_item.get("has_dts_x")
                if cfg_audio.convert_dts and has_regular_dts:
                    ep_item["needs_audio_conversion"] = True
                if cfg_audio.convert_dts_x and ep_item.get("has_dts_x"):
                    ep_item["needs_audio_conversion"] = True
                if cfg_audio.convert_truehd and ep_item["has_truehd"]:
                    ep_item["needs_audio_conversion"] = True

        cfg_video_ep = core.config.video if core.config else None
        ep_item["needs_video_conversion"] = False
        if cfg_video_ep:
            vc = mi.get("videoCodec", "").lower()
            vbd = mi.get("videoBitDepth", 8) or 8
            is_h264_item = any(x in vc for x in ("x264", "avc", "h264"))
            if not cfg_video_ep.anime_only or series_is_anime:
                if (cfg_video_ep.convert_10bit_x264 and is_h264_item and vbd >= 10) or (
                    cfg_video_ep.convert_8bit_x264 and is_h264_item and vbd < 10
                ):
                    ep_item["needs_video_conversion"] = True

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
                    1
                    for e in eps
                    if e.get("needs_audio_conversion")
                    or e.get("needs_video_conversion")
                    or e.get("needs_cleanup")
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
