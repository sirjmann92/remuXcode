# remuXcode

A self-hosted media maintenance tool that automatically converts, cleans, and optimizes your Plex/Jellyfin library — triggered by Sonarr and Radarr webhooks or kicked off manually from the built-in web UI.

When new content is imported, remuXcode receives a webhook, analyzes the file, and runs whichever conversions are needed: DTS/DTS-HD audio gets converted to AC3/EAC3/AAC for broad device compatibility, 10-bit H.264 (or legacy codecs like VC-1 and MPEG-2) gets re-encoded to HEVC or AV1, and foreign-language audio and subtitle tracks get stripped out. All of this runs in the background with no manual intervention.

The web UI lets you browse your entire Sonarr/Radarr library — movies and shows — with poster grids, episode tables, filters, and per-file analysis. Queue individual files, full series, or bulk selections, and monitor everything from the Jobs page.

## Features

- **Audio conversion** — DTS/DTS-HD → AC3 (5.1), EAC3 (7.1+), or AAC (stereo); configurable bitrate caps; optional keep-original; TrueHD passthrough
- **Video encoding** — 10-bit H.264, 8-bit H.264 (optional), and legacy codecs (VC-1, MPEG-2, MPEG-4/XviD/DivX) → HEVC or AV1
- **Hardware acceleration** — Intel QSV/VAAPI and NVIDIA NVENC auto-detected at startup; software fallback when no GPU is available
- **Stream cleanup** — remove audio and subtitle tracks outside your keep-languages list; preserve forced subtitles, SDH, commentary, and audio description
- **Anime support** — per-worker anime-only mode, anime-optimized encoding presets, dual-audio preservation
- **Library browse** — Movies and Shows pages backed by Radarr/Sonarr APIs with poster art, filters, sort, multi-select batch queue
- **Analyze modal** — full stream detail (video, audio, subtitle streams) for any file via ffprobe
- **Job queue** — persistent SQLite-backed queue, resumable after restart, drag-and-drop reordering of pending jobs
- **Webhook-driven** — fire-and-forget automation; also fully usable in manual mode
- **Single container** — FastAPI backend + SvelteKit frontend, port 7889

See [docs/](docs/) for the full feature reference.

---

## Quick Start

### Option 1 — Prebuilt Image (recommended)

**1. Create `compose.yml`**

```yaml
services:
  remuxcode:
    container_name: remuxcode
    image: ghcr.io/sirjmann92/remuxcode:latest
    ports:
      - "7889:7889"
    volumes:
      - ./config:/app/config         # config.yaml, jobs.db, API key
      - ./logs:/app/logs
      - /mnt/yournas:/share:rw       # match the path Sonarr/Radarr use internally
    devices:
      - /dev/dri:/dev/dri            # optional — Intel QSV/VAAPI GPU passthrough
    environment:
      - TZ=America/Chicago
    restart: unless-stopped
```

> Mount your media at the **same path Sonarr/Radarr use inside their containers** so webhook file paths need no translation. Add additional volume mounts if your library spans multiple shares.

**2. Start**

```bash
docker compose up -d
```

`config/config.yaml` is created automatically on first run with sensible defaults. Open `http://localhost:7889/config` to enter your Sonarr/Radarr connection details.

Your API key is auto-generated on first start and stored in `config/.api_key`. It's also shown on the Settings page.

---

### Option 2 — Build from Source

```bash
git clone https://github.com/sirjmann92/remuXcode.git
cd remuXcode
```

Create a `compose.yml` as above, but replace `image: ghcr.io/...` with:

```yaml
build: .
```

Then:

```bash
docker compose up -d --build
```

---

## Webhook Setup

### Sonarr

Settings → Connect → Add → Webhook:

| Field | Value |
|-------|-------|
| URL | `http://<host>:7889/api/webhook` |
| Method | POST |
| Triggers | **On Import Complete** (recommended) |
| Headers | `X-API-Key: <your key>` |

> **On Import Complete** fires once after a full batch import (e.g. a whole season), delivering all file paths in a single payload — more efficient than per-episode triggers. You can also enable **On File Import** and/or **On File Upgrade** for per-file triggers.

### Radarr

Settings → Connect → Add → Webhook:

| Field | Value |
|-------|-------|
| URL | `http://<host>:7889/api/webhook` |
| Method | POST |
| Triggers | On Import, On Upgrade |
| Headers | `X-API-Key: <your key>` |

---

## API

All endpoints (except `/health`) require an `X-API-Key` header. Interactive API docs are available at `http://localhost:7889/docs`.

```bash
# Health check (no auth required)
curl http://localhost:7889/health

# Queue a single file for full conversion
curl -X POST http://localhost:7889/api/convert \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"path": "/share/movies/Movie (2024)/movie.mkv", "type": "full"}'

# type options: full | audio | video | cleanup

# Queue all files in a Radarr library (by Radarr movie IDs)
curl -X POST http://localhost:7889/api/convert/movies \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"movie_ids": [123, 456], "type": "full"}'

# Queue all episodes in a Sonarr series (by Sonarr series ID)
curl -X POST http://localhost:7889/api/convert/series \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"series_ids": [42], "type": "audio"}'

# List jobs
curl http://localhost:7889/api/jobs -H "X-API-Key: your-key"

# Analyze a file
curl "http://localhost:7889/api/analyze?path=/share/movies/Movie/movie.mkv" \
  -H "X-API-Key: your-key"
```

---

## Documentation

| Page | Description |
|------|-------------|
| [Dashboard](docs/dashboard.md) | Live stats, active jobs, queue management |
| [Movies](docs/movies.md) | Browse and process your movie library |
| [Shows](docs/shows.md) | Browse and process your TV library |
| [Jobs](docs/jobs.md) | Job history, filtering, controls |
| [Settings](docs/settings.md) | Complete settings reference |

---

## License

MIT
