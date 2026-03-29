#!/bin/sh
set -e

log_info() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S %z') INFO] [PUID/PGID] $1"
}

# Set PUID/PGID, defaulting to 1000 if not provided
PUID=${REMUXCODE_PUID:-${PUID:-1000}}
PGID=${REMUXCODE_PGID:-${PGID:-1000}}
USERNAME=appuser
GROUPNAME=appgroup

# Recreate user/group if UID/GID changed
if [ "$(id -u "$USERNAME" 2>/dev/null)" != "$PUID" ] || [ "$(id -g "$USERNAME" 2>/dev/null)" != "$PGID" ]; then
	if id "$USERNAME" >/dev/null 2>&1; then
		deluser "$USERNAME"
	fi
fi

if [ "$(getent group "$GROUPNAME" | cut -d: -f3 2>/dev/null)" != "$PGID" ]; then
	if getent group "$GROUPNAME" >/dev/null 2>&1; then
		delgroup "$GROUPNAME"
	fi
fi

# Handle GID conflicts
EXISTING_GROUP=$(getent group "$PGID" | cut -d: -f1 2>/dev/null)
if [ -n "$EXISTING_GROUP" ] && [ "$EXISTING_GROUP" != "$GROUPNAME" ]; then
	if [ "$EXISTING_GROUP" = "users" ] && [ "$PGID" = "100" ]; then
		log_info "Removing Alpine's 'users' group to use GID 100"
		getent passwd | grep ":100:" | cut -d: -f1 | while read -r username; do
			usermod -g nobody "$username" 2>/dev/null || true
		done
		delgroup users 2>/dev/null || exit 1
	else
		log_info "Error: GID $PGID is in use by system group '$EXISTING_GROUP'"
		exit 1
	fi
fi

# Create group and user
if ! getent group "$GROUPNAME" >/dev/null 2>&1; then
	addgroup -g "$PGID" "$GROUPNAME"
fi

if ! id "$USERNAME" >/dev/null 2>&1; then
	adduser -u "$PUID" -G "$GROUPNAME" -D -s /bin/sh "$USERNAME"
fi

FINAL_UID=$(id -u "$USERNAME" 2>/dev/null)
FINAL_GID=$(id -g "$USERNAME" 2>/dev/null)
log_info "User '$USERNAME' configured with PUID:$FINAL_UID PGID:$FINAL_GID"

# Fix ownership
chown -R "$PUID":"$PGID" /app/logs 2>/dev/null || true
chown -R "$PUID":"$PGID" /app/config 2>/dev/null || true

# Bootstrap config on first run
if [ ! -f /app/config/config.yaml ]; then
    mkdir -p /app/config
    cp /app/backend/config.yaml /app/config/config.yaml
    log_info "Copied default config.yaml to /app/config/ — edit this file to customise settings"
fi

# Normalize log level
case "${LOG_LEVEL:-info}" in
	CRITICAL|critical) uvicorn_loglevel=critical ;;
	ERROR|error) uvicorn_loglevel=error ;;
	WARNING|warning|WARN|warn) uvicorn_loglevel=warning ;;
	INFO|info) uvicorn_loglevel=info ;;
	DEBUG|debug) uvicorn_loglevel=debug ;;
	*) uvicorn_loglevel=info ;;
esac

exec su-exec "$USERNAME" uvicorn backend.app:app \
    --host 0.0.0.0 \
    --port "${PORT:-7889}" \
    --no-access-log \
    --log-level "$uvicorn_loglevel"
