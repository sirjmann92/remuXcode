#!/usr/bin/env python3
"""FFprobe wrapper.

Provides structured access to media file information via ffprobe.
Returns parsed JSON data about video, audio, and subtitle streams.
"""

import contextlib
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import re
import subprocess

logger = logging.getLogger(__name__)

# Audio-descriptor keywords that would appear in functional track titles
# but not in participant name lists.
_AUDIO_DESCRIPTOR_WORDS = frozenset(
    {
        "surround",
        "stereo",
        "mono",
        "atmos",
        "dts",
        "dolby",
        "truehd",
        "aac",
        "flac",
        "english",
        "spanish",
        "french",
        "german",
        "japanese",
        "italian",
        "portuguese",
        "russian",
        "arabic",
        "hindi",
        "korean",
        "chinese",
        "original",
        "dubbed",
        "subtitle",
        "director",
        "commentary",
        "description",
        "descriptive",
        "hearing",
        "impaired",
    }
)
# A single proper-name word: starts with a capital, rest are lowercase letters
# plus optional apostrophe/hyphen (e.g. O'Brien, Smith-Jones).
_NAME_WORD_RE = re.compile(r"^[A-Z][a-zA-Z'\-]+$")


def _looks_like_commentary_participants(title: str) -> bool:
    """Return True if *title* looks like a list of commentary participants.

    Matches Blu-ray convention of naming commentary tracks after the
    participants, e.g. "John Krasinski, Jenna Fischer, and Ken Kwapis".
    Requires at least two comma-separated segments that each look like
    proper names and contain no audio-descriptor keywords.
    """
    if not title or ("," not in title and " and " not in title.lower()):
        return False
    # Split on commas and the word "and" (handles "…, and Name")
    parts = re.split(r",\s*|\s+and\s+", title.strip())
    if len(parts) < 2:
        return False
    name_count = 0
    for part in parts:
        part = part.strip()
        if not part:
            continue
        words = part.split()
        # Reject if any word is an audio descriptor
        if any(w.lower() in _AUDIO_DESCRIPTOR_WORDS for w in words):
            return False
        # Each segment should be 1-4 capitalised words (first + last [+ middle])
        if 1 <= len(words) <= 4 and all(_NAME_WORD_RE.match(w) for w in words):
            name_count += 1
    return name_count >= 2


@dataclass
class VideoStream:
    """Represents a video stream in a media file."""

    index: int
    codec_name: str
    codec_long_name: str
    profile: str | None
    width: int
    height: int
    pix_fmt: str
    bit_depth: int
    frame_rate: str
    duration: float | None
    bitrate: int | None
    color_primaries: str | None = None
    color_trc: str | None = None
    color_space: str | None = None
    # HDR10 mastering display in x265-format: G(x,y)B(x,y)R(x,y)WP(x,y)L(max,min)
    hdr_master_display: str | None = None
    # HDR10 content light level: "maxCLL,maxFALL"
    hdr_max_cll: str | None = None
    # Dolby Vision RPU layer detected (DOVI configuration record side data)
    is_dolby_vision: bool = False
    # HDR10+ dynamic metadata detected (SMPTE ST 2094-40 side data)
    is_hdr10_plus: bool = False

    @property
    def is_hevc(self) -> bool:
        """Check if stream is HEVC/H.265."""
        return self.codec_name.lower() in ("hevc", "h265")

    @property
    def is_av1(self) -> bool:
        """Check if stream is AV1."""
        return self.codec_name.lower() in ("av1", "av01")

    @property
    def is_h264(self) -> bool:
        """Check if stream is H.264/AVC."""
        return self.codec_name.lower() in ("h264", "avc", "avc1")

    @property
    def is_10bit(self) -> bool:
        """Check if stream is 10-bit."""
        return self.bit_depth >= 10 or "10" in (self.pix_fmt or "")

    @property
    def is_10bit_h264(self) -> bool:
        """Check if stream is 10-bit H.264 (needs conversion for compatibility)."""
        return self.is_h264 and self.is_10bit

    @property
    def is_legacy_codec(self) -> bool:
        """Check if stream uses a legacy codec (VC-1, MPEG-2, MPEG-4/Xvid/DivX)."""
        return self.codec_name.lower() in (
            "vc1",
            "wmv3",
            "mpeg2video",
            "mpeg4",
            "msmpeg4v3",
            "msmpeg4v2",
            "divx",
            "xvid",
        )


