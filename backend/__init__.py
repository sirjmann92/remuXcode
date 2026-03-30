"""remuXcode backend.

Unified service for audio and video conversion with Sonarr/Radarr integration.
"""

import os

__version__ = os.getenv("APP_VERSION", "dev")
