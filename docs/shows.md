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

### Series Header Actions

The series header always shows these buttons on the left:

- **Open in Sonarr** — opens this series in Sonarr in a new tab (only shown when Sonarr is configured)
- **Select All** — selects every episode that needs work across all seasons
- **Rescan** — re-reads this show's episodes from disk and refreshes the episode list
- **Custom Encode All** — opens the custom encode options for all episodes in the series (downscale / HDR tone-map)

The far-right slot changes based on selection state:

- **No selection** → **Queue All Episodes** — queues every episode across all seasons that needs at least one conversion
- **Selection active** → **Clear (N)** + **Queue N Selected** — clears or queues only the checked episodes

### Season Level

Each season row shows:

- Season number (or "Specials" for Season 0)
- Episode count
- A **Queued** or **In Progress** badge if any episode in the season has an active job
- A **Needs Work** count
- A gear icon to open **Custom Encode** options for the season
- A **Queue Season** button — queues all episodes in this season that need work. When episodes from this season are checked, the button reads **Queue N Selected** and queues only the checked episodes instead.

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

Each season header has a **Queue Season** button. It queues only the episodes in that season that have at least one conversion needed — already-converted episodes are skipped.

If you have checked episodes from that season, the button changes to **Queue N Selected** and queues only those specific episodes.

### Queue an Entire Series

**Queue All Episodes** in the series header queues every episode across all seasons that needs at least one conversion.

### Multi-Select

Check the checkbox next to any episode row to build a selection. The series header's far-right slot shows **Clear (N)** and **Queue N Selected** whenever any episodes are checked. You can keep selecting (including **Select All**) before queuing — no mode switch happens and no buttons disappear.

At the season level, **Queue Season** changes to **Queue N Selected** when checked episodes from that season exist, letting you queue a season subset without scrolling back up to the series header.

---

## Analyze Modal

Click **Analyze** on any episode to open the stream detail modal. The modal has four tabs:

| Tab | Shows |
|-----|-------|
| **General** | Container format, duration, overall bitrate, file size |
| **Video** | Codec, profile, resolution, bit depth, framerate, pixel format, color space/transfer/primaries |
| **Audio** | Each track: codec, channels, language, bitrate, whether it would be converted |
| **Subtitles** | Each track: format, language, forced flag, SDH flag, whether it would be removed by cleanup |

### Fix Metadata

The **Audio** and **Subtitles** tabs let you edit track language and title directly in the modal. A language dropdown and title text field appear on each track card.

When any value differs from the original, a **Fix Metadata (N)** button appears at the bottom of the modal showing the number of pending changes. Clicking it queues a **Retag** job that corrects the track metadata without transcoding:

- **MKV files** — `mkvpropedit` rewrites only the relevant track header bytes in-place. No temp file, no size change, no quality loss.
- **Other containers** — ffmpeg remuxes with `-c copy` to apply the metadata, then atomically replaces the original.

The modal closes automatically once the job is queued.

---

## Rescan

The **Rescan** button in the series header re-reads all episode files for this show from disk and refreshes the episode list immediately. Use it when:

- You have added, removed, or renamed files outside of remuXcode
- Badge counts look stale after a conversion completes
- You want to confirm the current state of files on disk without a full Sonarr library refresh

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
