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

from collections.abc import Callable
import contextlib
import errno
import logging
from pathlib import Path
import shutil
import time

logger = logging.getLogger(__name__)

_BACKUP_SUFFIX = ".remuxcode-backup"


def wait_for_output_file(
    path: Path,
    *,
    delays: tuple[float, ...] = (1, 3, 10, 30),
    log_cb: Callable[[str, str, str], None] | None = None,
) -> bool:
    """Wait for a freshly-written file to become visible on disk.

    On CIFS/NFS mounts a file that a child process just wrote and closed can
    transiently fail ``stat()`` from the parent process — attribute-cache lag
    or a brief server hiccup — even though it exists on the server.  Check
    immediately, then retry with backoff; before each retry, list the parent
    directory to force the network filesystem to revalidate its dentry cache
    instead of re-serving a stale negative lookup.

    Returns True as soon as the file appears, False when all retries expire.
    """
    if path.exists():
        return True

    waited = 0.0
    for delay in delays:
        logger.warning(
            "Output file not yet visible (%.0f s elapsed), retrying in %.0f s: %s",
            waited,
            delay,
            path.name,
        )
        time.sleep(delay)
        waited += delay
        # Directory listing forces dentry-cache revalidation on CIFS/NFS.
        with contextlib.suppress(OSError):
            for _ in path.parent.iterdir():
                pass
        if path.exists():
            msg = f"Output file appeared after {waited:.0f} s (network fs attribute-cache lag): {path.name}"
            logger.warning(msg)
            if log_cb:
                log_cb("app", "warning", msg)
            return True

    return False


class SafeMoveError(Exception):
    """Raised when a safe move/replace operation fails."""


def _move_with_retry(src: Path, dst: Path, max_attempts: int = 3) -> None:
    """Move *src* to *dst*, handling cross-device boundaries and transient ENOENT.

    On FUSE/mergerfs mounts ``os.rename`` can fail with ``EXDEV`` when the
    create policy places the destination on a different underlying branch than
    the source.  ``shutil.move`` silently falls back to ``copy2`` + ``unlink``
    in that case, but if the source disk is briefly unavailable at that moment
    (Unraid spindown, heavy I/O) ``open(src)`` inside ``copy2`` raises ``ENOENT``
    and the exception propagates with no recovery.

    This function replicates the same two-step strategy but retries the copy
    on transient ``FileNotFoundError`` so a brief disk hiccup doesn't kill the
    job.
    """
    # Fast path: atomic same-device rename
    try:
        src.rename(dst)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise

    # Cross-device fallback: copy then remove with retry
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            shutil.copy2(str(src), str(dst))
            src.unlink()
            return
        except FileNotFoundError as exc:
            last_exc = exc
            if attempt < max_attempts - 1:
                delay = 0.5 * (attempt + 1)
                logger.warning(
                    "copy2 attempt %d/%d failed (ENOENT) – retrying in %.1fs: %s",
                    attempt + 1,
                    max_attempts,
                    delay,
                    src.name,
                )
                time.sleep(delay)

    raise last_exc  # type: ignore[misc]


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
    # Use a single stat() call rather than exists() + stat() to avoid a
    # TOCTOU window on FUSE/mergerfs mounts where consecutive lookups can
    # return different results under heavy I/O or after a cross-device
    # rename fallback (shutil.move → copy2 path).
    try:
        new_size = new_file.stat().st_size
    except FileNotFoundError:
        raise FileNotFoundError(f"New file does not exist: {new_file}") from None

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

        destination.rename(backup_path)
        has_backup = True
        logger.debug("Backed up original: %s → %s", destination.name, backup_path.name)

    # ------------------------------------------------------------------
    # 3. Move the new file into place
    # ------------------------------------------------------------------
    try:
        _move_with_retry(new_file, destination)
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
    except OSError as exc:
        logger.error("Cannot stat destination after move: %s", destination)
        if has_backup:
            _restore_backup(backup_path, destination)
            raise SafeMoveError(
                f"Destination missing after move — original file preserved: {destination.name}"
            ) from exc
        raise SafeMoveError(f"Destination missing after move: {destination}") from exc

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
        backup_path.rename(destination)
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
