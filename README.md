# remuXcode

Unified media converter service for Sonarr/Radarr that handles:
- **DTS Audio** → AC3/AAC (device compatibility)
- **10-bit H.264** → HEVC or AV1 (anime-only by default)
- **Stream Cleanup** → Remove unwanted language tracks

## Features

- 🎯 **Smart Format Selection**: AC3 for surround (5.1+), AAC for stereo/7.1+
- 🎬 **HEVC/AV1 Encoding**: Convert 10-bit H.264 to HEVC or AV1 (configurable)
- 🌸 **Anime Detection**: Auto-detects anime for optimized encoding (`-tune animation`)
- 🌍 **Language Cleanup**: Keep only original language + English tracks
- 📊 **Bitrate Matching**: Preserves quality while respecting format limits
- 🔄 **Automatic Renaming**: Triggers Sonarr/Radarr to update filenames
- 🌐 **Webhook-Based**: Runs on host, triggered by containerized Sonarr/Radarr
- 🚀 **Batch API**: Convert multiple movies or series in a single request
- 📋 **Job Queue**: Track conversion progress with persistent job status
- 💾 **SQLite Persistence**: Jobs survive restarts, resume interrupted conversions
- 🛑 **Job Cancellation**: Cancel pending or running jobs via API

---

## Quick Start

### 1. Prerequisites

```bash
# Required dependencies
sudo apt install ffmpeg python3 python3-pip sqlite3

# Or on NixOS
nix-shell -p ffmpeg python3 sqlite
```

### 2. Clone and Install

```bash
git clone https://github.com/yourusername/remuxcode.git
cd remuxcode
pip3 install -e .
```

### 3. Test Without Installing

```bash
python3 tests/test_workers.py "/path/to/your/movie.mkv"
```

### 4. Install as Service

```bash
# Edit service file paths
nano remuxcode.service
# Update: User, Group, WorkingDirectory, EnvironmentFile, ExecStart, ReadWritePaths

# Copy service file
sudo cp remuxcode.service /etc/systemd/system/

# Create log file
sudo touch /var/log/remuxcode.log
sudo chown $USER:$USER /var/log/remuxcode.log

# Configure environment
cp .env.example .env
nano .env  # Set your Sonarr/Radarr URLs and API keys

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable remuxcode
sudo systemctl start remuxcode

# Verify
curl http://localhost:7889/health
```

### 5. Configure Sonarr/Radarr Webhook

In Sonarr/Radarr → Settings → Connect → Add Webhook:
- **URL**: `http://YOUR_HOST_IP:7889/webhook`
- **Triggers**: On Import, On Upgrade (Radarr) / On Import Complete (Sonarr)
- **Headers**: `X-API-Key: your-api-key`

---

## API Reference

### Health Check (no auth required)

```bash
curl http://localhost:7889/health
```

### Analyze Single File

```bash
curl "http://localhost:7889/analyze?path=/path/to/file.mkv" \
  -H "X-API-Key: your-key"
```

**Response:**
```json
{
  "file": "/path/to/file.mkv",
  "video": {
    "codec": "h264",
    "bit_depth": 10,
    "profile": "High 10",
    "is_hevc": false,
    "is_10bit_h264": true
  },
  "has_dts": true,
  "has_truehd": false,
  "needs_audio_conversion": true,
  "needs_video_conversion": true,
  "is_anime": true,
  "content_type": "anime"
}
```

---

### Browse Library

#### List Movies (with full media analysis)

```bash
# All movies with full analysis
curl "http://localhost:7889/movies" -H "X-API-Key: your-key"

# Search by title
curl "http://localhost:7889/movies?search=inception" -H "X-API-Key: your-key"

# Fast mode (Radarr metadata only, no ffprobe)
curl "http://localhost:7889/movies?analyze=false" -H "X-API-Key: your-key"

# Filter results
curl "http://localhost:7889/movies?filter=video" -H "X-API-Key: your-key"   # 10-bit h264
curl "http://localhost:7889/movies?filter=audio" -H "X-API-Key: your-key"   # DTS/TrueHD
curl "http://localhost:7889/movies?filter=anime" -H "X-API-Key: your-key"   # Anime only
```

