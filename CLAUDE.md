# remuXcode — Project Context for AI Assistants

## What This Project Is

remuXcode is a self-hosted media transcoding server. It receives webhooks from Sonarr/Radarr when media is imported, then re-encodes video/audio and cleans up streams (language filtering, subtitle pruning) according to a config file. It exposes a FastAPI backend + SvelteKit frontend on port 7889.

---

## Network Topology

| Host | IP | Role |
|---|---|---|
| NixToy | 192.168.0.130 | Dev machine (this machine). Runs Sonarr, Radarr, and local Docker containers. |
| Encoding server | 192.168.0.134 | **Production remuXcode instance.** Separate machine — no SSH access. |

**There is no SSH access to 192.168.0.134.** All remote inspection must go through the remuXcode HTTP API at `http://192.168.0.134:7889/api/`.

The local workspace at `/home/jase/docker/remuxcode/` is the **development copy** — changes are built and deployed via `docker compose up -d --build` which pushes to the production container on 192.168.0.134 (or run locally for testing).

### API Keys

- **remuXcode API key**: `cat /home/jase/docker/remuxcode/config/.api_key`
- **Sonarr API key**: `grep -A5 'sonarr:' /home/jase/docker/remuxcode/config/config.yaml | grep api_key | awk '{print $2}'`
- **Radarr API key**: `c890cf5ad47a42e99da492cfa1f4e9ee` (also in config.yaml)

### Service URLs

- remuXcode (production): `http://192.168.0.134:7889`
- Sonarr: `http://192.168.0.130:8989`
- Radarr: `http://192.168.0.130:7878`

---

## Hardware

### NixToy (192.168.0.130) — Dev machine
- Intel 13th gen Raptor Lake (UHD 770, Xe-LP)
- UHD 770 supports `hevc_qsv` ✅ but does **NOT** support `av1_qsv` ❌
  - AV1 HW encoding requires Intel Arc (Alchemist/DG2) or Meteor Lake+
- Local remuXcode caps: `hevc=['hevc_qsv', 'hevc_vaapi', 'libx265']`, `av1=['av1_vaapi', 'libsvtav1']`

### Encoding server (192.168.0.134) — Production
- AMD GPU (VAAPI only — no QSV, no NVENC)
- Production remuXcode caps: `hevc=['hevc_vaapi', 'libx265']`, `av1=['av1_vaapi', 'libsvtav1']`
- `hw_accel: none` in production config (uses software encoding with libsvtav1/libx265)

---

## Tech Stack

### Backend
- Python 3.14, FastAPI, venv at `.venv/`
- Key modules: `backend/core.py` (job queue, pipeline), `backend/api_webhook.py`, `backend/workers/video.py`, `backend/workers/audio.py`, `backend/workers/cleanup.py`
- `backend/utils/hwaccel.py` — hardware capability detection with per-encoder probing
- Config: `config/config.yaml` (persisted volume, not in image)
- API key: `config/.api_key` (auto-generated, persisted volume)

### Frontend
- SvelteKit + Svelte 5 runes (`$state`, `$derived`, `$effect`)
- TypeScript, DaisyUI v5, Tailwind v4
- Built at Docker image build time (static files baked into image)

### Container
- `python:3.14-slim` base, FFmpeg 7.1, mkvtoolnix
- Two-stage build: Node.js frontend build → Python runtime
- Volumes: `./config:/app/config`, `/mnt/NASferatu:/share`, `/mnt/NAStradamus:/share-exp`, `/mnt/stash:/stash`
- NAS paths are mounted at the same paths Sonarr/Radarr use — no PATH_MAPPING env vars needed

### Run/Deploy

**This is a Docker-only application.** Every change — backend or frontend — requires a full image rebuild and redeploy. There is no hot-reload or live-mount for production code.

```bash
# Build and deploy (required after ANY code change)
docker compose up -d --build

# Run tests (local venv)
source .venv/bin/activate
pytest tests/

# Lint
ruff check backend/

# Before committing any code changes, run pre-commit
pre-commit run --files <changed files>
# or against all staged files:
pre-commit run
```

**Always run `pre-commit` before committing code.** It runs: ruff (Python lint+format), biome (TS/Svelte lint+format), svelte-check (type checking), codespell, shellcheck, and standard file hygiene checks. Docs, images, and non-code files are excluded by the config.

---

## Sonarr Webhook Integration

