"""Safe file replacement that protects against data loss on network mounts.

When ``shutil.move`` crosses filesystem boundaries (common with CIFS / NFS /
mergerfs mounts) it falls back to ``shutil.copy2`` which **truncates** the
destination before copying.  If the copy fails mid-way the original file is
irreversibly destroyed.

``safe_replace`` avoids this by:

1. Verifying the *new* file (temp output) exists and has a non-zero size.
2. Renaming the original to a temporary backup path (same directory, so it's
   a same-device ``os.rename`` – atomic on POSIX).
3. Moving the new file into the original's location.
4. Verifying the file landed with the expected size.
5. Only *then* deleting the backup.
6. If anything goes wrong at steps 3-4 the backup is restored.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

_BACKUP_SUFFIX = ".remuxcode-backup"


class SafeMoveError(Exception):
    """Raised when a safe move/replace operation fails."""


def safe_replace(
    new_file: Path,
    destination: Path,
    *,
    expected_size: int | None = None,
) -> None:
    """Replace *destination* with *new_file* without risking data loss.

    Parameters
    ----------
    new_file:
        The freshly created temp output file (e.g. inside ``.remuxcode-temp-*/``).
    destination:
        The original file to be replaced.
    expected_size:
        If provided, the destination is verified to have this exact size after
        the move.  When *None* the size of *new_file* is used.

    Raises
    ------
    SafeMoveError
        If the new file is missing/empty, if the move fails and backup
        restoration succeeds (the original is preserved), or if the size
        verification fails.
    FileNotFoundError
        If the new file doesn't exist at all.
    """

    # ------------------------------------------------------------------
    # 1. Validate the new file
    # ------------------------------------------------------------------
    if not new_file.exists():
        raise FileNotFoundError(f"New file does not exist: {new_file}")

    new_size = new_file.stat().st_size
    if new_size == 0:
        raise SafeMoveError(f"New file is 0 bytes – refusing to replace original: {new_file}")

    if expected_size is None:
        expected_size = new_size

    backup_path = destination.with_name(destination.name + _BACKUP_SUFFIX)

    # ------------------------------------------------------------------
    # 2. Create a backup of the original (same-dir rename = atomic)
    # ------------------------------------------------------------------
    has_backup = False
    if destination.exists():
        # Remove stale backup from a previous interrupted attempt
        if backup_path.exists():
            backup_path.unlink()

        os.rename(str(destination), str(backup_path))
        has_backup = True
        logger.debug("Backed up original: %s → %s", destination.name, backup_path.name)

    # ------------------------------------------------------------------
    # 3. Move the new file into place
    # ------------------------------------------------------------------
    try:
        shutil.move(str(new_file), str(destination))
    except Exception as exc:
        logger.error("Move failed: %s → %s: %s", new_file, destination, exc)
        if has_backup:
            _restore_backup(backup_path, destination)
            raise SafeMoveError(
                f"File move failed — original file preserved: {destination.name}"
            ) from exc
        raise

    # ------------------------------------------------------------------
    # 4. Verify size of the moved file
    # ------------------------------------------------------------------
    try:
        landed_size = destination.stat().st_size
    except OSError:
        logger.error("Cannot stat destination after move: %s", destination)
        if has_backup:
            _restore_backup(backup_path, destination)
            raise SafeMoveError(
                f"Destination missing after move — original file preserved: {destination.name}"
            )
        raise SafeMoveError(f"Destination missing after move: {destination}")

    if landed_size != expected_size:
        logger.error(
            "Size mismatch after move: expected %d, got %d – restoring backup",
            expected_size,
            landed_size,
        )
        if has_backup:
            _restore_backup(backup_path, destination)
            raise SafeMoveError(
                f"Size mismatch after move ({landed_size} != {expected_size}) "
                f"— original file preserved: {destination.name}"
            )
        raise SafeMoveError(
            f"Size mismatch after move ({landed_size} != {expected_size}): {destination}"
        )

    # ------------------------------------------------------------------
    # 5. Success – remove backup
    # ------------------------------------------------------------------
    if has_backup:
        try:
            backup_path.unlink()
        except OSError as exc:
            # Non-fatal: the important thing is the new file is in place
            logger.warning("Could not remove backup %s: %s", backup_path, exc)

    logger.debug("Safe replace succeeded: %s (%d bytes)", destination.name, landed_size)


def _restore_backup(backup_path: Path, destination: Path) -> None:
    """Best-effort restoration of the backup file."""
    try:
        # Remove the mangled destination if it exists
        if destination.exists():
            destination.unlink()
        os.rename(str(backup_path), str(destination))
        logger.info("Restored original from backup: %s", destination)
    except OSError as exc:
        logger.critical(
            "FAILED to restore backup %s → %s: %s  "
            "Manual recovery required – the backup file is at: %s",
            backup_path,
            destination,
            exc,
            backup_path,
        )
