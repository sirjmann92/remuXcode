# remuXcode - Future Roadmap

## Phase 1: Core Functionality ✓ (CURRENT)
- [x] Single file conversion script
- [x] Sonarr/Radarr integration via custom scripts
- [x] Batch converter for existing libraries
- [x] API-triggered automatic renaming
- [x] Smart format selection (AC3/AAC)
- [x] Bitrate matching
- [x] Comprehensive logging

### Phase 1.5: Production Hardening
- [ ] **Proper log storage and rotation**
  - Implement `logging.handlers.RotatingFileHandler` for automatic log rotation
  - Configurable retention (days/size limits)
  - Configurable log level (DEBUG for troubleshooting, INFO for production)
  
- [x] **Batch conversion API endpoint**
  - Accept multiple movie/series IDs in single request
  - `/api/convert/movies` - POST with array of Radarr movie IDs
  - `/api/convert/series` - POST with array of Sonarr series IDs
  - `/api/convert/episodes` - POST with array of specific episode IDs
  - Query Radarr/Sonarr API to get file paths for each ID
  - Queue all files in single batch job
  - Return job ID for progress tracking
  - Use case: Bulk re-convert after settings change
  
- [x] **Error recovery improvements**
  - Graceful handling of disk full scenarios
  - Better temp directory cleanup on crashes
  - Resume partial conversions (job persistence via SQLite)
  
- [x] **Monitoring & alerting**
  - Health check endpoint
  - Job statistics and tracking
  - Comprehensive logging with configurable levels

## Phase 1.6: Unified Media Converter Backend ✓ (COMPLETED)

### Goal
Combine DTS audio conversion, HEVC video encoding, and stream cleanup into a unified, modular backend.

### Completed Features
- [x] **HEVC Video Encoding**
  - Convert 10-bit H.264 (High 10) to HEVC for device compatibility
  - Content-aware encoding: anime vs live action detection
  - Different settings for anime (`-tune animation`, CRF 20) vs live action
  - VBV bitrate caps for streaming devices
  - Backup original files before conversion

- [x] **Anime Auto-Detection**
  - Path-based detection (`/Anime/`, `/アニメ/`)
  - NFO file parsing for genres (animation, anime)
  - Sonarr/Radarr API integration for series/movie type
  - Studio detection (Japanese animation studios)

- [x] **Language-Based Stream Cleanup**
  - Detect original content language from NFO, API, or path
  - Keep only original language + English audio/subtitles
  - Preserve forced subtitles and SDH tracks
  - Configurable language preferences

- [x] **Modular Architecture**
  - Separate workers: `audio.py`, `video.py`, `cleanup.py`
  - Shared utilities: `ffprobe.py`, `config.py`, `language.py`, `anime_detect.py`
  - YAML configuration with environment variable substitution
  - Typed dataclasses for configuration

### Backend Structure
```
backend/
├── config.yaml           # Main configuration file
├── utils/
│   ├── ffprobe.py       # Media analysis (streams, codecs, bit depth)
│   ├── config.py        # YAML config loader with typed dataclasses
│   ├── language.py      # Original language detection (NFO, API, path)
│   └── anime_detect.py  # Content type detection (anime vs live action)
└── workers/
    ├── audio.py         # DTS→AC3/AAC conversion
    ├── video.py         # H.264→HEVC encoding
    └── cleanup.py       # Language-based stream removal
```

### Completed Work
- [x] Job queue manager with SQLite persistence
- [x] Unified webhook handler for all conversion types
- [x] HTTP API endpoints for manual triggering
- [x] .env configuration controls for all components
- [x] Comprehensive code cleanup and optimization
- [x] Rebranding to remuXcode (v2.0.0)

## Phase 1.7: Configuration & Control Improvements ✓ (COMPLETED)

### Goal
Improve consistency and clarity of content filtering and processing controls.

### Completed Features
- [x] **Per-Worker Anime-Only Flags**
  - Individual `anime_only` setting per worker: Video, Audio, Cleanup
  - When `true`: that worker only processes anime content, skipping everything else
  - When `false` (default for Audio/Cleanup): that worker processes all content
  - Allows independent control — e.g. only encode anime video, but convert DTS audio on everything
  - Full UI support in Settings page with per-section Anime Only toggles

