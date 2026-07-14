# remuXcode — Roadmap

## What's Built

### Core Conversion Workers

- **Audio** — DTS/DTS-HD → AC3 (5.1), EAC3 (7.1+), AAC (stereo); separate DTS:X toggle; TrueHD passthrough; configurable bitrate caps; keep-original option with track ordering control; anime-only and live-action-only modes
- **Video** — 10-bit H.264 → HEVC or AV1; 8-bit H.264 (optional); legacy codecs (VC-1, MPEG-2, MPEG-4/XviD/DivX); interlaced source detection with automatic deinterlacing (`yadif`) on encode; anime-optimized presets (CRF, tune, framerate); anime-only and live-action-only modes; hardware acceleration (Intel QSV/VAAPI, NVIDIA NVENC) with automatic software fallback; **Dolby Vision retention** — Custom Encode option that re-encodes DV sources to HEVC while keeping Dolby Vision as Profile 8.1 with HDR10 fallback (Profile 7 remux RPUs converted via `dovi_tool -m 2`, re-attached through FFmpeg's `-dolbyvision` RPU passthrough; software x265 + original resolution only)
- **Cleanup** — mux-only pass that removes audio/subtitle tracks outside keep-languages list; preserves forced subtitles, SDH, commentary, audio description; rewrites foreign-language track titles to canonical English names (e.g. 「英語」 → "English") in-place via `mkvpropedit` for MKV files (ffmpeg fallback for other containers); anime dual-audio mode; live-action-only mode

### Automation & Integration

- Sonarr/Radarr webhook receiver (On Import Complete, On File Import, On File Upgrade)
- **Webhook test button** — Integrations page button that asks Sonarr/Radarr's own API to fire a real test webhook at remuXcode, verifying both the remuXcode→Sonarr/Radarr API call and the Sonarr/Radarr→remuXcode delivery in one round trip
- Post-conversion rename trigger via Sonarr/Radarr API — Sonarr RescanSeries + polled rename; Radarr polled rename
- Job persistence (SQLite) — survives container restarts, resumes pending jobs in queue order
- Anime detection — path patterns, NFO genre parsing, Sonarr/Radarr API genres and studio names
- Original language detection — NFO, API, path pattern fallback chain

### Web UI

- **Dashboard** — live job stats (processed, active, queued, storage saved), in-progress jobs with per-phase progress and size delta, pending queue with drag-and-drop reordering, recent activity
- **Movies** — Radarr-backed poster grid, work-needed badges (Audio/Video/Cleanup/Legacy), filter/sort/search, **resolution filter** (2160p/1080p/720p/SD), **Legacy (Xvid/DivX) codec filter**, per-file analyze modal with **Fix Metadata** for in-place track language/title correction, **Cover Art** display (thumbnail preview + in-place removal), and scan-type indicator (interlaced/progressive), per-movie Custom Encode (downscale / HDR / DV retention), individual and multi-select batch queue, job status indicators on posters, library refresh
- **Shows** — Sonarr-backed series list, season/episode drill-down, job indicators at series/season/episode level, **resolution filter** (2160p/1080p/720p/SD), **Legacy (Xvid/DivX) codec filter**, per-episode analyze modal with **Fix Metadata** for in-place track language/title correction, **Cover Art** display (thumbnail preview + in-place removal), and scan-type indicator (interlaced/progressive), per-episode and per-season Custom Encode (downscale / HDR / DV retention), individual and multi-select batch queue, **Queue Season** button that context-switches to **Queue N Selected** when season episodes are checked, **Queue All Episodes** and **Queue N Selected** at series level, per-show **Rescan** button, library refresh
- **Jobs** — full job history with status/worker/media/source/date-range filters and search, per-phase results (size delta per phase), full-text error display with clipboard copy button, drag-and-drop pending queue reordering, Stop Current / Clear Pending / Delete Completed controls, individual job cancel and delete, **retry failed jobs with one click**, load-more pagination that persists across background refreshes, back-to-top button for long queues, per-job log panel with source/level filters showing the exact `ffmpeg`/`mkvpropedit` command line(s) run for each phase, **Custom Encode** badge on jobs using non-default encode options
- **Settings** — all settings configurable from the UI: audio, video, cleanup, language detection, Sonarr/Radarr connections, workers, job retention, hardware acceleration, cover art stripping
- **Logs** — dedicated in-UI viewer tailing the application log (startup/shutdown, job lifecycle, webhook and integration events, errors), level filter chips (Debug/Info/Warning/Error/Critical) and search, download-full-log button, and a **runtime log level control** that changes and persists the effective logging level immediately (no restart or compose-file edit needed)
- Light/dark mode toggle (persisted per-browser)

### Infrastructure

- Single Docker container — FastAPI backend + SvelteKit frontend (port 7889)
- Multi-stage Dockerfile: Node.js builds frontend → Python 3 slim runtime
- Hardware acceleration auto-detection at startup (QSV / VAAPI / NVENC / software)
- Real-time FFmpeg progress via Unix FIFO; frame-count fallback for hardware encoders that report `out_time_us=N/A`
- File size tracking: before/after displayed per conversion phase in the Jobs UI
- Automatic job retry on startup for jobs interrupted mid-encode
- Per-job logs persist across container restarts (SQLite-backed, periodic flush for running jobs)
- Multi-phase jobs (audio + video + cleanup) chain through temp files instead of writing back to the original between every phase — only the last phase's output path is the real source file, and each worker now knows whether its own write *is* that final replacement (`is_final_write`), so the "Replacing file safely..." status/log message and the external mid-job-modification safety check both correctly apply only to the true final phase instead of firing (or being silently skipped) on every intermediate chain hop
- **mkvtoolnix** (`mkvpropedit`) — in-place MKV metadata editing without remuxing: track statistics tags (`BPS`/`DURATION`/`NUMBER_OF_FRAMES`) written after every video encode so MediaInfo and media servers report correct bitrate; in-place track language/title correction via the Retag worker; in-place title normalisation during the Cleanup pass
- **dovi_tool** — static binary baked into the image; converts Dolby Vision Profile 7 RPUs to Profile 8.1 (enhancement layer discarded) as the preparation step for DV-retaining re-encodes

---

## Planned / Future

### Job Queue & Scheduling

- **Pause entire queue** — global pause control with two modes: (1) let the currently-running job finish, then pause before starting the next pending job, or (2) cancel the current job immediately, pause, and re-queue it at the front of the pending list so it's picked up first when unpaused
- **Estimated completion time for the queue** — project a queue-wide ETA using the live encode rate of the current job plus historical per-file-type throughput from completed job history; falls back to the current job's live rate alone when no history is available yet

### Integrations

- **Multiple Sonarr/Radarr instances** — support configuring more than one Sonarr and/or Radarr connection (e.g. separate 4K, anime, or kids-content instances), with webhook routing and per-instance library browsing on the Movies/Shows pages
- **Multi-node remuXcode** — coordinate jobs across multiple remuXcode instances, e.g. a dedicated GPU/encode box picking up work queued by a lighter-duty host. Requires a shared job queue/store and consistent path mapping across nodes; see also Phase 3 below

### Automated Remux Compression

- **Compress/Encode Remuxes** — automated re-encoding of remux-grade files (any resolution) in the webhook/batch flow, with its own Settings card separate from the standard video worker settings:
  - **Remux detection** — primary signal: Radarr/Sonarr file quality via API (`Remux-2160p`, `Remux-1080p`, …), already available in webhook payloads and library browsing; fallback heuristic via ffprobe: bits-per-pixel (`bitrate / (w × h × fps)` — remuxes ≈ 0.2–0.4, encodes < ~0.1), lossless audio presence (TrueHD/DTS-HD MA/PCM), and DV Profile 7 (BD-sourced by definition). No filename parsing
  - **Remux-specific encode settings** — CRF / preset / VBV tuned for high-quality sources (e.g. CRF 18 + slower preset), independent of the standard worker defaults
  - **Dolby Vision policy** — replace the current `dv_to_hdr10` boolean with a three-way policy: **Skip / Strip to HDR10 / Retain as Profile 8.1** (reusing the dovi_tool + `-dolbyvision` RPU-passthrough pipeline built for Custom Encode)
  - **HDR10+ policy** — replace `hdr10plus_to_hdr10` with **Skip / Strip**, with retention as a follow-up (SVT-AV1 passes HDR10+ dynamic metadata through natively in FFmpeg; x265 needs an `hdr10plus_tool` extract → `dhdr10-info` JSON step, analogous to the dovi_tool flow)
  - **Minimum-savings guard** — skip when the projected size reduction is below a configurable threshold
  - Context: today `dv_to_hdr10` / `hdr10plus_to_hdr10` are effectively unreachable — all real DV/HDR10+ content is HEVC, which the automated flow skips at the already-target-codec check before those gates apply (and HEVC→AV1 is not a conversion criterion). This feature is what gives them a real job

### Custom Encode Enhancements

- **Pending-job action preview** — the "what will happen" summary (audio convert, video convert, cleanup, DV strip, etc.) is currently only shown once a job starts running. Surfacing the same summary for **pending** jobs lets a user decide whether to let a queued job run as-is or cancel and re-queue it with different options — instead of discovering the mismatch after it's already been processed once
- **Per-job VBV/buffer override** — no cap, global default, or a custom per-job max bitrate/buffer size
- **Per-job CRF override** — override the configured CRF for a single Custom Encode job
- **Per-job preset override** — override the configured encoder preset (e.g. `slow`, `medium`) for a single Custom Encode job
- **Resolution-aware Custom Encode defaults** — optionally define default CRF/preset/VBV per target resolution in Settings, which the Custom Encode modal inherits by default but can still override per job

### Phase 3 — Community & Distribution

- **Notification Center** — in-UI event log (info / warning / error) for conversion events, watchdog actions, and worker lifecycle; filterable by severity
- **Unraid Community Apps** — native plugin support
- **Plex/Jellyfin webhook support** *(stretch)* — for setups without Sonarr/Radarr; metadata sourcing is a significant undertaking

### Security

- **Application login (user authentication)** — a real sign-in for the web UI itself: a person authenticates to use the Dashboard/Movies/Shows/Jobs/Settings, distinct from the API key used by Sonarr/Radarr and external scripts. Not HTTP Basic Auth. Plan: no password exists until the admin chooses one — first launch with no password configured serves a `/setup` screen (not `/login`) where the user picks their own password directly in the browser; `POST /api/auth/setup` accepts it exactly once (bcrypt-hashed and persisted, mirroring how `.api_key` is stored today) and is permanently disabled as soon as a password exists, closing it off as an attack surface. Successful setup logs the user straight in via an `HttpOnly` session cookie, same as `POST /api/auth/login` does for every sign-in after that. A root-layout guard checks session state on load and routes to `/setup` or `/login` as appropriate. Recovery if the password is forgotten: delete the persisted hash (same mechanism as regenerating the API key) and restart to re-trigger first-run setup — no email/reset-token flow needed for a single-admin self-hosted app. The profile icon already reserved in the navbar (next to the light/dark toggle, currently an inert placeholder) becomes the sign-in/sign-out control. See "Optional Plex auth" below as a possible alternative/companion sign-in method once this lands.
- **Optional Plex auth** — allow signing in to the remuXcode UI with a Plex account (OAuth via plex.tv) as an alternative/companion to a local admin password, useful for multi-user households where sharing a shared password isn't desirable
- **Full API authentication (hardening)** — separate from the above: today only the Sonarr/Radarr webhook route (`api_webhook.py`) checks the `X-API-Key` header — every other endpoint (config, jobs, convert, browse, analyze) has no auth check at all and relies entirely on same-origin trust. Plan: extend `require_auth` to cover every route consistently, and have the frontend attach the key (or the session cookie from the item above) automatically. Closes the Sonarr/Radarr key leak via `GET /api/config` and prevents unauthenticated write access to config, job queue, and file processing endpoints from other LAN devices.

### Infrastructure / Dependencies

- **mkvtoolnix** — remaining capabilities not yet wired up: chapter manipulation, attachment management, and `mkvinfo`-based stream verification.

### Quality of Life

- **Settings change detection** — surface files in the library that no longer match current conversion settings (e.g. after loosening or tightening a rule)

### Defects, Bugs & Known Issues

- ✅ **FIXED — Dolby Vision 7 -> 8.1** — Preparing Dolby Vision base layer (Profile 7 -> 8.1) failed with "Dolby Vision preparation failed: cancelled" on large remuxes. Cause: the prep step (base-layer extract through dovi_tool) emitted no progress, so the 15-minute stale-job watchdog cancelled the job mid-prep. Fixed by pulsing the progress heartbeat every 10 s during prep and surfacing GB-written in the status detail
- ✅ **FIXED — Subtitle Cleanup** — When the only remaining subtitle track(s) were explicitly tagged with a non-kept language, the file was flagged for Cleanup but the worker's keep-all safety net silently refused to remove them (permanent no-op badge). The safety net is now tag-aware: it only keeps everything when a removal candidate is *untagged*; explicitly-tagged foreign subtitles are removed even if none remain, and `should_cleanup` mirrors the same rule so the badge always matches what the worker will do