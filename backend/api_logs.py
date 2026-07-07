"""Application log viewer routes — tails the rotating app log file."""

from datetime import datetime
import os
from pathlib import Path
import re
from typing import Any

from fastapi import APIRouter, Query

router = APIRouter(tags=["logs"])

_LOG_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d{3})?) (?P<level>[A-Z]+) \[(?P<name>[^\]]+)\] (?P<message>.*)$"
)


def _parse_ts(ts: str) -> float:
    """Parse a log timestamp, tolerating log lines written before millisecond precision was added."""
    fmt = "%Y-%m-%d %H:%M:%S.%f" if "." in ts else "%Y-%m-%d %H:%M:%S"
    return datetime.strptime(ts, fmt).timestamp()


def _log_file_path() -> Path:
    return Path(os.getenv("LOG_FILE", "/app/logs/remuxcode.log"))


def _parse_log_file(path: Path, max_entries: int) -> list[dict[str, Any]]:
    """Parse the log file into structured entries, grouping traceback continuation lines."""
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    with path.open(errors="replace") as f:
        for raw_line in f:
            line = raw_line.rstrip("\n")
            match = _LOG_LINE_RE.match(line)
            if match:
                ts = _parse_ts(match["ts"])
                entries.append(
                    {
                        "ts": ts,
                        "level": match["level"],
                        "logger": match["name"],
                        "message": match["message"],
                    }
                )
            elif entries:
                # Continuation line (e.g. traceback) — fold into the previous entry
                entries[-1]["message"] += "\n" + line
    return entries[-max_entries:]


@router.get("/logs")
async def get_logs(lines: int = Query(1000, ge=1, le=5000)) -> dict[str, Any]:
    """Return the most recent application log entries."""
    entries = _parse_log_file(_log_file_path(), lines)
    return {"entries": entries}
