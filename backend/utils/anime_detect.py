#!/usr/bin/env python3
"""Anime detection.

Detects whether content is anime or live action using:
1. Path patterns (fast, reliable for organized libraries)
2. NFO file genre tags
3. Sonarr/Radarr API genre/tags (optional TMDB/TVDB integration)
"""

from enum import Enum
import logging
from pathlib import Path
import xml.etree.ElementTree as ET

import requests

logger = logging.getLogger(__name__)


class ContentType(Enum):
    """Content type classification."""

    ANIME = "anime"
    LIVE_ACTION = "live_action"
    UNKNOWN = "unknown"


# Known anime studios (for NFO detection)
ANIME_STUDIOS = [
    "toei animation",
    "toei",
    "bones",
    "madhouse",
    "studio ghibli",
    "ghibli",
    "kyoto animation",
    "kyoani",
    "a-1 pictures",
    "ufotable",
    "mappa",
    "wit studio",
    "production i.g",
    "ig",
    "sunrise",
    "shaft",
    "trigger",
    "gainax",
    "j.c.staff",
    "cloverworks",
    "pierrot",
    "tms entertainment",
    "doga kobo",
    "white fox",
    "lerche",
    "david production",
    "silver link",
    "p.a.works",
    "kinema citrus",
    "orange",
    "polygon pictures",
    "science saru",
    "aniplex",
    "funimation",
    "crunchyroll",
    "nippon animation",
    "shin-ei animation",
    "olm",
    "bandai namco",
]

# Known Western animation studios (to EXCLUDE from anime detection)
WESTERN_ANIMATION_STUDIOS = [
    "illumination",
    "pixar",
    "dreamworks",
    "disney",
    "walt disney",
    "blue sky",
    "laika",
    "aardman",
    "sony pictures animation",
    "warner animation",
    "warner bros. animation",
    "cartoon network",
    "nickelodeon",
    "netflix animation",
    "amazon studios",
    "universal pictures",
    "20th century",
    "paramount animation",
    "rooster teeth",
    "nelvana",
    "hasbro",
    "mattel",
]

# East Asian origin languages that indicate anime/donghua/manhwa
EAST_ASIAN_LANGUAGES = [
    "japanese",
    "chinese",
    "mandarin",
    "cantonese",
    "korean",
]

# East Asian countries for NFO detection
EAST_ASIAN_COUNTRIES = [
    "japan",
    "china",
    "south korea",
    "korea",
]

# Path patterns that indicate anime
ANIME_PATH_PATTERNS = [
    "/anime/",
    "/アニメ/",
    "/anime]",
    "[anime]",
    "/animeseries/",
    "/anime series/",
    "/animation/japanese/",
    "/japanese animation/",
    "/japanimation/",
]


