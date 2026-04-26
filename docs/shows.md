# Shows

The Shows page (`/shows`) browses your Sonarr TV library and lets you process individual episodes, full seasons, or entire series.

> Requires Sonarr to be configured in Settings with a valid URL and API key.

---

## Series List

The main Shows page displays a list of all series in your Sonarr library. Each row shows:

- Series title and year
- A **Queued** badge if any episode in the series has a pending job
- An **In Progress** badge if any episode in the series is currently being processed
- A **Needs Work** indicator showing how many episodes have conversions pending
- Episode count

### Series-Level Filters

| Filter | Shows |
|--------|-------|
| **All** | Every series in your Sonarr library |
| **Needs Work** | Series with at least one episode needing conversion |
| **Audio** | Series with episodes needing audio conversion |
| **Video** | Series with episodes needing video encoding |
| **Cleanup** | Series with episodes needing stream cleanup |
| **Legacy** | Series with episodes using legacy video codecs |
| **Anime** | Series detected as anime |

### Sort and Search

- Sort by **Needs Work** (default), **Title**, or **Episode Count**
- Search by series title

---

## Series Detail

Click a series to open the detail view, which shows all seasons and their episodes.

### Season Level

Each season row shows:

- Season number (or "Specials" for Season 0)
- Episode count
- A **Queued** or **In Progress** badge if any episode in the season has an active job
- A **Needs Work** count

Click a season row to expand it and show individual episodes.

### Episode Level

Each episode row shows:

- Episode title and number
- File path (shortened)
- File size
- A **Queued** or **In Progress** badge for this specific episode
- Work-needed badges: **Audio**, **Video**, **Cleanup**, **Legacy**
- Individual **Queue** and **Analyze** buttons

---

## Queuing Episodes

### Queue a Single Episode

Click **Queue** on any episode row to add that episode to the job queue. A type selector lets you choose:

- **Full** — run all applicable workers (audio, video, cleanup)
- **Audio** — audio conversion only
- **Video** — video encoding only
- **Cleanup** — stream cleanup only

### Queue an Entire Season

A **Queue All** button appears at the season level when the season has episodes that need work. It queues only the episodes in that season that have at least one conversion needed — it will not queue episodes that are already up to standard.

### Queue an Entire Series

A **Queue All** button appears at the series header level. It queues every episode across all seasons that needs at least one conversion.

### Multi-Select

Check the checkbox next to one or more episode rows to enter multi-select mode. Then click **Queue Selected** to queue all checked episodes at once.

---

## Analyze Modal

Click **Analyze** on any episode to open the stream detail modal. The modal has four tabs:

| Tab | Shows |
|-----|-------|
| **General** | Container format, duration, overall bitrate, file size |
| **Video** | Codec, profile, resolution, bit depth, framerate, pixel format, color space/transfer/primaries |
| **Audio** | Each track: codec, channels, language, bitrate, whether it would be converted |
| **Subtitles** | Each track: format, language, forced flag, SDH flag, whether it would be removed by cleanup |

---

## Refresh Library

The **Refresh Library** button on the series detail page triggers Sonarr to perform a full metadata refresh and rescan for that series. Use this when:

- Episode files were renamed or moved outside of Sonarr
- Artwork or metadata is stale
- You want Sonarr to pick up newly converted filenames

A confirmation dialog appears before the refresh is sent. remuXcode polls Sonarr until the refresh and rename commands complete before re-fetching the episode list.

---

## Job Status Indicators

Job status is shown at three levels simultaneously:

- **Series level** — a badge on the series row in the main list
- **Season level** — a badge on the season header inside the series detail
- **Episode level** — a badge on the individual episode row

This means you can see at a glance whether a series or season has active jobs without expanding every level.

---

## Auto-Refresh

The Shows page automatically re-fetches data when a running job completes, so status badges update without a manual reload.
