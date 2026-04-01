# remuXcode

A self-hosted media library maintenance tool that keeps your Plex/Jellyfin library clean and compatible — automatically or on demand.

When Sonarr or Radarr imports a file, remuXcode receives a webhook, analyzes the media, and converts what needs fixing: DTS audio becomes AC3/AAC for broad device support, 10-bit H.264 anime gets re-encoded to HEVC or AV1, and unwanted language tracks are stripped out. Everything happens in the background with no manual intervention required.

The application also supports manually updating and maintaining existing Sonarr/Radarr libraries. The built-in web UI lets you browse your entire library — movies and shows — with poster grids, filters, and per-file analysis. See exactly which files have incompatible audio, which anime needs encoding, and kick off conversions for individual files or entire series with a click. It's both a fire-and-forget automation layer and a hands-on library management tool.

## Features

- 🎯 **Smart Format Selection**: AC3 for surround (5.1+), AAC for stereo/7.1+
- 🎬 **HEVC/AV1 Encoding**: Convert 10-bit H.264 to HEVC or AV1 (configurable)
- 🌸 **Anime Detection**: Auto-detects anime for optimized encoding (`-tune animation`)
- 🌍 **Language Cleanup**: Keep only original language + English tracks
- 📊 **Bitrate Matching**: Preserves quality while respecting format limits
- 🔄 **Automatic Renaming**: Triggers Sonarr/Radarr to update filenames after processing
- 🌐 **Webhook-Based**: Triggered by containerized Sonarr/Radarr, runs against your mounts
- 📋 **Job Queue**: Track conversion progress with persistent job status
- 💾 **SQLite Persistence**: Jobs survive restarts, resume interrupted conversions
- 🛑 **Job Cancellation**: Cancel pending or running jobs via API
- 🖥️ **Web UI**: Built-in SvelteKit dashboard with library browsing, job management, and config
- 🎞️ **Library Browse**: Movies & Shows pages with poster grids, filters, and processing previews
- 🔎 **File Analysis**: Full ffprobe/MediaInfo modal — video, audio, and subtitle stream details per file
- 📡 **Live Job Status**: Pending/running indicators on movie posters and episode rows, auto-refresh on completion
- 🔃 **Manual Library Refresh**: Force Sonarr/Radarr metadata re-read from the Movies/Shows pages
- 🎌 **Anime Dual-Audio**: Optionally keep original-language audio on anime while still cleaning subtitles
- 🔍 **Detection Accuracy**: Config-aware audio detection, EAC3 Atmos vs TrueHD distinction

---

## Quick Start

**Preferred: Use the Prebuilt Image**

The remuXcode image is published to Docker Hub and GitHub Container Registry. You do **not** need to build the image yourself unless you want to run the latest development version.

### 1. Create your `compose.yml`

```yaml
services:
  remuxcode:
    container_name: remuxcode
    image: ghcr.io/sirjmann92/remuxcode:latest # or sirjmann92/remuxcode:latest
    ports:
      - "7889:7889"
    volumes:
      - ./config:/app/config
      - ./logs:/app/logs
      - /mnt/yournas:/share:rw       # match Sonarr/Radarr's internal path
    environment:
      - TZ=America/Chicago
      - REMUXCODE_API_KEY=${REMUXCODE_API_KEY}
      - SONARR_URL=${SONARR_URL}
      - SONARR_API_KEY=${SONARR_API_KEY}
      - RADARR_URL=${RADARR_URL}
      - RADARR_API_KEY=${RADARR_API_KEY}
    restart: unless-stopped
```

### 2. Start

```bash
docker compose pull  # get the latest image
docker compose up -d
```

`config/config.yaml` is created automatically on first run with sensible defaults. All settings can be tuned from the Settings page in the web UI.

### 3. Configure Sonarr/Radarr Webhook

**Sonarr** → Settings → Connect → Add Webhook:
- **URL**: `http://YOUR_HOST_IP:7889/api/webhook`
- **Triggers**: On Import Complete (Alternatively On File Import and/or On File Upgrade)
- **Headers**: `X-API-Key: <API Key from remuXcode Config page>`

