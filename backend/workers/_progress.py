"""FFmpeg progress-reporting helper (imported by individual workers)."""

from collections.abc import Callable
import contextlib
import logging
import os
from pathlib import Path
import select
import subprocess
import tempfile
import threading

logger = logging.getLogger(__name__)


class CancelledError(Exception):
    """Raised when an ffmpeg job is cancelled via a cancel event."""


def run_ffmpeg_with_progress(
    cmd: list[str],
    duration_secs: float | None,
    progress_cb: Callable[[float], None] | None = None,
    timeout: float | None = None,
    cancel_event: threading.Event | None = None,
    total_frames: float | None = None,
) -> tuple[int, str]:
    """Run an ffmpeg command, reporting progress via callback.

    Injects ``-progress <fifo>`` so ffmpeg streams structured progress to
    a Unix named pipe (FIFO).  The FIFO reader thread opens with
    ``O_RDONLY | O_NONBLOCK`` to avoid the classic FIFO open-deadlock,
    then switches to blocking ``select()`` for efficient reading.

    If *cancel_event* is set, the ffmpeg process is killed immediately and
    a ``CancelledError`` is raised.

    Returns ``(returncode, stderr_text)``.
    """
    has_duration = bool(duration_secs and duration_secs > 0)
    has_frames = bool(total_frames and total_frames > 0)
    can_report = bool(progress_cb and (has_duration or has_frames))
    if not can_report:
        logger.warning(
            "Progress reporting disabled: progress_cb=%s duration_secs=%s total_frames=%s",
            progress_cb is not None,
            duration_secs,
            total_frames,
        )

    # Create a temporary FIFO for progress output.
    fifo_dir = Path(tempfile.mkdtemp(prefix=".remuxcode-progress-"))
    fifo_path = fifo_dir / "progress.fifo"
    os.mkfifo(fifo_path)

    # Inject -stats_period and -progress before the last argument (the output
    # path).  -stats_period 0.5 forces ffmpeg to emit progress blocks every
    # 500ms even when video is just copied (otherwise audio-only conversions
    # produce almost no intermediate updates).
    progress_cmd = [
        *cmd[:-1],
        "-stats_period",
        "0.5",
        "-progress",
        str(fifo_path),
        cmd[-1],
    ]

    stderr_chunks: list[str] = []
    cancelled = threading.Event()

    # Open the FIFO for reading FIRST with O_NONBLOCK so it doesn't block
    # waiting for a writer.  This way when ffmpeg opens the FIFO for writing,
    # both sides connect immediately.
    fifo_fd = os.open(fifo_path, os.O_RDONLY | os.O_NONBLOCK)

    proc = subprocess.Popen(
        progress_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )

    def _read_stderr() -> None:
        """Drain stderr in a background thread to prevent pipe deadlock."""
        assert proc.stderr is not None
        while True:
            chunk = proc.stderr.read(8192)
            if not chunk:
                break
            stderr_chunks.append(chunk)

    stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
    stderr_thread.start()

    out_time_us = 0
    current_frame = 0
    progress_blocks = 0
    try:
        buf = ""
        while True:
            # Check for cancellation
            if cancel_event is not None and cancel_event.is_set():
                cancelled.set()
                break

            # Wait up to 1s for data on the FIFO (lets us check cancellation)
            ready, _, _ = select.select([fifo_fd], [], [], 1.0)
            if not ready:
                # Timeout — check if ffmpeg is still alive
                if proc.poll() is not None:
                    # ffmpeg exited; drain remaining data
                    try:
                        leftover = os.read(fifo_fd, 65536)
                        if leftover:
                            buf += leftover.decode("utf-8", errors="replace")
                    except OSError:
                        pass
                    break
                continue

            try:
                chunk = os.read(fifo_fd, 4096)
            except OSError:
                break
            if not chunk:
                break  # EOF — ffmpeg closed the FIFO

            buf += chunk.decode("utf-8", errors="replace")

            # Process complete lines
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.strip()
                if line.startswith("out_time_us="):
                    with contextlib.suppress(ValueError):
                        val = int(line.split("=", 1)[1])
                        if val > 0:
                            out_time_us = val
                elif line.startswith("frame="):
                    with contextlib.suppress(ValueError):
                        val = int(line.split("=", 1)[1])
                        if val > 0:
                            current_frame = val
                elif line.startswith("progress="):
                    progress_blocks += 1
                    if can_report:
                        pct = 0.0
                        if out_time_us > 0 and has_duration:
                            pct = min(100.0, (out_time_us / 1_000_000.0) / duration_secs * 100.0)  # type: ignore[operator]
                        elif current_frame > 0 and has_frames:
                            pct = min(100.0, current_frame / total_frames * 100.0)  # type: ignore[operator]
                        if pct > 0:
                            progress_cb(pct)  # type: ignore[misc]
    except CancelledError:
        cancelled.set()
    except Exception:
        logger.exception("Error reading ffmpeg progress")
    finally:
        os.close(fifo_fd)
        # Clean up the FIFO
        with contextlib.suppress(OSError):
            fifo_path.unlink()
        with contextlib.suppress(OSError):
            fifo_dir.rmdir()

    if cancelled.is_set():
        proc.kill()
        proc.wait()
        stderr_thread.join(timeout=5)
        raise CancelledError("Job cancelled by user")

    if progress_blocks == 0:
        logger.warning("No progress blocks received from ffmpeg")

    # Check cancellation one more time before waiting
    if cancel_event is not None and cancel_event.is_set():
        proc.kill()
        proc.wait()
        stderr_thread.join(timeout=5)
        raise CancelledError("Job cancelled by user")

    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()

    stderr_thread.join(timeout=5)
    return proc.returncode, "".join(stderr_chunks)
