"""Regression tests for per-job Custom Encode overrides (CRF, preset, VBV).

Custom Encode already sends a free-form ``encode_options`` dict through to
each worker (used for target_resolution/strip_hdr/retain_dv/force_encode).
These tests cover the same mechanism extended with three more keys — crf,
preset, and vbv_maxrate — across every encoder path: software HEVC
(libx265), software AV1 (libsvtav1), and the three hardware encoders
(QSV/VAAPI/NVENC), each of which selects its "quality" value through the
shared ``_get_quality_params`` helper.

Command-list assertions only (no real ffmpeg run) — mirrors the existing
style in test_subtitle_transcode.py for command-builder-level coverage.
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.utils.anime_detect import ContentType
from backend.utils.config import VideoConfig
from backend.workers.video import VideoConverter


def _converter(hw_accel: str = "none", codec: str = "hevc") -> VideoConverter:
    config = VideoConfig(codec=codec, hw_accel=hw_accel)
    return VideoConverter(config=config, hw_accel=hw_accel)


def _x265_params(cmd: list[str]) -> str:
    return cmd[cmd.index("-x265-params") + 1]


def test_hevc_command_uses_configured_defaults_when_no_override():
    """No encode_options -> configured CRF/preset/VBV pass through unchanged."""
    conv = _converter()
    cmd = conv._build_hevc_command(
        "in.mkv", "out.mkv", ContentType.LIVE_ACTION, encode_options=None
    )
    params = _x265_params(cmd)
    assert f"crf={conv.config.live_action_crf}" in params
    assert f"vbv-maxrate={conv.config.vbv_maxrate}" in params
    assert conv.config.live_action_preset in cmd


def test_hevc_command_applies_crf_and_preset_override():
    """A per-job crf/preset override replaces the configured live-action defaults."""
    conv = _converter()
    cmd = conv._build_hevc_command(
        "in.mkv",
        "out.mkv",
        ContentType.LIVE_ACTION,
        encode_options={"crf": 17, "preset": "veryslow"},
    )
    assert "crf=17" in _x265_params(cmd)
    assert "veryslow" in cmd
    assert conv.config.live_action_preset != "veryslow"  # sanity: override actually changed it


def test_hevc_command_applies_vbv_override_with_2x_bufsize():
    """A vbv_maxrate override derives buffer size at the standard 2x ratio."""
    conv = _converter()
    cmd = conv._build_hevc_command(
        "in.mkv", "out.mkv", ContentType.LIVE_ACTION, encode_options={"vbv_maxrate": 10000}
    )
    params = _x265_params(cmd)
    assert "vbv-maxrate=10000" in params
    assert "vbv-bufsize=20000" in params


def test_hevc_command_vbv_zero_disables_cap():
    """0 is an explicit override (not "no override"), disabling the VBV cap."""
    conv = _converter()
    cmd = conv._build_hevc_command(
        "in.mkv", "out.mkv", ContentType.LIVE_ACTION, encode_options={"vbv_maxrate": 0}
    )
    params = _x265_params(cmd)
    assert "vbv-maxrate=0" in params
    assert "vbv-bufsize=0" in params


def test_av1_command_applies_crf_and_preset_override():
    """SVT-AV1's -crf/-preset flags honor the same override keys as HEVC."""
    conv = _converter(codec="av1")
    cmd = conv._build_av1_command(
        "in.mkv",
        "out.mkv",
        ContentType.LIVE_ACTION,
        encode_options={"crf": 15, "preset": 2},
    )
    assert "-crf" in cmd and cmd[cmd.index("-crf") + 1] == "15"
    assert "-preset" in cmd and cmd[cmd.index("-preset") + 1] == "2"


def test_get_quality_params_crf_override_applies_to_hw_encoders():
    """The same "crf" key overrides a hardware encoder's quality value too."""
    conv = _converter(hw_accel="qsv")
    quality, _ = conv._get_quality_params(
        ContentType.LIVE_ACTION, "hevc_qsv", encode_options={"crf": 12}
    )
    assert quality == 12


def test_get_quality_params_no_override_uses_encoder_specific_default():
    """Without an override, QSV reads its own config field, not the CRF fields."""
    conv = _converter(hw_accel="qsv")
    quality, _ = conv._get_quality_params(ContentType.LIVE_ACTION, "hevc_qsv", encode_options=None)
    assert quality == conv.config.qsv_live_action_quality
