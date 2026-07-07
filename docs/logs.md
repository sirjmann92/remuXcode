# Logs

The Logs page (`/logs`) tails remuXcode's application log directly in the browser — no shell or `docker logs` access needed. It shows startup/shutdown, job lifecycle events, webhook and Sonarr/Radarr integration activity, configuration changes, and errors.

This is **not** the same as the per-job log panel on the [Jobs page](jobs.md) — that shows the `ffmpeg`/`mkvpropedit` commands and output for one specific conversion. This page shows the application's overall activity across every job, worker, and integration.

The page auto-refreshes every 5 seconds and shows up to the 2,000 most recent log entries.

---

## Log Level

The **Log Level** dropdown (top-left) controls what the application actually writes to the log going forward — `DEBUG`, `INFO`, `WARNING`, or `ERROR`.

- Changing it takes effect **immediately**, with no container restart required
- The new value is persisted to `config/config.yaml` (`general.log_level`), so it survives restarts
- A **Saved** toast confirms the change, matching the same toast used on the [Settings page](settings.md)

Use `DEBUG` temporarily when troubleshooting something specific (e.g. a webhook that isn't firing, or a Sonarr/Radarr API call that's failing) — it's noticeably more verbose, so switch back to `INFO` once you're done.

> Before this setting existed, changing verbosity required uncommenting `LOG_LEVEL=DEBUG` in `compose.yml` and restarting the container. That environment variable still works as the initial default on first run, but the persisted setting here takes over after that.

---

## Level Filters

The row of chips (**DEBUG** / **INFO** / **WARNING** / **ERROR** / **CRITICAL**) is a separate, purely client-side **display** filter over whatever is currently in the log file — it does not change what gets captured.

By default, the chips match your current **Log Level** (e.g. if the level is `INFO`, the `INFO`/`WARNING`/`ERROR`/`CRITICAL` chips start active and `DEBUG` starts off), and changing the Log Level dropdown re-syncs them automatically so the effect is visible right away.

You can still toggle chips independently at any time — for example, turning **DEBUG** on will show any `DEBUG` lines already sitting in the log file, even if the effective Log Level is currently `INFO`. This is expected: the log file is a rotating history (10 MB per file, 5 backups), so entries logged under a more verbose setting earlier remain visible in the file — and therefore toggleable here — until they eventually roll off.

---

## Search

The search box filters visible entries by a case-insensitive substring match against the log message text. It combines with the level chips (both filters apply together).

---

## Log Entries

Each row shows:

| Column | Description |
|--------|-------------|
| **Timestamp** | Local time the line was logged (millisecond precision) |
| **Level** | Color-coded severity — Debug (dim), Info (blue), Warning (yellow), Error (red), Critical (bold red) |
| **Logger** | The module or component that produced the entry (e.g. `main`, `config`, `cpu_affinity`, `video`) |
| **Message** | The log message itself |

Multi-line output (e.g. a Python traceback) is grouped under the entry that started it, rather than showing as separate unlabeled lines.

The panel auto-scrolls to the newest entry as new lines arrive. If you scroll up to read older entries, auto-scroll pauses until you scroll back to the bottom.

---

## Download

The **Download** button saves everything currently loaded in the panel (i.e. the same entries the page fetched, before search/level filtering) to a `remuxcode.log` file — useful for attaching to a bug report.

---

## Navigation

See the [Dashboard](dashboard.md#navigation) page for the full list of navbar links.
