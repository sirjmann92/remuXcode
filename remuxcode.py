#!/usr/bin/env python3
"""
remuXcode - Unified Media Transcoding Service

Combines DTS audio conversion, HEVC video encoding, and stream cleanup
into a single webhook service for Sonarr/Radarr integration.

Runs as a systemd service on the host machine.
"""

import os
import sys
import json
import logging
import shutil
import threading
import time
import uuid
import glob
import queue
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Dict, List, Any
from enum import Enum

import requests

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

# Import backend modules
from backend.utils.ffprobe import FFProbe
from backend.utils.config import Config, get_config
from backend.utils.anime_detect import AnimeDetector, ContentType
from backend.utils.language import LanguageDetector
from backend.utils.job_store import JobStore
from backend.workers.audio import AudioConverter, AudioConversionResult
from backend.workers.video import VideoConverter, VideoConversionResult
from backend.workers.cleanup import StreamCleanup, CleanupResult
from backend import __version__

# Service configuration
# Priority: REMUXCODE_* → MEDIA_* → DTS_* (backwards compatibility)
WEBHOOK_PORT = int(os.getenv('REMUXCODE_PORT', os.getenv('MEDIA_WEBHOOK_PORT', os.getenv('DTS_WEBHOOK_PORT', '7889'))))
WEBHOOK_HOST = os.getenv('REMUXCODE_HOST', os.getenv('MEDIA_WEBHOOK_HOST', os.getenv('DTS_WEBHOOK_HOST', '0.0.0.0')))
CONFIG_PATH = os.getenv('REMUXCODE_CONFIG_PATH', os.getenv('MEDIA_CONFIG_PATH', str(Path(__file__).parent / 'backend' / 'config.yaml')))

# Load path mappings from environment
PATH_MAPPINGS = []
for key in sorted(os.environ.keys()):
    if key.startswith('PATH_MAPPING_') and key.endswith('_CONTAINER'):
        index = key.replace('PATH_MAPPING_', '').replace('_CONTAINER', '')
        container_path = os.getenv(f'PATH_MAPPING_{index}_CONTAINER')
        host_path = os.getenv(f'PATH_MAPPING_{index}_HOST')
        if container_path and host_path:
            PATH_MAPPINGS.append((container_path, host_path))

PATH_MAPPINGS.sort(key=lambda x: len(x[0]), reverse=True)


class JobType(Enum):
    """Types of conversion jobs."""
    AUDIO = "audio"
    VIDEO = "video"
    CLEANUP = "cleanup"
    FULL = "full"  # All conversions


