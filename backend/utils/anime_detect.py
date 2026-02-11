#!/usr/bin/env python3
"""
Anime Detection

Detects whether content is anime or live action using:
1. Path patterns (fast, reliable for organized libraries)
2. NFO file genre tags
3. Sonarr/Radarr API genre/tags (optional TMDB/TVDB integration)
"""

import logging
import xml.etree.ElementTree as ET
from enum import Enum
from pathlib import Path
from typing import Optional, List

import requests

logger = logging.getLogger(__name__)


class ContentType(Enum):
    """Content type classification."""
    ANIME = 'anime'
    LIVE_ACTION = 'live_action'
    UNKNOWN = 'unknown'


# Known anime studios (for NFO detection)
ANIME_STUDIOS = [
    'toei animation', 'toei', 'bones', 'madhouse', 'studio ghibli', 'ghibli',
    'kyoto animation', 'kyoani', 'a-1 pictures', 'ufotable', 'mappa', 'wit studio',
    'production i.g', 'ig', 'sunrise', 'shaft', 'trigger', 'gainax', 'j.c.staff',
    'cloverworks', 'pierrot', 'tms entertainment', 'doga kobo', 'white fox',
    'lerche', 'david production', 'silver link', 'p.a.works', 'kinema citrus',
    'orange', 'polygon pictures', 'science saru', 'aniplex', 'funimation',
    'crunchyroll', 'nippon animation', 'shin-ei animation', 'olm', 'bandai namco'
]

# Path patterns that indicate anime
ANIME_PATH_PATTERNS = [
    '/anime/', '/アニメ/', '/anime]', '[anime]',
    '/animeseries/', '/anime series/', '/animation/japanese/',
    '/japanese animation/', '/japanimation/'
]


