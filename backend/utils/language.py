#!/usr/bin/env python3
"""
Language Detection

Hybrid approach for detecting original language of media:
1. NFO file parsing (fast, no network)
2. Sonarr/Radarr API query (authoritative)
3. Path-based fallback (heuristic)
"""

import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, List, Dict

import requests

logger = logging.getLogger(__name__)


# ISO 639-2 language code mappings
LANGUAGE_CODE_MAP = {
    # Language names → ISO 639-2
    'japanese': 'jpn',
    'english': 'eng',
    'korean': 'kor',
    'chinese': 'chi',
    'mandarin': 'chi',
    'cantonese': 'chi',
    'spanish': 'spa',
    'french': 'fre',
    'german': 'ger',
    'italian': 'ita',
    'portuguese': 'por',
    'russian': 'rus',
    'arabic': 'ara',
    'hindi': 'hin',
    'thai': 'tha',
    'vietnamese': 'vie',
    'polish': 'pol',
    'dutch': 'dut',
    'swedish': 'swe',
    'norwegian': 'nor',
    'danish': 'dan',
    'finnish': 'fin',
    'czech': 'cze',
    'hungarian': 'hun',
    'turkish': 'tur',
    'greek': 'gre',
    'hebrew': 'heb',
    
    # ISO 639-1 → ISO 639-2
    'ja': 'jpn',
    'en': 'eng',
    'ko': 'kor',
    'zh': 'chi',
    'es': 'spa',
    'fr': 'fre',
    'de': 'ger',
    'it': 'ita',
    'pt': 'por',
    'ru': 'rus',
    'ar': 'ara',
    'hi': 'hin',
    'th': 'tha',
    'vi': 'vie',
    'pl': 'pol',
    'nl': 'dut',
    'sv': 'swe',
    'no': 'nor',
    'da': 'dan',
    'fi': 'fin',
    'cs': 'cze',
    'hu': 'hun',
    'tr': 'tur',
    'el': 'gre',
    'he': 'heb',
    
    # Pass-through for already ISO 639-2
    'jpn': 'jpn',
    'eng': 'eng',
    'kor': 'kor',
    'chi': 'chi',
    'zho': 'chi',  # Alternative Chinese code
    'spa': 'spa',
    'fre': 'fre',
    'fra': 'fre',  # Alternative French code
    'ger': 'ger',
    'deu': 'ger',  # Alternative German code
    'ita': 'ita',
    'por': 'por',
    'rus': 'rus',
    'ara': 'ara',
    'hin': 'hin',
    'tha': 'tha',
    'vie': 'vie',
    'pol': 'pol',
    'dut': 'dut',
    'nld': 'dut',  # Alternative Dutch code
    'swe': 'swe',
    'nor': 'nor',
    'dan': 'dan',
    'fin': 'fin',
    'cze': 'cze',
    'ces': 'cze',  # Alternative Czech code
    'hun': 'hun',
    'tur': 'tur',
    'gre': 'gre',
    'ell': 'gre',  # Alternative Greek code
    'heb': 'heb',
}


def normalize_language_code(lang: str) -> str:
    """
    Normalize language name or code to ISO 639-2 code.
    
    Args:
        lang: Language name or code (any format)
    
    Returns:
        ISO 639-2 code or 'und' if unknown
    """
    if not lang:
        return 'und'
    
    lang_lower = lang.lower().strip()
    return LANGUAGE_CODE_MAP.get(lang_lower, 'und')