### Event Types
All Sonarr download events send `eventType: "Download"`. They are distinguished by:
- `isUpgrade: false` + `release.releaseType: "singleEpisode"` → On File Import (single)
- `isUpgrade: false` + `release.releaseType: "seasonPack"` → On Import Complete (batch)
- `isUpgrade: true` → On File Upgrade

### Payload Structure
- `episodeFiles[]` (plural array) contains file paths — always use this, not singular `episodeFile`
- `episodes[]` contains metadata only (no paths)
- File path: `episodeFiles[n].path`

### Webhook Config in Sonarr
- Notification name: **"Custom Converter"**
- URL: `http://192.168.0.134:7889/api/webhook`
- Auth: `X-API-Key` header (key from `config/.api_key`)
- Enabled events: `onImportComplete: true`, `onEpisodeFileDeleteForUpgrade: true`
- `onDownload: false`, `onUpgrade: false`

### Known Sonarr Issue
Sonarr v4.0.17.2952 throws `System.NullReferenceException` in `NotificationService` when processing `DownloadCompletedEvent` for certain large batch imports (confirmed June 2026 with 291-episode Dragon Ball Z import). If a large import doesn't queue, check Sonarr Docker logs for `[Error] EventAggregator: NotificationService failed while processing [DownloadCompletedEvent]`. Workaround: manually queue via remuXcode's `/api/convert/series` endpoint.

---

## Key Behaviors & Non-Obvious Details

### Job Deduplication
`create_job()` in `core.py` silently returns the existing job if a pending/running job already exists for the same file+type. This is intentional.

### Path Translation
Container paths are identical to Sonarr/Radarr paths (same NAS mounts). No `PATH_MAPPING` env vars are configured. `translate_path()` is a no-op in production.

### `should_convert()` Logic (video)
A file is skipped if:
- Already the target codec (HEVC or AV1)
- Dolby Vision and `dv_to_hdr10: false`
- HDR10+ and `hdr10plus_to_hdr10: false`
- `process_anime: false` and file is detected as anime (or vice versa)
- None of `convert_8bit_x264`, `convert_10bit_x264`, `convert_legacy_codecs` apply

### Stream Title Tag Clearing
`_clear_video_stream_tags()` in `video.py` clears these tags on re-encoded output: `BPS`, `NUMBER_OF_BYTES`, `NUMBER_OF_FRAMES`, `DURATION`, `SOURCE_ID`, `title`. The `title` tag was added specifically to prevent source stream titles like "Dolby Vision P7 FEL" from being inherited into re-encoded files.

### HW Accel Probing
`hwaccel.py` tests each encoder individually with a 1-frame encode at startup. Failed encoders are removed from caps. This is why `av1_qsv` is absent on the UHD 770 — it physically cannot encode AV1 and the probe removes it. The UI only shows codecs that passed the probe for the selected hw_accel method.

### Dolby Vision
DV Profile 7 FEL is dual-layer. FFmpeg can only re-encode the HDR10 base layer — the DV RPU is dropped. `dv_to_hdr10: false` (default) skips DV files entirely. When enabled, output is valid HDR10 but the DV badge is gone. Fix existing files with `mkvpropedit --edit track:v1 --delete name`.

### Anime Detection
Uses both Sonarr API (series genres/originalLanguage) and path-based heuristics (`/anime/` path pattern → `jpn`). Controlled by `process_anime` and `anime_auto_detect` config flags.

---

## Project Structure

```
backend/
  core.py          # Job queue, pipeline orchestration, Sonarr/Radarr triggers
  api_webhook.py   # POST /api/webhook — Sonarr/Radarr webhook handler
  api_convert.py   # Manual convert endpoints
  api_jobs.py      # Job CRUD, logs, cancel
  workers/
    video.py       # Video encoding (CPU SVT-AV1/x265, QSV, VAAPI, NVENC paths)
    audio.py       # DTS/TrueHD conversion
    cleanup.py     # Stream pruning (language/subtitle filtering)
  utils/
    hwaccel.py     # Hardware capability detection + per-encoder probing
    config.py      # Config loading/saving
    ffprobe.py     # Media info extraction
    anime_detect.py
frontend/
  src/
    routes/config/+page.svelte  # Settings UI (codec/hw_accel dropdowns, validation)
    lib/types.ts                # TypeScript types matching backend API responses
config/
  config.yaml      # Persisted runtime config (not in git — volume mount)
docs/
  sonarr-webhook-reference.md  # Live-captured Sonarr v4 webhook payload reference
```
