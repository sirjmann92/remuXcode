#!/usr/bin/env python3
"""Language detection.

Hybrid approach for detecting original language of media:
1. NFO file parsing (fast, no network)
2. Sonarr/Radarr API query (authoritative)
3. Path-based fallback (heuristic)
"""

import logging
from pathlib import Path
import xml.etree.ElementTree as ET

import requests

logger = logging.getLogger(__name__)


# ISO 639-2 language code mappings
LANGUAGE_CODE_MAP = {
    # Language names → ISO 639-2
    "japanese": "jpn",
    "english": "eng",
    "korean": "kor",
    "chinese": "chi",
    "mandarin": "chi",
    "cantonese": "chi",
    "spanish": "spa",
    "french": "fre",
    "german": "ger",
    "italian": "ita",
    "portuguese": "por",
    "russian": "rus",
    "arabic": "ara",
    "hindi": "hin",
    "thai": "tha",
    "vietnamese": "vie",
    "polish": "pol",
    "dutch": "dut",
    "swedish": "swe",
    "norwegian": "nor",
    "danish": "dan",
    "finnish": "fin",
    "czech": "cze",
    "hungarian": "hun",
    "turkish": "tur",
    "greek": "gre",
    "hebrew": "heb",
    # ISO 639-1 → ISO 639-2
    "ja": "jpn",
    "en": "eng",
    "ko": "kor",
    "zh": "chi",
    "es": "spa",
    "fr": "fre",
    "de": "ger",
    "it": "ita",
    "pt": "por",
    "ru": "rus",
    "ar": "ara",
    "hi": "hin",
    "th": "tha",
    "vi": "vie",
    "pl": "pol",
    "nl": "dut",
    "sv": "swe",
    "no": "nor",
    "da": "dan",
    "fi": "fin",
    "cs": "cze",
    "hu": "hun",
    "tr": "tur",
    "el": "gre",
    "he": "heb",
    # Pass-through for already ISO 639-2
    "jpn": "jpn",
    "eng": "eng",
    "kor": "kor",
    "chi": "chi",
    "zho": "chi",  # Alternative Chinese code
    "spa": "spa",
    "fre": "fre",
    "fra": "fre",  # Alternative French code
    "ger": "ger",
    "deu": "ger",  # Alternative German code
    "ita": "ita",
    "por": "por",
    "rus": "rus",
    "ara": "ara",
    "hin": "hin",
    "tha": "tha",
    "vie": "vie",
    "pol": "pol",
    "dut": "dut",
    "nld": "dut",  # Alternative Dutch code
    "swe": "swe",
    "nor": "nor",
    "dan": "dan",
    "fin": "fin",
    "cze": "cze",
    "ces": "cze",  # Alternative Czech code
    "hun": "hun",
    "tur": "tur",
    "gre": "gre",
    "ell": "gre",  # Alternative Greek code
    "heb": "heb",
}


def normalize_language_code(lang: str) -> str:
    """Normalize language name or code to ISO 639-2 code.

    Args:
        lang: Language name or code (any format)

    Returns:
        ISO 639-2 code or 'und' if unknown
    """
    if not lang:
        return "und"

    lang_lower = lang.lower().strip()
    return LANGUAGE_CODE_MAP.get(lang_lower, "und")


