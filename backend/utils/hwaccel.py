"""Hardware acceleration detection for FFmpeg encoding."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from pathlib import Path
import subprocess

logger = logging.getLogger(__name__)

# Encoder names grouped by acceleration method
HW_HEVC_ENCODERS = {
    "qsv": "hevc_qsv",
    "vaapi": "hevc_vaapi",
    "nvenc": "hevc_nvenc",
}
HW_AV1_ENCODERS = {
    "qsv": "av1_qsv",
    "vaapi": "av1_vaapi",
    "nvenc": "av1_nvenc",
}
SW_HEVC_ENCODER = "libx265"
SW_AV1_ENCODER = "libsvtav1"


@dataclass
class HWAccelCaps:
    """Detected hardware acceleration capabilities."""

    render_devices: list[str] = field(default_factory=list)
    gpu_vendor: str = ""  # "intel", "nvidia", "amd", ""
    vaapi_available: bool = False
    qsv_available: bool = False
    nvenc_available: bool = False
    hevc_encoders: list[str] = field(default_factory=list)
    av1_encoders: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """Serialise for the API."""
        return {
            "render_devices": self.render_devices,
            "gpu_vendor": self.gpu_vendor,
            "vaapi_available": self.vaapi_available,
            "qsv_available": self.qsv_available,
            "nvenc_available": self.nvenc_available,
            "hevc_encoders": self.hevc_encoders,
            "av1_encoders": self.av1_encoders,
        }


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

_cached_caps: HWAccelCaps | None = None


def detect_hw_capabilities(*, force: bool = False) -> HWAccelCaps:
    """Probe the system for hardware-accelerated encoders.

    Results are cached after the first call.  Pass *force=True* to re-probe.
    """
    global _cached_caps  # noqa: PLW0603
    if _cached_caps is not None and not force:
        return _cached_caps

    caps = HWAccelCaps()

    # 1. Check for DRI render devices (/dev/dri/renderD*)
    dri = Path("/dev/dri")
    if dri.exists():
        caps.render_devices = sorted(str(p) for p in dri.glob("renderD*"))

    if caps.render_devices:
        caps.gpu_vendor = _detect_gpu_vendor()

    # 2. Enumerate ffmpeg encoders
    encoder_text = _ffmpeg_encoders()

    hw_encoder_tokens = {
        "hevc_qsv",
        "hevc_vaapi",
        "hevc_nvenc",
        "av1_qsv",
        "av1_vaapi",
        "av1_nvenc",
    }
    for line in encoder_text.splitlines():
        stripped = line.strip()
        for tok in hw_encoder_tokens:
            if tok in stripped:
                if tok.startswith("hevc_"):
                    caps.hevc_encoders.append(tok)
                else:
                    caps.av1_encoders.append(tok)
                if tok.endswith("_qsv"):
                    caps.qsv_available = True
                elif tok.endswith("_vaapi"):
                    caps.vaapi_available = True
                elif tok.endswith("_nvenc"):
                    caps.nvenc_available = True

    # Always list software encoders last
    if SW_HEVC_ENCODER in encoder_text:
        caps.hevc_encoders.append(SW_HEVC_ENCODER)
    if SW_AV1_ENCODER in encoder_text:
        caps.av1_encoders.append(SW_AV1_ENCODER)

    # 3. If QSV was found in the encoder list, verify it can actually initialise
    if caps.qsv_available:
        if not caps.render_devices or not _test_qsv():
            logger.info("QSV encoder listed but init failed — disabling")
            caps.qsv_available = False
            caps.hevc_encoders = [e for e in caps.hevc_encoders if "_qsv" not in e]
            caps.av1_encoders = [e for e in caps.av1_encoders if "_qsv" not in e]

    if caps.vaapi_available and not caps.render_devices:
        caps.vaapi_available = False
        caps.hevc_encoders = [e for e in caps.hevc_encoders if "_vaapi" not in e]
        caps.av1_encoders = [e for e in caps.av1_encoders if "_vaapi" not in e]

    # NVENC requires an NVIDIA GPU — the encoder may be compiled in but unusable
    if caps.nvenc_available and caps.gpu_vendor != "nvidia":
        caps.nvenc_available = False
        caps.hevc_encoders = [e for e in caps.hevc_encoders if "_nvenc" not in e]
        caps.av1_encoders = [e for e in caps.av1_encoders if "_nvenc" not in e]

    _log_caps(caps)
    _cached_caps = caps
    return caps


def resolve_encoder(
    target_codec: str,
    hw_accel: str,
    caps: HWAccelCaps | None = None,
) -> str:
    """Return the ffmpeg encoder name to use, falling back to software.

    Args:
        target_codec: "hevc" or "av1"
        hw_accel: "auto", "qsv", "vaapi", "nvenc", or "none"
        caps: Pre-detected capabilities (auto-detected if *None*)
    """
    if hw_accel == "none":
        return SW_HEVC_ENCODER if target_codec == "hevc" else SW_AV1_ENCODER

    if caps is None:
        caps = detect_hw_capabilities()

    encoder_map = HW_HEVC_ENCODERS if target_codec == "hevc" else HW_AV1_ENCODERS
    encoder_list = caps.hevc_encoders if target_codec == "hevc" else caps.av1_encoders
    sw_fallback = SW_HEVC_ENCODER if target_codec == "hevc" else SW_AV1_ENCODER

    if hw_accel == "auto":
        # Prefer QSV > VAAPI > NVENC > software
        for method in ("qsv", "vaapi", "nvenc"):
            enc = encoder_map.get(method, "")
            if enc in encoder_list:
                return enc
        return sw_fallback

    # Explicit method requested
    enc = encoder_map.get(hw_accel, "")
    if enc in encoder_list:
        return enc

    logger.warning(
        "Requested hw_accel=%s but %s encoder not available — falling back to software",
        hw_accel,
        enc or hw_accel,
    )
    return sw_fallback


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ffmpeg_encoders() -> str:
    """Run ``ffmpeg -encoders`` and return stdout."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return result.stdout
    except (subprocess.SubprocessError, FileNotFoundError):
        logger.warning("Could not list ffmpeg encoders")
        return ""


