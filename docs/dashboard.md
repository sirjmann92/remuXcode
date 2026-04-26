# Dashboard

The dashboard (`/`) is the home page of remuXcode. It gives you an at-a-glance view of everything happening in the queue and a live feed of recent activity. It auto-refreshes every 3 seconds.

---

## Stats Bar

The top section shows live system status:

- **Jobs Processed** — total completed jobs across all time
- **Active** — number of currently running jobs
- **Queued** — number of pending jobs waiting to start
- **Storage Saved** — cumulative file size reduction across all completed conversions

Worker status indicators (Video, Audio, Cleanup) show which workers are enabled in your current Settings. If Sonarr and/or Radarr are configured, their connection status appears as a colored dot next to each integration name.

---

## In Progress

When a job is running, it appears in the **In Progress** section with:

- File name (basename)
- Current phase: **Audio**, **Video**, or **Cleanup**
- Live progress bar (percentage complete)
- Elapsed time since the job started
- Before/after file size delta displayed on each phase as it completes

If a job is running in a phase that doesn't apply to a particular file (e.g. the video worker skips a file that doesn't need encoding), that phase completes instantly and the job advances to the next one.

---

## Queued

Pending jobs appear in the **Queued** section in the order they will be processed.

### Drag-and-Drop Reordering

If more than one job is queued, you can drag jobs to reorder the queue:

1. Hover over a queued job card — a grip handle appears on the left edge
2. Drag the card to its new position
3. The order is saved to the backend immediately and persists through container restarts

After a reorder, the auto-refresh poll is suppressed for 2 seconds to prevent the updated order from being overwritten by a stale poll response.

---

## Recent Activity

The **Recent Activity** section at the bottom shows the most recently completed, failed, and cancelled jobs. Click any job card to expand it and see full phase-level results (which streams were converted, size changes, any errors).

---

## Navigation

The navbar links to all pages:

| Page | Path | Purpose |
|------|------|---------|
| Dashboard | `/` | Live queue and activity |
| Movies | `/movies` | Browse and process movie library |
| Shows | `/shows` | Browse and process TV library |
| Jobs | `/jobs` | Full job history and controls |
| Settings | `/config` | All configuration |
