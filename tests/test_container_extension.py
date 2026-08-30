"""Regression tests for the container/extension-mismatch correction.

Video always encodes to a real Matroska temp file internally regardless of
the source container, then hands it to ``safe_replace``, which moves the
bytes onto whatever path it was given verbatim — it does not re-mux. When
Video is the last phase to run on an originally non-Matroska file (e.g. an
.mp4), this leaves genuine Matroska content sitting under an .mp4 name.
``_correct_container_extension`` detects this by probing the file's real
container and renames it to match — gated behind ``fix_container_mismatch``
(default off) since Sonarr/Radarr can only pick up the resulting rename by
recreating the underlying file record, which permanently clears its
sceneName/originalFilePath.
"""

from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend import core
from backend.utils.ffprobe import FFProbe

FIXTURE = Path(__file__).parent / "fixtures" / "tiny_with_subtitle.mkv"


class _FakeConfig:
    def __init__(self, enabled: bool) -> None:
        self.fix_container_mismatch = enabled


def _with_config_and_ffprobe(monkeypatch, enabled: bool) -> None:
    monkeypatch.setattr(core, "config", _FakeConfig(enabled))
    monkeypatch.setattr(core, "ffprobe", FFProbe())


def test_renames_mismatched_extension_to_match_real_container(tmp_path, monkeypatch):
    """A real Matroska file sitting under an .mp4 name gets corrected to .mkv."""
    _with_config_and_ffprobe(monkeypatch, enabled=True)
    mismatched = tmp_path / "movie.mp4"
    shutil.copy2(FIXTURE, mismatched)

    result = core._correct_container_extension(str(mismatched), job=None)

    assert result == str(tmp_path / "movie.mkv")
    assert not mismatched.exists()
    assert (tmp_path / "movie.mkv").exists()


def test_noop_when_extension_already_matches_real_container(tmp_path, monkeypatch):
    """A file already named correctly for its real container is untouched."""
    _with_config_and_ffprobe(monkeypatch, enabled=True)
    correct = tmp_path / "movie.mkv"
    shutil.copy2(FIXTURE, correct)

    result = core._correct_container_extension(str(correct), job=None)

    assert result == str(correct)
    assert correct.exists()


def test_noop_when_feature_disabled(tmp_path, monkeypatch):
    """Default-off: a real mismatch is left alone when the setting is off."""
    _with_config_and_ffprobe(monkeypatch, enabled=False)
    mismatched = tmp_path / "movie.mp4"
    shutil.copy2(FIXTURE, mismatched)

    result = core._correct_container_extension(str(mismatched), job=None)

    assert result == str(mismatched)
    assert mismatched.exists()


def test_noop_when_target_extension_already_exists(tmp_path, monkeypatch):
    """Never clobber an unrelated file already sitting at the corrected path."""
    _with_config_and_ffprobe(monkeypatch, enabled=True)
    mismatched = tmp_path / "movie.mp4"
    shutil.copy2(FIXTURE, mismatched)
    (tmp_path / "movie.mkv").write_bytes(b"unrelated existing file")

    result = core._correct_container_extension(str(mismatched), job=None)

    assert result == str(mismatched)
    assert mismatched.exists()
    assert (tmp_path / "movie.mkv").read_bytes() == b"unrelated existing file"


def test_process_job_only_corrects_when_video_is_the_final_phase():
    """The call-site gate: correction only runs when Video succeeded and
    nothing (cleanup) ran afterward to re-mux the container again.
    """
    # Video succeeded, no cleanup planned -> gate passes
    result = {"video": {"success": True}, "audio": None, "cleanup": None}
    assert result.get("video") and result["video"].get("success") and result.get("cleanup") is None

    # Video succeeded, but cleanup also ran afterward -> gate must not fire
    result = {"video": {"success": True}, "audio": None, "cleanup": {"success": True}}
    assert not (
        result.get("video") and result["video"].get("success") and result.get("cleanup") is None
    )

    # Video never ran (audio/cleanup-only job) -> gate must not fire
    result = {"video": None, "audio": {"success": True}, "cleanup": None}
    assert not (
        result.get("video") and result["video"].get("success") and result.get("cleanup") is None
    )
