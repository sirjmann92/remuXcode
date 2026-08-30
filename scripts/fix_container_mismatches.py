#!/usr/bin/env python3
"""Library-wide sweep for pre-existing container/extension mismatches.

Finds files whose extension no longer matches their real container (e.g.
an .mp4 that actually holds Matroska content — most commonly left over from
a remuXcode video re-encode done before the ``fix_container_mismatch``
setting existed) and corrects them across the whole Sonarr/Radarr library.

Dry run by default. Nothing is renamed and no Sonarr/Radarr refresh is
triggered unless --apply is passed.

Usage:
    python scripts/fix_container_mismatches.py                  # report only
    python scripts/fix_container_mismatches.py --apply           # rename + refresh
    python scripts/fix_container_mismatches.py --movies-only
    python scripts/fix_container_mismatches.py --shows-only
    python scripts/fix_container_mismatches.py --limit 20 --apply   # try a few first

Safety:
    - Dry run by default.
    - Skips any file with a pending/running remuXcode job right now (checked
      against --remuxcode-url's /api/jobs), so it never races a live encode.
      With --apply, refuses to run at all if that check can't be reached —
      better to abort than guess.
    - Only ever renames in place (same directory, extension only) — never
      touches a Sonarr/Radarr file record directly, never deletes anything.
    - One Refresh per affected movie/series after all its files are renamed
      (not one per file) — RefreshMovie/RefreshSeries only, the same fast,
      correctly-scoped mechanism the shipped fix_container_mismatch setting
      uses. Never RescanMovie/RescanSeries (those scan the whole library).
    - Uses backend.core._CONTAINER_EXTENSIONS directly so this script's
      notion of "mismatched" can never drift from what a live job would do.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests

from backend import core
from backend.utils.config import get_config

# Path prefix translation: container path (as Sonarr/Radarr report it) -> host
# path (as this script, running directly on the host, needs to open it).
# Order matters: more specific prefixes first.
PATH_PREFIX_MAP = [
    ("/share-exp/", "/mnt/NAStradamus/"),
    ("/share/", "/mnt/NASferatu/"),
    ("/stash/", "/mnt/stash/"),
]

# Already the family remuXcode's video worker produces — nothing to fix.
SKIP_EXTENSIONS = {".mkv", ".webm"}


def to_host_path(container_path: str) -> str:
    for prefix, host_prefix in PATH_PREFIX_MAP:
        if container_path.startswith(prefix):
            return container_path.replace(prefix, host_prefix, 1)
    return container_path


def probe_format(path: str) -> str | None:
    """Return ffprobe's format_name string for *path*, or None on failure."""
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if proc.returncode != 0:
            return None
        data = json.loads(proc.stdout)
        return data.get("format", {}).get("format_name", "")
    except Exception:
        return None


def detect_mismatch(host_path: str) -> str | None:
    """Return the corrected extension if *host_path*'s real container
    disagrees with its current extension, else None.
    """
    current_ext = Path(host_path).suffix.lower()
    if current_ext in SKIP_EXTENSIONS:
        return None
    format_name = probe_format(host_path)
    if not format_name:
        return None
    families = {f.strip().lower() for f in format_name.split(",")}
    actual_ext = next(
        (ext for family, ext in core._CONTAINER_EXTENSIONS.items() if family in families),
        None,
    )
    if actual_ext is None or actual_ext == current_ext:
        return None
    return actual_ext


def get_active_job_paths(remuxcode_url: str, remuxcode_key: str) -> set[str]:
    """Container paths with a pending/running remuXcode job right now.

    Raises on failure rather than swallowing it — the caller decides
    whether that's fatal (--apply) or just a warning (dry run).
    """
    resp = requests.get(
        f"{remuxcode_url}/api/jobs", headers={"X-API-Key": remuxcode_key}, timeout=15
    )
    resp.raise_for_status()
    jobs = resp.json()
    jobs = jobs if isinstance(jobs, list) else jobs.get("jobs", [])
    return {j["file_path"] for j in jobs if j.get("status") in ("pending", "running")}


def list_radarr_files(radarr_url: str, radarr_key: str) -> list[dict]:
    resp = requests.get(f"{radarr_url}/api/v3/movie", headers={"X-Api-Key": radarr_key}, timeout=60)
    resp.raise_for_status()
    out = []
    for m in resp.json():
        if not m.get("hasFile"):
            continue
        path = (m.get("movieFile") or {}).get("path")
        if not path:
            continue
        out.append({"media_type": "movie", "id": m["id"], "title": m.get("title"), "path": path})
    return out


def list_sonarr_files(sonarr_url: str, sonarr_key: str) -> list[dict]:
    resp = requests.get(
        f"{sonarr_url}/api/v3/series", headers={"X-Api-Key": sonarr_key}, timeout=60
    )
    resp.raise_for_status()
    out = []
    for s in resp.json():
        if not (s.get("statistics") or {}).get("episodeFileCount", 0):
            continue
        try:
            r = requests.get(
                f"{sonarr_url}/api/v3/episodefile",
                params={"seriesId": s["id"]},
                headers={"X-Api-Key": sonarr_key},
                timeout=30,
            )
            r.raise_for_status()
        except Exception as exc:
            print(
                f"WARNING: could not list episode files for {s.get('title')}: {exc}",
                file=sys.stderr,
            )
            continue
        for ef in r.json():
            path = ef.get("path")
            if not path:
                continue
            out.append({"media_type": "tv", "id": s["id"], "title": s.get("title"), "path": path})
    return out


