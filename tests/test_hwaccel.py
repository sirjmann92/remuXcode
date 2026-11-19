"""Tests for VAAPI runtime probing in hardware capability detection."""

from unittest.mock import patch

from backend.utils import hwaccel

ENCODERS = """
 V....D hevc_vaapi           H.265/HEVC (VAAPI) (codec hevc)
 V....D av1_vaapi            AV1 (VAAPI) (codec av1)
 V....D libx265              libx265 H.265 / HEVC (codec hevc)
 V....D libsvtav1            SVT-AV1 (codec av1)
"""


def _detect(probe):
    with (
        patch.object(hwaccel, "_ffmpeg_encoders", return_value=ENCODERS),
        patch.object(hwaccel, "_detect_gpu_vendor", return_value="amd"),
        patch.object(hwaccel, "_test_vaapi_encoder", side_effect=probe),
        patch.object(hwaccel.Path, "glob", return_value=[hwaccel.Path("/dev/dri/renderD128")]),
        patch.object(hwaccel.Path, "exists", return_value=True),
    ):
        return hwaccel.detect_hw_capabilities(force=True)


def test_unsupported_vaapi_encoder_is_dropped():
    """AMD Vega-class GPUs encode HEVC but not AV1 — auto must not pick av1_vaapi."""
    caps = _detect(lambda enc, _dev: enc == "hevc_vaapi")
    assert caps.vaapi_available
    assert caps.hevc_encoders == ["hevc_vaapi", "libx265"]
    assert caps.av1_encoders == ["libsvtav1"]
    assert hwaccel.resolve_encoder("av1", "auto", caps) == "libsvtav1"
    assert hwaccel.resolve_encoder("hevc", "auto", caps) == "hevc_vaapi"


def test_broken_va_driver_disables_vaapi():
    """A missing VA driver fails every probe, so VAAPI must be reported unavailable."""
    caps = _detect(lambda _enc, _dev: False)
    assert not caps.vaapi_available
    assert hwaccel.resolve_encoder("hevc", "vaapi", caps) == "libx265"
