"""Authentication helpers for API routes."""

import os
import uuid
from pathlib import Path

from fastapi import HTTPException, Request


def get_api_key() -> str:
    """Get the active API key. Runtime value takes priority over env vars."""
    import backend.core as core

    # Runtime value (set at startup, updated on regeneration)
    if core.api_key:
        return core.api_key

    # Fallback for calls before initialize_components()
    key = os.getenv(
        "REMUXCODE_API_KEY",
        os.getenv("MEDIA_API_KEY", os.getenv("DTS_WEBHOOK_API_KEY", "")),
    ).strip()
    if key:
        return key

    config_path = os.getenv(
        "REMUXCODE_CONFIG_PATH",
        str(Path(__file__).parent / "config.yaml"),
    )
    key_file = Path(config_path).parent / ".api_key"
    if key_file.is_file():
        return key_file.read_text().strip()

    return ""


def regenerate_api_key() -> str:
    """Generate a new API key and persist it. Returns the new key."""
    import backend.core as core

    new_key = uuid.uuid4().hex + uuid.uuid4().hex  # 64-char hex
    config_path = os.getenv(
        "REMUXCODE_CONFIG_PATH",
        str(Path(__file__).parent / "config.yaml"),
    )
    key_file = Path(config_path).parent / ".api_key"
    key_file.write_text(new_key)
    key_file.chmod(0o600)

    # Update the in-memory copy so get_api_key() picks it up immediately
    core.api_key = new_key
    return new_key


async def require_auth(request: Request) -> None:
    """Validate X-API-Key header. Skip if no key configured."""
    api_key = get_api_key()
    if not api_key:
        return

    request_key = (request.headers.get("X-API-Key") or "").strip()
    if request_key == api_key:
        return

    raise HTTPException(status_code=401, detail="Unauthorized")
