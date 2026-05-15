#!/usr/bin/env python3
"""Fix null subtitle language tags in remuxcode-processed MKV files.

Remuxes affected files in-place using ``ffmpeg -c copy`` with explicit
``-metadata:s:s:N language=<lang>`` flags.  No re-encoding; just rewrites
the MKV track headers.

Language tags are inferred from an unprocessed sibling episode in the same
show directory.  A ``--languages`` fallback lets you specify them manually
when no reference file is available.

Usage examples
--------------
# Dry-run — show what would happen without touching files
python fix_subtitle_langs.py --dry-run "/share/Shows/Breaking Bad (2008)"

# Fix in-place, using sibling episodes as reference
python fix_subtitle_langs.py "/share/Shows/Breaking Bad (2008)"

# Force specific language tags (comma-separated, in stream order)
python fix_subtitle_langs.py --languages eng,eng "/share/Shows/Breaking Bad (2008)"

# Multiple shows
python fix_subtitle_langs.py "/share/Shows/Breaking Bad (2008)" "/share/Shows/The Wire (2002)"

# Run inside the remuxcode Docker container
docker exec remuxcode python /app/fix_subtitle_langs.py "/share/Shows/Breaking Bad (2008)"
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import shutil
import subprocess
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

_NULL_LANGS = {"", "und", "N/A", None}


# ---------------------------------------------------------------------------
# ffprobe helpers
# ---------------------------------------------------------------------------


def _ffprobe(path: Path) -> dict:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return {}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}


def _subtitle_streams(probe: dict) -> list[dict]:
    """Return list of subtitle stream dicts with index/language/title."""
    return [
        {
            "index": s["index"],
            "language": (s.get("tags") or {}).get("language") or None,
            "title": (s.get("tags") or {}).get("title") or None,
        }
        for s in probe.get("streams", [])
        if s.get("codec_type") == "subtitle"
    ]


def _is_remuxcode(probe: dict) -> bool:
    tags = probe.get("format", {}).get("tags", {})
    return bool(tags.get("ENCODED_BY") or tags.get("encoded_by"))


def _has_null_lang(subs: list[dict]) -> bool:
    return any(s["language"] in _NULL_LANGS for s in subs)


# ---------------------------------------------------------------------------
# Reference file search
# ---------------------------------------------------------------------------


def _find_reference(
    show_dir: Path, exclude: Path, need_count: int
) -> tuple[Path, list[dict]] | None:
    """Find an unprocessed MKV in *show_dir* that has ≥ *need_count* tagged subtitle streams."""
    for candidate in sorted(show_dir.rglob("*.mkv")):
        if candidate == exclude:
            continue
        probe = _ffprobe(candidate)
        if not probe or _is_remuxcode(probe):
            continue
        subs = _subtitle_streams(probe)
        tagged = [s for s in subs if s["language"] not in _NULL_LANGS]
        if len(tagged) >= need_count:
            return candidate, tagged
    return None


# ---------------------------------------------------------------------------
# Remux
# ---------------------------------------------------------------------------


def _remux(input_file: Path, lang_map: list[tuple[str, str | None]], dry_run: bool) -> bool:
    """Remux *input_file* in-place, applying per-subtitle language/title metadata.

    *lang_map* is a list of ``(language, title|None)`` tuples, one per subtitle
    stream in output order (index 0, 1, 2 …).
    """
    tmp = input_file.with_suffix(".sublangfix.mkv")

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-stats",
        "-i",
        str(input_file),
        "-map",
        "0",
        "-c",
        "copy",
    ]

    for i, (lang, title) in enumerate(lang_map):
        cmd += [f"-metadata:s:s:{i}", f"language={lang}"]
        if title:
            cmd += [f"-metadata:s:s:{i}", f"title={title}"]

    cmd.append(str(tmp))

    log.info("    cmd: %s", " ".join(cmd))

    if dry_run:
        log.info("    [dry-run] skipping execution")
        return True

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            log.error("    ffmpeg error:\n%s", result.stderr.strip())
            if tmp.exists():
                tmp.unlink()
            return False
        # Verify the output is non-empty before replacing
        if not tmp.exists() or tmp.stat().st_size < 1024:
            log.error("    output file missing or suspiciously small")
            if tmp.exists():
                tmp.unlink()
            return False
        shutil.move(str(tmp), str(input_file))
        return True
    except Exception as exc:
        log.error("    exception: %s", exc)
        if tmp.exists():
            tmp.unlink()
        return False


# ---------------------------------------------------------------------------
# Main scan
# ---------------------------------------------------------------------------


def process_show(
    show_dir: Path,
    fallback_langs: list[str] | None,
    dry_run: bool,
) -> tuple[int, int]:
    """Scan *show_dir* and fix affected files.  Returns (fixed, skipped)."""
    log.info("Scanning  %s", show_dir)

    affected: list[tuple[Path, list[dict]]] = []
    for mkv in sorted(show_dir.rglob("*.mkv")):
        probe = _ffprobe(mkv)
        if not probe or not _is_remuxcode(probe):
            continue
        subs = _subtitle_streams(probe)
        if not subs or not _has_null_lang(subs):
            continue
        affected.append((mkv, subs))

    if not affected:
        log.info("  No affected files found.")
        return 0, 0

    log.info("  %d affected file(s):", len(affected))
    for f, subs in affected:
        null_count = sum(1 for s in subs if s["language"] in _NULL_LANGS)
        log.info("    %s  (%d null-language sub stream(s))", f.name, null_count)

    fixed = skipped = 0

    for mkv, subs in affected:
        log.info("")
        log.info("  Processing: %s", mkv.name)

        # Build the language map for ALL subtitle streams (not just null ones)
        # because ffmpeg output indices are positional.
        null_count = sum(1 for s in subs if s["language"] in _NULL_LANGS)
        lang_map: list[tuple[str, str | None]] = []

        if fallback_langs:
            # Manual mapping: apply provided languages in order, keep already-tagged ones
            fallback_iter = iter(fallback_langs)
            for sub in subs:
                if sub["language"] in _NULL_LANGS:
                    try:
                        lang_map.append((next(fallback_iter), sub["title"]))
                    except StopIteration:
                        log.warning(
                            "    Ran out of --languages values — skipping remaining null-lang subs"
                        )
                        break
                else:
                    lang_map.append((sub["language"], sub["title"]))
        else:
            ref = _find_reference(show_dir, mkv, null_count)
            if ref is None:
                log.warning(
                    "    No unprocessed reference file found — use --languages to specify manually"
                )
                skipped += 1
                continue
            ref_path, ref_subs = ref
            log.info("    Reference: %s", ref_path.name)
            ref_iter = iter(ref_subs)
            for sub in subs:
                if sub["language"] in _NULL_LANGS:
                    ref_sub = next(ref_iter, None)
                    if ref_sub is None:
                        log.warning(
                            "    Reference has fewer subtitle streams than processed file — stopping here"
                        )
                        break
                    lang_map.append((ref_sub["language"], ref_sub.get("title")))
                else:
                    lang_map.append((sub["language"], sub["title"]))

        if not lang_map:
            log.warning("    Nothing to apply — skipping")
            skipped += 1
            continue

        for i, (lang, title) in enumerate(lang_map):
            log.info("    stream s:%d  language=%s  title=%s", i, lang, title or "(unchanged)")

        ok = _remux(mkv, lang_map, dry_run=dry_run)
        if ok:
            log.info("    %s", "✓ Would fix" if dry_run else "✓ Fixed")
            fixed += 1
        else:
            log.error("    ✗ Failed")
            skipped += 1

    return fixed, skipped


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point."""
    parser = argparse.ArgumentParser(
        description="Fix null subtitle language tags in remuxcode-processed MKV files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "paths", nargs="+", metavar="SHOW_DIR", help="Show directory/directories to scan"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without modifying any files",
    )
    parser.add_argument(
        "--languages",
        metavar="LANG[,LANG...]",
        help="Comma-separated language codes to apply to null-language subtitle streams in order "
        "(e.g. eng,eng).  Used when no unprocessed reference file is available.",
    )
    args = parser.parse_args()

    fallback_langs = (
        [lang.strip() for lang in args.languages.split(",")] if args.languages else None
    )

    total_fixed = total_skipped = 0
    for path_str in args.paths:
        path = Path(path_str)
        if not path.exists():
            log.error("Path not found: %s", path)
            sys.exit(1)
        f, s = process_show(path, fallback_langs, dry_run=args.dry_run)
        total_fixed += f
        total_skipped += s

    log.info("")
    log.info(
        "Done.  Fixed: %d  Skipped: %d%s",
        total_fixed,
        total_skipped,
        "  (dry-run)" if args.dry_run else "",
    )


if __name__ == "__main__":
    main()