**Response:**
```json
{
  "total": 150,
  "summary": {
    "needs_video_conversion": 12,
    "needs_audio_conversion": 45,
    "anime": 8
  },
  "movies": [
    {
      "id": 123,
      "title": "Movie Name",
      "year": 2020,
      "path": "/media/movies/Movie Name/movie.mkv",
      "video": {
        "codec": "h264",
        "bit_depth": 10,
        "is_10bit_h264": true,
        "is_hevc": false
      },
      "has_dts": true,
      "needs_audio_conversion": true,
      "needs_video_conversion": true,
      "is_anime": false
    }
  ]
}
```

#### List Series (with full media analysis)

```bash
# All series
curl "http://localhost:7889/series" -H "X-API-Key: your-key"

# Search and filter
curl "http://localhost:7889/series?search=attack+titan&filter=anime" -H "X-API-Key: your-key"
```

**Response:**
```json
{
  "total": 50,
  "summary": {
    "total_dts_episodes": 234,
    "total_needs_video": 156,
    "anime_series": 12
  },
  "series": [
    {
      "id": 42,
      "title": "Attack on Titan",
      "year": 2013,
      "path": "/media/anime/Attack on Titan",
      "total_episodes": 87,
      "dts_count": 0,
      "needs_video_count": 87,
      "anime_count": 87,
      "is_anime": true
    }
  ]
}
```

---

### Scan Directory (for non-library files)

Scan arbitrary directories not in your *arr libraries:

```bash
# Recursive scan
curl "http://localhost:7889/scan?path=/downloads/anime" -H "X-API-Key: your-key"

# Non-recursive
curl "http://localhost:7889/scan?path=/downloads&recursive=false" -H "X-API-Key: your-key"

# Filter by type
curl "http://localhost:7889/scan?path=/downloads&filter=video" -H "X-API-Key: your-key"
```

---

### Convert Files

#### Single File

```bash
# Full conversion (audio + video + cleanup)
curl -X POST http://localhost:7889/convert \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"path": "/path/to/file.mkv", "type": "full"}'

# Audio only (DTS → AC3/AAC)
curl -X POST http://localhost:7889/convert \
  -H "X-API-Key: your-key" \
  -d '{"path": "/path/to/file.mkv", "type": "audio"}'

# Video only (H.264 → HEVC)
curl -X POST http://localhost:7889/convert \
  -H "X-API-Key: your-key" \
  -d '{"path": "/path/to/file.mkv", "type": "video"}'

# Cleanup only (remove unwanted languages)
curl -X POST http://localhost:7889/convert \
  -H "X-API-Key: your-key" \
  -d '{"path": "/path/to/file.mkv", "type": "cleanup"}'
```

#### Batch Convert by ID

```bash
# Movies by Radarr ID
curl -X POST http://localhost:7889/api/convert/movies \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"movie_ids": [123, 456, 789], "type": "full"}'

# Series by Sonarr ID
curl -X POST http://localhost:7889/api/convert/series \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"series_ids": [42, 99], "type": "audio"}'
```

### Job Management

#### Check Job Status

```bash
# Single job
curl http://localhost:7889/jobs/abc-123-def -H "X-API-Key: your-key"

# All jobs
curl http://localhost:7889/jobs -H "X-API-Key: your-key"
```

**Response:**
```json
{
  "id": "abc-123-def",
  "status": "running",
  "file_path": "/media/anime/episode.mkv",
  "progress": 0.5,
  "created_at": 1234567890,
  "started_at": 1234567895,
  "error": null
}
```

**Status values:** `pending`, `running`, `completed`, `failed`, `cancelled`

#### Cancel a Job

```bash
# Cancel pending or running job
curl -X DELETE http://localhost:7889/jobs/abc-123-def -H "X-API-Key: your-key"

# Delete completed/failed job from history
curl -X DELETE http://localhost:7889/jobs/abc-123-def -H "X-API-Key: your-key"
```

**What happens:**
- **Pending jobs**: Immediately marked as cancelled, won't start
- **Running jobs**: Marked as cancelled, stops after current operation
- **Completed/failed**: Deleted from database