class AnimeDetector:
    """
    Detects whether content is anime or live action.
    
    Uses multiple detection methods with configurable priority.
    """
    
    def __init__(
        self,
        anime_paths: Optional[List[str]] = None,
        sonarr_url: str = '',
        sonarr_api_key: str = '',
        radarr_url: str = '',
        radarr_api_key: str = ''
    ):
        """
        Initialize anime detector.
        
        Args:
            anime_paths: List of path patterns that indicate anime
            sonarr_url: Sonarr API URL
            sonarr_api_key: Sonarr API key
            radarr_url: Radarr API URL
            radarr_api_key: Radarr API key
        """
        self.anime_paths = anime_paths or ANIME_PATH_PATTERNS
        self.sonarr_url = sonarr_url.rstrip('/') if sonarr_url else ''
        self.sonarr_api_key = sonarr_api_key
        self.radarr_url = radarr_url.rstrip('/') if radarr_url else ''
        self.radarr_api_key = radarr_api_key
    
    def detect(self, file_path: str, use_api: bool = True) -> ContentType:
        """
        Detect content type for a media file.
        
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
            logger.debug(f"Content type from path: {path_result}")
            return path_result
        
        # 2. NFO-based detection
        nfo_result = self._detect_from_nfo(path)
        if nfo_result != ContentType.UNKNOWN:
            logger.debug(f"Content type from NFO: {nfo_result}")
            return nfo_result
        
        # 3. API-based detection (optional)
        if use_api:
            api_result = self._detect_from_api(path)
            if api_result != ContentType.UNKNOWN:
                logger.debug(f"Content type from API: {api_result}")
                return api_result
        
        # Default to live action if unknown
        logger.debug(f"Could not detect content type for {file_path}, defaulting to live_action")
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
            
            if any(ind in path_str for ind in ['/shows/', '/tv/', 'season']):
                # TV show: look for tvshow.nfo in show root
                show_dir = path.parent.parent
                nfo_file = show_dir / 'tvshow.nfo'
            else:
                # Movie: look for movie.nfo or <filename>.nfo
                nfo_file = path.parent / (path.stem + '.nfo')
                if not nfo_file.exists():
                    nfo_file = path.parent / 'movie.nfo'
            
            if not nfo_file.exists():
                return ContentType.UNKNOWN
            
            # Parse NFO XML
            tree = ET.parse(nfo_file)
            root = tree.getroot()
            
            # Check genre tags
            genres = []
            for genre_elem in root.findall('.//genre'):
                if genre_elem.text:
                    genres.append(genre_elem.text.lower())
            
            if any('anime' in g or 'animation' in g for g in genres):
                # Animation genre found, but is it anime specifically?
                # Check for Japanese origin indicators
                
                # Check studio
                studio_elem = root.find('.//studio')
                if studio_elem is not None and studio_elem.text:
                    studio = studio_elem.text.lower()
                    if any(anime_studio in studio for anime_studio in ANIME_STUDIOS):
                        return ContentType.ANIME
                
                # Check country
                country_elem = root.find('.//country')
                if country_elem is not None and country_elem.text:
                    country = country_elem.text.lower()
                    if 'japan' in country or 'jp' in country:
                        return ContentType.ANIME
                
                # Check original title for Japanese characters
                orig_title = root.find('.//originaltitle')
                if orig_title is not None and orig_title.text:
                    # Check for Japanese characters (Hiragana, Katakana, Kanji)
                    if any('\u3040' <= c <= '\u309f' or  # Hiragana
                           '\u30a0' <= c <= '\u30ff' or  # Katakana
                           '\u4e00' <= c <= '\u9fff'     # Kanji
                           for c in orig_title.text):
                        return ContentType.ANIME
                
                # If genre is 'anime' specifically (not just 'animation')
                if any(g == 'anime' for g in genres):
                    return ContentType.ANIME
            
            return ContentType.UNKNOWN
            
        except ET.ParseError as e:
            logger.warning(f"Failed to parse NFO: {e}")
            return ContentType.UNKNOWN
        except Exception as e:
            logger.error(f"Error reading NFO: {e}")
            return ContentType.UNKNOWN
    
    def _detect_from_api(self, path: Path) -> ContentType:
        """Detect content type from Sonarr/Radarr API."""
        path_str = str(path).lower()
        
        try:
            if any(ind in path_str for ind in ['/shows/', '/tv/', 'season']):
                return self._query_sonarr(path)
            else:
                return self._query_radarr(path)
        except Exception as e:
            logger.error(f"API detection failed: {e}")
            return ContentType.UNKNOWN
    
    def _query_sonarr(self, path: Path) -> ContentType:
        """Query Sonarr for series genres/tags."""
        if not self.sonarr_url or not self.sonarr_api_key:
            return ContentType.UNKNOWN
        
        try:
            response = requests.get(
                f"{self.sonarr_url}/api/v3/series",
                headers={'X-Api-Key': self.sonarr_api_key},
                timeout=10
            )
            response.raise_for_status()
            series_list = response.json()
            
            # Find series by path
            show_dir = str(path.parent.parent)
            
            for series in series_list:
                if show_dir.startswith(series.get('path', '')):
                    # Check genres
                    genres = [g.lower() for g in series.get('genres', [])]
                    if 'anime' in genres:
                        return ContentType.ANIME
                    
                    # Check tags
                    # Note: Would need to query /api/v3/tag to resolve tag IDs
                    
                    # Check series type
                    series_type = series.get('seriesType', '').lower()
                    if series_type == 'anime':
                        return ContentType.ANIME
                    
                    return ContentType.LIVE_ACTION
            
            return ContentType.UNKNOWN
            
        except requests.RequestException as e:
            logger.warning(f"Sonarr API request failed: {e}")
            return ContentType.UNKNOWN
    
    def _query_radarr(self, path: Path) -> ContentType:
        """Query Radarr for movie genres/tags."""
        if not self.radarr_url or not self.radarr_api_key:
            return ContentType.UNKNOWN
        
        try:
            response = requests.get(
                f"{self.radarr_url}/api/v3/movie",
                headers={'X-Api-Key': self.radarr_api_key},
                timeout=10
            )
            response.raise_for_status()
            movie_list = response.json()
            
            # Find movie by path
            movie_dir = str(path.parent)
            
            for movie in movie_list:
                if movie_dir.startswith(movie.get('path', '')):
                    # Check genres
                    genres = [g.lower() for g in movie.get('genres', [])]
                    if 'anime' in genres:
                        return ContentType.ANIME
                    
                    # Check if animation + Japanese origin
                    if 'animation' in genres:
                        orig_lang = movie.get('originalLanguage', {})
                        if isinstance(orig_lang, dict):
                            lang_name = orig_lang.get('name', '').lower()
                        else:
                            lang_name = str(orig_lang).lower()
                        
                        if 'japanese' in lang_name:
                            return ContentType.ANIME
                    
                    return ContentType.LIVE_ACTION
            
            return ContentType.UNKNOWN
            
        except requests.RequestException as e:
            logger.warning(f"Radarr API request failed: {e}")
            return ContentType.UNKNOWN


def is_anime(file_path: str) -> bool:
    """
    Simple function to check if a file is anime.
    
    Uses default detector with path-based detection only.
    """
    detector = AnimeDetector()
    return detector.is_anime(file_path, use_api=False)
