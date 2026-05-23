"""Retag worker — corrects MKV track metadata (language, title) without remuxing.

For MKV files, mkvpropedit rewrites only the track header bytes in-place: no
stream copy, no temp file, no size change.  For other containers (mp4, etc.)
we fall back to an ffmpeg -c copy remux to a temp file followed by safe_replace.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.workers._safe_move import safe_replace

logger = logging.getLogger("remuxcode")


@dataclass
class TrackChange:
    """Records what changed on a single track."""

    track_type: str  # "audio" | "subtitle"
    track_index: int  # 0-based index within that track type
    old_language: str | None
    new_language: str | None
    old_title: str | None
    new_title: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "track_type": self.track_type,
            "track_index": self.track_index,
            "old_language": self.old_language,
            "new_language": self.new_language,
            "old_title": self.old_title,
            "new_title": self.new_title,
        }


@dataclass
class RetagResult:
    """Result of a retag operation."""

    success: bool
    file: str
    changes: list[TrackChange] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "file": self.file,
            "changes": [c.to_dict() for c in self.changes],
            "error": self.error,
        }


@dataclass
class TrackOverride:
    """Override spec for a single track."""

    track_type: str  # "audio" | "subtitle"
    track_index: int  # 0-based index within that track type
    language: str | None = None  # ISO 639-2 code, e.g. "eng"
    title: str | None = None  # track name, e.g. "English"


class Retagger:
    """Corrects stream metadata without transcoding."""

    def retag(self, file_path: str, overrides: list[TrackOverride]) -> RetagResult:
        """Apply track metadata overrides to *file_path*.

        For MKV files this is done in-place with mkvpropedit.
        Other containers are remuxed via ``ffmpeg -c copy`` (safe_replace).
        """
        path = Path(file_path)
        if not path.exists():
            return RetagResult(
                success=False,
                file=file_path,
                error=f"File not found: {file_path}",
            )

        if not overrides:
            return RetagResult(success=True, file=file_path)

        if path.suffix.lower() == ".mkv":
            return self._retag_mkv(file_path, overrides)
        return self._retag_ffmpeg(file_path, overrides)

    # ------------------------------------------------------------------
    # MKV path — mkvpropedit (in-place, instant)
    # ------------------------------------------------------------------

    def _retag_mkv(self, file_path: str, overrides: list[TrackOverride]) -> RetagResult:
        """Use mkvpropedit to rewrite track headers in-place."""
        cmd = ["mkvpropedit", file_path]
        changes: list[TrackChange] = []

        for ov in overrides:
            # mkvpropedit uses 1-based track indices prefixed by type letter
            type_letter = "a" if ov.track_type == "audio" else "s"
            selector = f"track:{type_letter}{ov.track_index + 1}"
            edits: list[str] = []

            if ov.language is not None:
                edits += ["--set", f"language={ov.language}"]
            if ov.title is not None:
                edits += ["--set", f"name={ov.title}"]

            if edits:
                cmd += ["--edit", selector] + edits
                changes.append(
                    TrackChange(
                        track_type=ov.track_type,
                        track_index=ov.track_index,
                        old_language=None,  # populated by caller if needed
                        new_language=ov.language,
                        old_title=None,
                        new_title=ov.title,
                    )
                )

        if len(cmd) == 2:
            # No actual edits (all overrides were no-ops)
            return RetagResult(success=True, file=file_path)

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            return RetagResult(
                success=False, file=file_path, error="mkvpropedit timed out"
            )
        except FileNotFoundError:
            return RetagResult(
                success=False,
                file=file_path,
                error="mkvpropedit not found — is mkvtoolnix installed?",
            )

        if proc.returncode != 0:
            err = proc.stderr.strip() or proc.stdout.strip()
            logger.error("mkvpropedit failed for %s: %s", Path(file_path).name, err)
            return RetagResult(success=False, file=file_path, error=err)

        logger.info(
            "Retagged %d track(s) via mkvpropedit: %s",
            len(changes),
            Path(file_path).name,
        )
        return RetagResult(success=True, file=file_path, changes=changes)

    # ------------------------------------------------------------------
    # Non-MKV fallback — ffmpeg -c copy remux
    # ------------------------------------------------------------------

    def _retag_ffmpeg(self, file_path: str, overrides: list[TrackOverride]) -> RetagResult:
        """Remux with ffmpeg -c copy applying metadata overrides.

        Creates a temp MKV next to the original, then replaces the original.
        """
        path = Path(file_path)
        changes: list[TrackChange] = []

        # Build output path in the same directory as the input
        with tempfile.NamedTemporaryFile(
            suffix=".mkv",
            dir=path.parent,
            delete=False,
            prefix=f".remuxcode-retag-{path.stem}-",
        ) as tmp:
            tmp_path = Path(tmp.name)

        try:
            cmd = [
                "ffmpeg", "-y",
                "-i", file_path,
                "-c", "copy",
            ]

            for ov in overrides:
                type_letter = "a" if ov.track_type == "audio" else "s"
                if ov.language is not None:
                    cmd += [f"-metadata:s:{type_letter}:{ov.track_index}", f"language={ov.language}"]
                if ov.title is not None:
                    cmd += [f"-metadata:s:{type_letter}:{ov.track_index}", f"title={ov.title}"]
                changes.append(
                    TrackChange(
                        track_type=ov.track_type,
                        track_index=ov.track_index,
                        old_language=None,
                        new_language=ov.language,
                        old_title=None,
                        new_title=ov.title,
                    )
                )

            cmd.append(str(tmp_path))

            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,
            )

            if proc.returncode != 0:
                err = proc.stderr[-500:] if proc.stderr else "ffmpeg failed"
                logger.error("ffmpeg retag failed for %s: %s", path.name, err)
                tmp_path.unlink(missing_ok=True)
                return RetagResult(success=False, file=file_path, error=err)

            safe_replace(tmp_path, path)
            logger.info(
                "Retagged %d track(s) via ffmpeg: %s", len(changes), path.name
            )
            return RetagResult(success=True, file=file_path, changes=changes)

        except Exception as exc:
            tmp_path.unlink(missing_ok=True)
            return RetagResult(success=False, file=file_path, error=str(exc))
