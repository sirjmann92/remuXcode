# Jobs

The Jobs page (`/jobs`) is the full history and control center for all conversion jobs. It shows every job that has been run, is currently running, or is waiting to run — with detailed per-phase results, error information, and a complete set of management controls.

The page auto-refreshes every 5 seconds while viewing recent or active jobs.

---

## Job Cards

Each job is displayed as a card showing:

- **File name** and a link to the file's entry in the Movies or Shows page
- **Status badge** — Pending, Running, Completed, Failed, or Cancelled
- **Job type** — the conversion type: Full, Audio, Video, or Cleanup
- **Source** — how the job was created: Webhook, API, or Batch
- **Media type** — Movie or Episode
- **Start time** and **completion time** (locale-formatted)
- **Elapsed time** for completed jobs

### Phase Results

Clicking a job card (or when `detailed` view is active) expands it to show per-phase results:

| Phase | What it shows |
|-------|---------------|
| **Audio** | Streams converted, source codec → target codec, before/after file size |
| **Video** | Encoder used, CRF/quality setting, before/after file size |
| **Cleanup** | Tracks removed (codec, language), before/after file size |

Each phase shows a size delta (e.g. `−12% · 4.2 GB → 3.7 GB`).

If a phase produced no output (nothing to convert), it is marked as skipped or shows "No work needed."

### Errors

If a job fails, the full error message is displayed in a monospace block. A **Copy** button (clipboard icon) copies the error text to your clipboard — useful for filing bug reports or troubleshooting with FFmpeg output.

---

## Status Filter

The filter bar at the top lets you view jobs by status:

| Button | Shows |
|--------|-------|
| **All** | Every job in history |
| **Running** | Currently processing |
| **Pending** | Waiting in queue |
| **Completed** | Successfully finished |
| **Failed** | Finished with an error |
| **Cancelled** | Manually cancelled |

Each button shows a count badge when there are jobs in that state.

---

## Search

The search box filters by file path (case-insensitive, partial match).

---

## Advanced Filters

Click the **Filters** button to expand additional filter options:

| Filter | Options |
|--------|---------|
| **Worker** | All / Video / Audio / Cleanup |
| **Media** | All / Movies / Episodes |
| **Source** | All / Webhook / API / Batch |
| **From** | Date range start (inclusive) |
| **To** | Date range end (inclusive) |

Multiple filters can be combined. An active filter indicator (dot) appears on the Filters button. Click **Clear** to reset all advanced filters.

---

## Drag-and-Drop Queue Reordering

When jobs are in the **Pending** state and the filter shows them, you can reorder the queue by dragging:

1. Hover over a pending job — a grip handle appears on the left edge
2. Drag the card up or down to the desired position
3. The new order is saved to the backend immediately and persists through restarts

After a reorder, auto-refresh is suppressed for 2 seconds to prevent the updated order from being overwritten.

> Only pending jobs can be reordered. Running, completed, failed, and cancelled jobs cannot be dragged.

---

## Job Controls

Three action buttons appear in the top-right area of the job list when relevant:

| Button | Appears when | Action |
|--------|-------------|--------|
| **Stop Current** | `running > 0` | Cancels all running jobs immediately (kills the FFmpeg process) |
| **Clear Pending** | `pending > 0` | Cancels all pending jobs in the queue (does not affect running jobs) |
| **Delete Completed** | `completed + failed + cancelled > 0` | Permanently deletes all finished jobs from the database |

**Delete Completed** opens a confirmation dialog first. It shows the number of jobs that will be deleted and warns that the action cannot be undone.

---

## Individual Job Actions

Each job card has action buttons depending on its status:

| Status | Available actions |
|--------|------------------|
| **Pending** | Cancel (removes from queue) |
| **Running** | Cancel (stops FFmpeg, marks as cancelled) |
| **Completed** | Delete (removes from history) |
| **Failed** | Delete (removes from history) |
| **Cancelled** | Delete (removes from history) |

---

## Load More

The Jobs page loads up to 100 jobs at a time. If there are more, a **Load more** button appears at the bottom. Clicking it appends the next page. Note that loading more jobs disables auto-refresh for that session (to prevent the list from jumping).

---

## Job Persistence

All jobs are stored in `config/jobs.db` (SQLite). On container restart:

- **Pending** jobs are automatically resumed in queue order
- **Running** jobs (interrupted mid-encode) are re-queued and started again
- **Completed / Failed / Cancelled** jobs remain in history until deleted

Jobs older than the configured **Job Retention** period are pruned automatically on startup (default: 30 days).