**Radarr** → Settings → Connect → Add Webhook:
- **URL**: `http://YOUR_HOST_IP:7889/api/webhook`
- **Triggers**: On File Import, On File Upgrade
- **Headers**: `X-API-Key: <API Key from remuXcode Config page>`

> Sonarr's **On Import Complete** fires once after a full batch import (e.g. a whole season), sending all file paths together. Radarr uses the standard per-file events.

---

## Configuration

All conversion settings — audio, video, cleanup, languages, integrations, and processing options — are configurable through the **Settings** page in the web UI at `http://localhost:7889/config`. Changes take effect immediately.

On first run, a default `config/config.yaml` is created automatically. You can also edit this file directly if you prefer, but the UI is the recommended approach.

### `.env` (secrets only)

The only values that must be set outside the UI are the initial connection secrets, passed via environment variables or a `.env` file:

```ini
SONARR_URL=http://localhost:8989
SONARR_API_KEY=your-sonarr-key
RADARR_URL=http://localhost:7878
RADARR_API_KEY=your-radarr-key
REMUXCODE_API_KEY=   # leave blank to auto-generate on first run
```

Once the container is running, these values (and all other settings) can be updated from the Settings page.

---

## Conversion Rules

### Audio

| Source | Target | Notes |
|--------|--------|-------|
| Stereo DTS (2ch) | AAC | Max 320 kbps |
| 5.1 DTS (6ch) | AC3 | Max 640 kbps |
| 7.1+ DTS (8ch) | E-AC3 | Max 1536 kbps |

### Video

| Setting | Anime | Live Action |
|---------|-------|-------------|
| CRF (HEVC) | 19 | 22 |
| Preset | slow | medium |
| Tune | animation | none |
| Output | HEVC 10-bit | HEVC 10-bit |

AV1 mode is also available (set codec to `av1` in Settings) — ~30% better compression, slower encoding, less hardware decoder support.

> Video conversion is **anime-only** by default. Audio conversion and stream cleanup apply to all content. All of these defaults can be changed from the Settings page.

### Job Persistence

Jobs are persisted to `config/jobs.db`. On startup, pending jobs are automatically resumed and old completed jobs are pruned after the configured retention period (default: 30 days).

---

## API Reference (Advanced)

If you'd like to create custom scripts or manually use a CLI to manage your media, remuXcode comes with a full set of APIs. You can find all endpoints and options at http://localhost:7889/docs

All endpoints require `X-API-Key` header except `/health`.

### Health Check

```bash
curl http://localhost:7889/health
```

### Analyze Single File

```bash
curl "http://localhost:7889/api/analyze?path=/share/movies/Movie/movie.mkv" \
  -H "X-API-Key: your-key"
```

### Browse Library

```bash
# Movies (with full media analysis)
curl "http://localhost:7889/api/movies" -H "X-API-Key: your-key"

# Filter results
curl "http://localhost:7889/api/movies?filter=video" -H "X-API-Key: your-key"   # needs HEVC encode
curl "http://localhost:7889/api/movies?filter=audio" -H "X-API-Key: your-key"   # has DTS/TrueHD
curl "http://localhost:7889/api/movies?filter=anime" -H "X-API-Key: your-key"   # anime only
curl "http://localhost:7889/api/movies?search=inception" -H "X-API-Key: your-key"

# Series
curl "http://localhost:7889/api/series" -H "X-API-Key: your-key"

# Scan arbitrary directory
curl "http://localhost:7889/api/scan?path=/share/downloads" -H "X-API-Key: your-key"
```

### Convert Files

```bash
# Single file — full conversion (audio + video + cleanup)
curl -X POST http://localhost:7889/api/convert \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"path": "/share/movies/Movie/movie.mkv", "type": "full"}'

# type options: full | audio | video | cleanup

# Batch — movies by Radarr ID
curl -X POST http://localhost:7889/api/convert/movies \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"movie_ids": [123, 456], "type": "full"}'

# Batch — series by Sonarr ID
curl -X POST http://localhost:7889/api/convert/series \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"series_ids": [42], "type": "audio"}'
```

