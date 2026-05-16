"""FFmpeg progress-reporting helper (imported by individual workers)."""

from collections.abc import Callable
import contextlib
import logging
import os
from pathlib import Path
import select
import signal
import subprocess
import tempfile
import threading
import time

logger = logging.getLogger(__name__)

# Progress-stat keywords that appear in FFmpeg's legacy stderr line:
#   frame=N fps=N q=N.N size=NKiB time=H:M:S.ms bitrate=N speed=Nx
# We use these to distinguish real error lines from progress noise.
_PROGRESS_STAT_KEYWORDS = frozenset(["fps=", "speed=", "bitrate=", "out_time", "Svt["])


def ffmpeg_error_summary(returncode: int, stderr_text: str) -> str:
    """Return a concise error description suitable for a job failure message.

    Prefers actual error/warning lines from stderr over raw progress stats.
    When *returncode* is negative the process was killed by a Unix signal
    (e.g. SIGABRT from an encoder assertion, SIGSEGV from a crash) — that
    signal name is prepended so it's immediately visible in the UI.
    """
    # Decode signal number when the process was killed by a signal.
    signal_prefix = ""
    if returncode < 0:
        try:
            sig_name = signal.Signals(-returncode).name
        except ValueError:
            sig_name = f"signal {-returncode}"
        signal_prefix = f"FFmpeg killed by {sig_name}\n"

    # Scan for lines that look like real errors rather than progress stats.
    meaningful: list[str] = []
    for ln in stderr_text.splitlines():
        ln_strip = ln.strip()
        if not ln_strip:
            continue
        # Skip pure progress-stat lines.
        if any(kw in ln_strip for kw in _PROGRESS_STAT_KEYWORDS):
            continue
        # Skip the standard "frame=N fps=N ..." stderr format line.
        if ln_strip.startswith("frame=") and "fps=" in ln_strip:
            continue
        meaningful.append(ln_strip)

    if meaningful:
        # Last 30 meaningful lines give a good picture without being overwhelming.
        summary = "\n".join(meaningful[-30:])
    else:
        # Nothing meaningful found — fall back to the raw tail.
        summary = stderr_text[-2000:]

    return f"{signal_prefix}{summary}"


class CancelledError(Exception):
    """Raised when an ffmpeg job is cancelled via a cancel event."""


