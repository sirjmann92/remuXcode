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
  - Implement logrotate or Python logging.handlers.RotatingFileHandler
  - Separate log files: conversions.log, errors.log, webhook.log
  - Configurable retention (days/size limits)
  - Compressed old logs (gzip)
  - User-accessible log viewer endpoint
  - Log levels per component (DEBUG for troubleshooting, INFO for production)
  
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

## Phase 1.7: Configuration & Control Improvements

### Goal
Improve consistency and clarity of content filtering and processing controls.

### Planned Features
- [ ] **Global Content Filter (ANIME_ONLY refactor)**
  - Current issue: `ANIME_ONLY` only affects video encoding, audio/cleanup ignore it
  - Make `ANIME_ONLY` a **global** content filter applied to ALL workers
  - When `ANIME_ONLY=true`: All workers (video, audio, cleanup) skip non-anime files
  - When `ANIME_ONLY=false`: Process all content types
  - Clear hierarchy: Content filter → Worker enable flags → Worker-specific settings
  
- [ ] **Future: Extended Content Type Filtering**
  - Consider adding more granular content type controls:
    - Option 1: List-based `CONTENT_TYPES=anime,live_action,animated`
    - Option 2: Individual flags `PROCESS_ANIME`, `PROCESS_LIVE_ACTION`, etc.
  - Only implement if needed based on user feedback

### Architecture
```
ANIME_ONLY (global content filter)
├─ true  → Only process anime files (all workers check this)
└─ false → Process all content (anime + non-anime)

Then independently control operations:
├─ VIDEO_ENCODING_ENABLED     → Enable/disable video encoding
├─ AUDIO_CONVERSION_ENABLED    → Enable/disable audio conversion
├─ STREAM_CLEANUP_ENABLED      → Enable/disable stream cleanup
└─ [worker-specific settings]  → CRF, presets, bitrates, etc.
```

### Implementation Notes
- Move anime detection check into `process_file()` before workers run
- All workers respect global content filter (exit early if filtered)
- Update documentation to clarify the configuration hierarchy
- Backward compatible: Default behavior unchanged (`ANIME_ONLY=true` still default)

## Phase 2: Docker + WebUI (NEXT)

### Architecture
```
┌─────────────────────────────────────────────────┐
│  Web UI (React/Vue + Tailwind)                  │
│  - Dashboard with stats                         │
│  - Queue management                             │
│  - Live progress monitoring                     │
│  - Settings configuration                       │
└─────────────────┬───────────────────────────────┘
                  │ REST API
┌─────────────────▼───────────────────────────────┐
│  FastAPI Backend                                │
│  - Task queue (Celery/RQ)                       │
│  - WebSocket for real-time updates              │
│  - Job scheduling                               │
│  - Statistics & reporting                       │
└─────────────────┬───────────────────────────────┘
                  │
┌─────────────────▼───────────────────────────────┐
│  DTS Converter Core (Current Python Scripts)    │
│  - File processing                              │
│  - FFmpeg operations                            │
│  - API integration                              │
└──────────────────────────────────────────────────┘
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
- **FastAPI** - Modern Python web framework
- **Celery** or **RQ** - Task queue for async processing
- **Redis** - Queue broker & caching
- **SQLite/PostgreSQL** - Job history & statistics
- **WebSockets** - Real-time updates

#### Frontend
- **React** or **Vue.js** - Modern reactive UI
- **Tailwind CSS** - Styling
- **Chart.js** - Statistics visualization
- **React Query** - API state management

#### Infrastructure
- **Docker Compose** - Multi-container orchestration
- **Nginx** - Reverse proxy
- **Volume mounts** - Media library access

### Docker Compose Structure

```yaml
version: '3.8'