def _detect_gpu_vendor() -> str:
    """Best-effort GPU vendor detection via /sys or lspci."""
    # Check sysfs for DRI device vendor
    for card in sorted(Path("/sys/class/drm").glob("card[0-9]*")):
        vendor_path = card / "device" / "vendor"
        if vendor_path.exists():
            vendor_id = vendor_path.read_text().strip()
            if vendor_id == "0x8086":
                return "intel"
            if vendor_id in ("0x10de", "0x12d2"):
                return "nvidia"
            if vendor_id in ("0x1002", "0x1022"):
                return "amd"
    return ""


def _test_qsv() -> bool:
    """Quick test: can QSV initialise and encode on this machine.

    Uses ``-init_hw_device qsv`` + software-decoded synthetic input to
    match the actual encoding pipeline (SW decode → QSV encode).
    """
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-init_hw_device",
                "qsv=hw",
                "-filter_hw_device",
                "hw",
                "-f",
                "lavfi",
                "-i",
                "nullsrc=s=256x256:d=0.1",
                "-vf",
                "format=nv12,hwupload=extra_hw_frames=64",
                "-frames:v",
                "1",
                "-c:v",
                "hevc_qsv",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def _log_caps(caps: HWAccelCaps) -> None:
    """Log a summary of detected capabilities."""
    if not caps.render_devices:
        logger.info("HW accel: no render devices found — software encoding only")
        return

    methods = []
    if caps.qsv_available:
        methods.append("QSV")
    if caps.vaapi_available:
        methods.append("VAAPI")
    if caps.nvenc_available:
        methods.append("NVENC")

    logger.info(
        "HW accel: vendor=%s devices=%s methods=[%s] hevc=%s av1=%s",
        caps.gpu_vendor,
        caps.render_devices,
        ", ".join(methods) or "none",
        caps.hevc_encoders,
        caps.av1_encoders,
    )