class LanguageDetector:
    """Detects original language of media content using hybrid approach.

    Priority:
    1. NFO file (tvshow.nfo or movie.nfo)
    2. Sonarr/Radarr API
    3. Path-based heuristics
    """

    def __init__(
        self,
        sonarr_url: str = "",
        sonarr_api_key: str = "",
        radarr_url: str = "",
        radarr_api_key: str = "",
        path_mappings: list[tuple] | None = None,
    ):
        """Initialize language detector with optional Sonarr/Radarr configuration."""
        self.sonarr_url = sonarr_url.rstrip("/")
        self.sonarr_api_key = sonarr_api_key
        self.radarr_url = radarr_url.rstrip("/")
        self.radarr_api_key = radarr_api_key
        # List of (container_prefix, host_prefix) tuples for path translation
        self.path_mappings = path_mappings or []

    def _to_container_path(self, host_path: str) -> str:
        """Reverse-translate a host path back to container path for API matching."""
        for container_prefix, host_prefix in self.path_mappings:
            if host_path.startswith(host_prefix):
                return container_prefix + host_path[len(host_prefix) :]
        return host_path

    def detect_original_language(self, file_path: str, media_type: str = "auto") -> str:
        """Detect original language for a media file.

        Args:
            file_path: Path to the media file
            media_type: 'tv', 'movie', or 'auto' (detect from path)

        Returns:
            ISO 639-2 language code (e.g., 'jpn', 'eng')
        """
        path = Path(file_path)

        # Determine if TV or movie
        if media_type == "auto":
            media_type = self._detect_media_type(path)

        # 1. Try NFO file first (fast, no network)
        nfo_lang = self._get_from_nfo(path, media_type)
        if nfo_lang and nfo_lang != "und":
            logger.debug("Language from NFO: %s", nfo_lang)
            return nfo_lang

        # 2. Try Sonarr/Radarr API
        api_lang = self._get_from_api(path, media_type)
        if api_lang and api_lang != "und":
            logger.debug("Language from API: %s", api_lang)
            return api_lang

        # 3. Path-based fallback
        path_lang = self._get_from_path(path)
        if path_lang and path_lang != "und":
            logger.debug("Language from path: %s", path_lang)
            return path_lang

        # Default to English if unknown
        logger.warning("Could not detect language for %s, defaulting to 'eng'", file_path)
        return "eng"

    def _detect_media_type(self, path: Path) -> str:
        """Detect if path is TV show or movie based on path structure."""
        path_str = str(path).lower()

        # Look for TV indicators
        if any(
            indicator in path_str
            for indicator in ["/shows/", "/tv/", "/series/", "season", "s0", "s1", "s2"]
        ):
            return "tv"

        # Look for movie indicators
        if any(indicator in path_str for indicator in ["/movies/", "/films/"]):
            return "movie"

        return "movie"  # Default to movie

    def _get_from_nfo(self, path: Path, media_type: str) -> str | None:
        """Extract original language from NFO file."""
        try:
            if media_type == "tv":
                # Look for tvshow.nfo in show root directory
                # Path structure: /Shows/Series Name/Season XX/episode.mkv
                show_dir = path.parent.parent
                nfo_file = show_dir / "tvshow.nfo"
            else:
                # Look for movie.nfo in same directory
                nfo_file = path.parent / (path.stem + ".nfo")
                if not nfo_file.exists():
                    nfo_file = path.parent / "movie.nfo"

            if not nfo_file.exists():
                logger.debug("NFO file not found: %s", nfo_file)
                return None

            # Parse XML
            tree = ET.parse(nfo_file)
            root = tree.getroot()

            # Prefer explicit original language tags anywhere in the document
            # (some formats nest these under metadata blocks)
            for tag in (".//originallanguage", ".//original_language"):
                lang_elem = root.find(tag)
                if lang_elem is not None and lang_elem.text:
                    return normalize_language_code(lang_elem.text)

            # Fallback: only use top-level/movie-level language fields.
            # Do NOT use .//language because many NFOs include streamdetails
            # language tags for video/audio/subtitle streams, which are not the
            # original content language.
            for tag in ("./language", "./movie/language"):
                lang_elem = root.find(tag)
                if lang_elem is not None and lang_elem.text:
                    return normalize_language_code(lang_elem.text)

            return None

        except ET.ParseError as e:
            logger.warning("Failed to parse NFO file: %s", e)
            return None
        except Exception as e:
            logger.error("Error reading NFO file: %s", e)
            return None

    def _get_from_api(self, path: Path, media_type: str) -> str | None:
        """Query Sonarr/Radarr API for original language."""
        try:
            if media_type == "tv":
                return self._query_sonarr(path)
            return self._query_radarr(path)
        except Exception as e:
            logger.error("API query failed: %s", e)
            return None

    def _query_sonarr(self, path: Path) -> str | None:
        """Query Sonarr API for series original language."""
        if not self.sonarr_url or not self.sonarr_api_key:
            return None

        try:
            # Get all series
            response = requests.get(
                f"{self.sonarr_url}/api/v3/series",
                headers={"X-Api-Key": self.sonarr_api_key},
                timeout=10,
            )
            response.raise_for_status()
            series_list = response.json()

            # Find series by path match
            # Path structure: /Shows/Series Name/Season XX/episode.mkv
            # Sonarr stores container paths, so reverse-translate if mappings provided
            show_dir = self._to_container_path(str(path.parent.parent))

            for series in series_list:
                if show_dir.startswith(series.get("path", "")):
                    orig_lang = series.get("originalLanguage", {})
                    if isinstance(orig_lang, dict):
                        lang_name = orig_lang.get("name", "")
                    else:
                        lang_name = str(orig_lang)

                    if lang_name:
                        return normalize_language_code(lang_name)

            return None

        except requests.RequestException as e:
            logger.warning("Sonarr API request failed: %s", e)
            return None

    def _query_radarr(self, path: Path) -> str | None:
        """Query Radarr API for movie original language."""
        if not self.radarr_url or not self.radarr_api_key:
            return None

        try:
            # Get all movies
            response = requests.get(
                f"{self.radarr_url}/api/v3/movie",
                headers={"X-Api-Key": self.radarr_api_key},
                timeout=10,
            )
            response.raise_for_status()
            movie_list = response.json()

            # Find movie by path match
            # Radarr stores container paths, so reverse-translate if mappings provided
            movie_dir = self._to_container_path(str(path.parent))

            for movie in movie_list:
                if movie_dir.startswith(movie.get("path", "")):
                    orig_lang = movie.get("originalLanguage", {})
                    if isinstance(orig_lang, dict):
                        lang_name = orig_lang.get("name", "")
                    else:
                        lang_name = str(orig_lang)

                    if lang_name:
                        return normalize_language_code(lang_name)

            return None

        except requests.RequestException as e:
            logger.warning("Radarr API request failed: %s", e)
            return None

    def _get_from_path(self, path: Path) -> str | None:
        """Detect original language from path heuristics.

        Looks for:
        - Anime paths → Japanese
        - K-Drama/Korean paths → Korean
        - C-Drama/Chinese paths → Chinese
        """
        path_str = str(path).lower()

        # Anime is typically Japanese
        if any(indicator in path_str for indicator in ["/anime/", "/アニメ/", "[anime]"]):
            return "jpn"

        # Korean content
        if any(
            indicator in path_str for indicator in ["/korean/", "/k-drama/", "/kdrama/", "[korean]"]
        ):
            return "kor"

        # Chinese content
        if any(
            indicator in path_str
            for indicator in ["/chinese/", "/c-drama/", "/cdrama/", "[chinese]", "/mandarin/"]
        ):
            return "chi"

        # Spanish content
        if any(indicator in path_str for indicator in ["/spanish/", "/telenovela/", "[spanish]"]):
            return "spa"

        return None

    def get_languages_to_keep(
        self, file_path: str, always_keep: list[str], keep_original: bool = True
    ) -> list[str]:
        """Get list of language codes to keep for a file.

        Args:
            file_path: Path to the media file
            always_keep: Languages to always keep (e.g., ['eng'])
            keep_original: Whether to keep original language

        Returns:
            List of ISO 639-2 language codes to keep
        """
        languages = list(always_keep)

        if keep_original:
            original = self.detect_original_language(file_path)
            if original and original not in languages:
                languages.append(original)

        return languages
