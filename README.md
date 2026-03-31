# remuXcode

A self-hosted media library maintenance tool that keeps your Plex/Jellyfin library clean and compatible — automatically or on demand.

When Sonarr or Radarr imports a file, remuXcode receives a webhook, analyzes the media, and converts what needs fixing: DTS audio becomes AC3/AAC for broad device support, 10-bit H.264 anime gets re-encoded to HEVC or AV1, and unwanted language tracks are stripped out. Everything happens in the background with no manual intervention required.

But not every file comes through Sonarr/Radarr. The built-in web UI lets you browse your entire library — movies and shows — with poster grids, filters, and per-file analysis. See exactly which files have incompatible audio, which anime needs encoding, and kick off conversions for individual files or entire series with a click. It's both a fire-and-forget automation layer and a hands-on library management tool.

## Features

- 🎯 **Smart Format Selection**: AC3 for surround (5.1+), AAC for stereo/7.1+
- 🎬 **HEVC/AV1 Encoding**: Convert 10-bit H.264 to HEVC or AV1 (configurable)
- 🌸 **Anime Detection**: Auto-detects anime for optimized encoding (`-tune animation`)
- 🌍 **Language Cleanup**: Keep only original language + English tracks
- 📊 **Bitrate Matching**: Preserves quality while respecting format limits
- 🔄 **Automatic Renaming**: Triggers Sonarr/Radarr to update filenames
- 🌐 **Webhook-Based**: Triggered by containerized Sonarr/Radarr, runs against your mounts
- 📋 **Job Queue**: Track conversion progress with persistent job status
- 💾 **SQLite Persistence**: Jobs survive restarts, resume interrupted conversions
- 🛑 **Job Cancellation**: Cancel pending or running jobs via API
- 🖥️ **Web UI**: Built-in SvelteKit dashboard with library browsing, job management, and config
- 🎞️ **Library Browse**: Movies & Shows pages with poster grids, filters, and processing previews
- 🔍 **Detection Accuracy**: Config-aware audio detection, EAC3 Atmos vs TrueHD distinction

---

## Quick Start

See [QUICKSTART.md](QUICKSTART.md) for step-by-step setup.

### 1. Clone and configure

```bash
git clone https://github.com/sirjmann92/remuXcode.git
cd remuXcode
cp .env.example .env
nano .env  # Set SONARR_URL, SONARR_API_KEY, RADARR_URL, RADARR_API_KEY
```

### 2. Create your `compose.yml`

`compose.yml` is not included in the repo — create your own. Mount your media at the **same paths Sonarr/Radarr use internally** so webhook paths work without translation:

```yaml
services:
  remuxcode:
    container_name: remuxcode
    build: .
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

### 3. Start

```bash
docker compose up -d
```

`config/config.yaml` is created automatically on first run. Edit it to tune encoding settings.

### 4. Configure Sonarr/Radarr Webhook

**Sonarr** → Settings → Connect → Add Webhook:
- **URL**: `http://YOUR_HOST_IP:7889/api/webhook`
- **Triggers**: On Import Complete
- **Headers**: `X-API-Key: <value from config/.api_key>`

**Radarr** → Settings → Connect → Add Webhook:
- **URL**: `http://YOUR_HOST_IP:7889/api/webhook`
- **Triggers**: On Import, On Upgrade
- **Headers**: `X-API-Key: <value from config/.api_key>`

> Sonarr's **On Import Complete** fires once after a full batch import (e.g. a whole season), sending all file paths together. Radarr uses the standard per-file events.

---

## Configuration

### `.env` (secrets only)

```ini
SONARR_URL=http://localhost:8989
SONARR_API_KEY=your-sonarr-key
RADARR_URL=http://localhost:7878
RADARR_API_KEY=your-radarr-key
REMUXCODE_API_KEY=   # leave blank to auto-generate
```

### `config/config.yaml` (everything else)

Auto-created on first run from the built-in template. Key settings:

```yaml
video:
  enabled: true
  convert_10bit_x264: true    # Main target
  convert_8bit_x264: false    # Optional (often makes files larger)
  anime_only: true            # Only convert anime content
  anime_crf: 19               # Quality (lower = better/larger)
  anime_preset: slow          # Encoding speed

audio:
  enabled: true
  convert_dts: true           # DTS → AC3/AAC
  prefer_ac3: true            # AC3 for 5.1, AAC for stereo/7.1+

cleanup:
  enabled: true
  keep_languages: [eng]       # Always keep English
  keep_commentary: true
  keep_sdh: true
```

See [backend/config.yaml](backend/config.yaml) for the full reference with all options.

---

## API Reference

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

AV1 mode is also available (`codec: av1` in config.yaml) — ~30% better compression, slower encoding, less hardware decoder support.

> Video conversion is **anime-only** by default (`anime_only: true`). Audio conversion and stream cleanup apply to all content.

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

## Container Management

```bash
# Start
docker compose up -d

# Stop
docker compose down

# Restart (picks up config changes)
docker compose restart

# Rebuild and restart (after code changes)
docker compose up -d --build

# Update
git pull && docker compose up -d --build
```

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
│   │   └── components/   # JobCard, Navbar, StatusBadge
│   └── routes/
│       ├── +page.svelte   # Dashboard
│       ├── movies/        # Library browse (poster grid + detail modal)
│       ├── shows/         # Series browse (drill-down to episodes)
│       ├── jobs/          # Job queue with processing details
│       └── config/        # Configuration editor
```

### Job Persistence

Jobs are persisted to `config/jobs.db`. On startup, pending jobs are automatically resumed and old completed jobs are pruned after `general.job_history_days` (default: 30) days.

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
