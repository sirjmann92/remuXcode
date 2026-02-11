#!/usr/bin/env python3
"""
SQLite-based persistent job storage.

Provides crash recovery and job history for long-running conversions.
"""

import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


class JobStore:
    """
    Thread-safe SQLite job storage.
    
    Features:
    - Persists jobs across service restarts
    - Track job history and status
    - Resume interrupted conversions
    - Automatic cleanup of old completed jobs
    """
    
    def __init__(self, db_path: str = "jobs.db"):
        """
        Initialize job store.
        
        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self._lock = threading.Lock()
        self._init_db()
        logger.info(f"Job store initialized: {self.db_path}")
    
    @contextmanager
    def _get_connection(self):
        """Get thread-safe database connection."""
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    
    def _init_db(self):
        """Create tables if they don't exist."""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    file_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress REAL DEFAULT 0,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    video_converted INTEGER DEFAULT 0,
                    audio_converted INTEGER DEFAULT 0,
                    streams_cleaned INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_status 
                ON jobs(status)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_created 
                ON jobs(created_at DESC)
            """)
    
    def save_job(self, job_data: Dict[str, Any]) -> None:
        """
        Save or update a job.
        
        Args:
            job_data: Job dictionary with at least id, file_path, status
        """
        with self._lock:
            now = datetime.now().isoformat()
            
            with self._get_connection() as conn:
                # Check if job exists
                existing = conn.execute(
                    "SELECT id FROM jobs WHERE id = ?", 
                    (job_data['id'],)
                ).fetchone()
                
                if existing:
                    # Update existing job
                    conn.execute("""
                        UPDATE jobs 
                        SET status = ?, 
                            progress = ?,
                            error = ?,
                            updated_at = ?,
                            started_at = COALESCE(?, started_at),
                            completed_at = ?,
                            video_converted = ?,
                            audio_converted = ?,
                            streams_cleaned = ?
                        WHERE id = ?
                    """, (
                        job_data.get('status'),
                        job_data.get('progress', 0),
                        job_data.get('error'),
                        now,
                        job_data.get('started_at'),
                        job_data.get('completed_at'),
                        job_data.get('video_converted', 0),
                        job_data.get('audio_converted', 0),
                        job_data.get('streams_cleaned', 0),
                        job_data['id']
                    ))
                else:
                    # Insert new job
                    conn.execute("""
                        INSERT INTO jobs (
                            id, file_path, status, progress, error,
                            created_at, updated_at, started_at, completed_at,
                            video_converted, audio_converted, streams_cleaned
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        job_data['id'],
                        job_data['file_path'],
                        job_data.get('status', 'queued'),
                        job_data.get('progress', 0),
                        job_data.get('error'),
                        job_data.get('created_at', now),
                        now,
                        job_data.get('started_at'),
                        job_data.get('completed_at'),
                        job_data.get('video_converted', 0),
                        job_data.get('audio_converted', 0),
                        job_data.get('streams_cleaned', 0)
                    ))
    
    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a job by ID.
        
        Args:
            job_id: Job identifier
            
        Returns:
            Job dictionary or None if not found
        """
        with self._lock:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT * FROM jobs WHERE id = ?", 
                    (job_id,)
                ).fetchone()
                
                if row:
                    return dict(row)
                return None
    
    def get_all_jobs(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Get all jobs, newest first.
        
        Args:
            limit: Maximum number of jobs to return
            
        Returns:
            List of job dictionaries
        """
        with self._lock:
            with self._get_connection() as conn:
                query = "SELECT * FROM jobs ORDER BY created_at DESC"
                if limit:
                    query += f" LIMIT {limit}"
                
                rows = conn.execute(query).fetchall()
                return [dict(row) for row in rows]
    
    def get_pending_jobs(self) -> List[Dict[str, Any]]:
        """
        Get jobs that should be resumed (pending or running).
        
        Returns:
            List of job dictionaries
        """
        with self._lock:
            with self._get_connection() as conn:
                rows = conn.execute("""
                    SELECT * FROM jobs 
                    WHERE status IN ('pending', 'running')
                    ORDER BY created_at ASC
                """).fetchall()
                return [dict(row) for row in rows]
    
    def delete_job(self, job_id: str) -> bool:
        """
        Delete a job.
        
        Args:
            job_id: Job identifier
            
        Returns:
            True if deleted, False if not found
        """
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "DELETE FROM jobs WHERE id = ?", 
                    (job_id,)
                )
                return cursor.rowcount > 0
    
    def cleanup_old_jobs(self, days: int = 30, statuses: Optional[List[str]] = None) -> int:
        """
        Delete old completed/failed jobs.
        
        Args:
            days: Delete jobs older than this many days
            statuses: Only delete jobs with these statuses (default: completed, failed)
            
        Returns:
            Number of jobs deleted
        """
        if statuses is None:
            statuses = ['completed', 'failed']
        
        with self._lock:
            cutoff = datetime.now() - timedelta(days=days)
            cutoff_str = cutoff.isoformat()
            
            with self._get_connection() as conn:
                placeholders = ','.join('?' * len(statuses))
                cursor = conn.execute(f"""
                    DELETE FROM jobs 
                    WHERE status IN ({placeholders})
                    AND completed_at < ?
                """, (*statuses, cutoff_str))
                
                count = cursor.rowcount
                if count > 0:
                    logger.info(f"Cleaned up {count} old jobs (>{days} days)")
                return count
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get job statistics.
        
        Returns:
            Dictionary with job counts by status
        """
        with self._lock:
            with self._get_connection() as conn:
                total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
                
                status_counts = {}
                rows = conn.execute("""
                    SELECT status, COUNT(*) as count 
                    FROM jobs 
                    GROUP BY status
                """).fetchall()
                
                for row in rows:
                    status_counts[row['status']] = row['count']
                
                return {
                    'total': total,
                    'by_status': status_counts
                }