class LanguageDetector:
    """
    Detects original language of media content using hybrid approach.
    
    Priority:
    1. NFO file (tvshow.nfo or movie.nfo)
    2. Sonarr/Radarr API
    3. Path-based heuristics
    """
    
    def __init__(
        self,
        sonarr_url: str = '',
        sonarr_api_key: str = '',
        radarr_url: str = '',
        radarr_api_key: str = ''
    ):
        self.sonarr_url = sonarr_url.rstrip('/')
        self.sonarr_api_key = sonarr_api_key
        self.radarr_url = radarr_url.rstrip('/')
        self.radarr_api_key = radarr_api_key
    
    def detect_original_language(
        self,
        file_path: str,
        media_type: str = 'auto'
    ) -> str:
        """
        Detect original language for a media file.
        
        Args:
            file_path: Path to the media file
            media_type: 'tv', 'movie', or 'auto' (detect from path)
        
        Returns:
            ISO 639-2 language code (e.g., 'jpn', 'eng')
        """
        path = Path(file_path)
        
        # Determine if TV or movie
        if media_type == 'auto':
            media_type = self._detect_media_type(path)
        
        # 1. Try NFO file first (fast, no network)
        nfo_lang = self._get_from_nfo(path, media_type)
        if nfo_lang and nfo_lang != 'und':
            logger.debug(f"Language from NFO: {nfo_lang}")
            return nfo_lang
        
        # 2. Try Sonarr/Radarr API
        api_lang = self._get_from_api(path, media_type)
        if api_lang and api_lang != 'und':
            logger.debug(f"Language from API: {api_lang}")
            return api_lang
        
        # 3. Path-based fallback
        path_lang = self._get_from_path(path)
        if path_lang and path_lang != 'und':
            logger.debug(f"Language from path: {path_lang}")
            return path_lang
        
        # Default to English if unknown
        logger.warning(f"Could not detect language for {file_path}, defaulting to 'eng'")
        return 'eng'
    
    def _detect_media_type(self, path: Path) -> str:
        """Detect if path is TV show or movie based on path structure."""
        path_str = str(path).lower()
        
        # Look for TV indicators
        if any(indicator in path_str for indicator in [
            '/shows/', '/tv/', '/series/', 'season', 's0', 's1', 's2'
        ]):
            return 'tv'
        
        # Look for movie indicators
        if any(indicator in path_str for indicator in [
            '/movies/', '/films/'
        ]):
            return 'movie'
        
        return 'movie'  # Default to movie
    
    def _get_from_nfo(self, path: Path, media_type: str) -> Optional[str]:
        """Extract original language from NFO file."""
        try:
            if media_type == 'tv':
                # Look for tvshow.nfo in show root directory
                # Path structure: /Shows/Series Name/Season XX/episode.mkv
                show_dir = path.parent.parent
                nfo_file = show_dir / 'tvshow.nfo'
            else:
                # Look for movie.nfo in same directory
                nfo_file = path.parent / (path.stem + '.nfo')
                if not nfo_file.exists():
                    nfo_file = path.parent / 'movie.nfo'
            
            if not nfo_file.exists():
                logger.debug(f"NFO file not found: {nfo_file}")
                return None
            
            # Parse XML
            tree = ET.parse(nfo_file)
            root = tree.getroot()
            
            # Look for language tag
            lang_elem = root.find('.//language')
            if lang_elem is not None and lang_elem.text:
                return normalize_language_code(lang_elem.text)
            
            # Try originallanguage tag
            lang_elem = root.find('.//originallanguage')
            if lang_elem is not None and lang_elem.text:
                return normalize_language_code(lang_elem.text)
            
            # Try original_language (some NFO formats)
            lang_elem = root.find('.//original_language')
            if lang_elem is not None and lang_elem.text:
                return normalize_language_code(lang_elem.text)
            
            return None
            
        except ET.ParseError as e:
            logger.warning(f"Failed to parse NFO file: {e}")
            return None
        except Exception as e:
            logger.error(f"Error reading NFO file: {e}")
            return None
    
    def _get_from_api(self, path: Path, media_type: str) -> Optional[str]:
        """Query Sonarr/Radarr API for original language."""
        try:
            if media_type == 'tv':
                return self._query_sonarr(path)
            else:
                return self._query_radarr(path)
        except Exception as e:
            logger.error(f"API query failed: {e}")
            return None
    
    def _query_sonarr(self, path: Path) -> Optional[str]:
        """Query Sonarr API for series original language."""
        if not self.sonarr_url or not self.sonarr_api_key:
            return None
        
        try:
            # Get all series
            response = requests.get(
                f"{self.sonarr_url}/api/v3/series",
                headers={'X-Api-Key': self.sonarr_api_key},
                timeout=10
            )
            response.raise_for_status()
            series_list = response.json()
            
            # Find series by path match
            # Path structure: /Shows/Series Name/Season XX/episode.mkv
            show_dir = str(path.parent.parent)
            
            for series in series_list:
                if show_dir.startswith(series.get('path', '')):
                    orig_lang = series.get('originalLanguage', {})
                    if isinstance(orig_lang, dict):
                        lang_name = orig_lang.get('name', '')
                    else:
                        lang_name = str(orig_lang)
                    
                    if lang_name:
                        return normalize_language_code(lang_name)
            
            return None
            
        except requests.RequestException as e:
            logger.warning(f"Sonarr API request failed: {e}")
            return None
    
    def _query_radarr(self, path: Path) -> Optional[str]:
        """Query Radarr API for movie original language."""
        if not self.radarr_url or not self.radarr_api_key:
            return None
        
        try:
            # Get all movies
            response = requests.get(
                f"{self.radarr_url}/api/v3/movie",
                headers={'X-Api-Key': self.radarr_api_key},
                timeout=10
            )
            response.raise_for_status()
            movie_list = response.json()
            
            # Find movie by path match
            movie_dir = str(path.parent)
            
            for movie in movie_list:
                if movie_dir.startswith(movie.get('path', '')):
                    orig_lang = movie.get('originalLanguage', {})
                    if isinstance(orig_lang, dict):
                        lang_name = orig_lang.get('name', '')
                    else:
                        lang_name = str(orig_lang)
                    
                    if lang_name:
                        return normalize_language_code(lang_name)
            
            return None
            
        except requests.RequestException as e:
            logger.warning(f"Radarr API request failed: {e}")
            return None
    
    def _get_from_path(self, path: Path) -> Optional[str]:
        """
        Detect original language from path heuristics.
        
        Looks for:
        - Anime paths → Japanese
        - K-Drama/Korean paths → Korean
        - C-Drama/Chinese paths → Chinese
        """
        path_str = str(path).lower()
        
        # Anime is typically Japanese
        if any(indicator in path_str for indicator in [
            '/anime/', '/アニメ/', '[anime]'
        ]):
            return 'jpn'
        
        # Korean content
        if any(indicator in path_str for indicator in [
            '/korean/', '/k-drama/', '/kdrama/', '[korean]'
        ]):
            return 'kor'
        
        # Chinese content
        if any(indicator in path_str for indicator in [
            '/chinese/', '/c-drama/', '/cdrama/', '[chinese]', '/mandarin/'
        ]):
            return 'chi'
        
        # Spanish content
        if any(indicator in path_str for indicator in [
            '/spanish/', '/telenovela/', '[spanish]'
        ]):
            return 'spa'
        
        return None
    
    def get_languages_to_keep(
        self,
        file_path: str,
        always_keep: List[str],
        keep_original: bool = True
    ) -> List[str]:
        """
        Get list of language codes to keep for a file.
        
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
