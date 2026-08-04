"""Regression tests for check_media_mounts().

A prior version checked whether each configured Sonarr/Radarr *root folder*
was non-empty. That produced false positives for root folders that are
legitimate, intentionally-unused organizational categories (e.g. a "Cartoon"
root with zero series ever assigned to it) — empty by the user's own choice,
nothing to do with mount health. This flagged a live production instance as
degraded on every restart even though its mounts were completely healthy.

check_media_mounts() now samples actual episode/movie files Sonarr/Radarr
report as already present, never root folders — these tests lock that in.
"""

from pathlib import Path
import sys
import tempfile
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend import core


class _FakeConfig:
    class sonarr:
        url = "http://fake-sonarr:8989"
        api_key = "fakekey"

    class radarr:
        url = "http://fake-radarr:7878"
        api_key = "fakekey"


def _reset(path_mappings: list[tuple[str, str]]) -> None:
    core.config = _FakeConfig()
    core.PATH_MAPPINGS = path_mappings
    core._mount_health_cache["checked_at"] = 0.0


def test_empty_organizational_root_folder_is_not_flagged():
    """The exact real-world bug: zero series assigned to a configured
    category root folder must never be treated as a mount problem, since
    check_media_mounts no longer looks at root folders at all — only at
    files Sonarr/Radarr say actually exist, wherever they may be.
    """
    with tempfile.TemporaryDirectory() as populated:
        Path(populated, "ep1.mkv").touch()
        _reset([("/share/Shows/Good", populated)])

        def fake_get(url, params=None, headers=None, timeout=None):
            resp = MagicMock()
            resp.raise_for_status = lambda: None
            if "episodefile" in url:
                resp.json = lambda: [{"path": "/share/Shows/Good/ep1.mkv"}]
                return resp
            if "series" in url:
                # One real series with a file, mirroring three additional
                # configured-but-empty category root folders that simply
                # have zero series assigned (never queried by this function).
                resp.json = lambda: [{"id": 1, "statistics": {"episodeFileCount": 5}}]
                return resp
            resp.json = list
            return resp

        with patch("backend.core.requests.get", side_effect=fake_get):
            assert core.check_media_mounts() == []


def test_genuine_outage_is_flagged():
    """When every sampled file Sonarr reports is invisible, that's a real
    problem and must be reported.
    """
    _reset([("/share/Shows/Good", "/nonexistent")])

    def fake_get(url, params=None, headers=None, timeout=None):
        resp = MagicMock()
        resp.raise_for_status = lambda: None
        if "episodefile" in url:
            resp.json = lambda: [{"path": "/share/Shows/Good/ep1.mkv"}]
            return resp
        if "series" in url:
            resp.json = lambda: [
                {"id": i, "statistics": {"episodeFileCount": 1}} for i in range(1, 6)
            ]
            return resp
        resp.json = list
        return resp

    with patch("backend.core.requests.get", side_effect=fake_get):
        warnings = core.check_media_mounts()
        assert len(warnings) == 1
        assert "Sonarr" in warnings[0]


def test_partial_visibility_is_not_flagged():
    """Some sampled files missing but at least one visible is not treated as
    an outage — a genuine share outage fails everything, not a fraction.
    """
    with tempfile.TemporaryDirectory() as d:
        Path(d, "exists.mkv").touch()
        _reset([("/share/Shows/A", d)])

        def fake_get(url, params=None, headers=None, timeout=None):
            resp = MagicMock()
            resp.raise_for_status = lambda: None
            if "episodefile" in url:
                sid = params.get("seriesId")
                path = (
                    "/share/Shows/A/exists.mkv" if sid == 1 else "/share/Shows/A/does-not-exist.mkv"
                )
                resp.json = lambda: [{"path": path}]
                return resp
            if "series" in url:
                resp.json = lambda: [
                    {"id": i, "statistics": {"episodeFileCount": 1}} for i in (1, 2, 3)
                ]
                return resp
            resp.json = list
            return resp

        with patch("backend.core.requests.get", side_effect=fake_get):
            assert core.check_media_mounts() == []


def test_fresh_install_with_no_files_anywhere_is_not_flagged():
    """Nothing to sample (no series/movies have files yet) means nothing to
    check — not an error.
    """
    _reset([])

    def fake_get(url, params=None, headers=None, timeout=None):
        resp = MagicMock()
        resp.raise_for_status = lambda: None
        if "series" in url:
            resp.json = lambda: [{"id": 1, "statistics": {"episodeFileCount": 0}}]
        elif "movie" in url:
            resp.json = lambda: [{"hasFile": False, "movieFile": {}}]
        else:
            resp.json = list
        return resp

    with patch("backend.core.requests.get", side_effect=fake_get):
        assert core.check_media_mounts() == []


def test_radarr_genuine_outage_is_flagged():
    """Same outage detection for Radarr movies, using the embedded
    movieFile.path (no extra per-movie API call needed).
    """
    _reset([])

    def fake_get(url, params=None, headers=None, timeout=None):
        resp = MagicMock()
        resp.raise_for_status = lambda: None
        if "movie" in url:
            resp.json = lambda: [
                {"hasFile": True, "movieFile": {"path": f"/share/Movies/M{i}/movie.mkv"}}
                for i in range(5)
            ]
        else:
            resp.json = list
        return resp

    with patch("backend.core.requests.get", side_effect=fake_get):
        warnings = core.check_media_mounts()
        assert len(warnings) == 1
        assert "Radarr" in warnings[0]
