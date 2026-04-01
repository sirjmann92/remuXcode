# Quick Start Guide

## Prerequisites

- Docker + Docker Compose
- Sonarr and/or Radarr running in Docker (or accessible over the network)
- Media files on a shared NAS or local volume

---

## 1. Create your `compose.yml`

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
    restart: unless-stopped
```

---

## 2. Start

```bash
docker compose up -d
```

On first run, `config/config.yaml` is created automatically with sensible defaults.
An API key is auto-generated and stored in `config/.api_key`.

---

## 3. Configure via Settings Page

Open `http://localhost:7889/config` and fill in your **Sonarr/Radarr connection details** (URL and API key). All other settings — encoding, audio, cleanup, languages — can be tuned here too.

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

### Sonarr

In Sonarr → **Settings → Connect → Add → Webhook**:

| Field | Value |
|-------|-------|
| URL | `http://YOUR_HOST_IP:7889/api/webhook` |
| Method | POST |
| Triggers | **On Import Complete** |
| Headers | `X-API-Key: <your key from config/.api_key>` |

> **On Import Complete** fires once after a full batch of files are imported (e.g. an entire season), passing all episode file paths in a single payload. This is more efficient than `On Import` + `On Upgrade`, which fire individually per episode.

### Radarr

In Radarr → **Settings → Connect → Add → Webhook**:

| Field | Value |
|-------|-------|
| URL | `http://YOUR_HOST_IP:7889/api/webhook` |
| Method | POST |
| Triggers | On Import, On Upgrade |
| Headers | `X-API-Key: <your key from config/.api_key>` |

Click **Test** on either — you should see a `{"status":"ok"}` response.

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