@dataclass
class AudioStream:
    """Represents an audio stream in a media file."""

    index: int
    codec_name: str
    codec_long_name: str
    profile: str | None
    channels: int
    channel_layout: str | None
    sample_rate: int
    bitrate: int | None
    language: str | None
    title: str | None
    is_default: bool
    is_forced: bool
    # True when the track is a director's commentary or similar secondary track.
    # Such tracks must not be treated as a "compatible companion" for the main
    # audio stream — otherwise a 2.0 commentary AC3 would suppress conversion
    # of the primary DTS-HD MA 5.1 track.
    is_commentary: bool = False

    @property
    def is_dts(self) -> bool:
        """Check if stream is DTS (any variant including DTS:X)."""
        return self.codec_name.lower().startswith("dts")

    @property
    def is_dts_x(self) -> bool:
        """Check if stream is DTS:X (object-based DTS)."""
        if not self.is_dts:
            return False
        if self.profile:
            # Normalise to uppercase, strip punctuation for matching
            normalised = self.profile.upper().replace("-", "").replace(":", "").replace(" ", "")
            if "DTSX" in normalised:
                return True
        return False

    @property
    def is_truehd(self) -> bool:
        """Check if stream is TrueHD."""
        return self.codec_name.lower() == "truehd"

    @property
    def is_lossless(self) -> bool:
        """Check if stream is lossless (DTS-HD MA, TrueHD, FLAC, etc.)."""
        lossless_codecs = ("truehd", "flac", "alac", "pcm", "mlp")
        return (
            self.codec_name.lower() in lossless_codecs
            or "dts-hd ma" in (self.codec_long_name or "").lower()
            or "dts_ma" in self.codec_name.lower()
        )


@dataclass
class SubtitleStream:
    """Represents a subtitle stream in a media file."""

    index: int
    codec_name: str
    language: str | None
    title: str | None
    is_default: bool
    is_forced: bool
    is_hearing_impaired: bool

    @property
    def is_sdh(self) -> bool:
        """Alias for is_hearing_impaired (SDH = Subtitles for Deaf/Hard of Hearing)."""
        return self.is_hearing_impaired


@dataclass
class AttachmentStream:
    """Represents an attachment stream (fonts, images, etc.)."""

    index: int
    codec_name: str
    filename: str | None = None
    mimetype: str | None = None


@dataclass
class MediaInfo:
    """Complete information about a media file."""

    path: Path
    format_name: str
    duration: float
    size: int
    bitrate: int
    video_streams: list[VideoStream]
    audio_streams: list[AudioStream]
    subtitle_streams: list[SubtitleStream]
    attachment_streams: list[AttachmentStream]
    chapters: list[dict]
    format_tags: dict[str, str] = None  # type: ignore[assignment]  # container-level metadata

    def __post_init__(self) -> None:
        """Initialize mutable default fields."""
        if self.format_tags is None:
            self.format_tags = {}

    @property
    def primary_video(self) -> VideoStream | None:
        """Get the primary (first) video stream."""
        return self.video_streams[0] if self.video_streams else None

    @property
    def has_dts(self) -> bool:
        """Check if file has any DTS audio streams."""
        return any(s.is_dts for s in self.audio_streams)

    @property
    def has_dts_x(self) -> bool:
        """Check if file has any DTS:X audio streams."""
        return any(s.is_dts_x for s in self.audio_streams)

    @property
    def has_truehd(self) -> bool:
        """Check if file has any TrueHD audio streams."""
        return any(s.is_truehd for s in self.audio_streams)

    @property
    def is_hevc(self) -> bool:
        """Check if primary video is already HEVC."""
        video = self.primary_video
        return video is not None and video.is_hevc

    @property
    def is_av1(self) -> bool:
        """Check if primary video is already AV1."""
        video = self.primary_video
        return video is not None and video.is_av1