class AnimeDetector:
    """Detects whether content is anime or live action.

    Uses multiple detection methods with configurable priority.
    """

    def __init__(
        self,
        anime_paths: list[str] | None = None,
        sonarr_url: str = "",
        sonarr_api_key: str = "",
        radarr_url: str = "",
        radarr_api_key: str = "",
    ):
        """Initialize anime detector.

        Args:
            anime_paths: List of path patterns that indicate anime
            sonarr_url: Sonarr API URL
            sonarr_api_key: Sonarr API key
            radarr_url: Radarr API URL
            radarr_api_key: Radarr API key
        """
        self.anime_paths = anime_paths or ANIME_PATH_PATTERNS
        self.sonarr_url = sonarr_url.rstrip("/") if sonarr_url else ""
        self.sonarr_api_key = sonarr_api_key
        self.radarr_url = radarr_url.rstrip("/") if radarr_url else ""
        self.radarr_api_key = radarr_api_key

    def detect(self, file_path: str, use_api: bool = True) -> ContentType:
        """Detect content type for a media file.

        Args:
            file_path: Path to the media file
            use_api: Whether to use Sonarr/Radarr API for detection

        Returns:
            ContentType enum value
        """
        path = Path(file_path)

        # 1. Path-based detection (fastest, most reliable)
        path_result = self._detect_from_path(path)
        if path_result != ContentType.UNKNOWN:
            logger.debug("Content type from path: %s", path_result)
            return path_result

        # 2. NFO-based detection
        nfo_result = self._detect_from_nfo(path)
        if nfo_result != ContentType.UNKNOWN:
            logger.debug("Content type from NFO: %s", nfo_result)
            return nfo_result

        # 3. API-based detection (optional)
        if use_api:
            api_result = self._detect_from_api(path)
            if api_result != ContentType.UNKNOWN:
                logger.debug("Content type from API: %s", api_result)
                return api_result

        # Default to live action if unknown
        logger.debug("Could not detect content type for %s, defaulting to live_action", file_path)
        return ContentType.LIVE_ACTION

    def is_anime(self, file_path: str, use_api: bool = True) -> bool:
        """Simple boolean check if content is anime."""
        return self.detect(file_path, use_api) == ContentType.ANIME

    def _detect_from_path(self, path: Path) -> ContentType:
        """Detect content type from path patterns."""
        path_str = str(path).lower()

        # Check anime path patterns
        for pattern in self.anime_paths:
            if pattern.lower() in path_str:
                return ContentType.ANIME

        return ContentType.UNKNOWN

    def _detect_from_nfo(self, path: Path) -> ContentType:
        """Detect content type from NFO file."""
        try:
            # Determine media type and find NFO
            path_str = str(path).lower()

            if any(ind in path_str for ind in ["/shows/", "/tv/", "season"]):
                # TV show: look for tvshow.nfo in show root
                show_dir = path.parent.parent
                nfo_file = show_dir / "tvshow.nfo"
            else:
                # Movie: look for movie.nfo or <filename>.nfo
                nfo_file = path.parent / (path.stem + ".nfo")
                if not nfo_file.exists():
                    nfo_file = path.parent / "movie.nfo"

            if not nfo_file.exists():
                return ContentType.UNKNOWN

            # Parse NFO XML
            tree = ET.parse(nfo_file)
            root = tree.getroot()

            # Check genre tags
            genres = [
                genre_elem.text.lower()
                for genre_elem in root.findall(".//genre")
                if genre_elem.text
            ]

            # Exact "anime" genre is definitive
            if any(g == "anime" for g in genres):
                return ContentType.ANIME

            if any("animation" in g for g in genres):
                # "Animation" genre found — need additional signals to confirm anime.
                # Collect all studios and countries from the NFO.
                studios = [el.text.lower() for el in root.findall(".//studio") if el.text]
                countries = [el.text.lower() for el in root.findall(".//country") if el.text]

                # If ANY studio is a known Western animation studio → not anime
                if any(ws in studio for studio in studios for ws in WESTERN_ANIMATION_STUDIOS):
                    return ContentType.LIVE_ACTION

                # If a studio is a known anime studio → anime
                if any(ans in studio for studio in studios for ans in ANIME_STUDIOS):
                    return ContentType.ANIME

                # Check countries: must be ONLY East Asian (co-productions
                # with Western countries like "Japan" + "United States" are not anime)
                if countries:
                    all_east_asian = all(
                        any(ea in c for ea in EAST_ASIAN_COUNTRIES) for c in countries
                    )
                    has_east_asian = any(
                        any(ea in c for ea in EAST_ASIAN_COUNTRIES) for c in countries
                    )
                    if all_east_asian:
                        return ContentType.ANIME
                    if not has_east_asian:
                        return ContentType.LIVE_ACTION
                    # Mixed countries (e.g. Japan + US) — not enough alone, check title

                # Check original title for Japanese/CJK characters
                orig_title = root.find(".//originaltitle")
                if orig_title is not None and orig_title.text:
                    if any(
                        "\u3040" <= c <= "\u309f"  # Hiragana
                        or "\u30a0" <= c <= "\u30ff"  # Katakana
                        or "\u4e00" <= c <= "\u9fff"  # CJK Unified
                        or "\uac00" <= c <= "\ud7af"  # Korean Hangul
                        for c in orig_title.text
                    ):
                        return ContentType.ANIME

            return ContentType.UNKNOWN

        except ET.ParseError as e:
            logger.warning("Failed to parse NFO: %s", e)
            return ContentType.UNKNOWN
        except Exception as e:
            logger.error("Error reading NFO: %s", e)
            return ContentType.UNKNOWN

    def _detect_from_api(self, path: Path) -> ContentType:
        """Detect content type from Sonarr/Radarr API."""
        path_str = str(path).lower()

        try:
            if any(ind in path_str for ind in ["/shows/", "/tv/", "season"]):
                return self._query_sonarr(path)
            return self._query_radarr(path)
        except Exception as e:
            logger.error("API detection failed: %s", e)
            return ContentType.UNKNOWN

    def _query_sonarr(self, path: Path) -> ContentType:
        """Query Sonarr for series genres/tags."""
        if not self.sonarr_url or not self.sonarr_api_key:
            return ContentType.UNKNOWN

        try:
            response = requests.get(
                f"{self.sonarr_url}/api/v3/series",
                headers={"X-Api-Key": self.sonarr_api_key},
                timeout=10,
            )
            response.raise_for_status()
            series_list = response.json()

            # Find series by path
            show_dir = str(path.parent.parent)

            for series in series_list:
                if show_dir.startswith(series.get("path", "")):
                    # Check genres
                    genres = [g.lower() for g in series.get("genres", [])]
                    if "anime" in genres:
                        return ContentType.ANIME

                    # Check tags
                    # Note: Would need to query /api/v3/tag to resolve tag IDs

                    # Check series type
                    series_type = series.get("seriesType", "").lower()
                    if series_type == "anime":
                        return ContentType.ANIME

                    return ContentType.LIVE_ACTION

            return ContentType.UNKNOWN

        except requests.RequestException as e:
            logger.warning("Sonarr API request failed: %s", e)
            return ContentType.UNKNOWN

    def _query_radarr(self, path: Path) -> ContentType:
        """Query Radarr for movie genres/tags."""
        if not self.radarr_url or not self.radarr_api_key:
            return ContentType.UNKNOWN

        try:
            response = requests.get(
                f"{self.radarr_url}/api/v3/movie",
                headers={"X-Api-Key": self.radarr_api_key},
                timeout=10,
            )
            response.raise_for_status()
            movie_list = response.json()

            # Find movie by path
            movie_dir = str(path.parent)

            for movie in movie_list:
                if movie_dir.startswith(movie.get("path", "")):
                    # Check genres
                    genres = [g.lower() for g in movie.get("genres", [])]
                    if "anime" in genres:
                        return ContentType.ANIME

                    # Check if animation + East Asian origin
                    if "animation" in genres:
                        orig_lang = movie.get("originalLanguage", {})
                        if isinstance(orig_lang, dict):
                            lang_name = orig_lang.get("name", "").lower()
                        else:
                            lang_name = str(orig_lang).lower()

                        if any(lang in lang_name for lang in EAST_ASIAN_LANGUAGES):
                            # East Asian animation — but check studio to exclude
                            # Western co-productions (e.g. Illumination + Nintendo)
                            studio_name = movie.get("studio", "").lower()
                            if not any(ws in studio_name for ws in WESTERN_ANIMATION_STUDIOS):
                                return ContentType.ANIME

                    return ContentType.LIVE_ACTION

            return ContentType.UNKNOWN

        except requests.RequestException as e:
            logger.warning("Radarr API request failed: %s", e)
            return ContentType.UNKNOWN