- [x] **DTS:X Object-Based Audio Awareness**
  - Separate `convert_dts_x` toggle (off by default — DTS:X is high quality)
  - Per-type keep-original: `keep_original` for DTS, `keep_original_dts_x` for DTS:X
  - `original_as_secondary`: converted track first (default player pick), original second
  - ffprobe profile detection for DTS:X streams

### Implementation Notes
- Per-worker anime check evaluated inside each worker's `should_*` method
- Backward compatible: existing behavior unchanged if new flags are not configured

## Phase 2: Docker + WebUI ✓ (COMPLETED)

### Architecture
```
┌─────────────────────────────────────────────────┐
│  Docker Container (single service, port 7889)   │
│                                                 │
│  ┌───────────────────────────────────────────┐  │
│  │  SvelteKit Frontend (static build)        │  │
│  │  - Dashboard, job queue, config UI        │  │
│  └──────────────────────┬────────────────────┘  │
│                          │ REST API              │
│  ┌──────────────────────▼────────────────────┐  │
│  │  FastAPI Backend                          │  │
│  │  - Webhook endpoints (Sonarr/Radarr)      │  │
│  │  - Job queue & status (SQLite)            │  │
│  │  - Media analysis & conversion workers    │  │
│  └───────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
```

### Features

#### Dashboard
- **Real-time statistics**
  - Total files processed
  - DTS files found/converted
  - Storage saved
  - Processing queue status
  
- **Live monitoring**
  - Currently processing files
  - Progress bars
  - ETA calculations
  - Worker status
  
- **Recent activity log**
  - Last 50 conversions
  - Errors with details
  - Success/failure rate

#### Queue Management
- **Add jobs**
  - Directory scanning
  - Manual file selection
  - Drag & drop interface
  
- **Priority control**
  - High/Normal/Low priority
  - Pause/Resume/Cancel
  
- **Scheduling**
  - Cron-like scheduling
  - "Process during off-hours"
  - Bandwidth/CPU throttling

#### Configuration UI
- **Conversion settings**
  - Format preferences (AC3/AAC)
  - Bitrate caps
  - Quality presets
  
- **Integration settings**
  - Sonarr/Radarr URLs & API keys
  - Test connections
  - Auto-discovery
  
- **Watch folders**
  - Multiple directory monitoring
  - File pattern filters
  - Auto-processing triggers

#### Advanced Features
- **Webhook notifications**
  - Discord/Slack integration
  - Custom HTTP callbacks
  - Email alerts
  
- **Statistics & Analytics**
  - Conversion trends over time
  - Most common audio formats
  - Storage savings graphs
  - Processing time metrics
  
- **Batch operations**
  - Resume interrupted batches
  - Retry failed conversions
  - Rollback (restore from backup)

### Technology Stack

#### Backend
- **FastAPI** - Python web framework
- **SQLite** - Job history & persistence
- **asyncio / threading** - In-process async job workers

#### Frontend
- **SvelteKit** - Reactive UI framework
- **Tailwind CSS + DaisyUI** - Styling
- **TypeScript** - Type-safe API client

#### Infrastructure
- **Docker** - Single-container deployment
- **Volume mounts** - Media library access, config & log persistence

### Docker Compose Structure

```yaml
services:
  remuxcode:
    container_name: remuxcode
    build: .
    ports:
      - "7889:7889"
    volumes:
      - ./config:/app/config   # Persists config.yaml and jobs.db
      - ./logs:/app/logs
      - /mnt/your-media:/share:rw
    environment:
      - TZ=America/Chicago
      - REMUXCODE_API_KEY=${REMUXCODE_API_KEY}
      - SONARR_URL=${SONARR_URL}
      - SONARR_API_KEY=${SONARR_API_KEY}
      - RADARR_URL=${RADARR_URL}
      - RADARR_API_KEY=${RADARR_API_KEY}
    restart: unless-stopped
```

### Development Phases

#### 2.1: Backend API
- [x] FastAPI project setup
- [x] In-process async job workers (no external queue required)
- [x] Core endpoints (browse, convert, jobs, config, webhooks)
- [x] Database models for job history (SQLite)
- [x] API documentation (OpenAPI/Swagger)

