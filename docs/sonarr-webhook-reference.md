# Sonarr Webhook Payload Reference

Live-captured payloads from Sonarr v4. The official Sonarr docs are sparse and out of date — this documents what the API actually sends as of early 2026.

---

## Nomenclature Changes (undocumented by Sonarr)

| Old name | New name | Notes |
|---|---|---|
| On Import | **On File Import** | Per-episode event when a single file is imported |
| *(new)* | **On Import Complete** | Fires once after a full batch import (season pack, multi-episode) |
| On Upgrade | **On File Upgrade** | Fires when a file is replaced with a higher-quality version |

---

## Event Types

All Sonarr download events share `eventType: "Download"`. Distinguish them by:

| Scenario | `eventType` | `isUpgrade` | `release.releaseType` |
|---|---|---|---|
| On File Import (single episode) | `Download` | `false` / absent | `singleEpisode` |
| On Import Complete (batch/season pack) | `Download` | `false` / absent | `seasonPack` or `singleEpisode` |
| On File Upgrade | `Download` | `true` | `singleEpisode` |
| Test connection | `Test` | — | — |

---

## Key Payload Structure (Sonarr — all Download events)

```json
{
  "eventType": "Download",
  "isUpgrade": false,
  "instanceName": "Sonarr",
  "applicationUrl": "",
  "downloadClient": "SABnzbd",
  "downloadClientType": "SABnzbd",
  "downloadId": "SABnzbd_nzo_xxxxxxxx",
  "fileCount": 1,
  "sourcePath": "/downloads/usenet/complete/Show.Name.S01E01.../",
  "destinationPath": "/media/Shows/Show Name (2025)/Season 01",

  "series": {
    "id": 123,
    "title": "Show Name",
    "titleSlug": "show-name",
    "path": "/media/Shows/Show Name (2025)",
    "year": 2025,
    "type": "standard",
    "tvdbId": 123456,
    "tvMazeId": 12345,
    "tmdbId": 123456,
    "imdbId": "tt1234567",
    "originalLanguage": { "id": 8, "name": "Japanese" },
    "genres": ["Action", "Animation", "Anime", "Drama"],
    "tags": [],
    "images": [ ... ]
  },

  "episodes": [
    {
      "id": 10001,
      "episodeNumber": 1,
      "seasonNumber": 1,
      "title": "Episode Title",
      "overview": "Episode description...",
      "airDate": "2025-01-01",
      "airDateUtc": "2025-01-01T13:00:00Z",
      "seriesId": 123,
      "tvdbId": 9876543
    }
  ],

  "episodeFiles": [
    {
      "id": 10001,
      "relativePath": "Season 01/Show Name - S01E01 - Episode Title [WEBDL-1080p][...].mkv",
      "path": "/media/Shows/Show Name (2025)/Season 01/Show Name - S01E01 - Episode Title [WEBDL-1080p][...].mkv",
      "quality": "WEBDL-1080p",
      "qualityVersion": 1,
      "releaseGroup": "GroupName",
      "sceneName": "Show.Name.S01E01.Episode.Title.1080p...",
      "size": 1500000000,
      "dateAdded": "2025-01-01T12:00:00Z",
      "languages": [
        { "id": 8, "name": "Japanese" },
        { "id": 1, "name": "English" }
      ],
      "mediaInfo": {
        "audioChannels": 2.0,
        "audioCodec": "AAC",
        "audioLanguages": ["jpn", "eng"],
        "height": 1080,
        "width": 1920,
        "videoCodec": "x264",
        "videoDynamicRange": "",
        "videoDynamicRangeType": "",
        "subtitles": ["eng", "ara", "chi", "fre", "ger", "ind", "ita", "may", "por", "rus", "spa", "tha", "vie"]  # codespell:ignore tha,vie
      }
    }
  ],

  "release": {
    "releaseTitle": "Show.Name.S01E01.Episode.Title.1080p...",
    "indexer": "Prowlarr",
    "size": 1600000000,
    "releaseType": "singleEpisode"
  }
}
```

### Critical notes

- **`episodes[]` contains metadata only** — it does NOT contain file paths.
- **`episodeFiles[]` is always a plural array**, even for single-episode events (one item in the array). This is a breaking change from older Sonarr versions that used a singular `episodeFile` object.
- For **On Import Complete** (batch), `episodeFiles[]` contains all imported files in one payload. `episodes[]` will also have multiple entries.
- File path to use: `episodeFiles[n].path` (absolute path on disk).

---

## Useful Fields for Future Features

| Field | Location | Use case |
|---|---|---|
| `series.genres` | top level | Anime detection (`"Anime"` in genres list) |
| `series.originalLanguage.name` | top level | Anime detection (`"Japanese"`) |
| `episodeFiles[n].mediaInfo.subtitles` | episodeFiles array | Pre-check subtitle tracks before processing — skip if already clean |
| `episodeFiles[n].mediaInfo.audioLanguages` | episodeFiles array | Pre-check audio tracks — skip if already correct |
| `episodeFiles[n].mediaInfo.audioCodec` | episodeFiles array | Identify codec before audio worker runs |
| `episodeFiles[n].mediaInfo.videoCodec` | episodeFiles array | Identify codec before video worker runs |
| `episodeFiles[n].mediaInfo.videoDynamicRange` | episodeFiles array | HDR detection |
| `isUpgrade` | top level | Skip re-processing logic / different job type for upgrades |
| `release.releaseType` | release object | Distinguish single vs. season pack |
| `downloadClient` | top level | Logging / source tracking |

---

## Radarr Payload (movies)

Radarr uses `movieFile` (singular object, not an array) at the top level. The `movie` key signals a Radarr payload.

```json
{
  "eventType": "Download",
  "movie": {
    "id": 456,
    "title": "Movie Title",
    "year": 2024,
    "tmdbId": 123456,
    "imdbId": "tt1234567",
    "folderPath": "/media/Movies/Movie Title (2024)"
  },
  "movieFile": {
    "id": 789,
    "relativePath": "Movie Title (2024) [Bluray-1080p][...].mkv",
    "path": "/media/Movies/Movie Title (2024)/Movie Title (2024) [Bluray-1080p][...].mkv",
    "quality": "Bluray-1080p",
    "size": 12000000000,
    "mediaInfo": { ... }
  },
  "release": { ... }
}
```

File path to use: `movieFile.path`.
