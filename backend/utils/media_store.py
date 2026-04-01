"""SQLite-based persistent media analysis store.

Caches ffprobe results per media file keyed on Sonarr/Radarr media file IDs.
Survives restarts, handles renames via stable IDs, and self-maintains via
lazy staleness checks and startup orphan purge.
"""

from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
import json
import logging
from pathlib import Path
import sqlite3
import threading
from typing import Any

logger = logging.getLogger(__name__)


class MediaStore:
    """Thread-safe SQLite media analysis cache."""

    def __init__(self, db_path: str = "media.db"):
        """Initialize the media store with the given database path."""
        self.db_path = Path(db_path)
        self._lock = threading.Lock()
        self._init_db()
        logger.info("Media store initialized: %s", self.db_path)

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS media_analysis (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path TEXT NOT NULL,
                    sonarr_episode_file_id INTEGER,
                    radarr_movie_file_id INTEGER,
                    file_mtime REAL,
                    file_size INTEGER,
                    analysis_json TEXT NOT NULL,
                    analyzed_at TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_media_path ON media_analysis(file_path)")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_media_radarr"
                " ON media_analysis(radarr_movie_file_id)"
                " WHERE radarr_movie_file_id IS NOT NULL"
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_media_sonarr"
                " ON media_analysis(sonarr_episode_file_id)"
                " WHERE sonarr_episode_file_id IS NOT NULL"
            )

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def upsert(
        self,
        file_path: str,
        analysis: dict[str, Any],
        file_mtime: float,
        file_size: int,
        *,
        radarr_movie_file_id: int | None = None,
        sonarr_episode_file_id: int | None = None,
    ) -> None:
        """Insert or update a media analysis row.

        If a row with the same radarr/sonarr ID already exists it is updated;
        otherwise a path-based match is attempted before inserting a new row.
        """
        now = datetime.now(UTC).isoformat()
        analysis_json = json.dumps(analysis)

        with self._lock, self._get_connection() as conn:
            # Try matching by stable media file ID first
            existing_id = None
            if radarr_movie_file_id is not None:
                row = conn.execute(
                    "SELECT id FROM media_analysis WHERE radarr_movie_file_id = ?",
                    (radarr_movie_file_id,),
                ).fetchone()
                if row:
                    existing_id = row["id"]
            if existing_id is None and sonarr_episode_file_id is not None:
                row = conn.execute(
                    "SELECT id FROM media_analysis WHERE sonarr_episode_file_id = ?",
                    (sonarr_episode_file_id,),
                ).fetchone()
                if row:
                    existing_id = row["id"]
            # Fall back to path match
            if existing_id is None:
                row = conn.execute(
                    "SELECT id FROM media_analysis WHERE file_path = ?",
                    (file_path,),
                ).fetchone()
                if row:
                    existing_id = row["id"]

            if existing_id is not None:
                conn.execute(
                    """UPDATE media_analysis
                       SET file_path = ?, file_mtime = ?, file_size = ?,
                           analysis_json = ?, analyzed_at = ?,
                           radarr_movie_file_id = COALESCE(?, radarr_movie_file_id),
                           sonarr_episode_file_id = COALESCE(?, sonarr_episode_file_id)
                     WHERE id = ?""",
                    (
                        file_path,
                        file_mtime,
                        file_size,
                        analysis_json,
                        now,
                        radarr_movie_file_id,
                        sonarr_episode_file_id,
                        existing_id,
                    ),
                )
            else:
                conn.execute(
                    """INSERT INTO media_analysis
                       (file_path, sonarr_episode_file_id, radarr_movie_file_id,
                        file_mtime, file_size, analysis_json, analyzed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        file_path,
                        sonarr_episode_file_id,
                        radarr_movie_file_id,
                        file_mtime,
                        file_size,
                        analysis_json,
                        now,
                    ),
                )

    def delete(self, row_id: int) -> None:
        """Delete a single analysis row by ID."""
        with self._lock, self._get_connection() as conn:
            conn.execute("DELETE FROM media_analysis WHERE id = ?", (row_id,))

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        d = dict(row)
        d["analysis"] = json.loads(d.pop("analysis_json", "{}"))
        return d

    def get_by_path(self, file_path: str) -> dict[str, Any] | None:
        """Look up analysis by file path."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM media_analysis WHERE file_path = ?", (file_path,)
            ).fetchone()
        return self._parse_row(row)

    def get_by_radarr_id(self, movie_file_id: int) -> dict[str, Any] | None:
        """Look up analysis by Radarr movie file ID."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM media_analysis WHERE radarr_movie_file_id = ?",
                (movie_file_id,),
            ).fetchone()
        return self._parse_row(row)

    def get_by_sonarr_id(self, episode_file_id: int) -> dict[str, Any] | None:
        """Look up analysis by Sonarr episode file ID."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM media_analysis WHERE sonarr_episode_file_id = ?",
                (episode_file_id,),
            ).fetchone()
        return self._parse_row(row)

    def bulk_lookup_radarr(self, movie_file_ids: list[int]) -> dict[int, dict[str, Any]]:
        """Look up multiple rows by Radarr movie file IDs."""
        if not movie_file_ids:
            return {}
        result: dict[int, dict[str, Any]] = {}
        with self._get_connection() as conn:
            placeholders = ",".join("?" * len(movie_file_ids))
            rows = conn.execute(
                f"SELECT * FROM media_analysis WHERE radarr_movie_file_id IN ({placeholders})",  # noqa: S608
                movie_file_ids,
            ).fetchall()
        for row in rows:
            parsed = self._parse_row(row)
            if parsed and parsed.get("radarr_movie_file_id"):
                result[parsed["radarr_movie_file_id"]] = parsed
        return result

    def bulk_lookup_sonarr(self, episode_file_ids: list[int]) -> dict[int, dict[str, Any]]:
        """Look up multiple rows by Sonarr episode file IDs."""
        if not episode_file_ids:
            return {}
        result: dict[int, dict[str, Any]] = {}
        with self._get_connection() as conn:
            placeholders = ",".join("?" * len(episode_file_ids))
            rows = conn.execute(
                f"SELECT * FROM media_analysis WHERE sonarr_episode_file_id IN ({placeholders})",  # noqa: S608
                episode_file_ids,
            ).fetchall()
        for row in rows:
            parsed = self._parse_row(row)
            if parsed and parsed.get("sonarr_episode_file_id"):
                result[parsed["sonarr_episode_file_id"]] = parsed
        return result

    # ------------------------------------------------------------------
    # Staleness
    # ------------------------------------------------------------------

    @staticmethod
    def is_fresh(entry: dict[str, Any]) -> bool:
        """Check if a cached entry is still valid by comparing mtime+size."""
        path = Path(entry.get("file_path", ""))
        try:
            stat = path.stat()
        except OSError:
            return False
        return abs(stat.st_mtime - (entry.get("file_mtime") or 0)) < 1.0 and stat.st_size == (
            entry.get("file_size") or -1
        )

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def purge_missing(self) -> int:
        """Delete rows whose files no longer exist on disk. Run on startup."""
        with self._get_connection() as conn:
            rows = conn.execute("SELECT id, file_path FROM media_analysis").fetchall()

        to_delete = [r["id"] for r in rows if not Path(r["file_path"]).exists()]
        if to_delete:
            with self._lock, self._get_connection() as conn:
                placeholders = ",".join("?" * len(to_delete))
                conn.execute(
                    f"DELETE FROM media_analysis WHERE id IN ({placeholders})",  # noqa: S608
                    to_delete,
                )
            logger.info("Purged %d orphaned media analysis rows", len(to_delete))
        return len(to_delete)

    def get_stats(self) -> dict[str, Any]:
        """Return analysis coverage stats."""
        with self._get_connection() as conn:
            total = conn.execute("SELECT COUNT(*) AS n FROM media_analysis").fetchone()["n"]
            radarr = conn.execute(
                "SELECT COUNT(*) AS n FROM media_analysis WHERE radarr_movie_file_id IS NOT NULL"
            ).fetchone()["n"]
            sonarr = conn.execute(
                "SELECT COUNT(*) AS n FROM media_analysis WHERE sonarr_episode_file_id IS NOT NULL"
            ).fetchone()["n"]
        return {
            "total_analyzed": total,
            "radarr_files": radarr,
            "sonarr_files": sonarr,
        }

    def count(self) -> int:
        """Return total number of analyzed files."""
        with self._get_connection() as conn:
            return conn.execute("SELECT COUNT(*) AS n FROM media_analysis").fetchone()["n"]
