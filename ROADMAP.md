# remuXcode — Roadmap

## What's Built

### Core Conversion Workers

- **Audio** — DTS/DTS-HD → AC3 (5.1), EAC3 (7.1+), AAC (stereo); separate DTS:X toggle; TrueHD passthrough; configurable bitrate caps; keep-original option with track ordering control; anime-only and live-action-only modes
- **Video** — 10-bit H.264 → HEVC or AV1; 8-bit H.264 (optional); legacy codecs (VC-1, MPEG-2, MPEG-4/XviD/DivX); anime-optimized presets (CRF, tune, framerate); anime-only and live-action-only modes; hardware acceleration (Intel QSV/VAAPI, NVIDIA NVENC) with automatic software fallback
- **Cleanup** — mux-only pass that removes audio/subtitle tracks outside keep-languages list; preserves forced subtitles, SDH, commentary, audio description; rewrites foreign-language track titles to canonical English names (e.g. 「英語」 → "English") in-place via `mkvpropedit` for MKV files (ffmpeg fallback for other containers); anime dual-audio mode; live-action-only mode

### Automation & Integration

- Sonarr/Radarr webhook receiver (On Import Complete, On File Import, On File Upgrade)
- Post-conversion rename trigger via Sonarr/Radarr API — Sonarr RescanSeries + polled rename; Radarr polled rename
- Job persistence (SQLite) — survives container restarts, resumes pending jobs in queue order
- Anime detection — path patterns, NFO genre parsing, Sonarr/Radarr API genres and studio names
- Original language detection — NFO, API, path pattern fallback chain

### Web UI

- **Dashboard** — live job stats (processed, active, queued, storage saved), in-progress jobs with per-phase progress and size delta, pending queue with drag-and-drop reordering, recent activity
- **Movies** — Radarr-backed poster grid, work-needed badges (Audio/Video/Cleanup/Legacy), filter/sort/search, per-file analyze modal with **Fix Metadata** for in-place track language/title correction and **Cover Art** display (thumbnail preview + in-place removal), individual and multi-select batch queue, job status indicators on posters, library refresh
- **Shows** — Sonarr-backed series list, season/episode drill-down, job indicators at series/season/episode level, per-episode analyze modal with **Fix Metadata** for in-place track language/title correction and **Cover Art** display (thumbnail preview + in-place removal), per-episode and per-season Custom Encode (downscale / HDR), individual and multi-select batch queue, **Queue Season** button that context-switches to **Queue N Selected** when season episodes are checked, **Queue All Episodes** and **Queue N Selected** at series level, per-show **Rescan** button, library refresh
- **Jobs** — full job history with status/worker/media/source/date-range filters and search, per-phase results (size delta per phase), full-text error display with clipboard copy button, drag-and-drop pending queue reordering, Stop Current / Clear Pending / Delete Completed controls, individual job cancel and delete, load-more pagination
- **Settings** — all settings configurable from the UI: audio, video, cleanup, language detection, Sonarr/Radarr connections, workers, job retention, hardware acceleration, cover art stripping

### Infrastructure

- Single Docker container — FastAPI backend + SvelteKit frontend (port 7889)
- Multi-stage Dockerfile: Node.js builds frontend → Python 3 slim runtime
- Hardware acceleration auto-detection at startup (QSV / VAAPI / NVENC / software)
- Real-time FFmpeg progress via Unix FIFO; frame-count fallback for hardware encoders that report `out_time_us=N/A`
- File size tracking: before/after displayed per conversion phase in the Jobs UI
- Automatic job retry on startup for jobs interrupted mid-encode
- **mkvtoolnix** (`mkvpropedit`) — in-place MKV metadata editing without remuxing: track statistics tags (`BPS`/`DURATION`/`NUMBER_OF_FRAMES`) written after every video encode so MediaInfo and media servers report correct bitrate; in-place track language/title correction via the Retag worker; in-place title normalisation during the Cleanup pass

---

## Planned / Future

### Phase 3 — Community & Distribution

- **Notification Center** — in-UI event log (info / warning / error) for conversion events, watchdog actions, and worker lifecycle; filterable by severity
- **Unraid Community Apps** — native plugin support
- **Multi-node federation** — coordinate jobs across multiple remuXcode instances (e.g. a dedicated encode box picking up work from a lighter-duty host)
- **Plex/Jellyfin webhook support** *(stretch)* — for setups without Sonarr/Radarr; metadata sourcing is a significant undertaking

### Security

- **Full API authentication** — all endpoints currently rely on same-origin trust; external callers (curl, scripts) and the UI should use a consistent auth mechanism. Plan: frontend fetches the API key once from a localhost-only bootstrap endpoint on first load, stores it as an `HttpOnly` session cookie, and FastAPI accepts either the cookie or the `X-Api-Key` header. Closes the Sonarr/Radarr key leak via `GET /api/config` and prevents unauthenticated write access to config, job queue, and file processing endpoints from other LAN devices.

### Infrastructure / Dependencies

- **mkvtoolnix** — remaining capabilities not yet wired up: chapter manipulation, attachment management, and `mkvinfo`-based stream verification.

### Quality of Life

- **Retry from UI** — re-queue failed jobs from the Jobs page with one click
- **Settings change detection** — surface files in the library that no longer match current conversion settings (e.g. after loosening or tightening a rule)
- **Webhook test button** — send a test payload from the Settings page to verify connectivity end-to-end
- **In-UI log viewer** — tail recent log output from the Settings or Jobs page without needing shell access
