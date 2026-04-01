"""FFmpeg progress-reporting helper (imported by individual workers)."""

from collections.abc import Callable
import contextlib
import subprocess
import threading


class CancelledError(Exception):
    """Raised when an ffmpeg job is cancelled via a cancel event."""


def run_ffmpeg_with_progress(
    cmd: list[str],
    duration_secs: float | None,
    progress_cb: Callable[[float], None] | None = None,
    timeout: float | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[int, str]:
    """Run an ffmpeg command, reporting progress via callback.

    Injects ``-progress pipe:1`` before the output path so ffmpeg streams
    structured progress to stdout. Parses ``out_time_us`` to compute the
    percentage and calls *progress_cb* with a float 0-100 after each block.

    If *cancel_event* is set, the ffmpeg process is killed immediately and
    a ``CancelledError`` is raised.

    Returns ``(returncode, stderr_text)``.
    """
    # Inject -progress pipe:1 before the last argument (the output path)
    progress_cmd = [*cmd[:-1], "-progress", "pipe:1", cmd[-1]]

    stderr_lines: list[str] = []

    proc = subprocess.Popen(
        progress_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    def _read_stderr() -> None:
        if proc.stderr is not None:
            stderr_lines.extend(proc.stderr)

    stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
    stderr_thread.start()

    out_time_us = 0
    try:
        for line in proc.stdout:  # type: ignore[union-attr]
            # Check for cancellation on each progress line
            if cancel_event is not None and cancel_event.is_set():
                proc.kill()
                proc.wait()
                stderr_thread.join(timeout=5)
                raise CancelledError("Job cancelled by user")

            line = line.strip()
            if line.startswith("out_time_us="):
                with contextlib.suppress(ValueError):
                    out_time_us = int(line.split("=", 1)[1])
            elif line.startswith("progress="):
                if progress_cb and duration_secs and duration_secs > 0:
                    pct = min(100.0, (out_time_us / 1_000_000.0) / duration_secs * 100.0)
                    progress_cb(pct)
    except CancelledError:
        raise
    except Exception:
        pass

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
    return proc.returncode, "".join(stderr_lines)