---

## Expected Workflow

### Automatic (Webhook)
Sonarr/Radarr import → Webhook → Convert → Rename → Done

### Manual Library Conversion

1. **Browse** your library with `/movies` or `/series` endpoints
2. **Filter** to find items needing conversion (`?filter=video`, `?filter=audio`)
3. **Convert** using the returned IDs via `/api/convert/movies` or `/api/convert/series`
4. **Monitor** progress via `/jobs`

**Example:**
```bash
# Find all anime movies with 10-bit h264
curl "http://localhost:7889/movies?filter=video" -H "X-API-Key: key" | jq '.movies[] | select(.is_anime) | {id, title}'

# Convert them
curl -X POST http://localhost:7889/api/convert/movies \
  -H "X-API-Key: key" \
  -d '{"movie_ids": [123, 456], "type": "video"}'
```

---

## Configuration

### Environment Variables (.env)

```bash
# Required: Sonarr/Radarr
SONARR_URL=http://localhost:8989
SONARR_API_KEY=your-sonarr-key
RADARR_URL=http://localhost:7878
RADARR_API_KEY=your-radarr-key

# Required: Path Mappings (container → host)
PATH_MAPPING_1_CONTAINER=/container/path
PATH_MAPPING_1_HOST=/host/path
# Add more as needed:
# PATH_MAPPING_2_CONTAINER=/share-4k
# PATH_MAPPING_2_HOST=/mnt/4KDrive

# Optional: Security (recommended)
REMUXCODE_API_KEY=your-api-key  # Generate: openssl rand -hex 32

# Optional: Service
MEDIA_WEBHOOK_PORT=7889
LOG_LEVEL=INFO
```

### YAML Configuration (Optional)

The [backend/config.yaml](backend/config.yaml) file contains advanced settings. **Most users don't need to edit this** - the defaults work well and can be overridden via `.env` variables.

```yaml
video:
  enabled: true
  convert_10bit_x264: true    # Main target
  convert_8bit_x264: false    # Optional
  anime_only: true            # Only convert anime content
  anime_auto_detect: true     # Auto-detect anime
  anime_crf: 19               # Quality (lower = better)
  anime_preset: slow          # Encoding speed
  anime_tune: animation       # x265 tune for anime

audio:
  enabled: true
  convert_dts: true           # DTS → AC3/AAC
  convert_truehd: true        # TrueHD → AC3/AAC
  prefer_ac3: true            # AC3 for 5.1, AAC for stereo/7.1+

cleanup:
  enabled: true
  keep_languages: [eng]       # Always keep English
  keep_commentary: true       # Preserve commentary tracks
  keep_sdh: true              # Keep SDH subtitles
```

---

## Conversion Rules

### Audio

| Source | Target | Notes |
|--------|--------|-------|
| Stereo DTS (2ch) | AAC | Max 320kbps |
| 5.1 DTS (6ch) | AC3 | Max 640kbps |
| 7.1+ DTS (8ch) | E-AC3 | Max 1536kbps |
| TrueHD | AC3/E-AC3 | Based on channels |

### Video

Codec preference is configurable via `VIDEO_CODEC` (default: `hevc`).

#### HEVC Mode (default)

| Setting | Anime | Live Action |
|---------|-------|-------------|
| CRF | 19 | 22 |
| Preset | slow | medium |
| Tune | animation | none |
| Output | HEVC 10-bit | HEVC 10-bit |

#### AV1 Mode

| Setting | Anime | Live Action |
|---------|-------|-------------|
| CRF | 28 | 30 |
| Preset | 6 | 8 |
| Encoder | SVT-AV1 | SVT-AV1 |
| Output | AV1 10-bit | AV1 10-bit |

> **AV1 vs HEVC:** AV1 achieves ~30% better compression but encodes significantly slower and has less hardware decoder support. HEVC is recommended for most users.

**Note:** Video conversion is anime-only by default (`anime_only: true`). DTS audio conversion applies to all content.

---

## Logs & Debugging

```bash
# Service status
sudo systemctl status remuxcode

# Live logs
sudo journalctl -u remuxcode -f

# Log file
tail -f /var/log/remuxcode.log
```