class FFProbe:
    """Wrapper around ffprobe for media file analysis."""

    def __init__(self, ffprobe_path: str = "ffprobe"):
        """Initialize with path to the ffprobe executable."""
        self.ffprobe_path = ffprobe_path

    def get_file_info(self, file_path: str) -> MediaInfo | None:
        """Get complete information about a media file.

        Args:
            file_path: Path to the media file

        Returns:
            MediaInfo object or None if analysis fails
        """
        path = Path(file_path)
        if not path.exists():
            logger.error("File not found: %s", file_path)
            return None

        try:
            result = subprocess.run(
                [
                    self.ffprobe_path,
                    "-v",
                    "quiet",
                    "-analyzeduration",
                    "200M",
                    "-probesize",
                    "200M",
                    "-print_format",
                    "json",
                    "-show_format",
                    "-show_streams",
                    "-show_chapters",
                    str(path),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )

            if result.returncode != 0:
                logger.error("ffprobe failed for %s: %s", file_path, result.stderr)
                return None

            data = json.loads(result.stdout)
            return self._parse_media_info(path, data)

        except subprocess.TimeoutExpired:
            logger.error("ffprobe timeout for %s", file_path)
            return None
        except json.JSONDecodeError as e:
            logger.error("Failed to parse ffprobe output for %s: %s", file_path, e)
            return None
        except Exception as e:
            logger.error("Error analyzing %s: %s", file_path, e)
            return None

    def _parse_media_info(self, path: Path, data: dict) -> MediaInfo:
        """Parse ffprobe JSON output into MediaInfo object."""
        format_info = data.get("format", {})
        streams = data.get("streams", [])
        chapters = data.get("chapters", [])

        video_streams = []
        audio_streams = []
        subtitle_streams = []
        attachment_streams = []

        for stream in streams:
            codec_type = stream.get("codec_type")

            if codec_type == "video":
                video_streams.append(self._parse_video_stream(stream))
            elif codec_type == "audio":
                audio_streams.append(self._parse_audio_stream(stream))
            elif codec_type == "subtitle":
                subtitle_streams.append(self._parse_subtitle_stream(stream))
            elif codec_type == "attachment":
                attachment_streams.append(self._parse_attachment_stream(stream))

        return MediaInfo(
            path=path,
            format_name=format_info.get("format_name", ""),
            duration=float(format_info.get("duration", 0)),
            size=int(format_info.get("size", 0)),
            bitrate=int(format_info.get("bit_rate", 0)),
            video_streams=video_streams,
            audio_streams=audio_streams,
            subtitle_streams=subtitle_streams,
            attachment_streams=attachment_streams,
            chapters=chapters,
            format_tags={k.upper(): v for k, v in format_info.get("tags", {}).items()},
        )

    @staticmethod
    def _parse_hdr_side_data(
        side_data_list: list[dict],
    ) -> tuple[str | None, str | None, bool, bool]:
        """Extract HDR metadata from stream side data.

        Returns:
            (master_display, max_cll, is_dolby_vision, is_hdr10_plus)
            master_display: x265-format string e.g. "G(...)B(...)R(...)WP(...)L(...)"
            max_cll: "maxCLL,maxFALL" e.g. "1000,400"
            is_dolby_vision: True if DOVI configuration record present
            is_hdr10_plus: True if HDR Dynamic Metadata SMPTE2094-40 present
        """
        master_display: str | None = None
        max_cll: str | None = None
        is_dolby_vision = False
        is_hdr10_plus = False

        for sd in side_data_list:
            sd_type = sd.get("side_data_type", "").lower()

            if "dovi configuration" in sd_type or "dolby vision" in sd_type:
                is_dolby_vision = True
            elif "smpte2094-40" in sd_type or "hdr dynamic metadata" in sd_type:
                is_hdr10_plus = True
            elif "mastering display" in sd_type and master_display is None:
                try:

                    def _frac(frac: str, scale: int) -> int:
                        n, d = frac.split("/")
                        return round(int(n) * scale / int(d))

                    gx = _frac(sd["green_x"], 50000)
                    gy = _frac(sd["green_y"], 50000)
                    bx = _frac(sd["blue_x"], 50000)
                    by = _frac(sd["blue_y"], 50000)
                    rx = _frac(sd["red_x"], 50000)
                    ry = _frac(sd["red_y"], 50000)
                    wpx = _frac(sd["white_point_x"], 50000)
                    wpy = _frac(sd["white_point_y"], 50000)
                    max_l = _frac(sd["max_luminance"], 10000)
                    min_l = _frac(sd["min_luminance"], 10000)
                    master_display = (
                        f"G({gx},{gy})B({bx},{by})R({rx},{ry})WP({wpx},{wpy})L({max_l},{min_l})"
                    )
                except (KeyError, ValueError, ZeroDivisionError):
                    pass

            elif "content light level" in sd_type and max_cll is None:
                with contextlib.suppress(TypeError, ValueError):
                    max_cll = f"{sd.get('max_content', 0)},{sd.get('max_average', 0)}"

        return master_display, max_cll, is_dolby_vision, is_hdr10_plus

    def _parse_video_stream(self, stream: dict) -> VideoStream:
        """Parse video stream information."""
        # Determine bit depth
        pix_fmt = stream.get("pix_fmt", "")
        bit_depth = 8
        if "p10" in pix_fmt or "10le" in pix_fmt or "10be" in pix_fmt:
            bit_depth = 10
        elif "p12" in pix_fmt or "12le" in pix_fmt or "12be" in pix_fmt:
            bit_depth = 12
        elif stream.get("bits_per_raw_sample"):
            bit_depth = int(stream.get("bits_per_raw_sample", 8))

        # Parse frame rate
        r_frame_rate = stream.get("r_frame_rate", "0/1")

        # Parse HDR side data
        side_data_list = stream.get("side_data_list", [])
        hdr_master_display, hdr_max_cll, is_dolby_vision, is_hdr10_plus = self._parse_hdr_side_data(
            side_data_list
        )

        return VideoStream(
            index=stream.get("index", 0),
            codec_name=stream.get("codec_name", ""),
            codec_long_name=stream.get("codec_long_name", ""),
            profile=stream.get("profile"),
            width=stream.get("width", 0),
            height=stream.get("height", 0),
            pix_fmt=pix_fmt,
            bit_depth=bit_depth,
            frame_rate=r_frame_rate,
            duration=float(stream.get("duration", 0)) if stream.get("duration") else None,
            bitrate=int(stream.get("bit_rate", 0)) if stream.get("bit_rate") else None,
            color_primaries=stream.get("color_primaries") or None,
            color_trc=stream.get("color_transfer") or None,
            color_space=stream.get("color_space") or None,
            hdr_master_display=hdr_master_display,
            hdr_max_cll=hdr_max_cll,
            is_dolby_vision=is_dolby_vision,
            is_hdr10_plus=is_hdr10_plus,
        )

    def _parse_audio_stream(self, stream: dict) -> AudioStream:
        """Parse audio stream information."""
        tags = stream.get("tags", {})
        disposition = stream.get("disposition", {})

        # Some lossless codecs (DTS-HD MA, TrueHD) report channels=0 when
        # ffprobe cannot fully decode stream parameters.  Fall back to
        # channel_layout for a reliable channel count.
        _LAYOUT_CHANNELS = {
            "mono": 1,
            "stereo": 2,
            "2.1": 3,
            "3.0": 3,
            "3.0(back)": 3,
            "4.0": 4,
            "quad": 4,
            "quad(side)": 4,
            "3.1": 4,
            "5.0": 5,
            "5.0(side)": 5,
            "4.1": 5,
            "5.1": 6,
            "5.1(side)": 6,
            "6.0": 6,
            "6.0(front)": 6,
            "hexagonal": 6,
            "6.1": 7,
            "6.1(back)": 7,
            "6.1(front)": 7,
            "7.0": 7,
            "7.0(front)": 7,
            "7.1": 8,
            "7.1(wide)": 8,
            "7.1(wide-side)": 8,
            "octagonal": 8,
        }
        raw_channels = stream.get("channels", 0)
        raw_layout = stream.get("channel_layout") or ""
        channels = raw_channels or _LAYOUT_CHANNELS.get(raw_layout.lower(), 0)

        # A track is commentary if:
        #   • disposition.comment is set in the container, OR
        #   • the title contains the word "commentary", OR
        #   • the title looks like a participant name list (Blu-ray convention:
        #     tracks are titled with the speakers rather than "Commentary").
        _track_title = tags.get("title") or ""
        is_commentary = (
            disposition.get("comment", 0) == 1
            or "commentary" in _track_title.lower()
            or _looks_like_commentary_participants(_track_title)
        )

        return AudioStream(
            index=stream.get("index", 0),
            codec_name=stream.get("codec_name", ""),
            codec_long_name=stream.get("codec_long_name", ""),
            profile=stream.get("profile"),
            channels=channels,
            channel_layout=stream.get("channel_layout"),
            sample_rate=int(stream.get("sample_rate", 0)),
            bitrate=int(stream.get("bit_rate", 0)) if stream.get("bit_rate") else None,
            language=tags.get("language"),
            title=tags.get("title"),
            is_default=disposition.get("default", 0) == 1,
            is_forced=disposition.get("forced", 0) == 1,
            is_commentary=is_commentary,
        )

    def _parse_subtitle_stream(self, stream: dict) -> SubtitleStream:
        """Parse subtitle stream information."""
        tags = stream.get("tags", {})
        disposition = stream.get("disposition", {})
        title = tags.get("title", "").lower()

        # Detect hearing impaired from title or disposition
        is_hi = (
            disposition.get("hearing_impaired", 0) == 1
            or "sdh" in title
            or "hearing" in title
            or "impaired" in title
            or "cc" in title
        )

        return SubtitleStream(
            index=stream.get("index", 0),
            codec_name=stream.get("codec_name", ""),
            language=tags.get("language"),
            title=tags.get("title"),
            is_default=disposition.get("default", 0) == 1,
            is_forced=disposition.get("forced", 0) == 1,
            is_hearing_impaired=is_hi,
        )

    def _parse_attachment_stream(self, stream: dict) -> AttachmentStream:
        """Parse attachment stream information."""
        tags = stream.get("tags", {})

        return AttachmentStream(
            index=stream.get("index", 0),
            codec_name=stream.get("codec_name", ""),
            filename=tags.get("filename"),
            mimetype=tags.get("mimetype"),
        )
