# GitHub Copilot Instructions — remuXcode

Read `CLAUDE.md` at the project root first. It contains the full project context including network topology, hardware details, API keys, deployment workflow, and key behavioral notes.

## Quick Reference

- **Production instance**: `http://192.168.0.134:7889` — no SSH, use HTTP API only
- **This machine (NixToy)**: `192.168.0.130` — runs Sonarr (`:8989`), Radarr (`:7878`), dev Docker
- **API key**: `cat /home/jase/docker/remuxcode/config/.api_key`
- **Deploy**: `docker compose up -d --build` — **required after every change**, backend or frontend. This is Docker-only; there is no hot-reload.
- **Pre-commit**: run `pre-commit run` before committing any code changes (excludes docs/images). Runs ruff, biome, svelte-check, codespell, shellcheck.
- **Python venv**: `.venv/` — `source .venv/bin/activate`

## Development Conventions

- Backend: Python 3.14, FastAPI, typed throughout — maintain type annotations on all new functions
- Frontend: Svelte 5 runes only (`$state`, `$derived`, `$effect`) — do not use legacy Svelte 4 reactive syntax (`$:`, `export let`)
- Linting: `ruff` for Python, `biome` for TypeScript/Svelte (see `biome.json`)
- Do not add docstrings or comments to code you didn't change
- The `config/config.yaml` and `config/.api_key` files are runtime volumes — never committed to git, never modified in the image

## Testing

```bash
source .venv/bin/activate
pytest tests/
```

There is currently only one test file: `tests/test_workers.py`. Add new tests there or in new files under `tests/`.

## Common Debugging Commands

```bash
# Check production job queue
curl -s http://192.168.0.134:7889/api/jobs?limit=20 -H "X-API-Key: $(cat config/.api_key)"

# Check production system info / hardware caps
curl -s http://192.168.0.134:7889/api/system/info -H "X-API-Key: $(cat config/.api_key)"

# Check production config
curl -s http://192.168.0.134:7889/api/config -H "X-API-Key: $(cat config/.api_key)"

# Sonarr history for a series
SONARR_KEY=$(grep -A5 'sonarr:' config/config.yaml | grep api_key | awk '{print $2}')
curl -s "http://192.168.0.130:8989/api/v3/history/series?seriesId=ID&apiKey=$SONARR_KEY"

# Sonarr Docker logs (Sonarr runs on this machine)
docker logs sonarr --since 1h 2>&1 | grep -v "RecycleBin\|Plex\|at NzbDrone"
```
