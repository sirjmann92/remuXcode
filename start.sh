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
		userdel "$USERNAME"
	fi
fi

if [ "$(getent group "$GROUPNAME" | cut -d: -f3 2>/dev/null)" != "$PGID" ]; then
	if getent group "$GROUPNAME" >/dev/null 2>&1; then
		groupdel "$GROUPNAME"
	fi
fi

# Handle GID conflicts
EXISTING_GROUP=$(getent group "$PGID" | cut -d: -f1 2>/dev/null)
if [ -n "$EXISTING_GROUP" ] && [ "$EXISTING_GROUP" != "$GROUPNAME" ]; then
	if [ "$EXISTING_GROUP" = "users" ] && [ "$PGID" = "100" ]; then
		log_info "Removing system 'users' group to use GID 100"
		getent passwd | grep ":100:" | cut -d: -f1 | while read -r username; do
			usermod -g nobody "$username" 2>/dev/null || true
		done
		groupdel users 2>/dev/null || exit 1
	else
		log_info "Error: GID $PGID is in use by system group '$EXISTING_GROUP'"
		exit 1
	fi
fi

# Create group and user
if ! getent group "$GROUPNAME" >/dev/null 2>&1; then
	groupadd -g "$PGID" "$GROUPNAME"
fi

if ! id "$USERNAME" >/dev/null 2>&1; then
	useradd -u "$PUID" -g "$GROUPNAME" -s /bin/sh -M "$USERNAME"
fi

FINAL_UID=$(id -u "$USERNAME" 2>/dev/null)
FINAL_GID=$(id -g "$USERNAME" 2>/dev/null)
log_info "User '$USERNAME' configured with PUID:$FINAL_UID PGID:$FINAL_GID"

# Add user to render/video groups for GPU access (/dev/dri)
for dev in /dev/dri/renderD* /dev/dri/card*; do
	[ -e "$dev" ] || continue
	DEV_GID=$(stat -c '%g' "$dev")
	DEV_GROUP=$(getent group "$DEV_GID" | cut -d: -f1 2>/dev/null)
	if [ -z "$DEV_GROUP" ]; then
		DEV_GROUP="devgid${DEV_GID}"
		groupadd -g "$DEV_GID" "$DEV_GROUP" 2>/dev/null || true
	fi
	if ! id -nG "$USERNAME" 2>/dev/null | grep -qw "$DEV_GROUP"; then
		usermod -aG "$DEV_GROUP" "$USERNAME"
		log_info "Added '$USERNAME' to group '$DEV_GROUP' (GID $DEV_GID) for GPU access"
	fi
done

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

exec gosu "$USERNAME" uvicorn backend.app:app \
    --host 0.0.0.0 \
    --port "${PORT:-7889}" \
    --no-access-log \
    --log-level "$uvicorn_loglevel"