---

## Architecture

```
Sonarr/Radarr Container → Webhook → Media Converter (Host) → ffmpeg → Files
                                         ↓                         ↓
                                    Jobs Database          Rename Trigger
```

### Backend Structure

```
backend/
├── config.yaml           # Main configuration
├── utils/
│   ├── ffprobe.py       # Media analysis
│   ├── config.py        # YAML config loader
│   ├── language.py      # Original language detection
│   ├── anime_detect.py  # Content type detection
│   └── job_store.py     # SQLite job persistence
└── workers/
    ├── audio.py         # DTS→AC3/AAC
    ├── video.py         # H.264→HEVC
    └── cleanup.py       # Stream removal
```

### Job Persistence

Jobs are persisted to SQLite database: `./jobs.db` (in project root)

**Database Schema:**
```sql
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,              -- Job UUID
    file_path TEXT NOT NULL,          -- Full path to media file
    status TEXT NOT NULL,             -- queued/processing/completed/failed/cancelled
    progress REAL DEFAULT 0,          -- 0-1 progress indicator
    error TEXT,                       -- Error message if failed
    created_at TEXT NOT NULL,         -- ISO 8601 timestamp
    updated_at TEXT NOT NULL,         -- ISO 8601 timestamp
    started_at TEXT,                  -- ISO 8601 timestamp
    completed_at TEXT,                -- ISO 8601 timestamp
    video_converted INTEGER DEFAULT 0,-- 1 if video was converted
    audio_converted INTEGER DEFAULT 0,-- 1 if audio was converted
    streams_cleaned INTEGER DEFAULT 0 -- 1 if streams were cleaned
);
```

**Benefits:**
- ✅ Jobs survive service restarts/crashes
- ✅ Pending jobs automatically resume on startup
- ✅ Full job history and statistics
- ✅ Track which conversions were performed

**Database Management:**

```bash
# View current queue (pending/running jobs)
sqlite3 -header -column jobs.db \
  "SELECT id, status, file_path, created_at FROM jobs 
   WHERE status IN ('pending', 'running') 
   ORDER BY created_at;"

# Job statistics
sqlite3 -header -column jobs.db \
  "SELECT status, COUNT(*) as count FROM jobs GROUP BY status;"

# View recent jobs (all statuses)
sqlite3 -header -column jobs.db \
  "SELECT id, status, file_path, created_at FROM jobs ORDER BY created_at DESC LIMIT 10;"

# Find failed jobs
sqlite3 -header -column jobs.db \
  "SELECT file_path, error FROM jobs WHERE status = 'failed';"

# Clean up old completed jobs (30+ days)
sqlite3 jobs.db \
  "DELETE FROM jobs WHERE status IN ('completed', 'failed') \
   AND completed_at < date('now', '-30 days');"
```

---

## Troubleshooting

### Common Issues

**"Connection refused"**
- Check service: `sudo systemctl status media-converter`
- Check port: `curl http://localhost:7889/health`

**"No conversion needed"**
- File doesn't have DTS audio or 10-bit h264
- Check with: `curl "http://localhost:7889/analyze?path=/path/to/file.mkv"`

**"Failed to trigger rename"**
- Verify Sonarr/Radarr URLs and API keys in `.env`
- Check *arr API connectivity

### Verify File Info

```bash
# Quick codec check
ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,profile -of default=noprint_wrappers=1 file.mkv

# Audio streams
ffprobe -v error -select_streams a -show_entries stream=index,codec_name,channels -of csv file.mkv
```

---

## Service Management

```bash
sudo systemctl start remuxcode
sudo systemctl stop remuxcode
sudo systemctl restart remuxcode
sudo systemctl status remuxcode
```

### Update

```bash
cd ~/Projects/remuxcode
git pull
sudo systemctl restart remuxcode
```

---

## Future Enhancements

See [ROADMAP.md](ROADMAP.md) for Phase 2 planning:
- FastAPI backend with WebUI
- WebSocket progress updates
- Docker container
- Real-time encoding progress (currently binary: queued/running/done)
