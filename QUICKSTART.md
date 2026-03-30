# Quick Start Guide

## Prerequisites

- Docker + Docker Compose
- Sonarr and/or Radarr running in Docker (or accessible over the network)
- Media files on a shared NAS or local volume

---

## 1. Configure

Copy the example environment file and fill in your Sonarr/Radarr details:

```bash
cp .env.example .env
nano .env
```

Set at minimum:

```ini
SONARR_URL=http://your-sonarr-host:8989
SONARR_API_KEY=your_sonarr_api_key

RADARR_URL=http://your-radarr-host:7878
RADARR_API_KEY=your_radarr_api_key
```

Leave `REMUXCODE_API_KEY` blank — a key is auto-generated on first run and saved to `config/.api_key`.

---

## 2. Create your `compose.yml`

`compose.yml` is not included in the repo — create your own. Mount your media at the **same paths Sonarr/Radarr use internally** so webhook paths work without any translation:

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
      - /mnt/yournas:/share:rw        # matches Sonarr/Radarr's /share
      - /mnt/yournas2:/share-exp:rw   # add more mounts as needed
    environment:
      - TZ=America/Chicago
      - REMUXCODE_API_KEY=${REMUXCODE_API_KEY}
      - SONARR_URL=${SONARR_URL}
      - SONARR_API_KEY=${SONARR_API_KEY}
      - RADARR_URL=${RADARR_URL}
      - RADARR_API_KEY=${RADARR_API_KEY}
    restart: unless-stopped
```

---

## 3. Start

```bash
docker compose up -d
```

On first run, `config/config.yaml` is created automatically from the built-in template.
Edit it to adjust encoding quality, audio settings, language preferences, etc.

---

## 4. Verify

```bash
# Health check
curl http://localhost:7889/health

# View your API key (auto-generated on first run)
cat config/.api_key
```

---

## 5. Configure Sonarr / Radarr Webhook

In Sonarr/Radarr → **Settings → Connect → Add → Webhook**:

| Field | Value |
|-------|-------|
| URL | `http://YOUR_HOST_IP:7889/api/webhook` |
| Method | POST |
| Triggers | On Import, On Upgrade |
| Headers | `X-API-Key: <your key from config/.api_key>` |

Click **Test** — you should see a `{"status":"ok"}` response.

---

## 6. Watch It Work

```bash
# Live logs
docker compose logs -f

# Check the job queue in the UI
open http://localhost:7889
```

---

## Troubleshooting

**Container won't start?**
```bash
docker compose logs --tail=50
```

**Webhook test fails?**
```bash
# Confirm service is up
curl http://localhost:7889/health

# Check your API key
cat config/.api_key
curl -H "X-API-Key: $(cat config/.api_key)" http://localhost:7889/api/jobs
```

**Files not being found?**
- Verify your volume mounts in `compose.yml` match the paths Sonarr/Radarr report in the webhook payload
- Check logs for "File not found" errors

**Restart after config changes:**
```bash
docker compose restart
```

**Rebuild after code changes:**
```bash
docker compose up -d --build
```