### Job Management

```bash
# List all jobs
curl http://localhost:7889/api/jobs -H "X-API-Key: your-key"

# Single job
curl http://localhost:7889/api/jobs/abc-123 -H "X-API-Key: your-key"

# Cancel or delete job
curl -X DELETE http://localhost:7889/api/jobs/abc-123 -H "X-API-Key: your-key"
```

**Job status values:** `pending`, `running`, `completed`, `failed`, `cancelled`

---

## Architecture

```
Sonarr/Radarr → POST /api/webhook → Job Queue → ffmpeg workers → files
                                         ↓                      ↓
                                    SQLite (config/jobs.db)   Rename trigger
                                         ↑
                              SvelteKit UI (port 7889)
```

### Project Structure

```
backend/
├── config.yaml           # Configuration template (shipped with repo)
├── app.py                # FastAPI application
├── core.py               # Job queue, workers, singletons
├── api_browse.py         # /api/movies, /api/series, /api/scan, /api/analyze
├── api_convert.py        # /api/convert
├── api_jobs.py           # /api/jobs
├── api_webhook.py        # /api/webhook
├── api_config.py         # /api/config
├── utils/
│   ├── ffprobe.py        # Media analysis
│   ├── config.py         # YAML config loader with env var substitution
│   ├── language.py       # Original language detection
│   ├── anime_detect.py   # Content type detection
│   └── job_store.py      # SQLite job persistence
└── workers/
    ├── audio.py          # DTS → AC3/AAC
    ├── video.py          # H.264 → HEVC/AV1
    └── cleanup.py        # Stream removal/reorder

config/                   # Docker volume — created on first run
├── config.yaml           # Your active configuration
├── jobs.db               # Job history (SQLite)
└── .api_key              # Auto-generated API key

frontend/                 # SvelteKit UI (built into Docker image)
├── src/
│   ├── lib/
│   │   ├── api.ts        # API client
│   │   ├── types.ts      # TypeScript interfaces
│   │   └── components/   # JobCard, Navbar, StatusBadge, AnalyzeModal
│   └── routes/
│       ├── +page.svelte   # Dashboard
│       ├── movies/        # Library browse (poster grid + detail modal + job status)
│       ├── shows/         # Series browse (drill-down to episodes + job status)
│       ├── jobs/          # Job queue with processing details
│       └── config/        # Settings page
```
---

## Logs & Debugging

```bash
# Live logs
docker compose logs -f

# Last 100 lines
docker compose logs --tail=100

# Quick codec check
ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,profile \
  -of default=noprint_wrappers=1 file.mkv
```

---

## Troubleshooting

**Container won't start**
```bash
docker compose logs --tail=50
```

**Webhook test fails**
```bash
curl http://localhost:7889/health
cat config/.api_key
```

**Files not being converted**
```bash
# Check what the analyzer sees
curl "http://localhost:7889/api/analyze?path=/share/your/file.mkv" \
  -H "X-API-Key: $(cat config/.api_key)"
```

**"Failed to trigger rename"**
- Verify Sonarr/Radarr URLs and API keys in `.env`

---

## Build from Source (Optional)

If you want to make local changes, you can build the image yourself:

### 1. Clone and configure

```bash
git clone https://github.com/sirjmann92/remuXcode.git
cd remuXcode
cp .env.example .env
nano .env  # Set SONARR_URL, SONARR_API_KEY, RADARR_URL, RADARR_API_KEY
```

### 2. Create and update compose.yml
Follow the instructions above to create your `compose.yml` file, then replace
```yaml
image: ghcr.io/sirjmann92/remuxcode:latest
```
with
```yaml
build: .
```

### 3. Build and start

```bash
docker compose up -d --build
```

The rest of the setup (webhook, config, etc) is the same as above.

---
````