def run_ffmpeg_with_progress(
    cmd: list[str],
    duration_secs: float | None,
    progress_cb: Callable[[float], None] | None = None,
    timeout: float | None = None,
    cancel_event: threading.Event | None = None,
    total_frames: float | None = None,
    log_cb: Callable[[str, str, str], None] | None = None,
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

    # Hold a dummy write-end open for the lifetime of the read loop.
    # ffmpeg's -progress implementation opens/writes/closes the FIFO once per
    # stats period.  Without this, when ffmpeg closes its write-end the kernel
    # sets the EOF flag on the FIFO, causing os.read() to return b"" and the
    # progress loop to exit prematurely while ffmpeg is still encoding.
    fifo_wr_dummy = os.open(fifo_path, os.O_WRONLY | os.O_NONBLOCK)

    proc = subprocess.Popen(
        progress_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )

    def _read_stderr() -> None:
        """Drain stderr in a background thread to prevent pipe deadlock."""
        assert proc.stderr is not None
        line_buf = ""
        while True:
            chunk = proc.stderr.read(8192)
            if not chunk:
                break
            stderr_chunks.append(chunk)
            if log_cb:
                line_buf += chunk
                while "\n" in line_buf:
                    line, line_buf = line_buf.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    if "fps=" in line and "speed=" in line:
                        log_cb("ffmpeg", "stats", line)
                    elif "error" in line.lower():
                        log_cb("ffmpeg", "error", line)
                    elif any(
                        kw in line.lower()
                        for kw in ("warning", "deprecated", "unsupported", "invalid", "corrupt")
                    ):
                        log_cb("ffmpeg", "warning", line)
                    else:
                        log_cb("ffmpeg", "info", line)
        if log_cb and line_buf.strip():
            line = line_buf.strip()
            if "fps=" in line and "speed=" in line:
                log_cb("ffmpeg", "stats", line)
            elif "error" in line.lower():
                log_cb("ffmpeg", "error", line)
            elif any(
                kw in line.lower()
                for kw in ("warning", "deprecated", "unsupported", "invalid", "corrupt")
            ):
                log_cb("ffmpeg", "warning", line)
            else:
                log_cb("ffmpeg", "info", line)

    stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
    stderr_thread.start()

    out_time_us = 0
    current_frame = 0
    progress_blocks = 0
    # Stall watchdog: kill ffmpeg if encoding progress stops advancing.
    # Two complementary checks:
    #   1. No FIFO data at all for _STALL_TIMEOUT seconds (complete silence).
    #   2. FIFO data arriving but both out_time_us AND frame haven't advanced
    #      for _PROGRESS_STALL_TIMEOUT seconds.
    #
    # IMPORTANT: out_time_us (output mux time) and current_frame (input/filter
    # side) are tracked independently.  Encoders with large internal pipelines
    # (e.g. SVT-AV1 with look-ahead depth 40 + GOP 120) can have out_time_us
    # lag current_frame by many minutes while the encode is perfectly healthy.
    # Advancing EITHER metric resets the stall timer, so the watchdog only
    # fires when the entire pipeline — both input and output — has truly frozen.
    #
    # _PROGRESS_STALL_TIMEOUT is intentionally long (10 min) because large MKV
    # files with many subtitle tracks (e.g. full multi-language remuxes) require
    # significant time for the MKV muxer to finalize the seek/cue tables after
    # all video frames are encoded.  During that finalization phase out_time_us
    # and the frame counter freeze at their final values while FFmpeg is still
    # blocking on the output file — especially over NFS/CIFS mounts.  120 s was
    # too aggressive and caused false kills on valid encodes.
    _STALL_TIMEOUT = 300.0
    _PROGRESS_STALL_TIMEOUT = 600.0
    last_fifo_activity = time.monotonic()
    last_out_time_us = -1  # highest out_time_us seen
    last_frame = -1  # highest current_frame seen
    last_progress_advance = time.monotonic()
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
                # Watchdog 1: no FIFO data at all
                stall_secs = time.monotonic() - last_fifo_activity
                if stall_secs > _STALL_TIMEOUT:
                    logger.error(
                        "FFmpeg stall detected: no FIFO data for %.0f s — killing process",
                        stall_secs,
                    )
                    proc.kill()
                    break
                continue

            last_fifo_activity = time.monotonic()

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
                    # Watchdog 2: FIFO data arriving but progress not advancing.
                    # Advance the timer when EITHER out_time_us OR current_frame
                    # increases.  This prevents false kills when SVT-AV1's large
                    # pipeline (look-ahead + GOP buffering) causes out_time_us to
                    # lag current_frame by many minutes while the encode is still
                    # actively processing input frames.
                    _advanced = False
                    if out_time_us > 0 and out_time_us > last_out_time_us:
                        last_out_time_us = out_time_us
                        _advanced = True
                    if current_frame > 0 and current_frame > last_frame:
                        last_frame = current_frame
                        _advanced = True
                    if _advanced:
                        last_progress_advance = time.monotonic()
                    elif progress_blocks > 10:
                        # Only start checking after initial startup (10 blocks)
                        frozen_secs = time.monotonic() - last_progress_advance
                        if frozen_secs > _PROGRESS_STALL_TIMEOUT:
                            logger.error(
                                "FFmpeg encoder stall detected: progress frozen for %.0f s "
                                "(out_time_us=%d, frame=%d) — killing process",
                                frozen_secs,
                                out_time_us,
                                current_frame,
                            )
                            proc.kill()
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
        with contextlib.suppress(OSError):
            os.close(fifo_wr_dummy)
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
