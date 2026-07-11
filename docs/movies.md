# Movies

The Movies page (`/movies`) browses your Radarr movie library and shows which files need processing based on your current conversion settings.

> Requires Radarr to be configured in Settings with a valid URL and API key.

---

## Library Grid

Movies are displayed as a poster grid fetched from Radarr. Each poster shows:

- Movie title and year
- A **Queued** badge (yellow) if a pending job exists for this file
- An **In Progress** badge (animated blue) if a job is currently running
- Work-needed badges showing what conversions are detected:
  - **Audio** — has unconverted DTS, DTS-HD, or other configured audio
  - **Video** — video codec meets your encode criteria
  - **Cleanup** — has tracks outside your keep-languages list
  - **Legacy** — has a legacy codec (VC-1, MPEG-2, MPEG-4/XviD/DivX)
- An **Open in Radarr** icon button (bottom-left of poster, only shown when Radarr is configured)

The badges are config-aware — they reflect your current Settings, so toggling a worker or changing codec preferences updates which movies show as needing work.

---

## Filters

The filter buttons at the top narrow the poster grid:

| Filter | Shows |
|--------|-------|
| **All** | Every movie in your Radarr library |
| **Needs Work** | Movies with at least one conversion needed |
| **Audio** | Has audio that would be converted (DTS, DTS-HD, DTS:X, TrueHD — based on your audio settings) |
| **Video** | Has video that meets your encode criteria (10-bit H.264, 8-bit H.264 if enabled, or a legacy codec if enabled) |
| **Cleanup** | Has audio or subtitle tracks outside your keep-languages list |
| **Legacy** | Has a legacy video codec (VC-1, MPEG-2, MPEG-4/XviD/DivX) |
| **Anime** | Detected as anime content |

---

## Sort

The sort control orders the grid:

| Sort | Description |
|------|-------------|
| **Needs Work** | Items needing conversion appear first (default) |
| **Title** | Alphabetical A–Z |
| **Year** | Newest or oldest first |
| **Size** | Largest or smallest file first |

---

## Search

The search box filters by movie title (case-insensitive, partial match).

---

## Movie Detail Panel

Click a poster to open the detail panel for that movie. It shows:

- Full file path
- Video codec, resolution, bit depth, HDR/color info
- Audio tracks — codec, channels, language, bitrate — with a note on what would be converted
- Subtitle tracks — format, language, forced/SDH flags — with a note on what would be removed
- A summary of which conversion phases would run

### Queuing from the Detail Panel

- **Queue** — add this movie to the job queue for full conversion
- Choose a specific job type: **Full**, **Audio**, **Video**, or **Cleanup**
- **Custom Encode** — opens the custom encode options for this movie (see below)
- **Open in Radarr** — opens this movie's Radarr page in a new tab (only shown when Radarr is configured)

---

## Custom Encode

The **Custom Encode** button (in the detail panel and the Analyze modal) queues a forced re-encode with one-off options that override your library-wide settings:

- **Target Resolution** — Original, 1080p, or 720p (downscale only; sources at or below the target are not upscaled)
- **HDR Handling**:
  - **Keep HDR** — HDR10 metadata is preserved; Dolby Vision RPU is stripped (HDR10 base layer kept)
  - **Keep HDR + DV** — re-encodes Dolby Vision sources (including Profile 7 remuxes) while retaining DV as **Profile 8.1** with HDR10 fallback. Profile 7 RPUs are converted with `dovi_tool` before the encode. Requires a DV source and software HEVC (libx265) encoding, and locks resolution to Original
  - **Strip to SDR** — tone-maps HDR (including DV) to BT.709 SDR
- The job always force-re-encodes the video; audio and cleanup phases still run per your configuration settings

Jobs queued this way show a **Custom Encode** badge on the Jobs page listing the chosen options.

---

## Multi-Select

Click the checkbox on one or more movie posters to enter multi-select mode:

- **Queue Selected** — adds all selected movies to the job queue
- The checkmark reappears on each poster. Clicking a selected poster deselects it.
- **Clear** (or deselect all) — exits multi-select mode

---

## Queue All

The **Queue All** button (top right) queues every movie currently visible in the filtered list that needs at least one conversion. It respects the active filter — for example:

- Filter = **Audio** → only movies needing audio conversion are queued
- Filter = **Needs Work** → all movies with any conversion needed are queued
- Filter = **All** → every movie in the library is queued (regardless of whether work is needed)

---

## Analyze Modal

Click **Analyze** on any movie to open a detailed stream inspection view. The modal has four tabs:

| Tab | Shows |
|-----|-------|
| **General** | Container format, duration, overall bitrate, file size |
| **Video** | Codec, profile, resolution, bit depth, framerate, pixel format, color space/transfer/primaries; embedded cover art thumbnails with a Remove button |
| **Audio** | Each track: codec, channels, language, bitrate, whether it would be converted |
| **Subtitles** | Each track: format, language, forced flag, SDH flag, whether it would be removed by cleanup |

### Fix Metadata

The **Audio** and **Subtitles** tabs let you edit track language and title directly in the modal. A language dropdown and title text field appear on each track card.

When any value differs from the original, a **Fix Metadata (N)** button appears at the bottom of the modal showing the number of pending changes. Clicking it queues a **Retag** job that corrects the track metadata without transcoding:

- **MKV files** — `mkvpropedit` rewrites only the relevant track header bytes in-place. No temp file, no size change, no quality loss.
- **Other containers** — ffmpeg remuxes with `-c copy` to apply the metadata, then atomically replaces the original.

The modal closes automatically once the job is queued.

### Cover Art

If the file contains an embedded cover art stream (attached picture), the **Video** tab shows a thumbnail preview of the image alongside its stream index and codec.

A **Remove** button strips all embedded cover art from the file immediately (no job is queued — the operation runs synchronously and the modal refreshes when done):

- ffmpeg remuxes the file with `-c copy`, excluding the attached picture stream(s)
- The original file is atomically replaced
- The Remove button is disabled if a conversion job is already queued or running for this file

This is useful for files where malformed cover art causes ffmpeg errors during conversion, or when you simply want a clean container without embedded images.

---

## Refresh Library

The **Refresh Library** button triggers Radarr to perform a full metadata refresh and rescan for all movies. Use this when:

- Files were added or modified outside of Radarr
- Poster art or metadata is stale
- You want Radarr to pick up renamed files

A confirmation dialog appears before the refresh is sent. The operation runs asynchronously in Radarr; the Movies page will auto-refresh when complete.

---

## Auto-Refresh

The Movies page automatically re-fetches data when a running job completes, so job status badges update without a manual reload.