class JobStatus(Enum):
    """Job status values."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class ConversionJob:
    """Represents a conversion job."""
    id: str
    job_type: JobType
    file_path: str
    status: JobStatus
    created_at: float
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    progress: float = 0.0
    result: Optional[Dict] = None
    error: Optional[str] = None
    source: str = "webhook"  # webhook, api, batch
    
    def to_dict(self) -> Dict:
        return {
            'id': self.id,
            'job_type': self.job_type.value,
            'file_path': self.file_path,
            'status': self.status.value,
            'created_at': self.created_at,
            'started_at': self.started_at,
            'completed_at': self.completed_at,
            'progress': self.progress,
            'result': self.result,
            'error': self.error,
            'source': self.source,
        }


class JobQueue:
    """Thread-safe job queue with status tracking and persistent storage."""
    
    def __init__(self, max_workers: int = 1, job_store: Optional[JobStore] = None):
        self.jobs: Dict[str, ConversionJob] = {}
        self.pending_queue: queue.Queue = queue.Queue()
        self.lock = threading.Lock()
        self.workers: List[threading.Thread] = []
        self.running = False
        self.max_workers = max_workers
        self.job_store = job_store
    
    def start(self):
        """Start worker threads."""
        self.running = True
        for i in range(self.max_workers):
            worker = threading.Thread(target=self._worker_loop, name=f"Worker-{i}", daemon=True)
            worker.start()
            self.workers.append(worker)
        logger.info(f"Started {self.max_workers} job worker(s)")
    
    def stop(self):
        """Stop worker threads."""
        self.running = False
        for worker in self.workers:
            worker.join(timeout=5)
    
    def add_job(self, job: ConversionJob) -> str:
        """Add a job to the queue."""
        with self.lock:
            self.jobs[job.id] = job
        
        # Save to persistent storage
        if self.job_store:
            self._save_job_to_store(job)
        
        self.pending_queue.put(job.id)
        logger.info(f"Queued job {job.id}: {job.job_type.value} for {Path(job.file_path).name}")
        return job.id
    
    def get_job(self, job_id: str) -> Optional[ConversionJob]:
        """Get job by ID."""
        return self.jobs.get(job_id)
    
    def get_all_jobs(self) -> List[ConversionJob]:
        """Get all jobs."""
        return list(self.jobs.values())
    
    def cancel_job(self, job_id: str) -> bool:
        """
        Cancel a pending or running job.
        
        Args:
            job_id: Job identifier
            
        Returns:
            True if job was cancelled, False if not found or already completed
        """
        job = self.jobs.get(job_id)
        if not job:
            return False
        
        # Can only cancel pending or running jobs
        if job.status not in [JobStatus.PENDING, JobStatus.RUNNING]:
            return False
        
        job.status = JobStatus.CANCELLED
        job.completed_at = time.time()
        job.error = "Cancelled by user"
        
        # Save to persistent storage
        if self.job_store:
            self._save_job_to_store(job)
        
        logger.info(f"Cancelled job {job_id}")
        return True
    
    def _save_job_to_store(self, job: ConversionJob):
        """Save job to persistent storage."""
        if not self.job_store:
            return
        
        job_data = {
            'id': job.id,
            'file_path': job.file_path,
            'status': job.status.value,
            'progress': job.progress,
            'error': job.error,
            'created_at': time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(job.created_at)),
            'started_at': time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(job.started_at)) if job.started_at else None,
            'completed_at': time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(job.completed_at)) if job.completed_at else None,
        }
        
        # Extract conversion flags from result
        if job.result:
            job_data['video_converted'] = 1 if (job.result.get('video') and job.result['video'].get('success')) else 0
            job_data['audio_converted'] = 1 if (job.result.get('audio') and job.result['audio'].get('success')) else 0
            job_data['streams_cleaned'] = 1 if (job.result.get('cleanup') and job.result['cleanup'].get('success')) else 0
        
        self.job_store.save_job(job_data)
    
    def load_pending_jobs(self):
        """Load pending jobs from database and queue them."""
        if not self.job_store:
            return 0
        
        pending = self.job_store.get_pending_jobs()
        count = 0
        
        for job_data in pending:
            # Convert back to ConversionJob
            # Reset to queued status for retry
            job = ConversionJob(
                id=job_data['id'],
                job_type=JobType.FULL,  # Default to full conversion
                file_path=job_data['file_path'],
                status=JobStatus.PENDING,
                created_at=time.time(),  # Use current time for requeued jobs
                progress=0.0,
            )
            
            with self.lock:
                self.jobs[job.id] = job
            self.pending_queue.put(job.id)
            count += 1
        
        if count > 0:
            logger.info(f"Loaded {count} pending job(s) from database")
        
        return count
    
    def _worker_loop(self):
        """Worker thread main loop."""
        while self.running:
            try:
                job_id = self.pending_queue.get(timeout=1)
                self._process_job(job_id)
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Worker error: {e}")
    
    def _process_job(self, job_id: str):
        """Process a single job."""
        job = self.jobs.get(job_id)
        if not job:
            return
        
        # Check if job was cancelled before we started
        if job.status == JobStatus.CANCELLED:
            logger.info(f"Job {job_id} was cancelled, skipping")
            return
        
        job.status = JobStatus.RUNNING
        job.started_at = time.time()
        
        # Save status to database
        if self.job_store:
            self._save_job_to_store(job)
        
        try:
            result = process_file(job.file_path, job.job_type, job_id)
            
            # Check if cancelled during processing
            if job.status == JobStatus.CANCELLED:
                logger.info(f"Job {job_id} was cancelled during processing")
                return
            
            job.result = result
            job.status = JobStatus.COMPLETED
            logger.info(f"Job {job_id} completed successfully")
            
            # Trigger Sonarr/Radarr refresh and rename if any conversion was successful
            any_success = (
                (result.get('audio') and result['audio'].get('success')) or
                (result.get('video') and result['video'].get('success')) or
                (result.get('cleanup') and result['cleanup'].get('success'))
            )
            
            if any_success:
                try:
                    trigger_rename(job.file_path)
                except Exception as rename_err:
                    logger.warning(f"Rename trigger failed (job still completed): {rename_err}")
            
        except Exception as e:
            job.error = str(e)
            job.status = JobStatus.FAILED
            logger.error(f"Job {job_id} failed: {e}")
        finally:
            job.completed_at = time.time()
            
            # Save final status to database
            if self.job_store:
                self._save_job_to_store(job)


# Global instances
logger = logging.getLogger('media-converter')
config: Optional[Config] = None
job_queue: Optional[JobQueue] = None
job_store: Optional[JobStore] = None
ffprobe: Optional[FFProbe] = None
anime_detector: Optional[AnimeDetector] = None
language_detector: Optional[LanguageDetector] = None
audio_converter: Optional[AudioConverter] = None
video_converter: Optional[VideoConverter] = None
stream_cleanup: Optional[StreamCleanup] = None


def setup_logging():
    """Configure logging."""
    log_level_str = os.getenv('LOG_LEVEL', 'INFO').upper()
    log_level = getattr(logging, log_level_str, logging.INFO)
    
    # Custom filter to shorten logger names
    class ShortLoggerFilter(logging.Filter):
        def filter(self, record):
            # Shorten backend.workers.video → video
            if record.name.startswith('backend.workers.'):
                record.name = record.name.replace('backend.workers.', '')
            elif record.name.startswith('backend.utils.'):
                record.name = record.name.replace('backend.utils.', '')
            elif record.name == 'media-converter':
                record.name = 'main'
            return True
    
    handlers = [logging.StreamHandler()]
    
    log_file = os.getenv('LOG_FILE', '/var/log/remuxcode.log')
    try:
        handlers.append(logging.FileHandler(log_file))
    except PermissionError:
        try:
            handlers.append(logging.FileHandler(os.path.expanduser('~/remuxcode.log')))
        except Exception:
            pass
    
    # Add filter to all handlers
    log_filter = ShortLoggerFilter()
    for handler in handlers:
        handler.addFilter(log_filter)
    
    # Shorter format: no timestamp (systemd has it), shortened names
    logging.basicConfig(
        level=log_level,
        format='%(levelname)s [%(name)s] %(message)s',
        handlers=handlers
    )


def initialize_components():
    """Initialize all converter components."""
    global config, job_queue, job_store, ffprobe, anime_detector, language_detector
    global audio_converter, video_converter, stream_cleanup
    
    logger.info("Initializing remuXcode components...")
    
    # Load configuration
    config = get_config(CONFIG_PATH)
    
    # Initialize utilities
    ffprobe = FFProbe()
    anime_detector = AnimeDetector(
        sonarr_url=os.getenv('SONARR_URL', config.sonarr.url),
        sonarr_api_key=os.getenv('SONARR_API_KEY', config.sonarr.api_key),
        radarr_url=os.getenv('RADARR_URL', config.radarr.url),
        radarr_api_key=os.getenv('RADARR_API_KEY', config.radarr.api_key),
    )
    language_detector = LanguageDetector(
        sonarr_url=os.getenv('SONARR_URL', config.sonarr.url),
        sonarr_api_key=os.getenv('SONARR_API_KEY', config.sonarr.api_key),
        radarr_url=os.getenv('RADARR_URL', config.radarr.url),
        radarr_api_key=os.getenv('RADARR_API_KEY', config.radarr.api_key),
    )
    
    # Initialize workers
    audio_converter = AudioConverter(
        config=config.audio,
        ffprobe=ffprobe,
        get_volume_root=get_volume_root,
    )
    video_converter = VideoConverter(
        config=config.video,
        ffprobe=ffprobe,
        anime_detector=anime_detector,
        get_volume_root=get_volume_root,
    )
    stream_cleanup = StreamCleanup(
        config=config.cleanup,
        ffprobe=ffprobe,
        language_detector=language_detector,
        get_volume_root=get_volume_root,
    )
    
    # Initialize persistent job storage
    db_path = os.path.join(str(Path(__file__).parent), 'jobs.db')
    job_store = JobStore(db_path=db_path)
    
    # Initialize job queue with persistent storage
    job_queue = JobQueue(max_workers=config.workers, job_store=job_store)
    job_queue.start()
    
    # Load any pending jobs from previous runs
    pending_count = job_queue.load_pending_jobs()
    if pending_count > 0:
        logger.info(f"Resumed {pending_count} pending job(s) from database")
    
    logger.info("All components initialized successfully")


def translate_path(container_path: str) -> str:
    """Translate container path to host path."""
    for container_prefix, host_prefix in PATH_MAPPINGS:
        if container_path.startswith(container_prefix):
            return container_path.replace(container_prefix, host_prefix, 1)
    return container_path


def get_volume_root(file_path: str) -> str:
    """Get volume root for temp directory."""
    temp_dir_override = os.getenv('TEMP_DIR', '').strip()
    if temp_dir_override:
        return temp_dir_override
    
    for _, host_prefix in PATH_MAPPINGS:
        if file_path.startswith(host_prefix):
            return host_prefix
    
    return '/tmp'


def process_file(file_path: str, job_type: JobType, job_id: str = None) -> Dict[str, Any]:
    """
    Process a single file with the specified conversion type.
    
    Args:
        file_path: Path to the media file
        job_type: Type of conversion to perform
        job_id: Unique identifier for this job (used for temp directories)
    
    Returns:
        Dictionary with conversion results
    """
    results = {
        'file': file_path,
        'audio': None,
        'video': None,
        'cleanup': None,
    }
    
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")
    
    # Determine what to process
    do_audio = job_type in (JobType.AUDIO, JobType.FULL)
    do_video = job_type in (JobType.VIDEO, JobType.FULL)
    do_cleanup = job_type in (JobType.CLEANUP, JobType.FULL)
    
    # Audio conversion (DTS -> AC3/AAC)
    if do_audio and audio_converter.should_convert(file_path):
        logger.info(f"Converting audio: {Path(file_path).name}")
        result = audio_converter.convert(file_path, job_id=job_id)
        results['audio'] = {
            'success': result.success,
            'streams_converted': result.streams_converted,
            'error': result.error,
        }
        if not result.success:
            logger.error(f"Audio conversion failed: {result.error}")
    
    # Video conversion (H.264 -> HEVC/AV1)
    if do_video and video_converter.should_convert(file_path):
        logger.info(f"Converting video: {Path(file_path).name}")
        result = video_converter.convert(file_path, job_id=job_id)
        results['video'] = {
            'success': result.success,
            'codec_from': result.codec_from,
            'codec_to': result.codec_to,
            'content_type': result.content_type,
            'size_change_percent': result.size_change_percent,
            'error': result.error,
        }
        if not result.success:
            logger.error(f"Video conversion failed: {result.error}")
    
    # Stream cleanup (remove unwanted languages)
    if do_cleanup and stream_cleanup.should_cleanup(file_path):
        logger.info(f"Cleaning streams: {Path(file_path).name}")
        result = stream_cleanup.cleanup(file_path, job_id=job_id)
        results['cleanup'] = {
            'success': result.success,
            'audio_removed': result.audio_removed,
            'subtitle_removed': result.subtitle_removed,
            'original_language': result.original_language,
            'error': result.error,
        }
        if not result.success:
            logger.error(f"Stream cleanup failed: {result.error}")
    
    return results


def trigger_rename(file_path: str, media_type: str = 'auto'):
    """
    Trigger Sonarr/Radarr refresh and rename after conversion.
    
    Uses polling to ensure refresh completes before triggering rename.
    This is critical for:
    - Updating media info in Sonarr/Radarr after codec changes
    - Triggering rename (which fires user's custom scripts)
    - Ensuring library metadata is current
    """
    if media_type == 'auto':
        path_lower = file_path.lower()
        if '/movies/' in path_lower or '/films/' in path_lower:
            media_type = 'movie'
        else:
            media_type = 'tv'
    
    try:
        if media_type == 'movie':
            radarr_url = os.getenv('RADARR_URL', config.radarr.url).rstrip('/')
            radarr_key = os.getenv('RADARR_API_KEY', config.radarr.api_key)
            
            if not radarr_url or not radarr_key:
                logger.warning("Radarr not configured, skipping rename")
                return
            
            # Find movie by folder name (works across different mount points)
            logger.debug(f"Looking up movie for file: {file_path}")
            response = requests.get(
                f"{radarr_url}/api/v3/movie",
                headers={'X-Api-Key': radarr_key},
                timeout=30
            )
            response.raise_for_status()
            
            movies = response.json()
            movie_id = None
            file_path_obj = Path(file_path).resolve()
            
            # Match by folder name (works across different mount points)
            for movie in movies:
                movie_path = Path(movie.get('path', ''))
                movie_folder = movie_path.name
                if movie_folder in file_path_obj.parts:
                    movie_id = movie['id']
                    logger.info(f"Triggering Radarr refresh/rename for: {movie.get('title')}")
                    break
            
            if not movie_id:
                logger.warning(f"Movie not found in Radarr for path: {file_path}")
                return
            
            # Step 1: Trigger refresh to update media info
            response = requests.post(
                f"{radarr_url}/api/v3/command",
                headers={'X-Api-Key': radarr_key},
                json={'name': 'RefreshMovie', 'movieIds': [movie_id]},
                timeout=30
            )
            response.raise_for_status()
            refresh_command_id = response.json().get('id')
            logger.info(f"Triggered Radarr refresh for movie ID {movie_id}")
            
            # Step 2: Poll for refresh completion
            max_wait = 30  # seconds
            waited = 0
            while waited < max_wait:
                time.sleep(2)
                waited += 2
                
                status_response = requests.get(
                    f"{radarr_url}/api/v3/command/{refresh_command_id}",
                    headers={'X-Api-Key': radarr_key},
                    timeout=10
                )
                status_response.raise_for_status()
                status = status_response.json().get('status')
                
                if status == 'completed':
                    logger.info("Radarr refresh completed")
                    break
                elif status == 'failed':
                    logger.error("Radarr refresh failed")
                    return
            
            # Step 3: Get movie file ID
            movie_file_id = None
            response = requests.get(
                f"{radarr_url}/api/v3/moviefile",
                params={'movieId': movie_id},
                headers={'X-Api-Key': radarr_key},
                timeout=30
            )
            if response.status_code == 200:
                for mf in response.json():
                    if Path(mf.get('path', '')).name == file_path_obj.name:
                        movie_file_id = mf.get('id')
                        break
            
            # Step 4: Trigger rename (fires custom scripts)
            rename_payload = {'name': 'RenameMovie', 'movieIds': [movie_id]}
            if movie_file_id:
                rename_payload['movieFileIds'] = [movie_file_id]
            
            requests.post(
                f"{radarr_url}/api/v3/command",
                headers={'X-Api-Key': radarr_key},
                json=rename_payload,
                timeout=30
            )
            
            logger.info(f"Radarr rename triggered successfully")
            
        else:  # TV series
            sonarr_url = os.getenv('SONARR_URL', config.sonarr.url).rstrip('/')
            sonarr_key = os.getenv('SONARR_API_KEY', config.sonarr.api_key)
            
            if not sonarr_url or not sonarr_key:
                logger.warning("Sonarr not configured, skipping rename")
                return
            
            # Find series by folder name (works across different mount points)
            logger.debug(f"Looking up series for file: {file_path}")
            response = requests.get(
                f"{sonarr_url}/api/v3/series",
                headers={'X-Api-Key': sonarr_key},
                timeout=30
            )
            response.raise_for_status()
            
            all_series = response.json()
            series_id = None
            file_path_obj = Path(file_path).resolve()
            
            # Match by folder name and check for Season folder
            for series in all_series:
                series_path = Path(series.get('path', ''))
                series_folder = series_path.name
                if series_folder in file_path_obj.parts and any('Season' in part for part in file_path_obj.parts):
                    series_id = series['id']
                    logger.info(f"Triggering Sonarr refresh/rename for: {series.get('title')}")
                    break
            
            if not series_id:
                logger.warning(f"Series not found in Sonarr for path: {file_path}")
                return
            
            # Step 1: Trigger refresh to update media info
            response = requests.post(
                f"{sonarr_url}/api/v3/command",
                headers={'X-Api-Key': sonarr_key},
                json={'name': 'RefreshSeries', 'seriesId': series_id},
                timeout=30
            )
            response.raise_for_status()
            refresh_command_id = response.json().get('id')
            logger.info(f"Triggered Sonarr refresh for series ID {series_id}")
            
            # Step 2: Poll for refresh completion
            max_wait = 30  # seconds
            waited = 0
            while waited < max_wait:
                time.sleep(2)
                waited += 2
                
                status_response = requests.get(
                    f"{sonarr_url}/api/v3/command/{refresh_command_id}",
                    headers={'X-Api-Key': sonarr_key},
                    timeout=10
                )
                status_response.raise_for_status()
                status = status_response.json().get('status')
                
                if status == 'completed':
                    logger.info("Sonarr refresh completed")
                    break
                elif status == 'failed':
                    logger.error("Sonarr refresh failed")
                    return
            
            # Step 3: Get episode file ID
            episode_file_id = None
            response = requests.get(
                f"{sonarr_url}/api/v3/episodefile",
                params={'seriesId': series_id},
                headers={'X-Api-Key': sonarr_key},
                timeout=30
            )
            if response.status_code == 200:
                for ef in response.json():
                    if Path(ef.get('path', '')).name == file_path_obj.name:
                        episode_file_id = ef.get('id')
                        break
            
            # Step 4: Trigger rename (fires custom scripts)
            rename_payload = {'name': 'RenameFiles', 'seriesId': series_id}
            if episode_file_id:
                rename_payload['files'] = [episode_file_id]
            
            requests.post(
                f"{sonarr_url}/api/v3/command",
                headers={'X-Api-Key': sonarr_key},
                json=rename_payload,
                timeout=30
            )
            
            logger.info(f"Sonarr rename triggered successfully")
            
    except Exception as e:
        logger.error(f"Failed to trigger rename: {e}")


class MediaWebhookHandler(BaseHTTPRequestHandler):
    """Handle incoming webhook and API requests."""
    
    def log_message(self, format, *args):
        logger.debug("%s - %s" % (self.address_string(), format % args))
    
    def validate_api_key(self) -> bool:
        """Validate API key from header."""
        # Check REMUXCODE_API_KEY first, fall back to legacy names for backwards compatibility
        api_key = os.getenv('REMUXCODE_API_KEY', 
                           os.getenv('MEDIA_API_KEY', 
                                    os.getenv('DTS_WEBHOOK_API_KEY', ''))).strip()
        if not api_key:
            return True
        request_key = self.headers.get('X-API-Key', '').strip()
        return request_key == api_key
    
    def send_json(self, status: int, data: Any):
        """Send JSON response."""
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2).encode())
    
    def do_GET(self):
        """Handle GET requests."""
        parsed = urlparse(self.path)
        path = parsed.path
        
        # Health check (no auth required)
        if path == '/health':
            self.send_json(200, {
                'status': 'healthy',
                'service': 'remuxcode',
                'version': __version__,
            })
            return
        
        # Auth required for other endpoints
        if not self.validate_api_key():
            self.send_json(401, {'error': 'Unauthorized'})
            return
        
        # Job status
        if path.startswith('/jobs/'):
            job_id = path.split('/')[-1]
            job = job_queue.get_job(job_id)
            if job:
                self.send_json(200, job.to_dict())
            else:
                self.send_json(404, {'error': 'Job not found'})
            return
        
        # List all jobs
        if path == '/jobs':
            jobs = [j.to_dict() for j in job_queue.get_all_jobs()]
            self.send_json(200, {'jobs': jobs})
            return
        
        # Analyze file (no conversion)
        if path == '/analyze':
            query = parse_qs(parsed.query)
            file_path = query.get('path', [None])[0]
            if not file_path:
                self.send_json(400, {'error': 'Missing path parameter'})
                return
            
            file_path = translate_path(file_path)
            if not os.path.exists(file_path):
                self.send_json(404, {'error': 'File not found'})
                return
            
            info = ffprobe.get_file_info(file_path)
            if not info:
                self.send_json(500, {'error': 'Failed to analyze file'})
                return
            
            content_type = anime_detector.detect(file_path, use_api=False)
            is_anime = content_type == ContentType.ANIME
            
            self.send_json(200, {
                'file': file_path,
                'format': info.format_name,
                'duration': info.duration,
                'size': info.size,
                'video': {
                    'codec': info.primary_video.codec_name if info.primary_video else None,
                    'bit_depth': info.primary_video.bit_depth if info.primary_video else None,
                    'resolution': f"{info.primary_video.width}x{info.primary_video.height}" if info.primary_video else None,
                    'profile': info.primary_video.profile if info.primary_video else None,
                    'is_hevc': info.is_hevc,
                    'is_10bit_h264': info.needs_video_conversion,
                },
                'audio_streams': len(info.audio_streams),
                'has_dts': info.has_dts,
                'has_truehd': info.has_truehd,
                'needs_audio_conversion': info.needs_audio_conversion,
                'needs_video_conversion': info.needs_video_conversion,
                'is_anime': is_anime,
                'content_type': content_type.value,
            })
            return
        
        # Scan directory for files needing conversion
        if path == '/scan':
            params = parse_qs(parsed.query)
            dir_path = params.get('path', [None])[0]
            if not dir_path:
                self.send_json(400, {'error': 'Missing path parameter'})
                return
            
            dir_path = translate_path(dir_path)
            if not os.path.isdir(dir_path):
                self.send_json(404, {'error': 'Directory not found'})
                return
            
            # Options
            recursive = params.get('recursive', ['true'])[0].lower() == 'true'
            filter_type = params.get('filter', ['any'])[0]  # any, video, audio, anime
            extensions = {'.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.m4v'}
            
            results = []
            pattern = '**/*' if recursive else '*'
            
            for ext in extensions:
                for file_path in Path(dir_path).glob(pattern + ext):
                    if not file_path.is_file():
                        continue
                    
                    try:
                        info = ffprobe.get_file_info(str(file_path))
                        if not info:
                            continue
                        
                        content_type = anime_detector.detect(str(file_path), use_api=False)
                        is_anime = content_type == ContentType.ANIME
                        
                        # Apply filters
                        if filter_type == 'video' and not info.needs_video_conversion:
                            continue
                        if filter_type == 'audio' and not info.needs_audio_conversion:
                            continue
                        if filter_type == 'anime' and not is_anime:
                            continue
                        
                        results.append({
                            'file': str(file_path),
                            'size': info.size,
                            'video': {
                                'codec': info.primary_video.codec_name if info.primary_video else None,
                                'bit_depth': info.primary_video.bit_depth if info.primary_video else None,
                                'is_10bit_h264': info.needs_video_conversion,
                                'is_hevc': info.is_hevc,
                            },
                            'has_dts': info.has_dts,
                            'has_truehd': info.has_truehd,
                            'needs_audio_conversion': info.needs_audio_conversion,
                            'needs_video_conversion': info.needs_video_conversion,
                            'is_anime': is_anime,
                        })
                    except Exception as e:
                        logger.warning(f"Error scanning {file_path}: {e}")
                        continue
            
            # Summary stats
            needs_video = sum(1 for r in results if r['needs_video_conversion'])
            needs_audio = sum(1 for r in results if r['needs_audio_conversion'])
            anime_count = sum(1 for r in results if r['is_anime'])
            
            self.send_json(200, {
                'directory': dir_path,
                'recursive': recursive,
                'filter': filter_type,
                'total_files': len(results),
                'summary': {
                    'needs_video_conversion': needs_video,
                    'needs_audio_conversion': needs_audio,
                    'anime_files': anime_count,
                },
                'files': results,
            })
            return
        
        # List movies from Radarr with full media analysis
        if path == '/movies':
            params = parse_qs(parsed.query)
            search = params.get('search', [None])[0]
            analyze = params.get('analyze', ['true'])[0].lower() == 'true'
            filter_type = params.get('filter', ['any'])[0]  # any, video, audio, anime
            
            radarr_url = os.getenv('RADARR_URL', config.radarr.url)
            radarr_key = os.getenv('RADARR_API_KEY', config.radarr.api_key)
            
            if not radarr_url or not radarr_key:
                self.send_json(500, {'error': 'Radarr not configured'})
                return
            
            try:
                response = requests.get(
                    f"{radarr_url}/api/v3/movie",
                    headers={'X-Api-Key': radarr_key},
                    timeout=30
                )
                response.raise_for_status()
                all_movies = response.json()
            except Exception as e:
                self.send_json(500, {'error': f'Failed to query Radarr: {e}'})
                return
            
            # Filter by search term
            if search:
                search_lower = search.lower()
                all_movies = [m for m in all_movies if search_lower in m.get('title', '').lower()]
            
            results = []
            for movie in all_movies:
                if not movie.get('hasFile'):
                    continue
                
                movie_file = movie.get('movieFile', {})
                file_path = movie_file.get('path')
                if not file_path:
                    continue
                
                # Translate path and check existence
                host_path = translate_path(file_path)
                
                item = {
                    'id': movie['id'],
                    'title': movie.get('title'),
                    'year': movie.get('year'),
                    'path': host_path,
                }
                
                # Quick check from Radarr mediaInfo (no ffprobe needed)
                media_info = movie_file.get('mediaInfo', {})
                audio_codec = media_info.get('audioCodec', '').upper()
                video_codec = media_info.get('videoCodec', '').upper()
                
                item['has_dts'] = 'DTS' in audio_codec
                item['video_codec'] = video_codec
                
                # Full analysis with ffprobe (optional, slower)
                if analyze and os.path.exists(host_path):
                    try:
                        info = ffprobe.get_file_info(host_path)
                        if info:
                            content_type = anime_detector.detect(host_path, use_api=False)
                            is_anime = content_type == ContentType.ANIME
                            
                            item.update({
                                'video': {
                                    'codec': info.primary_video.codec_name if info.primary_video else None,
                                    'bit_depth': info.primary_video.bit_depth if info.primary_video else None,
                                },
                                'has_dts': info.has_dts,
                                'has_truehd': info.has_truehd,
                                'needs_audio_conversion': info.needs_audio_conversion,
                                'is_10bit_h264': info.needs_video_conversion,
                                'is_anime': is_anime,
                            })
                    except Exception as e:
                        logger.warning(f"Error analyzing {host_path}: {e}")
                
                # Apply filter
                if filter_type == 'video' and not item.get('is_10bit_h264', False):
                    continue
                if filter_type == 'audio' and not item.get('has_dts', False) and not item.get('needs_audio_conversion', False):
                    continue
                if filter_type == 'anime' and not item.get('is_anime', False):
                    continue
                
                results.append(item)
            
            # Summary
            h264_10bit_count = sum(1 for r in results if r.get('is_10bit_h264', False))
            needs_audio = sum(1 for r in results if r.get('needs_audio_conversion', False) or r.get('has_dts', False))
            anime_count = sum(1 for r in results if r.get('is_anime', False))
            
            self.send_json(200, {
                'total': len(results),
                'summary': {
                    'h264_10bit_count': h264_10bit_count,
                    'needs_audio_conversion': needs_audio,
                    'anime': anime_count,
                },
                'movies': results,
            })
            return
        
        # List series from Sonarr with full media analysis
        if path == '/series':
            params = parse_qs(parsed.query)
            search = params.get('search', [None])[0]
            analyze = params.get('analyze', ['true'])[0].lower() == 'true'
            filter_type = params.get('filter', ['any'])[0]
            
            sonarr_url = os.getenv('SONARR_URL', config.sonarr.url)
            sonarr_key = os.getenv('SONARR_API_KEY', config.sonarr.api_key)
            
            if not sonarr_url or not sonarr_key:
                self.send_json(500, {'error': 'Sonarr not configured'})
                return
            
            try:
                response = requests.get(
                    f"{sonarr_url}/api/v3/series",
                    headers={'X-Api-Key': sonarr_key},
                    timeout=30
                )
                response.raise_for_status()
                all_series = response.json()
            except Exception as e:
                self.send_json(500, {'error': f'Failed to query Sonarr: {e}'})
                return
            
            # Filter by search
            if search:
                search_lower = search.lower()
                all_series = [s for s in all_series if search_lower in s.get('title', '').lower()]
            
            results = []
            for series in all_series:
                series_id = series['id']
                host_path = translate_path(series.get('path', ''))
                
                item = {
                    'id': series_id,
                    'title': series.get('title'),
                    'year': series.get('year'),
                    'path': host_path,
                    'total_episodes': 0,
                    'dts_count': 0,
                    'h264_10bit_count': 0,
                    'anime_count': 0,
                }
                
                # Get episode files
                try:
                    ep_response = requests.get(
                        f"{sonarr_url}/api/v3/episodefile",
                        params={'seriesId': series_id},
                        headers={'X-Api-Key': sonarr_key},
                        timeout=30
                    )
                    if ep_response.status_code == 200:
                        episode_files = ep_response.json()
                        item['total_episodes'] = len(episode_files)
                        
                        for ep_file in episode_files:
                            ep_path = translate_path(ep_file.get('path', ''))
                            media_info = ep_file.get('mediaInfo', {})
                            audio_codec = media_info.get('audioCodec', '').upper()
                            
                            if 'DTS' in audio_codec:
                                item['dts_count'] += 1
                            
                            # Full analysis (optional)
                            if analyze and os.path.exists(ep_path):
                                try:
                                    info = ffprobe.get_file_info(ep_path)
                                    if info:
                                        if info.needs_video_conversion:
                                            item['h264_10bit_count'] += 1
                                        content_type = anime_detector.detect(ep_path, use_api=False)
                                        if content_type == ContentType.ANIME:
                                            item['anime_count'] += 1
                                except Exception:
                                    pass
                except Exception as e:
                    logger.warning(f"Error getting episodes for series {series_id}: {e}")
                
                # Detect if series is anime (check path or first episode)
                item['is_anime'] = anime_detector.detect(host_path, use_api=False) == ContentType.ANIME
                
                # Apply filter
                if filter_type == 'video' and item['h264_10bit_count'] == 0:
                    continue
                if filter_type == 'audio' and item['dts_count'] == 0:
                    continue
                if filter_type == 'anime' and not item['is_anime']:
                    continue
                
                results.append(item)
            
            # Summary
            total_dts = sum(r['dts_count'] for r in results)
            total_h264_10bit = sum(r['h264_10bit_count'] for r in results)
            anime_series = sum(1 for r in results if r['is_anime'])
            
            self.send_json(200, {
                'total': len(results),
                'summary': {
                    'total_dts_episodes': total_dts,
                    'total_h264_10bit': total_h264_10bit,
                    'anime_series': anime_series,
                },
                'series': results,
            })
            return
        
        self.send_json(404, {'error': 'Not found'})
    
    def do_POST(self):
        """Handle POST requests."""
        parsed = urlparse(self.path)
        path = parsed.path
        
        if not self.validate_api_key():
            self.send_json(401, {'error': 'Unauthorized'})
            return
        
        # Read body
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode() if content_length > 0 else '{}'
        
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self.send_json(400, {'error': 'Invalid JSON'})
            return
        
        # Sonarr/Radarr webhook
        if path == '/webhook':
            self.handle_webhook(data)
            return
        
        # Convert single file
        if path == '/convert':
            file_path = data.get('path')
            if not file_path:
                self.send_json(400, {'error': 'Missing path'})
                return
            
            file_path = translate_path(file_path)
            job_type_str = data.get('type', 'full')
            try:
                job_type = JobType(job_type_str)
            except ValueError:
                self.send_json(400, {'error': f'Invalid type: {job_type_str}'})
                return
            
            job = ConversionJob(
                id=uuid.uuid4().hex[:12],
                job_type=job_type,
                file_path=file_path,
                status=JobStatus.PENDING,
                created_at=time.time(),
                source='api',
            )
            job_queue.add_job(job)
            
            self.send_json(202, {
                'message': 'Job queued',
                'job_id': job.id,
                'status_url': f'/jobs/{job.id}',
            })
            return
        
        # Batch convert movies
        if path == '/api/convert/movies':
            movie_ids = data.get('movie_ids', [])
            if not movie_ids:
                self.send_json(400, {'error': 'Missing movie_ids'})
                return
            self.handle_batch_movies(movie_ids, data.get('type', 'full'))
            return
        
        # Batch convert series
        if path == '/api/convert/series':
            series_ids = data.get('series_ids', [])
            if not series_ids:
                self.send_json(400, {'error': 'Missing series_ids'})
                return
            season_number = data.get('season_number')  # Optional: filter by season
            self.handle_batch_series(series_ids, data.get('type', 'full'), season_number)
            return
        
        self.send_json(404, {'error': 'Not found'})
    
    def handle_webhook(self, data: Dict):
        """Handle Sonarr/Radarr webhook."""
        event_type = data.get('eventType', '')
        
        # Determine source
        if 'movie' in data:
            source = 'radarr'
            files = [data.get('movieFile', {}).get('path')]
        elif 'episodes' in data:
            source = 'sonarr'
            files = [ep.get('episodeFile', {}).get('path') for ep in data.get('episodes', [])]
        else:
            self.send_json(400, {'error': 'Unknown webhook format'})
            return
        
        # Filter and translate paths
        files = [translate_path(f) for f in files if f]
        
        if not files:
            self.send_json(200, {'message': 'No files to process'})
            return
        
        # Queue jobs
        job_ids = []
        for file_path in files:
            if os.path.exists(file_path):
                job = ConversionJob(
                    id=uuid.uuid4().hex[:12],
                    job_type=JobType.FULL,
                    file_path=file_path,
                    status=JobStatus.PENDING,
                    created_at=time.time(),
                    source='webhook',
                )
                job_queue.add_job(job)
                job_ids.append(job.id)
        
        self.send_json(202, {
            'message': f'Queued {len(job_ids)} file(s)',
            'job_ids': job_ids,
        })
    
    def handle_batch_movies(self, movie_ids: List[int], job_type_str: str):
        """Handle batch movie conversion."""
        radarr_url = os.getenv('RADARR_URL', config.radarr.url)
        radarr_key = os.getenv('RADARR_API_KEY', config.radarr.api_key)
        
        if not radarr_url or not radarr_key:
            self.send_json(500, {'error': 'Radarr not configured'})
            return
        
        try:
            job_type = JobType(job_type_str)
        except ValueError:
            self.send_json(400, {'error': f'Invalid type: {job_type_str}'})
            return
        
        job_ids = []
        for movie_id in movie_ids:
            try:
                resp = requests.get(
                    f"{radarr_url}/api/v3/movie/{movie_id}",
                    headers={'X-Api-Key': radarr_key},
                    timeout=30
                )
                if resp.status_code != 200:
                    continue
                
                movie = resp.json()
                file_path = movie.get('movieFile', {}).get('path')
                if file_path:
                    file_path = translate_path(file_path)
                    if os.path.exists(file_path):
                        job = ConversionJob(
                            id=uuid.uuid4().hex[:12],
                            job_type=job_type,
                            file_path=file_path,
                            status=JobStatus.PENDING,
                            created_at=time.time(),
                            source='batch',
                        )
                        job_queue.add_job(job)
                        job_ids.append(job.id)
            except Exception as e:
                logger.error(f"Error getting movie {movie_id}: {e}")
        
        self.send_json(202, {
            'message': f'Queued {len(job_ids)} movie(s)',
            'job_ids': job_ids,
        })
    
    def handle_batch_series(self, series_ids: List[int], job_type_str: str, season_number: Optional[int] = None):
        """Handle batch series conversion.
        
        Args:
            series_ids: List of Sonarr series IDs
            job_type_str: Type of conversion (full, audio, video)
            season_number: Optional season number to filter episodes
        """
        sonarr_url = os.getenv('SONARR_URL', config.sonarr.url)
        sonarr_key = os.getenv('SONARR_API_KEY', config.sonarr.api_key)
        
        if not sonarr_url or not sonarr_key:
            self.send_json(500, {'error': 'Sonarr not configured'})
            return
        
        try:
            job_type = JobType(job_type_str)
        except ValueError:
            self.send_json(400, {'error': f'Invalid type: {job_type_str}'})
            return
        
        job_ids = []
        for series_id in series_ids:
            try:
                resp = requests.get(
                    f"{sonarr_url}/api/v3/episodefile",
                    params={'seriesId': series_id},
                    headers={'X-Api-Key': sonarr_key},
                    timeout=30
                )
                if resp.status_code != 200:
                    continue
                
                for ep_file in resp.json():
                    # Filter by season if specified
                    if season_number is not None:
                        # Get episode details to check season
                        episode_id = ep_file.get('episodeFileId') or ep_file.get('id')
                        if episode_id:
                            # Episode files include seasonNumber in the response
                            ep_season = ep_file.get('seasonNumber')
                            if ep_season != season_number:
                                continue
                    
                    file_path = ep_file.get('path')
                    if file_path:
                        file_path = translate_path(file_path)
                        if os.path.exists(file_path):
                            job = ConversionJob(
                                id=uuid.uuid4().hex[:12],
                                job_type=job_type,
                                file_path=file_path,
                                status=JobStatus.PENDING,
                                created_at=time.time(),
                                source='batch',
                            )
                            job_queue.add_job(job)
                            job_ids.append(job.id)
            except Exception as e:
                logger.error(f"Error getting series {series_id}: {e}")
        
        self.send_json(202, {
            'message': f'Queued {len(job_ids)} episode(s)',
            'job_ids': job_ids,
        })
    
    def do_DELETE(self):
        """Handle DELETE requests."""
        parsed = urlparse(self.path)
        path = parsed.path
        
        if not self.validate_api_key():
            self.send_json(401, {'error': 'Unauthorized'})
            return
        
        # Cancel/delete a job
        if path.startswith('/jobs/'):
            job_id = path.split('/')[-1]
            
            # Try to cancel the job
            if job_queue.cancel_job(job_id):
                self.send_json(200, {
                    'message': 'Job cancelled',
                    'job_id': job_id
                })
            else:
                # If can't cancel, try to delete from store
                job = job_queue.get_job(job_id)
                if job:
                    # Can only delete if already completed/failed
                    if job.status in [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED]:
                        if job_store and job_store.delete_job(job_id):
                            with job_queue.lock:
                                del job_queue.jobs[job_id]
                            self.send_json(200, {
                                'message': 'Job deleted',
                                'job_id': job_id
                            })
                        else:
                            self.send_json(500, {'error': 'Failed to delete job'})
                    else:
                        self.send_json(400, {'error': 'Cannot delete running job'})
                else:
                    self.send_json(404, {'error': 'Job not found'})
            return
        
        self.send_json(404, {'error': 'Not found'})


def cleanup_temp_dirs():
    """Clean up orphaned temp directories on startup."""
    logger.info("Cleaning up orphaned temp directories...")
    # Include old patterns for backwards compatibility and new unified pattern
    patterns = ['.dts-temp-*', '.audio-temp-*', '.cleanup-temp-*', '.hevc-tmp*', '.remuxcode-temp-*']
    
    volumes = set(host for _, host in PATH_MAPPINGS)
    temp_dir = os.getenv('TEMP_DIR', '/tmp')
    volumes.add(temp_dir)
    
    count = 0
    for volume in volumes:
        if not os.path.exists(volume):
            continue
        for pattern in patterns:
            for path in glob.glob(os.path.join(volume, pattern)):
                try:
                    if os.path.isdir(path):
                        shutil.rmtree(path)
                    else:
                        os.unlink(path)
                    count += 1
                except Exception as e:
                    logger.warning(f"Failed to remove {path}: {e}")
    
    if count:
        logger.info(f"Cleaned up {count} orphaned temp file(s)/directory(ies)")


def main():
    """Main entry point."""
    setup_logging()
    logger.info("="*60)
    logger.info("  remuXcode Starting")
    logger.info("="*60)
    
    # Cleanup from previous runs
    cleanup_temp_dirs()
    
    # Initialize components
    initialize_components()
    
    # Log configuration
    logger.info(f"Host: {WEBHOOK_HOST}:{WEBHOOK_PORT}")
    logger.info(f"Config: {CONFIG_PATH}")
    logger.info(f"Path mappings: {len(PATH_MAPPINGS)}")
    for container, host in PATH_MAPPINGS:
        logger.info(f"  {container} -> {host}")
    
    # Start HTTP server
    server = HTTPServer((WEBHOOK_HOST, WEBHOOK_PORT), MediaWebhookHandler)
    logger.info(f"Listening on http://{WEBHOOK_HOST}:{WEBHOOK_PORT}")
    logger.info("Ready to receive requests")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        job_queue.stop()
        server.shutdown()


if __name__ == '__main__':
    main()