services:
  dts-converter-web:
    build: ./web
    ports:
      - "8080:80"
    environment:
      - API_URL=http://api:5000
    depends_on:
      - api
  
  dts-converter-api:
    build: .
    ports:
      - "5000:5000"
    volumes:
      - /mnt/storage1:/media/storage1
      - /mnt/storage2:/media/storage2
      - ./config:/config
      - ./logs:/logs
    environment:
      - SONARR_URL=${SONARR_URL}
      - SONARR_API_KEY=${SONARR_API_KEY}
      - RADARR_URL=${RADARR_URL}
      - RADARR_API_KEY=${RADARR_API_KEY}
      - REDIS_URL=redis://redis:6379
    depends_on:
      - redis
      - db
  
  dts-converter-worker:
    build: .
    command: celery -A app.tasks worker --loglevel=info
    volumes:
      - /mnt/storage1:/media/storage1
      - /mnt/storage2:/media/storage2
      - ./config:/config
      - ./logs:/logs
    environment:
      - REDIS_URL=redis://redis:6379
    depends_on:
      - redis
  
  redis:
    image: redis:alpine
    volumes:
      - redis-data:/data
  
  db:
    image: postgres:alpine
    volumes:
      - db-data:/var/lib/postgresql/data
    environment:
      - POSTGRES_DB=dts_converter
      - POSTGRES_PASSWORD=${DB_PASSWORD}

volumes:
  redis-data:
  db-data:
```

### Development Phases

#### 2.1: Backend API (Week 1-2)
- [ ] FastAPI project setup
- [ ] Task queue integration (Celery)
- [ ] Core endpoints (scan, convert, queue)
- [ ] WebSocket support for progress
- [ ] Database models for job history
- [ ] API documentation (OpenAPI/Swagger)

#### 2.2: Frontend UI (Week 2-3)
- [ ] React/Vue project setup
- [ ] Dashboard layout
- [ ] Queue management interface
- [ ] Settings configuration page
- [ ] Real-time progress updates
- [ ] Responsive design

#### 2.3: Docker Integration (Week 3-4)
- [ ] Dockerfile for API
- [ ] Dockerfile for frontend
- [ ] Docker Compose configuration
- [ ] Volume mapping for media access
- [ ] Environment variable management
- [ ] Health checks

#### 2.4: Polish & Testing (Week 4-5)
- [ ] End-to-end testing
- [ ] Error handling improvements
- [ ] Documentation
- [ ] Demo environment
- [ ] GitHub release

## Phase 3: Community & Distribution

### Features
- **Unraid Plugin** - Native Unraid Community Apps support
- **Home Assistant Integration** - Control via HA
- **Plex/Jellyfin Webhooks** - Trigger on media added
- **Multi-user support** - Role-based access
- **Cloud storage support** - S3, GCS, etc.
- **Hardware acceleration** - GPU encoding support

### Distribution Channels
- Docker Hub
- GitHub Container Registry
- Unraid Community Apps
- TrueNAS plugins
- Proxmox templates

## MVP for Phase 2 (Docker + WebUI)

**Absolute minimum for first release:**

1. **Backend API** (FastAPI)
   - `/api/scan` - Scan directory for DTS files
   - `/api/convert` - Queue conversion job
   - `/api/jobs` - List all jobs
   - `/api/jobs/{id}` - Get job status
   - WebSocket `/ws/jobs` - Real-time updates

2. **Frontend** (React)
   - Dashboard page with stats
   - Simple queue view
   - Settings page (API keys, preferences)
   - Progress indicator

3. **Docker**
   - Single docker-compose.yml
   - Pre-configured for common setups
   - README with quick start

**Timeline: 2-3 weeks for MVP**

## Want to Build This Together?

This would be a great open-source project! Potential name ideas:
- **DTS-Destroyer** 🎬
- **AudioRefactor** 🎵
- **TranscodeHub** 🔄
- **StreamFixer** 📡
- **DTS2Everything** 🌟

Let me know when you're ready to start Phase 2, and we can kick it off! 🚀
