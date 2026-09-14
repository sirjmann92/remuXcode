# remuXcode — Contributor & Agent Guide

Conventions and hard-won operational knowledge for anyone working on this
repository, human or AI. Environment-specific details (hosts, addresses,
credentials) deliberately live outside this file — see the note at the end.

---

## Build, Test, Lint

**This is a Docker-only application.** Every change — backend or frontend —
requires a full image rebuild. There is no hot-reload or live-mount for
production code.

```bash
# Build and deploy (required after ANY code change)
docker compose up -d --build

# Tests (local venv)
source .venv/bin/activate
pytest tests/

# Lint — run what CI runs, NOT a --files subset
pre-commit run --all-files
```

### Linting rules that matter

- **Always `pre-commit run --all-files` before committing.** `--files <subset>`
  is not a substitute: CI runs `--all-files`, and a subset run has already let a
  `ruff format` failure through to a red `main`.
- A formatting hook that **modifies** a file fails the run *by design*, even
  though the fix itself succeeded. Re-stage and re-run; it is not a real error.
- CI runs **`mypy ./backend/`** as a **separate step**, so a clean pre-commit
  alone does not mean CI is green.
- `tests/test_workers.py` is a manual script requiring a real media file
  argument, not a pytest suite — it errors under plain collection. Run
  `pytest tests/ --ignore=tests/test_workers.py`.

### Biome's version is pinned in five places

They must all agree, and `scripts/check_biome_pins.py` (wired into pre-commit)
fails the run when they don't:

| Location |
| --- |
| `frontend/package.json` → `@biomejs/biome` |
| `frontend/package-lock.json` → resolved entry |
| `.pre-commit-config.yaml` → `rev` |
| `.pre-commit-config.yaml` → `additional_dependencies` |
| `biome.json` → `$schema` URL |

The pre-commit `rev`/`additional_dependencies` pair is what actually gates CI:
the hook runs its own pinned copy of Biome in an isolated environment and never
touches `frontend/node_modules`. `npm run lint` uses the npm pin but CI never
invokes it. When these drift, **nothing fails** — the gate just silently
enforces an older ruleset. That is how the repo once ended up enforcing 2.2.4
against a declared 2.5.11, nine minor releases apart.

Dependabot bumps the first two and **cannot** touch the other three, so expect
`check-biome-pins` to fail on the first run after a Biome bump. Update all five
together.

`frontend/node_modules` can also be stale relative to the lockfile — run
`npm ci` before trusting any local lint result. A stale tree is what masked the
nine-version discrepancy above.

---

## Operational Hazards — Sonarr/Radarr

All confirmed by live testing, several of them the expensive way. Read before
touching library files or *arr records.

### `DELETE /api/v3/moviefile/{id}?deleteFiles=false` DELETES THE FILE

The parameter does **not** do what its name implies. This call permanently
destroyed a real 2.7 GB movie file: Radarr logged `MediaFileDeletionService:
Deleting movie file` and, with no Recycle Bin configured, `deleting
permanently`. Only a NAS-level snapshot made recovery possible.

**Never use *arr DELETE endpoints to clean up a database record.** To make an
*arr forget a file, rename or move the file and trigger a refresh — the record
reconciles on its own. More generally: never trust a destructive API flag's
name without first testing it on something disposable.

### Unmonitor before renaming or moving files out from under an *arr

Renaming files directly on disk makes the *arr treat those episodes as missing.
Sonarr responded by firing `ReleaseSearchService: Searching indexers` for
content that was never gone — a near-miss on unwanted duplicate grabs.

Always: **unmonitor → do the file work → refresh → verify records reconciled →
re-monitor.** Afterwards check `GET /api/v3/queue` and history for `grabbed`
events to confirm nothing was pulled. Note that unmonitoring a series also
touches per-season `monitored` flags — capture their prior state first if you
intend a faithful restore.

### Refresh vs. Rescan

- `RefreshMovie` / `RefreshSeries` — correctly scoped to the given ID, normally
  seconds. This is what production code uses.
- `RescanMovie` / `RescanSeries` — has been observed sweeping the entire
  library. Avoid for routine reconciliation.
- Exception worth remembering: `RefreshSeries` once **hung indefinitely** on one
  series (reproducibly, even after a container restart) where `RescanSeries`
  completed in 9 s. If a refresh wedges, a scoped rescan is the fallback.

A refresh only churns the file record when the file on disk actually changed; a
refresh over unchanged files is harmless.

### `sceneName` / `originalFilePath` are unrecoverable once lost

Any rename-triggered reconciliation deletes and recreates the underlying
`moviefile`/`episodefile` row, permanently clearing these two fields.