def refresh_movie(radarr_url: str, radarr_key: str, movie_id: int) -> None:
    resp = requests.post(
        f"{radarr_url}/api/v3/command",
        headers={"X-Api-Key": radarr_key},
        json={"name": "RefreshMovie", "movieIds": [movie_id]},
        timeout=30,
    )
    resp.raise_for_status()
    cmd_id = resp.json().get("id")
    core._poll_command(radarr_url, radarr_key, cmd_id, f"Radarr refresh (movie {movie_id})")


def refresh_series(sonarr_url: str, sonarr_key: str, series_id: int) -> None:
    resp = requests.post(
        f"{sonarr_url}/api/v3/command",
        headers={"X-Api-Key": sonarr_key},
        json={"name": "RefreshSeries", "seriesId": series_id},
        timeout=30,
    )
    resp.raise_for_status()
    cmd_id = resp.json().get("id")
    core._poll_command(sonarr_url, sonarr_key, cmd_id, f"Sonarr refresh (series {series_id})")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--apply", action="store_true", help="Actually rename + refresh (default: dry run)"
    )
    parser.add_argument("--movies-only", action="store_true")
    parser.add_argument("--shows-only", action="store_true")
    parser.add_argument(
        "--limit", type=int, default=0, help="Stop after N mismatches found (0 = no limit)"
    )
    parser.add_argument("--remuxcode-url", default="http://192.168.0.134:7889")
    args = parser.parse_args()

    config_path = Path(__file__).parent.parent / "config" / "config.yaml"
    cfg = get_config(str(config_path) if config_path.exists() else None)
    core.config = cfg
    radarr_url, radarr_key = core._get_radarr_config()
    sonarr_url, sonarr_key = core._get_sonarr_config()

    key_path = Path(__file__).parent.parent / "config" / ".api_key"
    remuxcode_key = key_path.read_text().strip() if key_path.exists() else ""

    active_paths: set[str] = set()
    if remuxcode_key:
        try:
            active_paths = get_active_job_paths(args.remuxcode_url, remuxcode_key)
        except Exception as exc:
            if args.apply:
                print(
                    f"ERROR: could not check active remuXcode jobs ({exc}) — refusing to "
                    "--apply without this safety check. Run without --apply, or fix "
                    "--remuxcode-url, and try again.",
                    file=sys.stderr,
                )
                return 1
            print(
                f"WARNING: could not check active remuXcode jobs ({exc}); dry run "
                "continuing without this guard.",
                file=sys.stderr,
            )
    elif args.apply:
        print(
            "ERROR: no remuXcode API key found at config/.api_key — refusing to --apply "
            "without the active-job safety check.",
            file=sys.stderr,
        )
        return 1

    if active_paths:
        print(f"Skipping {len(active_paths)} file(s) with an active remuXcode job right now:")
        for p in sorted(active_paths):
            print(f"  - {p}")
        print()

    all_files: list[dict] = []
    if not args.shows_only and radarr_url and radarr_key:
        all_files += list_radarr_files(radarr_url, radarr_key)
    if not args.movies_only and sonarr_url and sonarr_key:
        all_files += list_sonarr_files(sonarr_url, sonarr_key)

    print(f"Scanning {len(all_files)} file(s)...")
    start = time.time()

    mismatches: list[dict] = []
    checked = 0
    for f in all_files:
        checked += 1
        if checked % 200 == 0:
            print(f"  ...{checked}/{len(all_files)} checked, {len(mismatches)} mismatch(es) so far")
        if f["path"] in active_paths:
            continue
        host_path = to_host_path(f["path"])
        if not Path(host_path).is_file():
            print(f"WARNING: file not found on disk, skipping: {host_path}", file=sys.stderr)
            continue
        new_ext = detect_mismatch(host_path)
        if new_ext is None:
            continue
        mismatches.append(
            {
                **f,
                "host_old": host_path,
                "host_new": str(Path(host_path).with_suffix(new_ext)),
            }
        )
        if args.limit and len(mismatches) >= args.limit:
            print(f"Reached --limit {args.limit}, stopping scan early.")
            break

    elapsed = time.time() - start
    print(f"\nScan finished in {elapsed:.0f}s.")
    print(f"=== {len(mismatches)} mismatch(es) found ===")

    by_media: dict[tuple, list] = {}
    for m in mismatches:
        by_media.setdefault((m["media_type"], m["id"], m["title"]), []).append(m)

    for (media_type, media_id, title), items in by_media.items():
        print(f"\n[{media_type}] {title} (id={media_id}) — {len(items)} file(s)")
        for m in items:
            print(f"    {Path(m['host_old']).name}  ->  {Path(m['host_new']).name}")

    if not mismatches:
        print("\nNothing to do.")
        return 0

    if not args.apply:
        print(
            f"\nDry run only — {len(mismatches)} file(s) across {len(by_media)} title(s) "
            "would be renamed. Re-run with --apply to actually rename + refresh."
        )
        return 0

    print(f"\nApplying: renaming {len(mismatches)} file(s) across {len(by_media)} title(s)...")
    for (media_type, media_id, title), items in by_media.items():
        renamed_any = False
        for m in items:
            src, dst = Path(m["host_old"]), Path(m["host_new"])
            if dst.exists():
                print(f"  SKIP (target already exists): {src} -> {dst}", file=sys.stderr)
                continue
            try:
                src.rename(dst)
                print(f"  renamed: {src.name} -> {dst.name}")
                renamed_any = True
            except OSError as exc:
                print(f"  FAILED to rename {src}: {exc}", file=sys.stderr)
        if not renamed_any:
            continue
        print(f"  refreshing {media_type} '{title}' (id={media_id})...")
        try:
            if media_type == "movie":
                refresh_movie(radarr_url, radarr_key, media_id)
            else:
                refresh_series(sonarr_url, sonarr_key, media_id)
        except Exception as exc:
            print(f"  WARNING: refresh failed for {title}: {exc}", file=sys.stderr)

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
