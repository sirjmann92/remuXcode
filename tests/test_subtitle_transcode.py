"""Regression tests for incompatible subtitle codec handling.

Some subtitle codecs (mov_text from MP4 sources, eia_608 closed captions)
make the Matroska muxer refuse to write the output header at all
("Subtitle codec N is not supported"), killing every stream in the file —
not just the subtitle. Workers must override just those streams to SubRip
when (and only when) the actual ffmpeg output target is Matroska; MP4
targets must leave mov_text alone, since it's the native, valid codec there
and forcing srt would break that mux instead of fixing it.
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.utils.config import CleanupConfig
from backend.utils.ffprobe import MediaInfo, SubtitleStream, subtitle_needs_transcode
from backend.workers.cleanup import StreamCleanup


def test_subtitle_needs_transcode_flags_known_incompatible_codecs():
    """mov_text (MP4) and eia_608 (closed captions) break the Matroska muxer."""
    assert subtitle_needs_transcode("mov_text") is True
    assert subtitle_needs_transcode("eia_608") is True


def test_subtitle_needs_transcode_leaves_compatible_codecs_alone():
    """Codecs the Matroska muxer already accepts must not be flagged."""
    for codec in ("subrip", "ass", "ssa", "hdmv_pgs_subtitle", "dvd_subtitle"):
        assert subtitle_needs_transcode(codec) is False


def _sub(index: int, codec_name: str, language: str = "eng") -> SubtitleStream:
    return SubtitleStream(
        index=index,
        codec_name=codec_name,
        language=language,
        title=None,
        is_default=False,
        is_forced=False,
        is_hearing_impaired=False,
    )


def _media_info(subs: list[SubtitleStream]) -> MediaInfo:
    return MediaInfo(
        path=Path("/library/Movie (2014)/Movie (2014).mp4"),
        format_name="mov,mp4,m4a,3gp,3g2,mj2",
        duration=1200.0,
        size=10**8,
        bitrate=2_000_000,
        video_streams=[],
        audio_streams=[],
        subtitle_streams=subs,
        attachment_streams=[],
        chapters=[],
    )


def test_cleanup_overrides_movtext_when_output_target_is_mkv():
    """A .mkv output target (e.g. chained after Video, which always writes
    .mkv) must transcode mov_text to srt — copying it would abort the mux.
    """
    worker = StreamCleanup(CleanupConfig())
    subs = [_sub(2, "mov_text")]
    info = _media_info(subs)
    cmd = worker._build_ffmpeg_command(
        "/tmp/in.mkv", "/tmp/.remuxcode-temp-x/in.mkv", info, [], subs
    )
    assert "-c:s:0" in cmd
    assert cmd[cmd.index("-c:s:0") + 1] == "srt"


def test_cleanup_leaves_movtext_alone_when_output_target_is_mp4():
    """A standalone Cleanup job on an untouched .mp4 source writes an .mp4
    temp file (mirrors the input's own extension) — mov_text is the native,
    correct codec there and must be left as a plain copy.
    """
    worker = StreamCleanup(CleanupConfig())
    subs = [_sub(2, "mov_text")]
    info = _media_info(subs)
    cmd = worker._build_ffmpeg_command(
        "/tmp/in.mp4", "/tmp/.remuxcode-temp-x/in.mp4", info, [], subs
    )
    assert "-c:s:0" not in cmd


def test_cleanup_does_not_touch_already_compatible_subtitle_codec():
    """A codec already valid in Matroska (subrip) is never overridden."""
    worker = StreamCleanup(CleanupConfig())
    subs = [_sub(2, "subrip")]
    info = _media_info(subs)
    cmd = worker._build_ffmpeg_command(
        "/tmp/in.mkv", "/tmp/.remuxcode-temp-x/in.mkv", info, [], subs
    )
    assert "-c:s:0" not in cmd