**This is settled — do not re-investigate.** Confirmed empirically: no API
accepts them (`PUT` silently ignores), manual import does not repopulate them,
and renaming a file to match the historical value does not restore it. Upstream
acknowledges it (Radarr#6547, Radarr#4339, Sonarr#2335). The only known recovery
is raw SQL against the *arr's own database via `History.SourceTitle`, which this
project deliberately does not do — it would mean coupling to another
application's private schema.

`customFormatScore` and `releaseGroup` **do** survive (release group only if the
filename keeps the `-GROUPNAME` convention).

### Read `customFormatScore` from the right endpoint

`GET /api/v3/movie/{id}`'s embedded `movieFile` object **omits**
`customFormatScore` and `customFormats` entirely — the keys are absent, not
null. Reading them there produces convincing false "the score was wiped"
conclusions. Use the dedicated `GET /api/v3/moviefile/{id}`.

### Manual imports do not fire webhooks

A `ManualImport` command issued via the API carries no download-client
provenance, and Radarr does not emit an `onDownload`/import notification for it
— so remuXcode never hears about it. Anything that needs to re-sync after a
manual reconciliation must call its own sync path directly rather than waiting
for a webhook to come back around.

---

## Working Agreements

- **`main` is branch-protected** (PR + "Run Linters" required). Prefer a PR,
  especially for anything touching CI or the lint gate itself — so the gate
  actually runs on the change before it lands.
- **Verify UI changes in a real browser**, not just via type-check and lint.
  A Svelte gotcha found exactly this way: `bind:value` on
  `<input type="number">` coerces to an actual JS **number**, so a `=== '0'`
  string comparison silently never matches.
- **Don't blanket-autofix a large lint resync.** When a tool version jumps
  several releases, review the diagnostics — a bump once began reformatting
  `app.html`/`favicon.svg` (reordered attributes, a trailing space before `>`,
  a rewritten pre-paint script) and erroring on the favicon, none of it wanted.
- **Prefer scoped lint globs over broad prefixes.** The Biome hook is scoped to
  source extensions rather than a bare `^frontend/`, because tools grow support
  for new file types and a broad prefix silently widens what they touch.
- **Ship user-visible changes with their docs.** See below — a change a user can
  see is not finished until the page describing it says so.

---

## Documentation Is Part of the Change

`docs/` is user-facing reference, not a changelog or an afterthought. **Any
change a user can observe must land in the same PR as its documentation
update.** That includes:

- a new feature, setting, toggle, page, button, modal, or job type
- a changed default, renamed setting, or altered behaviour of an existing one
- a removed or deprecated feature
- a new or changed API endpoint, request body, or response shape
- anything that changes what the UI shows or what a user has to do

Pure refactors, internal helpers, test-only changes, and dependency bumps do
not need a docs change — if a user cannot tell the difference, neither can the
docs.

### Where each change goes

| Change | Update |
| --- | --- |
| New/changed setting or default | `docs/settings.md` (the settings tables are exhaustive — keep them that way) |
| Dashboard, Movies, Shows, Jobs, or Logs page behaviour | the matching `docs/<page>.md` |
| Sonarr/Radarr integration, webhook triggers, workflow advice | `docs/integrations.md` |
| New API endpoint or changed payload | the `## API` section in `README.md` |
| New headline capability | the Features list in `README.md`, plus the relevant `docs/` page |
| New or changed screenshot-worthy UI | refresh the affected image in `images/` |

Two further rules, both learned the same way version pins were:

- **Don't duplicate.** `README.md` gets the one-line summary; `docs/` gets the
  detail. Repeating the detail in both guarantees they drift apart, exactly as
  a version pinned in five places does.
- **Document the *why* for anything non-obvious**, especially opt-in defaults.
  `general.fix_container_mismatch` ships off because enabling it makes an *arr
  recreate the file record and permanently lose `sceneName` — a user reading
  only "corrects the file extension" would turn it on and be surprised.

---

## Project Layout

```
backend/
  core.py          # Job queue, pipeline orchestration, Sonarr/Radarr triggers
  api_webhook.py   # POST /api/webhook — Sonarr/Radarr webhook handler
  api_convert.py   # Manual convert endpoints
  api_jobs.py      # Job CRUD, logs, cancel
  workers/
    video.py       # Video encoding (SVT-AV1/x265, QSV, VAAPI, NVENC paths)
    audio.py       # DTS/TrueHD conversion
    cleanup.py     # Stream pruning (language/subtitle filtering)
  utils/
    hwaccel.py     # Hardware capability detection + per-encoder probing
    config.py      # Config loading/saving
    ffprobe.py     # Media info extraction
frontend/
  src/routes/config/+page.svelte  # Settings UI
  src/lib/types.ts                # Types matching backend API responses
scripts/
  check_biome_pins.py             # Lint-gate version-drift guard
  fix_container_mismatches.py     # One-off library sweep, dry-run by default
docs/                             # User-facing reference
```

---

## A note on local environment context

Deployment topology, host addresses, and credential locations are intentionally
**not** in this file — this repository is public. Maintainers keep those in a
git-ignored `CLAUDE.md` (see `.gitignore`). If you are an agent working in a
checkout that has one, read it as well; it carries the environment specifics
that this file omits, and the two are not meant to duplicate each other.
