# Settings Reference

The Settings page (`/config`) lets you configure every aspect of remuXcode without editing any files. Changes take effect immediately — no restart required.

`config/config.yaml` is the backing store. You can edit it directly if you prefer, but the UI is the recommended approach. The file uses `${ENV_VAR:-default}` syntax for environment variable overrides.

---

## API Key

Your API key is shown at the top of the Settings page. All API requests (except `/health`) require this key in an `X-API-Key` header.

The key is auto-generated on first start and stored in `config/.api_key`. You can view it at any time:

```bash
cat config/.api_key
```

---

## General

| Setting | Description | Default |
|---------|-------------|---------|
| **Concurrent Workers** | Number of jobs that run simultaneously. Each job runs one FFmpeg process. Software encoding saturates all CPU cores, so `1` is recommended unless using hardware acceleration. | `1` |
| **Job Retention (days)** | How long completed, failed, and cancelled jobs are kept in the database before automatic cleanup on startup. | `30` |

---

## Sonarr

| Setting | Description |
|---------|-------------|
| **Enabled** | Whether remuXcode uses the Sonarr API for metadata, rename triggers, and library browsing |
| **URL** | Full base URL of your Sonarr instance, e.g. `http://192.168.1.100:8989` |
| **API Key** | Found in Sonarr → Settings → General → Security |

When Sonarr is configured, remuXcode:
- Queries Sonarr for series metadata (poster art, genres, original language) to improve detection accuracy
- Triggers a rename command after converting an episode file so Sonarr updates its file path
- Powers the Shows page library browse

---

## Radarr

| Setting | Description |
|---------|-------------|
| **Enabled** | Whether remuXcode uses the Radarr API for metadata, rename triggers, and library browsing |
| **URL** | Full base URL of your Radarr instance, e.g. `http://192.168.1.100:7878` |
| **API Key** | Found in Radarr → Settings → General → Security |

When Radarr is configured, remuXcode:
- Queries Radarr for movie metadata (poster art, genres, original language, studio) to improve detection accuracy
- Triggers a rename command after converting a movie file so Radarr updates its file path
- Powers the Movies page library browse

---

## Video

### Enable / Codec

| Setting | Description | Default |
|---------|-------------|---------|
| **Enabled** | Master toggle for video encoding. When off, no video conversion runs regardless of other settings. | `true` |
| **Codec** | Target codec: `hevc` (H.265) or `av1`. HEVC has broader device support and faster encoding. AV1 offers ~30% better compression but is slower and has less hardware decoder coverage. | `hevc` |

### What to Convert

| Setting | Description | Default |
|---------|-------------|---------|
| **Convert 10-bit H.264** | Re-encode 10-bit AVC (High 10 profile) to HEVC/AV1. This is the primary use case — 10-bit H.264 has limited hardware decoder support on older devices. | `true` |
| **Convert 8-bit H.264** | Re-encode standard 8-bit AVC. Results in larger output files in most cases — only enable if you specifically need to eliminate H.264. | `false` |
| **Convert Legacy Codecs** | Re-encode VC-1, MPEG-2, MPEG-4/XviD/DivX, WMV. These codecs have poor support on modern devices and streaming clients. | `true` |
| **Anime Only** | When enabled, the video worker only processes files detected as anime. All other content is skipped. | `true` |

### Encoding Quality — HEVC (software: `libx265`)

| Setting | Description | Default (Anime) | Default (Live Action) |
|---------|-------------|-----------------|----------------------|
| **CRF** | Constant Rate Factor. Lower = better quality, larger file. Range 0–51. | `19` | `22` |
| **Preset** | Encoding speed vs. compression trade-off. Options: `ultrafast` → `veryslow`. Slower = better compression at same CRF. | `slow` | `medium` |
| **Tune** | Encoder optimization. `animation` improves anime compression. Leave blank for live action. | `animation` | *(blank)* |
| **Framerate** | Force output framerate. Use `24000/1001` for 23.976 fps. Leave blank to inherit from source. | `24000/1001` | *(blank)* |

### Encoding Quality — AV1 (software: `libsvtav1`)

| Setting | Description | Default (Anime) | Default (Live Action) |
|---------|-------------|-----------------|----------------------|
| **CRF** | Quality level. AV1 uses a shifted scale — CRF 20 is roughly equivalent to HEVC CRF 19. | `20` | `22` |
| **Preset** | SVT-AV1 preset 0–13. Lower = slower/better. | `4` | `5` |
| **Framerate** | Force output framerate. Leave blank for auto. | `24000/1001` | *(blank)* |

### Hardware Acceleration

| Setting | Description |
|---------|-------------|
| **Hardware Accelerator** | `none` (software), `qsv` (Intel Quick Sync), `vaapi` (Intel VAAPI), or `nvenc` (NVIDIA). Auto-detected at startup — if a GPU is detected, the best available method is selected automatically. |

When hardware acceleration is active:
- Encoding quality is controlled by a **Quality** value (similar to CRF) per accelerator
- The **Preset** setting (for QSV: `medium`) controls encoding speed
- Software fallback (`libx265`/`libsvtav1`) is used automatically if the GPU is unavailable

#### Hardware Quality Settings

| Accelerator | Setting | Anime Default | Live Action Default |
|-------------|---------|---------------|---------------------|
| QSV | Quality | `18` | `21` |
| VAAPI | Quality | `18` | `21` |
| NVENC | Quality | `18` | `21` |

### Advanced Output Settings

