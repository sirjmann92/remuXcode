# Integrations

remuXcode integrates with Sonarr and Radarr in two ways: **webhooks** (automated processing when content is imported) and the **API** (metadata, poster art, library browse, and post-conversion rename triggers).

Both integrations are optional but highly recommended. remuXcode works in fully manual mode without them, but webhook automation is the primary intended workflow.

---

## Recommended Workflow

### Use Remux Releases

The single most impactful thing you can do for quality is configure Sonarr and Radarr to prefer **remux** releases — full disc remuxes with no re-encoding. A remux is a lossless copy of the original Blu-ray stream, retaining the original video bitrate, lossless audio (TrueHD, DTS-HD MA), and all tracks.

**Why this matters:** Video compression is lossy. Re-encoding an already-compressed video introduces *generation loss* — the encoder must make a second round of approximations on top of the first, compounding artifacts and degrading quality in ways that are not recoverable. Starting from a remux means remuXcode is always working from the best possible source.

**What remuXcode then does:**
- Converts lossless/incompatible audio (DTS-HD MA, TrueHD) to broadly-compatible formats (AC3, EAC3, AAC) without touching the video
- Re-encodes video only when the codec is incompatible (10-bit H.264, legacy VC-1/MPEG-2) — and only once, from the lossless source
- Strips unwanted audio and subtitle tracks in a mux-only pass (no re-encoding)

### Suggested Sonarr/Radarr Quality Profiles

- Add a **Remux** quality tier at the top of your profile
- Prefer releases tagged `REMUX`, `BDRemux`, or `BluRay REMUX`
- Set remuXcode's webhook to fire on **On Import Complete** (Sonarr) or **On Import / On Upgrade** (Radarr) so new content is processed automatically

---

## Configuration

Sonarr and Radarr connection details are set directly in `config/config.yaml` — they are not editable from the Settings UI. Edit the file (on the host at `./config/config.yaml`) and restart the container, or edit it while the container is running (changes are read on the next request).

```yaml
sonarr:
  url: "http://192.168.1.100:8989"
  api_key: "your-sonarr-api-key"

radarr:
  url: "http://192.168.1.100:7878"
  api_key: "your-radarr-api-key"
```

**Finding your API key:**
- Sonarr: Settings → General → Security → API Key
- Radarr: Settings → General → Security → API Key

Once configured, connection status appears on the Dashboard (colored dot next to the integration name) and the Shows/Movies pages become available.

---

## Webhook Setup

### Sonarr

In Sonarr → **Settings → Connect → Add → Webhook**:

| Field | Value |
|-------|-------|
| Name | `remuXcode` (or any label) |
| URL | `http://<host>:7889/api/webhook` |
| Method | POST |
| Triggers | **On Import Complete** |
| Headers | `X-API-Key: <your key from config/.api_key>` |

> **On Import Complete** fires once after a full batch of files are imported (e.g. an entire season), passing all episode file paths in a single payload. This is more efficient than `On File Import` + `On File Upgrade`, which fire per-episode.
>
> You can also enable **On File Import** and **On File Upgrade** for immediate per-episode triggering if you prefer not to wait for the batch event.

### Radarr

In Radarr → **Settings → Connect → Add → Webhook**:

| Field | Value |
|-------|-------|
| Name | `remuXcode` (or any label) |
| URL | `http://<host>:7889/api/webhook` |
| Method | POST |
| Triggers | On Import, On Upgrade |
| Headers | `X-API-Key: <your key from config/.api_key>` |

Click **Test** on either webhook — you should get a `{"status":"ok"}` response.

---

## What Each Integration Enables

### Sonarr

| Feature | Requires Sonarr |
|---------|----------------|
| **Shows page** — browse series/episodes with poster art and work-needed badges | Yes |
| **Webhook automation** — process episodes automatically on import | Yes (webhook configured) |
| **Post-conversion rename** — after remuXcode converts a file, Sonarr is notified to update its file path | Yes |
| **Metadata for detection** — series original language, genres (used for anime detection) | Yes |
| **Open in Sonarr** links — deep links from the Shows page directly to the series in Sonarr | Yes |

### Radarr

| Feature | Requires Radarr |
|---------|----------------|
| **Movies page** — browse movies with poster art and work-needed badges | Yes |
| **Webhook automation** — process movies automatically on import/upgrade | Yes (webhook configured) |
| **Post-conversion rename** — after remuXcode converts a file, Radarr is notified to update its file path | Yes |
| **Metadata for detection** — movie original language, genres, studio (used for anime detection) | Yes |
| **Open in Radarr** links — deep links from the Movies page and detail modal directly to the movie in Radarr | Yes |

---

## Path Mapping

remuXcode must be able to reach the same files that Sonarr/Radarr reference in webhook payloads. The simplest setup is mounting your media at **the same path inside all containers**:

```yaml
# All three containers mount /share at the same host path
volumes:
  - /mnt/nas:/share:rw
```

If your paths differ between containers, configure `path_mappings` in `config.yaml`:

```yaml
path_mappings:
  - container: /share
    host: /mnt/nas
```

This translates paths from webhook payloads before remuXcode tries to access the file.

---

## Sonarr Webhook Reference

For full details on the Sonarr webhook payload format (all fields, event types, and how remuXcode processes them), see [sonarr-webhook-reference.md](sonarr-webhook-reference.md).