#### 2.2: Frontend UI
- [x] SvelteKit project setup
- [x] Dashboard layout with real-time job status
- [x] Queue management interface with detailed processing results
- [x] Settings configuration page
- [x] Real-time progress updates
- [x] Responsive design
- [x] **Library Browse: Movies** — poster grid, detail modal with track removal preview, config-aware filters (Audio, Cleanup, Anime)
- [x] **Library Browse: Shows** — series list with drill-down to seasons/episodes, inline cleanup detail
- [x] **Detection Accuracy** — EAC3 Atmos vs TrueHD distinction, slash-separated mediaInfo parsing, genre-based anime detection
- [x] **File Analysis Modal** — full ffprobe/MediaInfo viewer with tabbed UI (General, Video, Audio, Subs) per file
- [x] **Live Job Status** — pending/running indicators on movie posters and episode rows with 3-second polling
- [x] **Auto-Refresh on Completion** — Movies/Shows pages automatically re-fetch data when a job finishes
- [x] **Manual Library Refresh** — Refresh Library buttons on Movies/Shows pages with confirmation modal, forces Sonarr/Radarr full metadata re-read
- [x] **Anime Dual-Audio** — `anime_keep_original_audio` config to preserve original-language audio on anime content
- [x] **Sonarr Integration Hardening** — RefreshSeries + RescanSeries (polled), polled rename commands, Specials folder support

#### 2.3: Docker Integration
- [x] Multi-stage Dockerfile (frontend build + Python backend)
- [x] Docker Compose configuration (single container)
- [x] Volume mapping for media access
- [x] Environment variable management
- [x] Health checks

#### 2.4: Polish & Testing
- [x] Error handling improvements
- [x] Documentation
- [x] GitHub release

#### 2.5: Batch Operations & UX Improvements
- [x] **Queue All Button** — Movies and Shows parent pages get a "Queue All" button that queues all currently filtered items needing work
- [x] **Fix Queue All Episodes** — "Queue All" on a series detail page should only queue episodes that need work, not all episodes
- [x] **Multi-Select Batch Queue** — Checkboxes on movie posters and episode rows to select multiple items and queue them in one action
- [x] **Sort Options** — Sort filtered lists by: Needs Work (default, work items first), Title, Year/Episodes, Size
- [x] **Editable Config Page** — Make settings editable in the UI (audio, video, cleanup options including `anime_keep_original_audio`)
- [x] **Stop & Clear Queue Controls** — Cancel pending jobs and clear finished jobs from the Jobs page
- [x] **Complete Language Name Map** — Fix abbreviated language codes (IND, MAY, THA, VIE, GRE, RUM, SLO, BUL, EST, HEB, HIN, etc.) in episode tables and Analyze modal
- [x] **Show/Season Job Indicators** — Display queued/in-progress badge at the show and season level so users can see activity without expanding seasons
- [x] **Individual & Bulk Job Cancellation** — Cancel running/pending jobs individually or stop all at once
- [x] **Language Select Dropdown** — Searchable multi-select component for keep-languages configuration, replacing raw text input
- [x] **Fully Editable Settings Page** — All settings including Sonarr/Radarr URLs, API keys, worker count, job retention, and all advanced options editable from the UI
- [x] **Code Audit & Cleanup** — Removed dead code, unused dependencies, deduplicated frontend utilities into shared `format.ts`, optimized Dockerfile

## Phase 3: Community & Distribution

### Features
- **Event Viewer / Notification Center** - Real-time activity log in the UI showing conversion events, data protection alerts, watchdog actions, and worker lifecycle events with filterable severity levels (info, warning, error, critical)
- **Unraid Plugin** - Native Unraid Community Apps support
- **Multi-user support** - Role-based access
- **Hardware acceleration** - GPU encoding support
- **Cloud storage support** - S3, GCS, etc.
- **Plex/Jellyfin Webhooks** *(nice-to-have, low priority)* - Support users without Sonarr/Radarr; metadata sourcing would be a significant undertaking

### Distribution Channels
- Docker Hub
- GitHub Container Registry
- Unraid Community Apps
- TrueNAS plugins
- Proxmox templates

## MVP for Phase 2 (Docker + WebUI) ✓ (DELIVERED)

1. **Backend API** (FastAPI)
   - `/api/browse` - Scan library for convertible files
   - `/api/convert` - Queue conversion job
   - `/api/jobs` - List all jobs with status
   - `/api/jobs/{id}` - Get job details & progress

2. **Frontend** (SvelteKit)
   - Dashboard with job queue and live progress
   - Settings/config page
   - Responsive design

3. **Docker**
   - Single-container `compose.yml`
   - Pre-configured for common setups
   - README with quick start