| Setting | Description | Default |
|---------|-------------|---------|
| **Pixel Format** | Output pixel format. `yuv420p10le` = 10-bit. Leave as-is unless you have a specific reason to change it. | `yuv420p10le` |
| **Profile / Level** | H.265 profile and level for compatibility. `main10` / `5.1` covers most devices. | `main10` / `5.1` |
| **VBV Max Rate** | Maximum bitrate cap in kbps. `0` = no cap (recommended). Set a value if you need to limit peak bitrate for a specific streaming device. | `0` |
| **VBV Buffer Size** | VBV buffer in kbps. `0` = no buffer cap. Used in conjunction with VBV Max Rate. | `0` |

---

## Audio

| Setting | Description | Default |
|---------|-------------|---------|
| **Enabled** | Master toggle for audio conversion. | `true` |
| **Anime Only** | Only process audio on anime content, skip everything else. | `false` |

### What to Convert

| Setting | Description | Default |
|---------|-------------|---------|
| **Convert DTS / DTS-HD** | Convert standard DTS, DTS-HD MA, and DTS-HD HRA to AC3/EAC3/AAC. | `true` |
| **Convert DTS:X** | Convert DTS:X (object-based, similar to Atmos). Disabled by default — DTS:X is high quality and has reasonable device support. | `false` |
| **Convert TrueHD** | Convert Dolby TrueHD (lossless). Off by default — TrueHD is a lossless Dolby format and generally desirable to keep. | `false` |

### Track Handling

| Setting | Description | Default |
|---------|-------------|---------|
| **Keep Original** | Keep the original DTS track alongside the new converted track. | `false` |
| **Keep Original (DTS:X)** | Keep the original DTS:X track alongside the converted track. | `false` |
| **Original as Secondary** | When keeping originals, place the converted track first so it is the default player selection. The original track becomes the secondary. | `true` |

### Format Preferences

| Setting | Description | Default |
|---------|-------------|---------|
| **Prefer AC3** | Use AC3 (Dolby Digital) for 5.1 surround instead of E-AC3. AC3 has the broadest device support. | `false` |

### Bitrate Caps

| Format | Setting | Max | Default |
|--------|---------|-----|---------|
| AC3 | `ac3_bitrate` | 640 kbps | `640` |
| E-AC3 | `eac3_bitrate` | 1536 kbps | `1536` |
| AAC (surround) | `aac_surround_bitrate` | — | `512` |
| AAC (stereo) | `aac_stereo_bitrate` | — | `320` |

### Conversion Logic

| Source | Channels | Target |
|--------|----------|--------|
| DTS / DTS-HD | 2ch (stereo) | AAC |
| DTS / DTS-HD | 6ch (5.1) | AC3 (or E-AC3 if Prefer AC3 is off) |
| DTS / DTS-HD | 8ch (7.1+) | E-AC3 |
| DTS:X | any | Same as above (when Convert DTS:X is on) |

---

## Cleanup

Stream cleanup removes audio and subtitle tracks that don't match your keep-languages list. It runs as a separate, lightweight pass (mux-only, no re-encoding).

| Setting | Description | Default |
|---------|-------------|---------|
| **Enabled** | Master toggle for stream cleanup. | `true` |
| **Anime Only** | Only run cleanup on anime content. | `false` |
| **Clean Audio** | Remove audio tracks in languages not in your keep list. | `true` |
| **Clean Subtitles** | Remove subtitle tracks in languages not in your keep list. | `true` |

### Keep Languages

The **Keep Languages** field is a searchable multi-select of ISO 639-2 language codes. Only tracks in these languages are kept. All others are removed.

Default: `eng` (English only).

To keep additional languages (e.g. French and Spanish alongside English), add `fre` and `spa`.

### Track Preservation

Certain tracks are always preserved regardless of language:

| Setting | Preserves | Default |
|---------|-----------|---------|
| **Keep Undefined** | Tracks with no language tag | `false` |
| **Keep Commentary** | Tracks flagged as commentary tracks | `true` |
| **Keep Audio Description** | Tracks flagged as audio description (for accessibility) | `true` |
| **Keep SDH** | Subtitles for the Deaf and Hard of Hearing | `true` |

### Anime Dual-Audio

| Setting | Description | Default |
|---------|-------------|---------|
| **Anime Keep Original Audio** | For anime content, keep the original-language audio track (e.g. Japanese) even if it's not in your keep-languages list. This enables dual-audio output (original + English). Has no effect on non-anime content. | `true` |

---

## Language Detection

remuXcode uses a multi-source chain to determine the original language of a file, which affects both the cleanup worker (which tracks to keep) and the video worker (anime detection).

| Setting | Description | Default |
|---------|-------------|---------|
| **NFO Enabled** | Read `.nfo` files alongside media files for original title and language metadata. | `true` |
| **API Enabled** | Query Sonarr/Radarr API for series/movie language and genre data. | `true` |
| **Path Fallback** | Infer language from folder path patterns (see below). | `true` |

Detection runs in order: NFO → API → path fallback → global default (`eng`).

### Path Patterns

Path patterns are regex patterns matched against the full file path. Each pattern maps to an ISO 639-2 language code. Default patterns:

| Pattern | Language |
|---------|---------|
| `(?i)/anime/` | Japanese (`jpn`) |
| `(?i)/korean/` | Korean (`kor`) |
| `(?i)/chinese/` | Chinese (`chi`) |
| `(?i)/spanish/` | Spanish (`spa`) |
| `(?i)/french/` | French (`fre`) |

Additional patterns can be added by editing `config/config.yaml` directly under `language.path_patterns`.

---

## Saving Settings

Changes are saved immediately when you click **Save** in each section. There is no page-wide save — each section saves independently. A brief confirmation toast appears after a successful save.

Settings take effect for the next job that starts. Jobs already in the queue use the settings active when they were created (for most parameters) but re-read config at the start of each phase.
